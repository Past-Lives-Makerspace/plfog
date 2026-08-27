"""Orientation orchestration: iCal invites, transactional emails, activity + notifications.

The models hold state and guards; this module wires the side effects around each
lifecycle transition (book → confirm / decline / cancel). Every member-facing
email carries an ``.ics`` so Google/Outlook can track the appointment, and each
transition logs ``SiteActivity`` and fires an in-app notification.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import icalendar
from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from core.events.emit import emit
from core.events.senders import emit_with_email_shell
from core.models import SiteActivity

if TYPE_CHECKING:
    from membership.models import Guild, Member, OrientationBooking, OrientationSlot

_ACTION_SALT = "orientation-action"
_ACTION_MAX_AGE = 90 * 24 * 3600  # 90 days — long enough to schedule, bounded for safety
_ACTIONS = frozenset({"confirm", "decline", "cancel"})


def _absolute_url(path: str) -> str:
    """Turn a relative hub path into an absolute URL using the member-site base."""
    base = settings.MEMBER_BASE_URL.rstrip("/")
    return f"{base}{path}"


def make_action_token(booking: OrientationBooking, action: str) -> str:
    """Sign a no-login token authorizing one ``action`` on one ``booking`` (for email links)."""
    return signing.dumps({"booking": booking.pk, "action": action}, salt=_ACTION_SALT)


def read_action_token(token: str) -> tuple[OrientationBooking, str]:
    """Decode an action token to its (booking, action).

    Raises:
        signing.BadSignature: If the token is invalid, expired, or names an unknown action.
        OrientationBooking.DoesNotExist: If the booking no longer exists.
    """
    from membership.models import OrientationBooking

    data = signing.loads(token, salt=_ACTION_SALT, max_age=_ACTION_MAX_AGE)
    action = data["action"]
    if action not in _ACTIONS:
        raise signing.BadSignature("unknown orientation action")
    booking = OrientationBooking.objects.select_related("slot", "guild", "member").get(pk=data["booking"])
    return booking, action


def apply_token_action(booking: OrientationBooking, action: str) -> str:
    """Apply an email-link action, honoring valid state transitions.

    Returns a short status: "confirmed", "declined", "cancelled", or "already"
    (when the booking was no longer in a state the action applies to).
    """
    from membership.models import OrientationBooking

    statuses = OrientationBooking.Status
    if action == "cancel":
        if booking.status in (statuses.REQUESTED, statuses.CONFIRMED):
            cancel_orientation(booking, actor_label=booking.member.display_name)
            return "cancelled"
        return "already"
    if booking.status != statuses.REQUESTED:
        return "already"
    if action == "confirm":
        confirm_orientation(booking)
        return "confirmed"
    decline_orientation(booking)
    return "declined"


def _action_url(booking: OrientationBooking, action: str) -> str:
    return _absolute_url(reverse("hub_orientation_action", args=[make_action_token(booking, action)]))


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
    event.add("description", f"Orientation for {booking.guild.name} at Past Lives Makerspace.")
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
        "cancel_url": _action_url(booking, "cancel"),
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


def request_orientation(slot: OrientationSlot, member: Member, *, note: str = "") -> OrientationBooking:
    """Book a slot (REQUESTED) and fan out the request emails, activity, and orienter notification.

    Raises:
        OrientationError: Propagated from ``slot.book`` when the slot can't be booked.
    """
    booking = slot.book(member, note=note)
    _emit_member_email(
        booking,
        action="request",
        subject=f"Orientation request received — {booking.guild.name}",
        template="orientation_request",
        ics=_ics(booking, method="REQUEST", status="TENTATIVE"),
    )
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_REQUESTED, actor=member.user, target=booking)
    _emit_lead_request(booking)
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


def _emit_lead_request(booking: OrientationBooking) -> None:
    """Email lead + all staff the request, and in-app-notify ALL orienters (Decision 7).

    Email recipients are the guild's whole leadership team (lead + staff), addressed as
    explicit addresses so the email recipient set is byte-identical to today. The in-app
    ``orientation_requested`` row now fans out to every orienter (the lead plus the
    ORIENTER-role staff) via the ``guild_orienters`` resolver — fixing the lead-only
    asymmetry the audit found. The activity row is logged by the caller.
    """
    recipients: list[str] = []
    for member in booking.guild.leadership_members():
        if member.primary_email and member.primary_email not in recipients:
            recipients.append(member.primary_email)
    ctx = _context(
        booking,
        respond_url=_absolute_url(reverse("hub_orientation_respond", args=[booking.pk])),
        confirm_url=_action_url(booking, "confirm"),
        decline_url=_action_url(booking, "decline"),
    )
    emit_with_email_shell(
        "orientation_requested",
        context={"guild": booking.guild},
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


def decline_orientation(booking: OrientationBooking, *, note: str = "") -> None:
    """Decline a request: update state, email the member, log + notify."""
    booking.decline(note=note)
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


def cancel_orientation(booking: OrientationBooking, *, actor_label: str) -> None:
    """Cancel a booking: update state, email the member, notify the orienters, log."""
    booking.cancel()
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
        context={"guild": booking.guild},
        title="Orientation cancelled",
        body=f"{actor_label} cancelled the orientation for {booking.guild.name}.",
        url=reverse("hub_orientation_respond", args=[booking.pk]),
        period=f"booking:{booking.pk}:cancel",
    )


def cancel_slot(slot: OrientationSlot, *, reason: str = "") -> None:
    """Cancel a slot and run the full cancel fan-out for each of its active bookings."""
    active = list(slot.bookings.active())
    slot.mark_cancelled(reason=reason)
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
                    "ends_at": timezone.make_aware(datetime.combine(day, rule.end_time)),
                    "seats": rule.seats,
                    "location": rule.location or settings_obj.default_location,
                    "source": OrientationSlot.Source.GENERATED,
                },
            )
            if was_created:
                created += 1
    return created


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
