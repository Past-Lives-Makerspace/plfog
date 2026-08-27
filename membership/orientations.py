"""Orientation orchestration: iCal invites, transactional emails, activity + notifications.

The models hold state and guards; this module wires the side effects around each
lifecycle transition (book → confirm / decline / cancel). Every member-facing
email carries an ``.ics`` so Google/Outlook can track the appointment, and each
transition logs ``SiteActivity`` and fires an in-app notification.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

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

    from membership.models import Guild, Member, OrientationAvailability, OrientationBooking, OrientationSlot

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
    event.add("summary", f"Orientation — {booking.guild.name}")
    event.add("dtstart", slot.starts_at)
    event.add("dtend", slot.ends_at)
    event.add("dtstamp", timezone.now())
    event.add("status", status)
    if slot.location:
        event.add("location", slot.location)
    description = f"Orientation for {booking.guild.name} at Past Lives Makerspace."
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
        "greeting_name": member.display_name,
        "guild_url": _absolute_url(reverse("hub_guild_detail", args=[booking.guild.slug])),
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
        url=reverse("hub_guild_detail", args=[booking.guild.slug]),
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
        subject=f"Orientation request received — {booking.guild.name}",
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


def request_custom_orientation(
    guild: Guild, member: Member, starts_at: datetime, *, note: str = ""
) -> OrientationBooking:
    """Create a one-off MANUAL slot at ``starts_at`` and request it, reusing :func:`request_orientation`.

    Mirrors the hub custom-request view: the guild must have ``GuildOrientationSettings``
    that is both accepting bookings *and* allowing custom requests, else an
    :class:`~membership.models.OrientationError`. The slot ends ``default_duration_minutes``
    after the start, holds a single seat, and sits at the guild's ``default_location``.

    Args:
        guild: The guild to orient for.
        member: The requesting member.
        starts_at: The proposed start (future, validated by :func:`parse_proposed_time`).
        note: Optional free-text note passed to the orienter.

    Returns:
        The created (REQUESTED) :class:`~membership.models.OrientationBooking`.

    Raises:
        OrientationError: If the guild isn't taking custom requests, or the booking fails.
            A booking failure deletes the orphan slot before re-raising, so a failed custom
            request never leaves a dangling slot.
    """
    from membership.models import GuildOrientationSettings, OrientationError, OrientationSlot

    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    if settings_obj is None or not settings_obj.is_accepting or not settings_obj.allow_custom_requests:
        raise OrientationError("This guild isn't taking custom orientation requests right now.")
    slot = OrientationSlot.objects.create(
        guild=guild,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=settings_obj.default_duration_minutes),
        seats=1,
        location=settings_obj.default_location,
        source=OrientationSlot.Source.MANUAL,
    )
    try:
        return request_orientation(slot, member, note=note)
    except OrientationError:
        slot.delete()
        raise


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
    from membership.models import GuildOrientationSettings, OrientationBooking, OrientationError

    settings_obj = GuildOrientationSettings.objects.filter(guild=slot.guild).first()
    if settings_obj is None or not settings_obj.is_paid:
        raise OrientationError("This guild doesn't charge for orientations.")
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
            amount_cents=settings_obj.price_cents,
            product_name=f"Orientation — {slot.guild.name}",
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


def start_custom_orientation_checkout(guild: Guild, member: Member, starts_at: datetime, *, note: str = "") -> str:
    """Custom-time variant of :func:`start_orientation_checkout` — pay-to-book at the same guild price.

    Creates the one-off 1-seat MANUAL slot (like :func:`request_custom_orientation`),
    then delegates. Any failure deletes the orphan slot (and the hold, handled by
    the delegate) before re-raising.

    Raises:
        OrientationError: If the guild isn't taking custom requests, or booking fails.
    """
    from membership.models import GuildOrientationSettings, OrientationError, OrientationSlot

    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    if settings_obj is None or not settings_obj.is_accepting or not settings_obj.allow_custom_requests:
        raise OrientationError("This guild isn't taking custom orientation requests right now.")
    slot = OrientationSlot.objects.create(
        guild=guild,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=settings_obj.default_duration_minutes),
        seats=1,
        location=settings_obj.default_location,
        source=OrientationSlot.Source.MANUAL,
    )
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
    is a quiet no-op). A custom-request hold also deletes its orphan 1-seat
    MANUAL slot.
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
    if slot.seats == 1 and slot.source == OrientationSlot.Source.MANUAL and not slot.bookings.exists():
        slot.delete()


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


def _request_audience(booking: OrientationBooking) -> list[Member]:
    """Who hears about a request: the slot's orienter + the lead (personal), or all leadership.

    A personal slot routes to the person the member actually booked, with the guild lead
    kept in the loop (deduped); a guild slot keeps the full leadership fan-out.
    """
    slot = booking.slot
    if slot.orienter_id is not None and slot.orienter is not None:
        audience = [slot.orienter]
        lead = booking.guild.guild_lead
        if lead is not None and lead.pk != slot.orienter_id:
            audience.append(lead)
        return audience
    return booking.guild.leadership_members()


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
    primary_responder = booking.slot.orienter if booking.slot.orienter_id is not None else booking.guild.guild_lead
    ctx = _context(
        booking,
        respond_url=_absolute_url(reverse("hub_orientation_respond", args=[booking.pk])),
        confirm_url=_action_url(booking, "confirm", recipient=primary_responder),
        decline_url=_action_url(booking, "decline", recipient=primary_responder),
    )
    emit_with_email_shell(
        "orientation_requested",
        context={"guild": booking.guild, "slot": booking.slot},
        subject=f"New orientation request — {booking.guild.name}",
        text_template="membership/emails/orientation_lead_request.txt",
        html_template="membership/emails/orientation_lead_request.html",
        template_context=ctx,
        in_app_title="New orientation request",
        in_app_body=f"{booking.member.display_name} requested an orientation for {booking.guild.name}.",
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
        subject=f"Orientation confirmed — {booking.guild.name}",
        template="orientation_confirmed",
        ics=_ics(booking, method="REQUEST", status="CONFIRMED"),
        in_app_title="Orientation confirmed",
        in_app_body=f"Your orientation for {booking.guild.name} is confirmed.",
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
        subject=f"About your orientation request — {booking.guild.name}",
        template="orientation_declined",
        ics=None,
        in_app_title="Orientation not confirmed",
        in_app_body=f"Your orientation request for {booking.guild.name} couldn't be confirmed.",
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
        subject=f"Orientation cancelled — {booking.guild.name}",
        template="orientation_cancelled",
        ics=_ics(booking, method="CANCEL", status="CANCELLED"),
        in_app_title="Orientation cancelled",
        in_app_body=f"The orientation for {booking.guild.name} was cancelled.",
    )
    # In-app ping to the orienters that a booking was cancelled (was lead-only; now
    # fans out to all orienters via the guild_orienters resolver — Decision 7). The
    # orientation_requested EMAIL channel defaults OFF (opt-in), so this matches the
    # old dispatch (in-app always; generic email only for an opted-in orienter).
    emit(
        "orientation_requested",
        context={"guild": booking.guild, "slot": booking.slot},
        title="Orientation cancelled",
        body=f"{actor_label} cancelled the orientation for {booking.guild.name}.",
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
    settings_obj = GuildOrientationSettings.objects.filter(guild=booking.guild).first()
    # The thank-you is on by default: send unless a guild explicitly turned it off. When the
    # guild hasn't written their own subject/body, the standard copy stands in.
    if settings_obj is None or settings_obj.thankyou_email_enabled:
        from membership.orientation_copy import STANDARD_THANKYOU_BODY, standard_thankyou_subject

        subject = (
            settings_obj.resolved_thankyou_subject if settings_obj else standard_thankyou_subject(booking.guild.name)
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
    welcome_ctx = _context(booking)  # guild, greeting_name (= member.display_name), guild_url
    emit(
        "orientation.completed",
        actor=None,  # system event; the member is the subject, not the actor
        target=booking,
        context={
            "guild": booking.guild,  # resolver key (guild_members) + _guild_broadcast destination
            "member_name": booking.member.display_name,
            "guild_name": booking.guild.name,
            "guild_url": welcome_ctx["guild_url"],
        },
        url=welcome_ctx["guild_url"],  # the in-app bell row's click-through
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


def generate_slots(*, guild: Guild | None = None, window_weeks: int = 8, now: datetime | None = None) -> int:
    """Materialize bookable slots from active recurring rules across a rolling window.

    Idempotent (a slot is keyed by its rule + start time), and skips guilds that
    aren't currently accepting bookings. Returns the number of slots created. Pass
    ``guild`` to materialize just one guild's rules (e.g. right after an editor save).
    """
    from membership.models import GuildOrientationSettings, OrientationAvailability, OrientationSlot

    reference = now or timezone.now()
    today = timezone.localdate()
    created = 0
    rules = OrientationAvailability.objects.filter(is_active=True).select_related("guild")
    if guild is not None:
        rules = rules.filter(guild=guild)
    for rule in rules:
        settings_obj = GuildOrientationSettings.objects.filter(guild=rule.guild).first()
        if settings_obj is None or not settings_obj.is_accepting:
            continue
        # Stale personal rule — its orienter left the guild's leadership without the
        # hub's retirement flow (e.g. a lead-FK change or a Django-admin removal).
        # Belt-and-braces: never materialize new slots for a departed staffer.
        if rule.orienter_id is not None and rule.orienter_id not in {
            member.pk for member in rule.guild.leadership_members()
        }:
            continue
        for offset in range(window_weeks * 7):
            day = today + timedelta(days=offset)
            if day.weekday() != rule.weekday:
                continue
            start_dt = timezone.make_aware(datetime.combine(day, rule.start_time))
            if start_dt <= reference:
                continue
            _slot, was_created = OrientationSlot.objects.get_or_create(
                availability=rule,
                starts_at=start_dt,
                defaults={
                    "guild": rule.guild,
                    "orienter": rule.orienter,
                    "ends_at": timezone.make_aware(datetime.combine(day, rule.end_time)),
                    "seats": rule.seats,
                    "location": rule.location or settings_obj.default_location,
                    "source": OrientationSlot.Source.GENERATED,
                },
            )
            if was_created:
                created += 1
    return created


def retire_rule(rule: OrientationAvailability) -> tuple[int, int]:
    """Delete a recurring rule AND its future open generated slots — the honest delete path.

    Future GENERATED slots holding no seat-holding booking are removed with the rule;
    slots someone already booked survive (keeping their ``orienter``; ``availability``
    goes SET_NULL) to be handled individually via the Upcoming Slots card. Past and
    MANUAL slots are never touched. Replaces the old silent slot-stranding.

    The keep/delete guard runs at ``bookings.seat_holding()`` scope — an
    ``active()``-only test would cascade a live paid ``PENDING_PAYMENT`` hold away
    with the slot, eating a checkout mid-payment.

    Returns:
        ``(open_slots_removed, kept_with_bookings)`` for the success message.
    """
    from membership.models import OrientationSlot

    removed = 0
    kept = 0
    future = rule.slots.filter(starts_at__gte=timezone.now(), source=OrientationSlot.Source.GENERATED)
    for slot in future:
        if slot.bookings.seat_holding().exists():
            kept += 1
        else:
            slot.delete()
            removed += 1
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
    """Fan out when a member joins a guild: welcome email (if configured), lead notification, activity.

    One :func:`emit` handles all three: the ``GUILD_JOINED`` activity row, the lead-only
    in-app ``guild_joined`` notification (resolver ``guild_lead`` — preserving the
    lead-only audience), and — when the guild configured a welcome email — the welcome
    email to the *member* via an explicit ``email_to`` (a different audience from the
    in-app, addressed directly so it sends regardless of preferences, as today).
    """
    from membership.models import GuildOrientationSettings

    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    # A configured welcome email needs a settings row that is join_email_ready; bind it to
    # a separate name so the type checker can narrow ``GuildOrientationSettings | None``.
    welcome_settings = settings_obj if (settings_obj is not None and settings_obj.join_email_ready) else None
    welcome_ready = welcome_settings is not None
    template_context = {
        "guild": guild,
        "greeting_name": member.display_name,
        "body": welcome_settings.join_email_body if welcome_settings is not None else "",
        "guild_url": _absolute_url(reverse("hub_guild_detail", args=[guild.slug])),
    }
    emit_with_email_shell(
        "guild_joined",
        actor=member.user,
        target=guild,
        context={"guild": guild},
        subject=welcome_settings.join_email_subject if welcome_settings is not None else "",
        text_template="membership/emails/guild_welcome.txt",
        html_template="membership/emails/guild_welcome.html",
        template_context=template_context,
        in_app_title="New follower",
        in_app_body=f"{member.display_name} now follows {guild.name}.",
        url=reverse("hub_guild_detail", args=[guild.slug]),
        email_to=member.primary_email if welcome_ready else None,
        period=f"guild:{guild.pk}:join:{member.pk}",
    )
