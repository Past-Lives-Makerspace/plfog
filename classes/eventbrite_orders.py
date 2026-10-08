"""Eventbrite orders into plfog registrations, and refunds back out through Eventbrite (#652).

Eventbrite signs nothing it sends, so a webhook body is never trusted: plfog keeps only the
order ID from a strict ``api_url`` match and fetches the order itself with its own token. The
fetched order decides what happens, whatever the action said, so ``order.placed``,
``order.updated`` and ``order.refunded`` share one path and arrive in any order harmlessly.

Each attendee (one ticket) becomes its own Registration, keyed on the attendee ID, so a
redelivered order adds nothing. A seat goes through the same model saves and methods as a site
booking: created PENDING, saved CONFIRMED, and freed by ``cancel`` or ``mark_refunded``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from classes.emails import (
    emit_instructor_new_registration,
    send_admin_registration_notification,
    send_eventbrite_oversold_alert,
    send_eventbrite_shared_email_alert,
)
from classes.eventbrite_finish import send_finish_email
from classes.models import ClassOffering, ClassSettings, Registration
from core.integrations.eventbrite import EventbriteClient, EventbriteError, EventbriteSync, field
from core.services.guest_account import ensure_account_for_registration

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from classes.forms import FinishRegistrationForm

logger = logging.getLogger(__name__)

ORDER_ACTIONS = frozenset({"order.placed", "order.updated", "order.refunded"})
_ORDER_URL = re.compile(r"https://www\.eventbriteapi\.com/v3/orders/(\d+)/?")
REFUNDED_IN_EVENTBRITE = "Refunded in Eventbrite."
CANCELLED_IN_EVENTBRITE = "Cancelled in Eventbrite."
REFUNDED_THROUGH_EVENTBRITE = "Refunded through Eventbrite."
# Eventbrite's refund endpoint documents no body; this is the reason its retention policy example uses.
REFUND_REASON = "no_longer_able_to_attend"
PARTIAL_REFUSED = (
    "Eventbrite would not refund one ticket of this order. Refund it in Eventbrite; "
    "plfog frees the seat when Eventbrite reports the refund."
)
NOTHING_TO_REFUND = "This ticket no longer holds a seat, so there is nothing to refund."
SYNC_OFF_REFUSED = "Eventbrite sync is off, so plfog cannot reach Eventbrite. Refund this ticket in Eventbrite."


class UnverifiedDeliveryError(Exception):
    """A webhook body that does not name an Eventbrite order the way Eventbrite does."""


class EventbriteRefundRefusedError(Exception):
    """Eventbrite did not take the refund; the message is what the refund panel shows."""


class AlreadyFinishedError(Exception):
    """The ticket's waiver was signed by another submit first."""


def order_id_from_delivery(body: bytes) -> str | None:
    """The order ID a webhook delivery names, or None for an action plfog does not act on.

    Raises:
        UnverifiedDeliveryError: The body is not JSON in Eventbrite's shape, or its ``api_url``
            is not an order on www.eventbriteapi.com.
    """
    try:
        payload = json.loads(body)
        action = payload["config"]["action"]
        api_url = payload["api_url"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UnverifiedDeliveryError(f"Not an Eventbrite delivery: {body[:200]!r}") from exc
    if action not in ORDER_ACTIONS:
        return None
    match = _ORDER_URL.fullmatch(str(api_url))
    if match is None:
        raise UnverifiedDeliveryError(f"Not an Eventbrite order URL: {api_url!r}"[:300])
    return match.group(1)


def apply_order(order_id: str) -> None:
    """Make plfog's registrations match the Eventbrite order as it stands now.

    Raises:
        EventbriteError: Sync is off or the order could not be fetched; the webhook answers
            503 so Eventbrite delivers it again.
    """
    client = EventbriteClient.from_settings()
    if not client.enabled:
        raise EventbriteError(EventbriteSync.SYNC_OFF)
    order = client.get_order(order_id)
    event_id = str(field(order, "event_id"))
    offering = ClassOffering.objects.exclude(eventbrite_event_id="").filter(eventbrite_event_id=event_id).first()
    if offering is None:
        logger.info("Eventbrite order %s is for event %s, which no plfog class lists.", order_id, event_id)
        return
    for attendee in field(order, "attendees"):
        _apply_attendee(offering, order_id, attendee)


def _apply_attendee(offering: ClassOffering, order_id: str, attendee: dict[str, Any]) -> None:
    """Seat a new attendee, or free the seat of one Eventbrite now marks refunded or cancelled."""
    attendee_id = str(field(attendee, "id"))
    refunded, cancelled = bool(field(attendee, "refunded")), bool(field(attendee, "cancelled"))
    existing = Registration.objects.filter(eventbrite_attendee_id=attendee_id).first()
    if existing is None:
        if not (refunded or cancelled):
            _seat(offering, order_id, attendee_id, attendee)
        return
    if not (refunded or cancelled):
        return
    with transaction.atomic():
        # Re-read under the lock: a refund from the panel may have freed this seat meanwhile.
        current = Registration.objects.select_for_update().get(pk=existing.pk)
        if not current.consumes_seat:
            return
        if refunded:
            current.mark_refunded(reason=REFUNDED_IN_EVENTBRITE)
        else:
            current.cancel(reason=CANCELLED_IN_EVENTBRITE)


def _seat(offering: ClassOffering, order_id: str, attendee_id: str, attendee: dict[str, Any]) -> None:
    """Create the attendee's confirmed registration and tell the Admins what needs a person.

    Two deliveries of one order can race. The unique attendee ID decides: the loser finds the
    winner's row and stops. A seat email taken in the same instant gets one fresh alias; a
    second collision raises, and Eventbrite delivers the order again.
    """
    profile = field(attendee, "profile")
    email = str(field(profile, "email"))
    was_full = offering.spots_remaining == 0
    try:
        registration = _create_confirmed(offering, order_id, attendee_id, attendee, email)
    except IntegrityError:
        if Registration.objects.filter(eventbrite_attendee_id=attendee_id).exists():
            return
        registration = _create_confirmed(offering, order_id, attendee_id, attendee, email)
    emit_instructor_new_registration(registration)
    send_admin_registration_notification(registration)
    if was_full:
        send_eventbrite_oversold_alert(registration)
    if registration.email != email:
        send_eventbrite_shared_email_alert(registration, email)
    send_finish_email(registration, to=email)


def _create_confirmed(
    offering: ClassOffering, order_id: str, attendee_id: str, attendee: dict[str, Any], email: str
) -> Registration:
    """Save the row PENDING then CONFIRMED, as a paid site booking is, inside one savepoint."""
    profile = field(attendee, "profile")
    registration = Registration(
        class_offering=offering,
        source=Registration.Source.EVENTBRITE,
        eventbrite_order_id=order_id,
        eventbrite_attendee_id=attendee_id,
        first_name=str(field(profile, "first_name"))[:100],
        last_name=str(field(profile, "last_name"))[:100],
        email=free_seat_email(offering, email),
    )
    with transaction.atomic():
        registration.save()
        registration.status = Registration.Status.CONFIRMED
        registration.confirmed_at = timezone.now()
        registration.amount_paid_cents = net_cents(attendee)
        registration.save(update_fields=["status", "confirmed_at", "amount_paid_cents"])
    return registration


def net_cents(attendee: dict[str, Any]) -> int:
    """What Past Lives receives for one ticket: the buyer's total less Eventbrite's fee, processing and tax."""
    costs = field(attendee, "costs")

    def cents(key: str) -> int:
        return int(field(field(costs, key), "value"))

    return cents("gross") - cents("eventbrite_fee") - cents("payment_fee") - cents("tax")


def free_seat_email(offering: ClassOffering, email: str) -> str:
    """``email``, or ``local+seatN@domain`` when that address already holds a seat in this class.

    Eventbrite copies the buyer's details onto every ticket unless it is set to ask per ticket,
    and plfog holds one seat per address per class. The alias is the convention the legacy
    signup import used for a second seat under one address.
    """
    local, _, domain = email.partition("@")
    candidate, seat = email, 1
    while offering.live_registration_for_email(candidate) is not None:
        seat += 1
        candidate = f"{local}+seat{seat}@{domain}"
    return candidate


def refund_registration(registration: Registration, *, reason: str, actor: User | None) -> None:
    """Refund an Eventbrite ticket through Eventbrite, then free its seat with ``mark_refunded``.

    The whole order when every other ticket on it is already refunded; otherwise a partial
    refund of this ticket's total, so a ticket cancelled without a refund is never paid back by
    accident. The order's rows stay locked from the check to ``mark_refunded``, so a second
    click waits, then finds the ticket refunded and is refused without calling Eventbrite.
    Once Eventbrite has taken the refund, the REFUNDED status commits on its own; the waitlist
    is promoted after, and a failure there is logged, never rolled back into a second refund.

    Raises:
        EventbriteRefundRefusedError: Sync is off, the ticket no longer holds a seat, or
            Eventbrite refused; nothing changed in plfog.
    """
    client = EventbriteClient.from_settings()
    if not client.enabled:
        raise EventbriteRefundRefusedError(SYNC_OFF_REFUSED)
    with transaction.atomic():
        rows = Registration.objects.select_for_update().filter(eventbrite_order_id=registration.eventbrite_order_id)
        locked = {row.pk: row for row in rows.order_by("pk")}
        ticket = locked.pop(registration.pk)
        if ticket.status != Registration.Status.CONFIRMED:
            raise EventbriteRefundRefusedError(NOTHING_TO_REFUND)
        partial = any(row.status != Registration.Status.REFUNDED for row in locked.values())
        body: dict[str, Any] = {"reason": REFUND_REASON}
        try:
            if partial:
                body["total_refund_amount"] = _ticket_total(client, ticket)
            client.refund_order(ticket.eventbrite_order_id, body)
        except EventbriteError as exc:
            logger.warning("Eventbrite refused the refund of registration %s: %s", ticket.pk, exc)
            raise EventbriteRefundRefusedError(
                PARTIAL_REFUSED if partial else f"Eventbrite refused the refund: {exc}"
            ) from exc
        ticket.mark_refunded(reason=reason or REFUNDED_THROUGH_EVENTBRITE, actor=actor, promote_waitlist=False)
    try:
        ticket.class_offering.promote_next_from_waitlist()
    except Exception:
        logger.exception("Waitlist promotion failed after refunding registration %s", ticket.pk)


def _ticket_total(client: EventbriteClient, registration: Registration) -> str:
    """This ticket's total as Eventbrite charged it, in the decimal string its refunds take."""
    order = client.get_order(registration.eventbrite_order_id)
    for attendee in field(order, "attendees"):
        if str(field(attendee, "id")) == registration.eventbrite_attendee_id:
            cents = int(field(field(field(attendee, "costs"), "gross"), "value"))
            return f"{cents / 100:.2f}"
    raise EventbriteError(f"Order {registration.eventbrite_order_id} no longer lists this ticket.")


def finish_registration(registration: Registration, form: FinishRegistrationForm, *, client_ip: str) -> None:
    """Save the finish page: the waivers and answers together, then the account if ticked.

    The account follows the commit and never raises (``ensure_account_for_registration``
    logs and swallows), so a failure there never loses a signed waiver.

    Raises:
        AlreadyFinishedError: A second submit lost the race to the unique waiver.
    """
    try:
        with transaction.atomic():
            form.save_to(registration, settings_obj=ClassSettings.load(), client_ip=client_ip)
    except IntegrityError as exc:
        raise AlreadyFinishedError from exc
    if form.wants_account:
        registration.create_account = True
        registration.save(update_fields=["create_account"])
        ensure_account_for_registration(registration, despite_invite_only=True)
