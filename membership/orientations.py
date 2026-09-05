"""Orientation orchestration: iCal invites, transactional emails, activity + notifications.

The models hold state and guards; this module wires the side effects around each
lifecycle transition (book → confirm / decline / cancel). Every member-facing
email carries an ``.ics`` so Google/Outlook can track the appointment, and each
transition logs ``SiteActivity`` and fires an in-app notification.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import icalendar
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.events.emit import emit
from core.events.senders import emit_with_email_shell
from core.models import SiteActivity

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import (
        Equipment,
        Guild,
        Member,
        OrientationAvailability,
        OrientationAvailabilityBlock,
        OrientationBooking,
        OrientationSlot,
        OrientationType,
    )

logger = logging.getLogger(__name__)

_ACTION_SALT = "orientation-action"
_ACTION_MAX_AGE = 90 * 24 * 3600  # 90 days — long enough to schedule, bounded for safety
_ACTIONS = frozenset({"confirm", "decline", "cancel"})

_CHECKOUT_SALT = "orientation-checkout"
_CHECKOUT_MAX_AGE = 30 * 24 * 3600  # 30 days — outlives any Checkout session by a wide margin
_CHECKOUT_SESSION_LIFETIME = timedelta(hours=1)  # Stripe expires_at; abandoned checkouts die server-side
_HOLD_SWEEP_AGE = timedelta(hours=2)  # strictly after session expiry, so the sweep never races a live checkout


def _absolute_url(path: str) -> str:
    """Turn a relative hub path into an absolute URL using the member-site base."""
    base = settings.MEMBER_BASE_URL.rstrip("/")
    return f"{base}{path}"


def make_action_token(booking: OrientationBooking, action: str, *, recipient: Member | None = None) -> str:
    """Sign a no-login token authorizing one ``action`` on one ``booking`` (for email links).

    ``recipient`` stamps the link's addressee into the payload so a no-login
    decline/cancel can credit the person who clicked it (refund attribution).
    """
    payload: dict[str, Any] = {"booking": booking.pk, "action": action}
    if recipient is not None:
        payload["recipient"] = recipient.pk
    return signing.dumps(payload, salt=_ACTION_SALT)


def read_action_token(token: str) -> tuple[OrientationBooking, str, Member | None]:
    """Decode an action token to its (booking, action, recipient).

    ``recipient`` is the Member the link was addressed to, or ``None`` for
    payloads that predate the field (or whose member has since been deleted).

    Raises:
        signing.BadSignature: If the token is invalid, expired, or names an unknown action.
        OrientationBooking.DoesNotExist: If the booking no longer exists.
    """
    from membership.models import Member, OrientationBooking

    data = signing.loads(token, salt=_ACTION_SALT, max_age=_ACTION_MAX_AGE)
    action = data["action"]
    if action not in _ACTIONS:
        raise signing.BadSignature("unknown orientation action")
    booking = OrientationBooking.objects.select_related("slot", "guild", "member").get(pk=data["booking"])
    recipient = Member.objects.filter(pk=data["recipient"]).first() if "recipient" in data else None
    return booking, action, recipient


def apply_token_action(booking: OrientationBooking, action: str, *, recipient: Member | None = None) -> str:
    """Apply an email-link action, honoring valid state transitions.

    ``recipient`` (from the token payload) attributes side effects — a paid
    booking's automatic refund credits the person whose link was clicked;
    ``None`` leaves the refund initiator as "System".

    Returns a short status: "confirmed", "declined", "cancelled", or "already"
    (when the booking was no longer in a state the action applies to).
    """
    from membership.models import OrientationBooking

    actor = recipient.user if recipient is not None else None
    statuses = OrientationBooking.Status
    if action == "cancel":
        if booking.status in (statuses.REQUESTED, statuses.CONFIRMED):
            cancel_orientation(booking, actor_label=booking.member.display_name, actor=actor)
            return "cancelled"
        return "already"
    if booking.status != statuses.REQUESTED:
        return "already"
    if action == "confirm":
        confirm_orientation(booking)
        return "confirmed"
    decline_orientation(booking, actor=actor)
    return "declined"


def _action_url(booking: OrientationBooking, action: str, *, recipient: Member | None = None) -> str:
    return _absolute_url(
        reverse("hub_orientation_action", args=[make_action_token(booking, action, recipient=recipient)])
    )


def build_ics(booking: OrientationBooking, *, method: str, status: str) -> bytes:
    """Build a single-VEVENT iCalendar invite for an orientation booking.

    Args:
        booking: The orientation booking to describe.
        method: iCalendar METHOD — "REQUEST" for create/update, "CANCEL" to retract.
        status: VEVENT STATUS — "TENTATIVE", "CONFIRMED", or "CANCELLED".

    Returns:
        The serialized iCalendar bytes (suitable as an email attachment).
    """
    slot = booking.slot
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Past Lives Makerspace//Orientations//EN")
    cal.add("version", "2.0")
    cal.add("method", method)
    event = icalendar.Event()
    event.add("uid", f"orientation-{booking.pk}@pastlives")
    event.add("summary", f"{booking.orientation_type.name} orientation — {booking.orientation_type.owner_name}")
    event.add("dtstart", slot.starts_at)
    event.add("dtend", slot.ends_at)
    event.add("dtstamp", timezone.now())
    event.add("status", status)
    if slot.location:
        event.add("location", slot.location)
    description = f"{booking.orientation_type.name} orientation for {booking.orientation_type.owner_name} at Past Lives Makerspace."
    label = slot.with_label
    if label:
        description += f" {label[0].upper()}{label[1:]}."
    event.add("description", description)
    cal.add_component(event)
    return cal.to_ical()


def _context(booking: OrientationBooking, **extra: Any) -> dict[str, Any]:
    member = booking.member
    return {
        "booking": booking,
        "slot": booking.slot,
        "guild": booking.guild,
        "orientation_type": booking.orientation_type,
        "greeting_name": member.display_name,
        "owner_name": booking.orientation_type.owner_name,
        "owner_url": booking.orientation_type.owner_page_url(),
        "owner_page_label": "equipment page" if booking.orientation_type.is_equipment_owned else "guild page",
        "cancel_url": _action_url(booking, "cancel", recipient=member),
        **extra,
    }


def _emit_member_email(
    booking: OrientationBooking,
    *,
    action: str,
    subject: str,
    template: str,
    ics: tuple[str, bytes, str] | None,
    in_app_title: str = "",
    in_app_body: str = "",
) -> None:
    """Emit a member-facing orientation email (structural shell + optional ``.ics``).

    The email body is the existing ``membership/emails/<template>.{txt,html}`` shell,
    preserved verbatim; the ``.ics`` rides along as an attachment. The
    ``TransactionalEmailLog`` audit label is the event key (``orientation_update``) — one
    vocabulary, so the audit log joins to the event + its preferences (Phase 7). When
    ``in_app_title`` is set the member also gets an ``orientation_update`` bell row
    (confirm/decline/cancel); when it is empty (the request-received email) no in-app row
    is created — the member context is suppressed so the resolver finds nobody and only
    the explicit email goes out.

    ``action`` buckets the idempotency window per booking + lifecycle step, so a booking's
    request / confirm / decline / cancel emails are independent (each one sends once),
    while a re-run of the SAME step is deduped — replacing the old "send every time".
    """
    ctx = _context(booking)
    # Member in-app only fires for confirm/decline/cancel (in_app_title set). For the
    # request-received email, suppress the in-app by giving the resolver no member.
    resolver_context: dict[str, Any] = {"booking": booking} if in_app_title else {"member": None}
    emit_with_email_shell(
        "orientation_update",
        target=booking,
        context=resolver_context,
        subject=subject,
        text_template=f"membership/emails/{template}.txt",
        html_template=f"membership/emails/{template}.html",
        template_context=ctx,
        in_app_title=in_app_title,
        in_app_body=in_app_body,
        url=booking.orientation_type.owner_page_path(),
        attachments=[ics] if ics is not None else None,
        email_to=booking.member.primary_email,
        period=f"booking:{booking.pk}:{action}",
    )


def _ics(booking: OrientationBooking, *, method: str, status: str) -> tuple[str, bytes, str]:
    return ("orientation.ics", build_ics(booking, method=method, status=status), "text/calendar")


def _fan_out_request(booking: OrientationBooking) -> None:
    """The full request fan-out: member "request received" email (+ TENTATIVE ``.ics``),
    the ``ORIENTATION_REQUESTED`` activity row, and the lead/orienter request email + in-app.

    Callable on an existing booking so the paid flow can fire it from the webhook —
    for a paid booking, emails only ever go out for money in hand. The ``emit``
    ``period`` dedupe (``booking:{pk}:request``) makes webhook re-delivery
    double-send-proof even beyond the status guard.
    """
    _emit_member_email(
        booking,
        action="request",
        subject=f"Orientation request received — {booking.orientation_type.owner_name}",
        template="orientation_request",
        ics=_ics(booking, method="REQUEST", status="TENTATIVE"),
    )
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_REQUESTED, actor=booking.member.user, target=booking)
    _emit_lead_request(booking)


def request_orientation(slot: OrientationSlot, member: Member, *, note: str = "") -> OrientationBooking:
    """Book a slot (REQUESTED) and fan out the request emails, activity, and orienter notification.

    Raises:
        OrientationError: Propagated from ``slot.book`` when the slot can't be booked.
    """
    booking = slot.book(member, note=note)
    _fan_out_request(booking)
    return booking


def _ensure_custom_requestable(guild: Guild, orientation_type: OrientationType) -> None:
    """Raise :class:`OrientationError` unless a custom request may target this guild + type."""
    from membership.models import GuildOrientationSettings, OrientationError

    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    if settings_obj is None or not settings_obj.is_accepting or not settings_obj.allow_custom_requests:
        raise OrientationError("This guild isn't taking custom orientation requests right now.")
    if orientation_type.guild_id != guild.pk or not orientation_type.is_active:
        raise OrientationError("That orientation isn't offered right now.")


def _create_custom_slot(guild: Guild, orientation_type: OrientationType, starts_at: datetime) -> OrientationSlot:
    """The one-off 1-seat MANUAL slot a custom request books — sized by ITS type.

    The slot runs ``orientation_type.duration_minutes`` and sits at the type's
    ``default_location`` (issue #282: custom requests use the picked type's config).
    """
    from membership.models import OrientationSlot

    return OrientationSlot.objects.create(
        guild=guild,
        orientation_type=orientation_type,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=orientation_type.duration_minutes),
        seats=1,
        location=orientation_type.default_location,
        source=OrientationSlot.Source.MANUAL,
    )


def request_custom_orientation(
    guild: Guild, member: Member, starts_at: datetime, *, orientation_type: OrientationType, note: str = ""
) -> OrientationBooking:
    """Create a one-off MANUAL slot at ``starts_at`` and request it, reusing :func:`request_orientation`.

    Mirrors the hub custom-request view: the guild must have ``GuildOrientationSettings``
    that is both accepting bookings *and* allowing custom requests, and the picked
    ``orientation_type`` must be one of this guild's active types, else an
    :class:`~membership.models.OrientationError`. The slot ends the type's
    ``duration_minutes`` after the start, holds a single seat, and sits at the type's
    ``default_location``.

    Args:
        guild: The guild to orient for.
        member: The requesting member.
        starts_at: The proposed start (future, validated by :func:`parse_proposed_time`).
        orientation_type: Which of the guild's orientations the member wants.
        note: Optional free-text note passed to the orienter.

    Returns:
        The created (REQUESTED) :class:`~membership.models.OrientationBooking`.

    Raises:
        OrientationError: If the guild isn't taking custom requests, the type isn't
            offered, or the booking fails. A booking failure deletes the orphan slot
            before re-raising, so a failed custom request never leaves a dangling slot.
    """
    from membership.models import OrientationError

    _ensure_custom_requestable(guild, orientation_type)
    slot = _create_custom_slot(guild, orientation_type, starts_at)
    try:
        return request_orientation(slot, member, note=note)
    except OrientationError:
        slot.delete()
        raise


def _carve_block_slot(
    block: OrientationAvailabilityBlock, orientation_type: OrientationType, starts_at: datetime
) -> OrientationSlot:
    """Lock the block row, recheck the interval, and carve out its 1-seat ``FROM_BLOCK`` slot.

    Must run inside ``transaction.atomic`` — ``select_for_update`` serializes
    concurrent bookings into the same block, and the recheck under the lock is what
    guarantees two members can't take one interval (the slot occupies its segment
    from the moment it exists, before its booking row lands).

    Raises:
        OrientationError: Propagated from :meth:`OrientationAvailabilityBlock.ensure_start_valid`.
    """
    from membership.models import OrientationAvailabilityBlock, OrientationSlot

    locked = OrientationAvailabilityBlock.objects.select_for_update().get(pk=block.pk)
    locked.ensure_start_valid(orientation_type, starts_at)
    return OrientationSlot.objects.create(
        guild=locked.guild,
        orientation_type=orientation_type,
        orienter=locked.orienter,
        block=locked,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=orientation_type.duration_minutes),
        seats=1,
        location=locked.location or orientation_type.default_location,
        source=OrientationSlot.Source.FROM_BLOCK,
    )


def request_block_orientation(
    block: OrientationAvailabilityBlock,
    member: Member,
    starts_at: datetime,
    *,
    orientation_type: OrientationType,
    note: str = "",
) -> OrientationBooking:
    """Book ``starts_at`` inside an availability block (free types) — issue #283.

    Carves the 1-seat ``FROM_BLOCK`` slot and creates its booking in ONE transaction
    under a ``select_for_update`` lock on the block row, so a concurrent attempt at
    an overlapping interval waits, rechecks, and fails cleanly. The request fan-out
    (emails, activity, notifications) fires after commit, exactly like
    :func:`request_orientation`.

    Raises:
        OrientationError: If the start is invalid (cancelled block, off-grid,
            doesn't fit, taken) or the member fails the per-type booking guards.
            A guard failure rolls the carved slot back — no dangling slot.
    """
    with transaction.atomic():
        slot = _carve_block_slot(block, orientation_type, starts_at)
        booking = slot.book(member, note=note)
    _fan_out_request(booking)
    return booking


def start_block_orientation_checkout(
    block: OrientationAvailabilityBlock,
    member: Member,
    starts_at: datetime,
    *,
    orientation_type: OrientationType,
    note: str = "",
) -> str:
    """Paid variant of :func:`request_block_orientation` — returns the Stripe Checkout URL.

    The carve + the ``PENDING_PAYMENT`` hold + the Checkout Session all run inside
    the block-row lock: the hold is what occupies the segment, so it must exist
    before the lock releases. The Stripe call inside the lock is a deliberate
    tradeoff — contention is scoped to one block row, and a failure rolls back the
    slot and hold together (the delegate also expires its session best-effort).

    Raises:
        OrientationError: Propagated from the interval recheck or the booking guards.
    """
    with transaction.atomic():
        slot = _carve_block_slot(block, orientation_type, starts_at)
        return start_orientation_checkout(slot, member, note=note)


def parse_proposed_time(date_str: str, time_str: str) -> datetime:
    """Parse a member's proposed orientation ``date`` + ``time`` into a future, tz-aware datetime.

    ``date_str`` is ``YYYY-MM-DD``; ``time_str`` is 24h ``HH:MM`` or 12h ``h:mm am/pm``
    (spaces optional, case-insensitive). The result is interpreted in the site timezone.

    Raises:
        OrientationError: With member-friendly copy when either part is unreadable or the
            combined moment is not in the future.
    """
    from membership.models import OrientationError

    _UNREADABLE = "I couldn't read that time — use YYYY-MM-DD and HH:MM."
    try:
        day = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise OrientationError(_UNREADABLE)
    cleaned = time_str.strip().lower().replace(" ", "")
    parsed_time = None
    for fmt in ("%H:%M", "%I:%M%p", "%I%p"):
        try:
            parsed_time = datetime.strptime(cleaned, fmt).time()
            break
        except ValueError:
            continue
    if parsed_time is None:
        raise OrientationError(_UNREADABLE)
    starts_at = timezone.make_aware(datetime.combine(day, parsed_time))
    if starts_at <= timezone.now():
        raise OrientationError("That time's already past — pick a future date and time.")
    return starts_at


# ── Paid orientations: Stripe Checkout orchestration ─────────────────────────


def make_checkout_token(booking: OrientationBooking) -> str:
    """Sign a token authorizing the Checkout return/cancelled pages for one booking."""
    return signing.dumps({"booking": booking.pk}, salt=_CHECKOUT_SALT)


def read_checkout_token(token: str) -> OrientationBooking:
    """Decode a checkout token to its booking.

    Raises:
        signing.BadSignature: If the token is invalid or expired.
        OrientationBooking.DoesNotExist: If the booking no longer exists.
    """
    from membership.models import OrientationBooking

    data = signing.loads(token, salt=_CHECKOUT_SALT, max_age=_CHECKOUT_MAX_AGE)
    return OrientationBooking.objects.select_related("slot", "guild", "member").get(pk=data["booking"])


def start_orientation_checkout(slot: OrientationSlot, member: Member, *, note: str = "") -> str:
    """Open a Stripe Checkout for a paid guild's slot and return the hosted Checkout URL.

    Creates the seat-holding ``PENDING_PAYMENT`` booking first (no emails, no
    activity, no notifications — nothing has happened yet), then the Checkout
    Session. A Stripe API failure deletes the hold (and its orphan custom slot)
    and re-raises, mirroring the classes rollback.

    Raises:
        OrientationError: Propagated from the ``slot.book()`` guards — including
            the friendly "checkout in progress" duplicate at ``seat_holding()`` scope.
    """
    from billing import stripe_utils
    from membership.models import OrientationBooking, OrientationError

    orientation_type = slot.orientation_type
    if not orientation_type.is_paid:
        raise OrientationError("This orientation doesn't charge to book.")
    slot.ensure_bookable_for(member)
    # amount_paid_cents stays 0 until money is actually in hand — the finalize
    # step stamps it from the session's amount_total. A provisional amount here
    # would make never-paid holds render as paid rows in staff surfaces.
    booking = OrientationBooking.objects.create(
        slot=slot,
        guild=slot.guild,
        member=member,
        member_note=note,
        status=OrientationBooking.Status.PENDING_PAYMENT,
    )
    token = make_checkout_token(booking)
    metadata = {"kind": "orientation_booking", "booking_id": str(booking.pk)}
    try:
        session = stripe_utils.create_checkout_session(
            amount_cents=orientation_type.price_cents,
            product_name=f"{orientation_type.name} orientation — {orientation_type.owner_name}",
            customer_email=member.primary_email,
            success_url=_absolute_url(reverse("hub_orientation_checkout_return", args=[token])),
            cancel_url=_absolute_url(reverse("hub_orientation_checkout_cancelled", args=[token])),
            metadata=metadata,
            # Stripe idempotency replays the original response, so this can never
            # revive an expired session — Resume mints fresh via re-booking instead.
            idempotency_key=f"orientation-checkout-{booking.pk}",
            expires_at=int((timezone.now() + _CHECKOUT_SESSION_LIFETIME).timestamp()),
        )
    except Exception:
        _delete_hold(booking)
        raise
    booking.stripe_session_id = session["id"]
    booking.save(update_fields=["stripe_session_id"])
    return session["url"]


def start_custom_orientation_checkout(
    guild: Guild, member: Member, starts_at: datetime, *, orientation_type: OrientationType, note: str = ""
) -> str:
    """Custom-time variant of :func:`start_orientation_checkout` — pay-to-book at the TYPE's price.

    Creates the one-off 1-seat MANUAL slot (like :func:`request_custom_orientation`),
    then delegates. Any failure deletes the orphan slot (and the hold, handled by
    the delegate) before re-raising.

    Raises:
        OrientationError: If the guild isn't taking custom requests, the type isn't
            offered, or booking fails.
    """
    from membership.models import OrientationSlot

    _ensure_custom_requestable(guild, orientation_type)
    slot = _create_custom_slot(guild, orientation_type, starts_at)
    try:
        return start_orientation_checkout(slot, member, note=note)
    except Exception:
        # The delegate already deleted its hold; remove the orphan slot if it survived.
        if OrientationSlot.objects.filter(pk=slot.pk).exists() and not slot.bookings.exists():
            slot.delete()
        raise


def finalize_paid_booking(
    booking: OrientationBooking, *, payment_intent: str, amount_total: int | None, session_id: str = ""
) -> str:
    """Flip a ``PENDING_PAYMENT`` hold to ``REQUESTED`` with the full request fan-out.

    THE single "money is in hand" transition — the webhook handler, the
    Stripe-verified release paths, Resume, and the sweep's lost-webhook recovery
    all funnel through here, and it is safe to race: the row is re-fetched under
    ``select_for_update`` and only a still-``PENDING_PAYMENT`` hold is flipped,
    so a sweep-vs-late-webhook race finalizes exactly once (one activity row,
    one fan-out) and can never resurrect a declined/cancelled/refunded booking.

    ``amount_total`` (from the session/webhook) is canonical; ``session_id``
    backfills a hold whose session id never got saved (crash mid-start).

    A hold whose slot was cancelled while the member was paying is finalized and
    then immediately cancelled with the slot-cancelled fan-out (member email +
    automatic full refund) — money is recorded first so the refund has a real
    payment to anchor to, and the member gets exactly one email (no request
    fan-out for a booking that is dead on arrival).

    Returns:
        ``"finalized"`` — this call flipped the hold (fan-out fired);
        ``"cancelled_slot"`` — flipped, then auto-cancelled + refunded;
        ``"already"`` — the row is no longer ``PENDING_PAYMENT`` (someone else
        finalized it, or it was resolved); nothing changed;
        ``"gone"`` — the row no longer exists; nothing changed.
    """
    from membership.models import OrientationBooking

    with transaction.atomic():
        locked = (
            OrientationBooking.objects.select_for_update()
            .select_related("slot", "guild", "member")
            .filter(pk=booking.pk)
            .first()
        )
        if locked is None:
            return "gone"
        if locked.status != OrientationBooking.Status.PENDING_PAYMENT:
            return "already"
        locked.status = OrientationBooking.Status.REQUESTED
        locked.stripe_payment_id = payment_intent
        if session_id and not locked.stripe_session_id:
            locked.stripe_session_id = session_id
        if amount_total is not None:
            locked.amount_paid_cents = amount_total
        locked.save(update_fields=["status", "stripe_payment_id", "stripe_session_id", "amount_paid_cents"])
        slot_cancelled = locked.slot.is_cancelled
    if slot_cancelled:
        # Fan-out outside the lock. The request fan-out is skipped on purpose —
        # the member gets one honest email: cancelled, with the refund promise.
        cancel_orientation(locked, actor_label="the guild")
        return "cancelled_slot"
    _fan_out_request(locked)
    return "finalized"


def _expire_session_best_effort(booking: OrientationBooking) -> None:
    """Expire the hold's Checkout Session so an open Stripe tab can't pay a dead booking.

    Best-effort by design: the session may already be expired or Stripe may be
    down — either way the release proceeds (the session's own ``expires_at`` and
    the webhook/sweep recovery paths are the backstop).
    """
    from billing import stripe_utils

    if not booking.stripe_session_id:
        return
    try:
        stripe_utils.expire_checkout_session(session_id=booking.stripe_session_id)
    except Exception:
        logger.info("Could not expire Checkout session for orientation hold %s (best effort).", booking.pk)


def _delete_hold(booking: OrientationBooking, *, expire_session: bool = True) -> None:
    """Delete a checkout hold — never CANCELLED: no fan-out ever fired, nothing should remember it.

    Expires the hold's Checkout Session first (best-effort) unless the caller
    knows it is already expired. The delete itself is status-guarded so a row a
    concurrent webhook just finalized is never destroyed (and a row already gone
    is a quiet no-op). A custom-request or block hold also deletes its orphan
    1-seat MANUAL / FROM_BLOCK slot — for a block, that is what frees the
    segment (a bookingless carved slot still occupies its span).
    """
    from membership.models import OrientationBooking, OrientationSlot

    if expire_session:
        _expire_session_best_effort(booking)
    slot = booking.slot
    deleted, _detail = OrientationBooking.objects.filter(
        pk=booking.pk, status=OrientationBooking.Status.PENDING_PAYMENT
    ).delete()
    if not deleted:
        return
    one_off_sources = (OrientationSlot.Source.MANUAL, OrientationSlot.Source.FROM_BLOCK)
    if slot.seats == 1 and slot.source in one_off_sources and not slot.bookings.exists():
        slot.delete()
        return
    _recap_orphaned_generated_slot(slot)


def _recap_orphaned_generated_slot(slot: OrientationSlot) -> None:
    """Re-cap a kept GENERATED slot whose rule is gone or paused after a hold on it is released.

    Retirement capped the slot to its taken seats so it could take no new booking
    through the ``availability`` SET_NULL door or a paused rule; when a hold that
    counted toward that cap expires, the freed seat must not reopen. With nothing
    seat-holding left the slot is cancelled outright (nothing remains to keep it
    for); otherwise it is capped again to what still holds a seat. A slot under a
    live rule is untouched, as is any MANUAL / FROM_BLOCK slot.
    """
    from membership.models import OrientationSlot

    if slot.source != OrientationSlot.Source.GENERATED:
        return
    # The caller's slot may predate the retirement that SET_NULL'd its rule and capped
    # its seats (a hold created before the rule went); read the current row.
    slot.refresh_from_db()
    rule = slot.availability
    if rule is not None and rule.is_active:
        return
    remaining = slot.bookings.seat_holding().count()
    if remaining == 0:
        slot.mark_cancelled(reason="The hours this time came from are no longer posted.")
    elif slot.seats != remaining:
        slot.seats = remaining
        slot.save(update_fields=["seats"])


def release_hold_if_unpaid(booking: OrientationBooking) -> str:
    """Release a ``PENDING_PAYMENT`` hold — but only after verifying with Stripe.

    A hold can be paid-but-webhook-lagged, and deleting it would eat the member's
    money. Returns ``"released"`` (unpaid — hold deleted, session expired
    best-effort, orphan custom slot too), ``"paid"`` (kept and flipped to
    REQUESTED with the full fan-out — the same recovery as the sweep), or
    ``"unknown"`` (Stripe unreachable — hold kept). A hold with no stored
    session id (crash between session create and save) is released outright —
    there is nothing to verify or pay.
    """
    from billing import stripe_utils

    if not booking.stripe_session_id:
        _delete_hold(booking, expire_session=False)
        return "released"
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=booking.stripe_session_id)
    except Exception:
        logger.exception("Could not verify Checkout session for orientation hold %s; keeping it.", booking.pk)
        return "unknown"
    if session["payment_status"] == "paid":
        finalize_paid_booking(booking, payment_intent=session["payment_intent"], amount_total=session["amount_total"])
        return "paid"
    _delete_hold(booking)
    return "released"


def reconcile_landed_checkout(booking: OrientationBooking) -> str:
    """Verify-and-finalize a ``PENDING_PAYMENT`` hold when the member lands on the success page.

    The webhook is the primary finalize path, but it can lag — and on
    environments with no Stripe webhook endpoint it never arrives, stranding the
    member on the "Finalizing Your Payment" spinner forever. The success_url
    landing asks Stripe directly and funnels a paid session through
    :func:`finalize_paid_booking` (idempotent and race-safe against the
    webhook), so the confirmation renders immediately.

    Returns ``finalize_paid_booking``'s outcome (``"finalized"`` /
    ``"cancelled_slot"`` / ``"already"`` / ``"gone"``) when Stripe says paid,
    ``"pending"`` when the session is not paid yet (or there is no session id to
    check — crash mid-start; the sweep owns that hold), or ``"unknown"`` when
    Stripe is unreachable (the poll retries).
    """
    from billing import stripe_utils

    if not booking.stripe_session_id:
        return "pending"
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=booking.stripe_session_id)
    except Exception:
        logger.exception("Landing reconcile: could not verify session for orientation hold %s.", booking.pk)
        return "unknown"
    if session["payment_status"] != "paid":
        return "pending"
    return finalize_paid_booking(
        booking, payment_intent=session["payment_intent"], amount_total=session["amount_total"]
    )


def expire_payment_holds(*, now: datetime | None = None) -> tuple[int, int]:
    """Sweep abandoned checkout holds — Stripe-verified, never on age alone.

    For each ``PENDING_PAYMENT`` booking older than two hours (strictly after the
    one-hour session expiry, so a live checkout is never raced), retrieve its
    Checkout Session and act on Stripe's answer: paid → flip to REQUESTED with
    the full fan-out (**the sweep IS the lost-webhook recovery path**); expired
    or unpaid → delete the hold and its orphan custom slot; Stripe unreachable →
    skip this tick and log, the next tick retries. Idempotent.

    Returns:
        ``(released, recovered)`` counts.
    """
    from billing import stripe_utils
    from membership.models import OrientationBooking

    cutoff = (now or timezone.now()) - _HOLD_SWEEP_AGE
    stale = OrientationBooking.objects.filter(
        status=OrientationBooking.Status.PENDING_PAYMENT, requested_at__lt=cutoff
    ).select_related("slot", "guild", "member")
    released = 0
    recovered = 0
    for booking in stale:
        if not booking.stripe_session_id:
            # Crash between session create and save: no session ever attached, so
            # there is nothing to verify — a blind delete is safe and frees the
            # otherwise forever-stranded seat.
            _delete_hold(booking, expire_session=False)
            released += 1
            continue
        try:
            session = stripe_utils.retrieve_checkout_session(session_id=booking.stripe_session_id)
        except Exception:
            logger.exception("Hold sweep: could not verify session for booking %s; retrying next tick.", booking.pk)
            continue
        if session["payment_status"] == "paid":
            outcome = finalize_paid_booking(
                booking, payment_intent=session["payment_intent"], amount_total=session["amount_total"]
            )
            if outcome in ("finalized", "cancelled_slot"):
                recovered += 1
            # "already"/"gone": a concurrent webhook or release won the race —
            # nothing changed here, so nothing to count.
        else:
            _delete_hold(booking)
            released += 1
    return released, recovered


def _refund_if_paid(booking: OrientationBooking, *, actor: User | None) -> None:
    """Auto-refund a paid booking in full on decline/cancel — flag, don't block, on failure.

    A refund API failure never blocks the decline/cancel: the state change is
    already saved, the member email still goes out ("your refund is being
    processed"), and the booking lands at ``refund_state == "failed"`` with the
    Payments panel's loud Retry action — a member is never silently unrefunded.
    Free bookings and already-refunded bookings never touch the engine.
    """
    from billing.exceptions import RefundError

    if booking.amount_paid_cents <= 0 or booking.refund_state != "none":
        return
    try:
        booking.issue_refund(actor=actor)
    except RefundError:
        logger.exception(
            "Automatic refund failed for orientation booking %s; flagged for retry in the Payments panel.",
            booking.pk,
        )


def _request_resolver_context(booking: OrientationBooking) -> dict[str, Any]:
    """The ``orientation_requested`` resolver context, keyed by the booking's owner type.

    Equipment-owned routes via the composed resolver's equipment leg
    (:func:`core.events.resolvers.guild_orienters_or_equipment_managers`);
    guild-owned keeps the guild + slot keys so personal-slot narrowing survives.
    """
    if booking.orientation_type.is_equipment_owned:
        return {"equipment": booking.orientation_type.equipment, "slot": booking.slot}
    return {"guild": booking.guild, "slot": booking.slot}


def _request_audience(booking: OrientationBooking) -> list[Member]:
    """Who hears about a request: the slot's orienter + the lead (personal), or all leadership.

    A personal slot routes to the person the member actually booked, with the guild lead
    kept in the loop (deduped); a guild slot keeps the full leadership fan-out.
    """
    orientation_type = booking.orientation_type
    if orientation_type.is_equipment_owned:
        # Equipment-owned: the three manage tiers, deduped — managers confirm requests.
        return cast("Equipment", orientation_type.equipment).manager_members()
    guild = cast("Guild", booking.guild)  # guild-owned: the one-owner constraint guarantees it
    slot = booking.slot
    if slot.orienter_id is not None and slot.orienter is not None:
        audience = [slot.orienter]
        lead = guild.guild_lead
        if lead is not None and lead.pk != slot.orienter_id:
            audience.append(lead)
        return audience
    return guild.leadership_members()


def _emit_lead_request(booking: OrientationBooking) -> None:
    """Email the request's audience, and in-app-notify the matching orienters (Decision 7).

    For a guild slot the email recipients are the guild's whole leadership team (lead +
    staff), byte-identical to before. For a personal slot both the email and the in-app
    ``orientation_requested`` row route to the slot's orienter + the guild lead (deduped)
    — the ``guild_orienters`` resolver honors the slot passed in context. The activity
    row is logged by the caller.
    """
    recipients: list[str] = []
    for member in _request_audience(booking):
        if member.primary_email and member.primary_email not in recipients:
            recipients.append(member.primary_email)
    # One body goes to the whole audience, so the confirm/decline links carry the
    # slot's primary responder (personal slot: the orienter; guild slot: the lead)
    # as the token recipient — a paid booking's email-link decline credits them.
    if booking.orientation_type.is_equipment_owned:
        # Managers confirm explicitly; an email-link decline is then unattributed
        # (make_action_token accepts recipient=None, same as a lead-less guild).
        primary_responder = None
    else:
        guild = cast("Guild", booking.guild)
        primary_responder = booking.slot.orienter if booking.slot.orienter_id is not None else guild.guild_lead
    ctx = _context(
        booking,
        respond_url=_absolute_url(reverse("hub_orientation_respond", args=[booking.pk])),
        confirm_url=_action_url(booking, "confirm", recipient=primary_responder),
        decline_url=_action_url(booking, "decline", recipient=primary_responder),
    )
    emit_with_email_shell(
        "orientation_requested",
        context=_request_resolver_context(booking),
        subject=f"New orientation request — {booking.orientation_type.owner_name}",
        text_template="membership/emails/orientation_lead_request.txt",
        html_template="membership/emails/orientation_lead_request.html",
        template_context=ctx,
        in_app_title="New orientation request",
        in_app_body=f"{booking.member.display_name} requested an orientation for {booking.orientation_type.owner_name}.",
        url=reverse("hub_orientation_respond", args=[booking.pk]),
        email_to=recipients or None,
        period=f"booking:{booking.pk}:request",
    )


def confirm_orientation(booking: OrientationBooking, *, oriented_by: Member | None = None) -> None:
    """Confirm a request: update state, email the member a CONFIRMED invite, log + notify.

    ``oriented_by`` credits the actual runner (Decision 7). The view passes the acting
    member; when omitted the booking model still defaults to the guild lead.
    """
    booking.confirm(oriented_by=oriented_by)
    actor = booking.oriented_by.user if booking.oriented_by is not None else None
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_CONFIRMED, actor=actor, target=booking)
    _emit_member_email(
        booking,
        action="confirm",
        subject=f"Orientation confirmed — {booking.orientation_type.owner_name}",
        template="orientation_confirmed",
        ics=_ics(booking, method="REQUEST", status="CONFIRMED"),
        in_app_title="Orientation confirmed",
        in_app_body=f"Your orientation for {booking.orientation_type.owner_name} is confirmed.",
    )


def decline_orientation(booking: OrientationBooking, *, note: str = "", actor: User | None = None) -> None:
    """Decline a request: update state, auto-refund a paid booking, email the member, log + notify.

    ``actor`` attributes the automatic refund (the acting user for authenticated
    declines; the token recipient's user for email-link declines; ``None`` = System).
    """
    booking.decline(note=note)
    _refund_if_paid(booking, actor=actor)
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_DECLINED, actor=None, target=booking)
    _emit_member_email(
        booking,
        action="decline",
        subject=f"About your orientation request — {booking.orientation_type.owner_name}",
        template="orientation_declined",
        ics=None,
        in_app_title="Orientation not confirmed",
        in_app_body=f"Your orientation request for {booking.orientation_type.owner_name} couldn't be confirmed.",
    )


def cancel_orientation(booking: OrientationBooking, *, actor_label: str, actor: User | None = None) -> None:
    """Cancel a booking: update state, auto-refund a paid booking, email the member, notify, log.

    Member cancels, lead cancels, slot cancels, and no-login token cancels all route
    through here — so every cancellation path refunds a paid booking automatically.
    """
    booking.cancel()
    _refund_if_paid(booking, actor=actor)
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_CANCELLED, actor=None, target=booking)
    _emit_member_email(
        booking,
        action="cancel",
        subject=f"Orientation cancelled — {booking.orientation_type.owner_name}",
        template="orientation_cancelled",
        ics=_ics(booking, method="CANCEL", status="CANCELLED"),
        in_app_title="Orientation cancelled",
        in_app_body=f"The orientation for {booking.orientation_type.owner_name} was cancelled.",
    )
    # In-app ping to the orienters that a booking was cancelled (was lead-only; now
    # fans out to all orienters via the guild_orienters resolver — Decision 7). The
    # orientation_requested EMAIL channel defaults OFF (opt-in), so this matches the
    # old dispatch (in-app always; generic email only for an opted-in orienter).
    emit(
        "orientation_requested",
        context=_request_resolver_context(booking),
        title="Orientation cancelled",
        body=f"{actor_label} cancelled the orientation for {booking.orientation_type.owner_name}.",
        url=reverse("hub_orientation_respond", args=[booking.pk]),
        period=f"booking:{booking.pk}:cancel",
    )


def cancel_slot(slot: OrientationSlot, *, reason: str = "") -> None:
    """Cancel a slot and run the full cancel fan-out for each of its active bookings.

    ``PENDING_PAYMENT`` checkout holds on the slot are released too, through the
    Stripe-verified release path: an unpaid hold has its session expired
    (best-effort, so an open Checkout tab can't pay for a cancelled slot) and its
    row deleted — no fan-out ever fired for it, so nothing should remember it.
    A hold Stripe reports as already paid routes into the finalize path, which
    sees the cancelled slot and auto-cancels + refunds with one honest email.
    """
    from membership.models import OrientationBooking

    active = list(slot.bookings.active())
    holds = list(slot.bookings.filter(status=OrientationBooking.Status.PENDING_PAYMENT))
    slot.mark_cancelled(reason=reason)
    for hold in holds:
        release_hold_if_unpaid(hold)
    for booking in active:
        cancel_orientation(booking, actor_label="the guild")


def complete_orientation(booking: OrientationBooking) -> None:
    """Mark a booking complete, send the lead's thank-you email (if set), and log activity."""
    from membership.models import GuildOrientationSettings

    booking.mark_completed()
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_COMPLETED, actor=None, target=booking)
    # Equipment-owned bookings have no settings row to consult (no-settings decision):
    # the standard thank-you copy sends, which is the desired v1 behavior.
    if booking.orientation_type.is_equipment_owned:
        settings_obj = None
    else:
        settings_obj = GuildOrientationSettings.objects.filter(guild=booking.guild).first()
    # The thank-you is on by default: send unless a guild explicitly turned it off. When the
    # guild hasn't written their own subject/body, the standard copy stands in.
    if settings_obj is None or settings_obj.thankyou_email_enabled:
        from membership.orientation_copy import STANDARD_THANKYOU_BODY, standard_thankyou_subject

        subject = (
            settings_obj.resolved_thankyou_subject
            if settings_obj
            else standard_thankyou_subject(booking.orientation_type.owner_name)
        )
        body = settings_obj.resolved_thankyou_body if settings_obj else STANDARD_THANKYOU_BODY
        ctx = _context(booking, body=body)
        # Email-only thank-you (no in-app pair today) → suppress the in-app by giving the
        # registrant resolver no member; the email goes to the explicit member address.
        emit_with_email_shell(
            "orientation_update",
            target=booking,
            context={"member": None},
            subject=subject,
            text_template="membership/emails/orientation_thankyou.txt",
            html_template="membership/emails/orientation_thankyou.html",
            template_context=ctx,
            email_to=booking.member.primary_email,
            period=f"booking:{booking.pk}:thankyou",
        )
    # Warm welcome to the guild's members — always fires (no opt-out), in-app + the guild's
    # own Discord channel. Copy-mode: no title/body, rendered from the seeded catalogue copy.
    owner_url = booking.orientation_type.owner_page_url()
    emit(
        "orientation.completed",
        actor=None,  # system event; the member is the subject, not the actor
        target=booking,
        context={
            # resolver key (guild_members) + _guild_broadcast destination; None for an
            # equipment-owned booking, which resolves to nobody and posts nowhere (the
            # guild-welcome moment has no equipment equivalent in v1).
            "guild": booking.guild,
            "member_name": booking.member.display_name,
            "guild_name": booking.orientation_type.owner_name,
            "guild_url": owner_url,
        },
        url=owner_url,  # the in-app bell row's click-through
        period=f"booking:{booking.pk}:completed",
    )


def auto_complete(*, now: datetime | None = None) -> int:
    """Complete confirmed orientations whose slot has ended. Returns the count completed."""
    from membership.models import OrientationBooking

    cutoff = now or timezone.now()
    pending = OrientationBooking.objects.filter(
        status=OrientationBooking.Status.CONFIRMED, is_completed=False, slot__ends_at__lt=cutoff
    ).select_related("slot", "guild", "member", "oriented_by")
    count = 0
    for booking in pending:
        complete_orientation(booking)
        count += 1
    return count


def _horizon_spans(rule: OrientationAvailability, *, today: date, window_weeks: int) -> list[tuple[datetime, datetime]]:
    """Every ``(start, end)`` span ``rule`` yields across the rolling window, in order."""
    spans: list[tuple[datetime, datetime]] = []
    for offset in range(window_weeks * 7):
        day = today + timedelta(days=offset)
        if day.weekday() != rule.weekday:
            continue
        spans.extend(rule.carve_spans(day))
    return spans


def _occupied_spans(equipment: Equipment, *, reference: datetime) -> list[tuple[int | None, datetime, datetime]]:
    """Every uncancelled, still-ahead slot on this equipment's owned types, as ``(rule id, start, end)``.

    Loaded ONCE per equipment per generation run — the slot-layer overlap check is
    list math over this, never a query per candidate.
    """
    from membership.models import OrientationSlot

    return list(
        OrientationSlot.objects.filter(
            orientation_type__equipment=equipment, is_cancelled=False, ends_at__gt=reference
        ).values_list("availability_id", "starts_at", "ends_at")
    )


def _overlaps_other_slot(
    occupied: list[tuple[int | None, datetime, datetime]], *, rule_id: int, start: datetime, end: datetime
) -> bool:
    """True when ``[start, end)`` overlaps any occupied span other than this rule's own slot at ``start``."""
    return any(
        slot_start < end and slot_end > start and not (rule_pk == rule_id and slot_start == start)
        for rule_pk, slot_start, slot_end in occupied
    )


def _rule_generates(rule: OrientationAvailability) -> bool:
    """The per-rule gate: an accepting owner, and (guild rules only) an orienter still on leadership.

    A stale personal rule — its orienter left the guild's leadership without the
    hub's retirement flow (a lead-FK change, a Django-admin removal) — must never
    materialize new slots. Equipment rules never carry an orienter, so that check
    is guild-only (``rule.guild`` is None for them).
    """
    if not rule.orientation_type.is_accepting:
        return False
    if rule.guild_id is None or rule.orienter_id is None:
        return True
    return rule.orienter_id in {member.pk for member in cast("Guild", rule.guild).leadership_members()}


def _retire_off_grid(rule: OrientationAvailability, spans: list[tuple[datetime, datetime]]) -> None:
    """Off-grid cleanup: retire the rule's future open generated slots that fell off its current grid.

    A kept old-grid slot whose booking was since cancelled must not linger and
    block the new grid. Booked off-grid slots survive (capped) exactly as in a
    delete or pause. Runs for every rule BEFORE any carving so the per-equipment
    occupied set is loaded clean.
    """
    grid = set(spans)
    off_grid = [
        slot
        for slot in _future_generated(rule).with_seat_holding_count()
        if (slot.starts_at, slot.ends_at) not in grid or slot.seats != rule.seats
    ]
    _retire_slots(off_grid)


def _materialize(
    rule: OrientationAvailability,
    spans: list[tuple[datetime, datetime]],
    *,
    reference: datetime,
    occupied: list[tuple[int | None, datetime, datetime]] | None,
) -> int:
    """Create the rule's missing future slots from ``spans``; returns how many were created.

    ``occupied`` (equipment rules only) is the tool's live overlap set: a candidate
    overlapping any other slot on it is skipped, and each created slot joins it so
    later rules in the same run see it. ``None`` (guild rules) skips the check.
    """
    from membership.models import OrientationSlot

    created = 0
    for start_dt, end_dt in spans:
        if start_dt <= reference:
            continue
        if occupied is not None and _overlaps_other_slot(occupied, rule_id=rule.pk, start=start_dt, end=end_dt):
            continue
        _slot, was_created = OrientationSlot.objects.get_or_create(
            availability=rule,
            starts_at=start_dt,
            defaults={
                "guild": rule.guild,
                # The rule's type rides onto every slot it materializes (issue #282).
                "orientation_type": rule.orientation_type,
                "orienter": rule.orienter,
                "ends_at": end_dt,
                "seats": rule.seats,
                "location": rule.location or rule.orientation_type.default_location,
                "source": OrientationSlot.Source.GENERATED,
            },
        )
        if was_created:
            created += 1
            if occupied is not None:
                occupied.append((rule.pk, start_dt, end_dt))
    return created


def generate_slots(
    *,
    guild: Guild | None = None,
    equipment: Equipment | None = None,
    window_weeks: int = 8,
    now: datetime | None = None,
) -> int:
    """Materialize bookable slots from active recurring rules across a rolling window.

    Idempotent (a slot is keyed by its rule + start time), and skips owners that
    aren't currently accepting bookings (``OrientationType.is_accepting``: the guild
    settings gate, or active + open equipment). Returns the number of slots created.
    Pass ``guild`` or ``equipment`` to materialize just one owner's rules (e.g. right
    after an editor save); neither means everything, as the nightly job runs it.

    Equipment rules are carved by their slot length (``carve_spans``) and the
    machine is the scarce resource: before carving, each rule's future open
    generated slots that fell off its current grid are retired, and a candidate that
    would overlap any other uncancelled slot on the tool (a booked slot kept from an
    old grid, a one time slot, a sibling type's slot) is skipped. The overlap set is
    loaded once per equipment per run. Guild rules keep their one-slot-per-window
    shape and generate over each other exactly as before.

    Raises:
        ValueError: If both ``guild`` and ``equipment`` are given.
    """
    from membership.models import OrientationAvailability

    if guild is not None and equipment is not None:
        raise ValueError("Pass guild or equipment, not both.")
    reference = now or timezone.now()
    today = timezone.localdate()
    # A rule whose orientation type was deactivated stops generating (existing slots
    # are handled by the bookable() type-active filter, not deleted).
    rules = OrientationAvailability.objects.filter(is_active=True, orientation_type__is_active=True).select_related(
        "guild", "orientation_type", "orientation_type__equipment"
    )
    if guild is not None:
        rules = rules.filter(guild=guild)
    if equipment is not None:
        rules = rules.filter(orientation_type__equipment=equipment)
    eligible = [
        (rule, _horizon_spans(rule, today=today, window_weeks=window_weeks)) for rule in rules if _rule_generates(rule)
    ]
    for rule, spans in eligible:
        if rule.orientation_type.equipment_id is not None:
            _retire_off_grid(rule, spans)
    occupied_by_equipment: dict[int, list[tuple[int | None, datetime, datetime]]] = {}
    created = 0
    for rule, spans in eligible:
        occupied: list[tuple[int | None, datetime, datetime]] | None = None
        equipment_id = rule.orientation_type.equipment_id
        if equipment_id is not None:
            if equipment_id not in occupied_by_equipment:
                occupied_by_equipment[equipment_id] = _occupied_spans(
                    cast("Equipment", rule.orientation_type.equipment), reference=reference
                )
            occupied = occupied_by_equipment[equipment_id]
        created += _materialize(rule, spans, reference=reference, occupied=occupied)
    return created


def _future_generated(rule: OrientationAvailability) -> Any:
    """The rule's GENERATED slots that have not started yet — the only slots retirement may touch."""
    from membership.models import OrientationSlot

    return rule.slots.filter(starts_at__gte=timezone.now(), source=OrientationSlot.Source.GENERATED)


def _retire_slots(slots: Any) -> tuple[int, int]:
    """Delete the open slots in ``slots`` (annotated with ``seat_holding_count``); cap and keep the rest.

    A kept slot is capped to its taken seats so the ``availability`` SET_NULL door
    (or a paused rule) can never let a 1 of 4 booked slot take three more bookings.

    Returns:
        ``(open_slots_removed, kept_with_bookings)``.
    """
    removed = kept = 0
    for slot in slots:
        if slot.seat_holding_count:
            kept += 1
            if slot.seats != slot.seat_holding_count:
                slot.seats = slot.seat_holding_count
                slot.save(update_fields=["seats"])
        else:
            slot.delete()
            removed += 1
    return removed, kept


def retire_open_slots(rule: OrientationAvailability) -> tuple[int, int]:
    """Retire a rule's future open generated slots WITHOUT deleting the rule.

    Future GENERATED slots holding no seat-holding booking are deleted; slots
    someone already booked survive, capped to their taken seats. Past and MANUAL
    slots are never touched. Used by delete (via :func:`retire_rule`), by a pause
    (Active toggled off) and by a re-grid (slot length / break / seats changed).

    The keep/delete guard runs at ``bookings.seat_holding()`` scope — an
    ``active()``-only test would cascade a live paid ``PENDING_PAYMENT`` hold away
    with the slot, eating a checkout mid-payment.

    Returns:
        ``(open_slots_removed, kept_with_bookings)`` for the success message.
    """
    return _retire_slots(_future_generated(rule).with_seat_holding_count())


def retire_rule(rule: OrientationAvailability) -> tuple[int, int]:
    """Delete a recurring rule AND its future open generated slots — the honest delete path.

    :func:`retire_open_slots` then ``rule.delete()``: kept booked slots survive
    (keeping their ``orienter``; ``availability`` goes SET_NULL) to be handled
    individually via the Upcoming Slots card. Replaces the old silent slot-stranding.

    Returns:
        ``(open_slots_removed, kept_with_bookings)`` for the success message.
    """
    removed, kept = retire_open_slots(rule)
    rule.delete()
    return removed, kept


def retire_orienter(guild: Guild, member: Member) -> tuple[int, int]:
    """Retire all of ``member``'s personal rules in ``guild`` (they left its leadership).

    Runs :func:`retire_rule` over each of their rules in this guild only — other guilds'
    rules are untouched. Their booked future slots stay theirs (the ex-staffer may still
    honor them, or a lead cancels each from the Upcoming Slots card).

    Returns:
        ``(open_slots_removed, booked_future_slots_remaining)`` — the second number
        feeds the staff-remove flash message.
    """
    from membership.models import OrientationAvailability, OrientationBooking, OrientationSlot

    removed = 0
    for rule in OrientationAvailability.objects.filter(guild=guild, orienter=member):
        rule_removed, _kept = retire_rule(rule)
        removed += rule_removed
    booked_remaining = (
        OrientationSlot.objects.filter(
            guild=guild,
            orienter=member,
            is_cancelled=False,
            starts_at__gte=timezone.now(),
            bookings__status__in=[OrientationBooking.Status.REQUESTED, OrientationBooking.Status.CONFIRMED],
        )
        .distinct()
        .count()
    )
    return removed, booked_remaining


def member_joined_guild(guild: Guild, member: Member) -> None:
    """Fan out when a member joins a guild: lead notification + activity (no email).

    A plain :func:`emit` handles both: the ``GUILD_JOINED`` activity row and the
    lead-only in-app ``guild_joined`` notification (resolver ``guild_lead`` —
    preserving the lead-only audience). ``guild_joined`` declares no email channel
    (``core/triggers.py``: ``no_email=True``), so no email is sent — the old
    guild welcome email was removed with its dead "Join This Guild" trigger.
    """
    emit(
        "guild_joined",
        actor=member.user,
        target=guild,
        context={"guild": guild},
        title="New follower",
        body=f"{member.display_name} now follows {guild.name}.",
        url=reverse("hub_guild_detail", args=[guild.slug]),
        period=f"guild:{guild.pk}:join:{member.pk}",
    )


def _guild_welcome_context(guild: Guild, greeting_name: str, body: str) -> dict[str, Any]:
    """Build the render context for the guild welcome email shell.

    The editable ``body`` is the lead's note (or the standard default). The rest of the
    email is personalized per guild so it reads cleanly in every state:

    - ``leadership``: the guild's lead plus staff, deduped (``Guild.leadership_members``),
      so the member meets who runs the guild. Empty list ⇒ the section is omitted.
    - ``studio_hours``: the guild's standing open-studio blocks (may be empty ⇒ omitted).
    - ``classes_url``: absolute link to this guild's public class catalog.
    - ``orientations_open``: True only when the guild has an orientation-settings row that is
      currently accepting bookings, so the "Book an orientation" line appears only when it works.

    ``help_url`` points at the member-facing guilds guide (the lead-authoring "your guild page"
    article is a different, guild_lead-audience page).
    """
    from membership.models import GuildOrientationSettings

    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    orientations_open = settings_obj is not None and settings_obj.is_accepting
    return {
        "guild": guild,
        "greeting_name": greeting_name,
        "body": body,
        "guild_url": _absolute_url(reverse("hub_guild_detail", args=[guild.slug])),
        "banner_url": _absolute_url(guild.banner_image.url) if guild.banner_image else "",
        "help_url": _absolute_url(reverse("hub_help_article", args=["guilds", "guilds-and-guild-pages"])),
        "leadership": guild.leadership_members(),
        "studio_hours": guild.studio_hours_display(),
        "classes_url": _absolute_url(guild.classes_link().url),
        "orientations_open": orientations_open,
    }


def send_guild_welcome(guild: Guild, member: Member) -> None:
    """Send a member the guild's welcome email once, on a deliberate join (idempotent).

    Fired ONLY on a deliberate join: the hero "Join This Guild" button with the welcome
    box checked, or the Discord ``/join-guild`` command. It is deliberately NOT called
    from :meth:`Member.subscribe_to_guild` / :func:`member_joined_guild`, so the
    first-login interest picker and the Settings notification toggle subscribe silently.

    Transactional, addressed with an explicit ``email_to`` (bypasses preferences — the
    member just asked to join). Deduped once per (member, guild) forever via the ``period``
    key, so a leave-then-rejoin months later never re-welcomes. Gated first on the
    site-wide ``SiteConfiguration.guild_welcome_email_enabled`` switch (off → nothing
    sends, from any caller; per-guild settings persist and take effect again when it is
    turned back on), then on the guild's ``welcome_email_enabled``; a guild with no
    settings row sends nothing.
    """
    from core.models import SiteConfiguration

    if not SiteConfiguration.load().guild_welcome_email_enabled:
        return
    from membership.models import GuildOrientationSettings

    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    if settings_obj is None or not settings_obj.welcome_email_ready:
        return
    emit_with_email_shell(
        "guild_welcome",
        target=guild,
        context={"member": None},  # resolver finds nobody → email-only, no in-app/push dup
        subject=settings_obj.welcome_email_subject_resolved,
        text_template="membership/emails/guild_welcome.txt",
        html_template="membership/emails/guild_welcome.html",
        template_context=_guild_welcome_context(guild, member.display_name, settings_obj.welcome_email_body_resolved),
        email_to=member.primary_email,
        email_trigger_kind="guild_welcome",
        period=f"guild:{guild.pk}:welcome:{member.pk}",
    )


def send_guild_welcome_test(guild: Guild, member: Member) -> None:
    """Send the guild's welcome email to a lead's own inbox as a proof (always sends).

    Powers the "Send test to me" button on the Welcome Email editor tab. Unlike the
    member-facing send it ignores the enabled gate (a lead may proof a draft before turning
    it on) and uses a per-instant idempotency bucket so repeated proofs all go out.
    """
    from membership.models import GuildOrientationSettings

    settings_obj, _created = GuildOrientationSettings.objects.get_or_create(guild=guild)
    stamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
    emit_with_email_shell(
        "guild_welcome",
        target=guild,
        context={"member": None},
        subject=settings_obj.welcome_email_subject_resolved,
        text_template="membership/emails/guild_welcome.txt",
        html_template="membership/emails/guild_welcome.html",
        template_context=_guild_welcome_context(guild, member.display_name, settings_obj.welcome_email_body_resolved),
        email_to=member.primary_email,
        email_trigger_kind="guild_welcome",
        period=f"guild:{guild.pk}:welcome:test:{member.pk}:{stamp}",
    )
