"""Channel adapters — pluggable delivery (design §2.4).

A :class:`ChannelAdapter` knows how to deliver one rendered message to one
recipient over one channel. The registry maps each :class:`core.events.registry.Channel`
to its adapter. Adding a future channel = one adapter + one registry entry +
(eventually) a preference column; events and call sites never change.

Phase-1 invariant: the three live adapters **wrap the existing mechanisms** with
no behavior change of their own —

* :class:`InAppAdapter` creates the same :class:`core.models.Notification` bell row
  ``dispatch()`` creates;
* :class:`EmailAdapter` sends through the ``core.email.send`` choke-point (so the
  ``TransactionalEmailLog`` audit row is still written);
* :class:`PushAdapter` sends through ``core/push.py`` for each of the user's
  subscriptions.

Phase 2 makes the three remaining adapters real:

* :class:`DiscordAdapter` is a **per-event BROADCAST** channel — it posts one embed
  to a configured webhook (``DISCORD_NOTIFY_WEBHOOK_URL`` by default, with a
  per-event routing override structure), not one message per recipient. It is a
  no-op when no webhook is configured and is best-effort (logged, never raises).
* :class:`ScheduledEmailAdapter` renders + sends through the email choke-point when
  invoked, and exposes a due-window helper for the (Phase-5) scheduler.
* :class:`DigestAdapter` does not send immediately — it **buffers** a digest event's
  email as a ``PENDING`` :class:`core.models.EventDelivery` row, and sends each person
  one 8 AM Pacific digest of what is still open (#894).

:class:`ChannelNotImplemented` and :class:`_ShellAdapter` remain for any future
register-before-build channel; the three above no longer use them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable
from zoneinfo import ZoneInfo

from django.utils import timezone
from django.utils.text import Truncator

from core.email import send as send_email
from core.events import discord as discord_module
from core.events import discord_dm as discord_dm_module
from core.events.registry import Channel
from core.fcm import (
    PUSH_CHANNEL_CLASSES,
    PUSH_CHANNEL_GENERAL,
    PUSH_CHANNEL_GUILDS,
    PUSH_CHANNEL_URGENT,
    send_fcm,
)
from core.models import EventDelivery, FcmDevice, Notification, PushSubscription, TransactionalEmailLog
from core.push import send_web_push
from core.urls_util import member_absolute_url, notification_click_url

if TYPE_CHECKING:
    from datetime import date, datetime, timedelta

    from django.contrib.auth.models import User

    from core.email import Attachment
    from core.email_prefs import StopLink


class ChannelNotImplemented(NotImplementedError):
    """Raised when a registered-but-unbuilt channel adapter is invoked (Phase 2)."""


@dataclass(frozen=True)
class Message:
    """A rendered message ready to hand to a channel adapter.

    One ``Message`` is built per event emission and reused across recipients and
    channels. ``html_body`` is optional (text-only channels ignore it); ``url`` is
    where an in-app / push click navigates; ``trigger_kind`` is the audit label
    passed to the email choke-point.

    ``discord_mention`` is the opt-in ping literal (``"@here"`` / ``"@everyone"``, or
    ``""`` for none) — only the Discord broadcast reads it (via
    :func:`core.events.discord.build_embed_payload`); every other channel ignores it,
    so a blank value leaves every existing payload byte-identical.

    ``discord_username`` / ``discord_avatar_url`` are the name and picture a Discord webhook
    post shows instead of the webhook's own (an announcement posted "as me", #730); blank
    leaves the webhook's default, and every other channel ignores them.

    ``stop_link`` is a past students email's stop link (#1042): the email channel hands it to
    ``core.email.send``, which fills its placeholder per recipient; every other channel ignores it.

    ``discord_image_url`` is an absolute picture URL the Discord embed shows (an announcement's
    image, #1064); blank leaves the embed as it was, and every other channel ignores it.
    """

    title: str
    body: str
    url: str = ""
    html_body: str | None = None
    trigger_kind: str = ""
    discord_mention: str = ""
    discord_username: str = ""
    discord_avatar_url: str = ""
    stop_link: StopLink | None = None
    discord_image_url: str = ""


@runtime_checkable
class ChannelAdapter(Protocol):
    """The interface every channel implements.

    ``channel`` identifies the adapter in the registry; ``deliver`` hands one
    message to one user. Adapters are best-effort and must not raise on ordinary
    delivery failure (the spine keeps fanning out to other recipients/channels).

    ``is_broadcast`` distinguishes the per-event broadcast channel (Discord) from
    the per-recipient channels: a broadcast adapter is invoked **once per event
    emission** via :meth:`broadcast`, not once per recipient via :meth:`deliver`.
    Per-recipient adapters leave ``is_broadcast`` False and never implement
    ``broadcast``.

    ``attachments`` is the per-recipient channel hook for files that ride along
    with the message (an orientation ``.ics``); channels that cannot carry files
    (in-app, push) ignore it. It defaults to ``None`` so every existing caller and
    adapter is unaffected.

    ``deliver`` returns whether the spine should keep the idempotency slot it claimed
    for this recipient. Return ``False`` only when nothing was sent AND sending it later
    is the right remedy — no address on file, a provider rejection — which releases the
    slot so a re-run reaches exactly the recipients who were missed (see
    :func:`core.events.emit._release_delivery`). Returning ``True`` when nothing was sent
    locks the recipient out of that event permanently.

    A channel the recipient simply has not set up returns ``True``, not ``False``: push
    with no registered device, a DM with no linked Discord account. Nothing was sent, but
    a retry would deliver a stale tray notification rather than repair anything, so the
    slot is spent deliberately.
    """

    channel: Channel
    is_broadcast: bool

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool: ...


def _fit(value: str, limit: int) -> str:
    """Clip a value to a CharField's ``max_length``.

    Bell rows are one-liners (``title`` 200, ``body`` 500). A long announcement body
    or subject would otherwise overflow the column and crash the whole ``emit()`` — so
    every in-app write is clipped here (the full content still rides the email).
    """
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


class InAppAdapter:
    """Writes one in-app bell :class:`core.models.Notification` row.

    Identical to the row ``dispatch()`` creates today (same ``trigger`` column =
    the event key, same title/body/url).
    """

    channel = Channel.IN_APP
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool:
        # In-app rows carry no file attachments — the bell shows title/body/url.
        Notification.objects.create(
            user=user,
            trigger=message.trigger_kind or "",
            title=_fit(message.title, 200),
            body=_fit(message.body, 500),
            url=notification_click_url(message.url),
        )
        # The row is written or the create raised — reaching here means delivered.
        return True


def notification_email_for(user: User) -> str:
    """The address event-driven notification email should go to for this user.

    The member's chosen ``notification_email`` wins only while it is still a
    verified address on this user; otherwise the allauth primary mirror
    (``user.email``) is used. Never raises; returns "" when the user has no email.

    Fail-soft by design: deleting or unverifying the chosen address silently falls
    back to the primary at send time — no cleanup plumbing, notifications are never
    stranded. One verification query per recipient is accepted (fan-outs are small);
    do not add caching or prefetch plumbing.
    """
    member = getattr(user, "member", None)
    chosen = (getattr(member, "notification_email", "") or "").strip()
    if chosen:
        from allauth.account.models import EmailAddress

        if EmailAddress.objects.filter(user=user, email__iexact=chosen, verified=True).exists():
            return chosen
    return user.email or ""


def email_category_for(trigger_kind: str) -> str | None:
    """The event's registry category for the ``X-Category`` header, or ``None``.

    ``trigger_kind`` is the event key for spine sends; an empty fallback or a
    non-event kind has no registry category, so the header is simply omitted.
    """
    if not trigger_kind:
        return None
    from core.events.registry import get_event

    try:
        return get_event(trigger_kind).category
    except KeyError:
        return None


# --- Native push notification channels (Android) -----------------------------------
# The four coarse channels the app creates (see static/js/native-push.js). A member
# tunes each one independently in their phone's notification settings, so every FCM
# message is tagged with exactly one channel id and lands in the right category. The
# ids themselves live in core.fcm (shared with the diagnostic push sender).
# Event keys that always ride the high-importance Urgent channel, whatever settings
# category they belong to: act-now, or you-are-inconvenienced-if-you-miss-it.
_URGENT_EVENT_KEYS: frozenset[str] = frozenset(
    {
        "tab_charge_failed",
        "class_reminder",
        "class_cancelled",
        "waitlist_spot_available",
        "event.happening_now",
        "lease_expiring",
    }
)

# Every settings-page category routes to one coarse channel. Kept total over the live
# categories (a completeness spec guards it), so a new category added without a mapping
# fails CI loudly rather than silently defaulting.
_CHANNEL_BY_CATEGORY: dict[str, str] = {
    "Guilds": PUSH_CHANNEL_GUILDS,
    "Voting": PUSH_CHANNEL_GUILDS,
    "Classes": PUSH_CHANNEL_CLASSES,
    "Teaching": PUSH_CHANNEL_CLASSES,
    "Announcements": PUSH_CHANNEL_GENERAL,
    "Billing": PUSH_CHANNEL_GENERAL,
    "Events": PUSH_CHANNEL_GENERAL,
    "Meetings": PUSH_CHANNEL_GENERAL,
    "Membership": PUSH_CHANNEL_GENERAL,
    "Orientations": PUSH_CHANNEL_GENERAL,
    "Spaces & Equipment": PUSH_CHANNEL_GENERAL,
    "Your requests": PUSH_CHANNEL_GENERAL,
    # The wiki is guild-shaped: a report routes to a guild's leadership and a
    # verification comes from one, so both ride the Guilds channel.
    "Wiki": PUSH_CHANNEL_GUILDS,
    # Kiln ticket messages run between a maker and the Ceramics Guild crew (#691).
    "Kiln tickets": PUSH_CHANNEL_GUILDS,
    # Facility tours: a guide hears a visitor booked, moved or cancelled (#948).
    "Tours": PUSH_CHANNEL_GENERAL,
}


def push_channel_for(trigger_kind: str) -> str:
    """Pick the native push channel id for an event.

    An explicitly urgent event key wins outright; otherwise the event's registry
    category selects the channel. A kind with no registry category (an empty fallback
    or a non-event kind) lands in General.
    """
    if trigger_kind in _URGENT_EVENT_KEYS:
        return PUSH_CHANNEL_URGENT
    category = email_category_for(trigger_kind)
    if category is None:
        return PUSH_CHANNEL_GENERAL
    return _CHANNEL_BY_CATEGORY[category]


class EmailAdapter:
    """Sends through the ``core.email.send`` choke-point (audited, best-effort).

    Mirrors the email path inside ``dispatch()`` exactly: subject = title, body =
    text/html, ``best_effort=True`` so an SMTP failure is logged but never raised.
    Skips users with no usable email (defensive — resolvers already filter these).
    """

    channel = Channel.EMAIL
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool:
        address = notification_email_for(user)
        if not address.strip():
            # No usable address — nothing was sent, so the spine must not keep the slot.
            # A member whose address is added later is otherwise locked out forever.
            return False
        log = send_email(
            to=address,
            subject=message.title,
            trigger_kind=message.trigger_kind or "notification",
            text_body=message.body,
            html_body=message.html_body,
            best_effort=True,
            attachments=attachments,
            category=email_category_for(message.trigger_kind),
            stop_link=message.stop_link,
        )
        # best_effort swallows the exception but still logs the outcome, and that log row
        # is the only signal a provider rejection (e.g. a 429 daily-quota reply) produces.
        return log.status == TransactionalEmailLog.Status.SENT


# A push shows a short bold title over a one-line body in the notification tray. Cap
# both generously — the OS truncates anyway, but a clean word-boundary ellipsis reads
# better than a hard mid-word cut, and it keeps a stray essay out of the tray.
_PUSH_TITLE_LIMIT = 80
_PUSH_BODY_LIMIT = 200


def _push_safe(title: str, body: str) -> tuple[str, str]:
    """Flatten copy to a single tag-free line and cap it for the notification tray."""
    from core.html_sanitize import rich_html_to_text

    flat_title = rich_html_to_text(title) or title
    flat_body = rich_html_to_text(body)
    return Truncator(flat_title).chars(_PUSH_TITLE_LIMIT), Truncator(flat_body).chars(_PUSH_BODY_LIMIT)


class PushAdapter:
    """Sends a push to every one of the user's devices.

    Two transports ride this one channel: browser Web Push subscriptions via
    ``core/push.py`` and native (Capacitor) FCM device tokens via ``core/fcm.py`` — the
    native app cannot receive Web Push, so it registers an FCM token instead. Both are
    best-effort and reap their own dead rows.
    """

    channel = Channel.PUSH
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool:
        # A push renders as one tray line: no HTML, no multi-paragraph body. Flatten
        # any rich/multi-line copy (an announcement body is sanitized HTML) to a single
        # clean line and cap both fields so the OS shows a tidy ellipsis instead of a
        # hard mid-word cut. Push carries no file attachments.
        title, body = _push_safe(message.title, message.body)
        channel_id = push_channel_for(message.trigger_kind)
        # The app's own host, never another: see notification_click_url.
        url = notification_click_url(message.url)
        for sub in PushSubscription.objects.filter(user=user):
            send_web_push(sub, title=title, body=body, url=url)
        for device in FcmDevice.objects.filter(user=user):
            send_fcm(device, title=title, body=body, url=url, channel_id=channel_id)
        # Always spend the slot. A member with no registered device received nothing, but
        # re-pushing on some later run would drop a stale line in their tray instead of
        # fixing anything — unlike email, the miss is not worth repairing late.
        return True


def push_device_count(user: User) -> int:
    """How many of a member's devices a push can currently reach.

    Counts both transports :class:`PushAdapter` fans out to, so the number a member reads
    on their settings page is derived from the same definition that does the sending and
    cannot drift from it. Used for the "Push On This Device" card's context line.
    """
    return PushSubscription.objects.filter(user=user).count() + FcmDevice.objects.filter(user=user).count()


class _ShellAdapter:
    """Base for any future registered-but-unbuilt adapter.

    Present so a new channel can be declared in the registry before its adapter is
    written; raises :class:`ChannelNotImplemented` if invoked — a premature call is
    a bug, not a silent no-op. (No live channel uses this base after Phase 2.)
    """

    channel: Channel
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool:
        raise ChannelNotImplemented(f"The {self.channel.value!r} channel is registered but not implemented.")


class ScheduledEmailAdapter:
    """Timed email — sends through the choke-point when its fire time arrives.

    Same delivery as :class:`EmailAdapter` (the choke-point, audited, best-effort);
    the *timing* is the difference. When the (Phase-5) generalized scheduler finds
    a scheduled event due (via :mod:`core.events.scheduling`), it calls
    :meth:`deliver` exactly as for any email. The due-window math lives in
    :mod:`core.events.scheduling`; this adapter re-exports :meth:`is_due` for
    callers that have the anchor in hand.

    SCHEDULER HANDOFF (Phase 5): nothing here is wired into ``run_scheduled_tasks``.
    The scheduler that walks the registry for due scheduled events, computes each
    event's anchor, and calls :func:`core.events.emit.emit` is built in Phase 5;
    this adapter + :mod:`core.events.scheduling` are the pieces it composes.
    """

    channel = Channel.SCHEDULED_EMAIL
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool:
        address = notification_email_for(user)
        if not address.strip():
            # No usable address — nothing was sent, so the spine must not keep the slot.
            # A member whose address is added later is otherwise locked out forever.
            return False
        log = send_email(
            to=address,
            subject=message.title,
            trigger_kind=message.trigger_kind or "notification",
            text_body=message.body,
            html_body=message.html_body,
            best_effort=True,
            attachments=attachments,
            category=email_category_for(message.trigger_kind),
        )
        # best_effort swallows the exception but still logs the outcome, and that log row
        # is the only signal a provider rejection (e.g. a 429 daily-quota reply) produces.
        return log.status == TransactionalEmailLog.Status.SENT

    @staticmethod
    def is_due(anchor: datetime, offset: timedelta, *, now: datetime | None = None) -> bool:
        """Whether an event anchored at ``anchor + offset`` is due this tick.

        Thin re-export of :func:`core.events.scheduling.is_due` so a caller holding
        the anchor (orientation slot, class session, month-end) can ask the adapter
        directly.
        """
        from core.events.scheduling import is_due as _is_due

        return _is_due(anchor, offset, now=now)


#: The staff digest goes out from this Pacific hour (#894), and takes only what was
#: queued before it, so a request filed at 3 PM waits for tomorrow morning.
DIGEST_HOUR = 8
PACIFIC = ZoneInfo("America/Los_Angeles")
#: The audit label on each digest's ``TransactionalEmailLog`` row, and the ledger key that
#: makes it once per person per Pacific date.
DIGEST_TRIGGER_KIND = "staff_digest"


class DigestAdapter:
    """Buffers a staff request for the 8 AM Pacific digest email (#894) — does NOT send now.

    :meth:`enqueue` writes one ``PENDING`` :class:`core.models.EventDelivery` row per
    (event, recipient, request), the request being the emit's ``period`` (a class review
    gate's ``approval:<pk>:request``), so a second request never overwrites the first.
    The row carries the line the digest shows: title, one line, link.

    :meth:`flush_due` is the daily send, called by the ``send_staff_digests`` job every
    tick. From 8:00 Pacific it takes the rows queued before today's 8:00, drops the
    requests decided since (each event's still-open check in
    :func:`core.events.resolvers.open_request_urls`) and the events a person has since
    switched their email off for, and sends each person one email, at most once per
    Pacific date. Nobody left with anything gets no email.
    """

    channel = Channel.DIGEST
    is_broadcast = False

    def enqueue(self, user: User, message: Message, *, event_key: str, period: str) -> bool:
        """Buffer ``message`` as ``user``'s digest line for the request ``period`` names.

        Returns ``False`` (nothing queued, so the spine hands its claim back) when the user
        has no address to send the digest to, the same rule :class:`EmailAdapter` keeps.
        """
        if not notification_email_for(user).strip():
            return False
        EventDelivery.objects.update_or_create(
            event_key=event_key,
            target_ref=f"user:{user.pk}",
            channel=self.channel.value,
            period=period,
            defaults={
                "status": EventDelivery.Status.PENDING,
                "title": message.title,
                "body": message.body,
                "url": message.url,
            },
        )
        return True

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool:
        # No event declares Channel.DIGEST; the digest is chosen by EventType.email_delivery
        # and reached through enqueue. Kept for the adapter interface, keyed on the link.
        return self.enqueue(user, message, event_key=message.trigger_kind, period=message.url)

    @staticmethod
    def pending_for(user: User) -> list[EventDelivery]:
        """Every buffered (still-PENDING) digest row for ``user``, oldest first."""
        return list(
            EventDelivery.objects.filter(
                target_ref=f"user:{user.pk}",
                channel=Channel.DIGEST.value,
                status=EventDelivery.Status.PENDING,
            ).order_by("created_at")
        )

    @classmethod
    def flush_due(cls, now: datetime | None = None) -> dict[str, list[int]]:
        """Send today's digests if it is 8:00 Pacific or later; return what each one carried.

        Returns ``{target_ref: [delivery_id, ...]}`` for every digest sent this call.
        Safe to run on every tick: a person already sent today is skipped on the ledger.
        """
        from django.contrib.auth.models import User

        current = (now or timezone.now()).astimezone(PACIFIC)
        cutoff = current.replace(hour=DIGEST_HOUR, minute=0, second=0, microsecond=0)
        if current < cutoff:
            return {}
        pending = EventDelivery.objects.filter(
            channel=Channel.DIGEST.value, status=EventDelivery.Status.PENDING, created_at__lt=cutoff
        ).order_by("created_at")
        by_target: dict[str, list[EventDelivery]] = {}
        kept, left_out = cls._drop_decided(list(pending))
        for row in kept:
            by_target.setdefault(row.target_ref, []).append(row)
        users = User.objects.select_related("member").in_bulk(
            [int(target_ref.removeprefix("user:")) for target_ref in by_target]
        )
        sent: dict[str, list[int]] = {}
        for target_ref, rows in by_target.items():
            user_pk = int(target_ref.removeprefix("user:"))
            if user_pk not in users:
                EventDelivery.objects.filter(pk__in=[row.pk for row in rows]).delete()
                continue
            wanted, switched_off = cls._drop_switched_off(users[user_pk], rows)
            left_out[target_ref] += switched_off
            if wanted and cls._send(users[user_pk], wanted, current.date(), left_out=left_out[target_ref]):
                ids = [row.pk for row in wanted]
                EventDelivery.objects.filter(pk__in=ids).update(status=EventDelivery.Status.SENT)
                sent[target_ref] = ids
        return sent

    @staticmethod
    def _drop_decided(rows: list[EventDelivery]) -> tuple[list[EventDelivery], Counter[str]]:
        """Delete the rows whose request was decided since it was queued.

        Returns the rest, and how many requests (distinct links) each person had left out.
        """
        from core.events.resolvers import open_request_urls

        rows_by_event: dict[str, list[EventDelivery]] = {}
        for row in rows:
            rows_by_event.setdefault(row.event_key, []).append(row)
        open_urls = {key: open_request_urls(key, event_rows) for key, event_rows in rows_by_event.items()}
        keep = [row for row in rows if row.url in open_urls[row.event_key]]
        kept_ids = {row.pk for row in keep}
        dropped = [row for row in rows if row.pk not in kept_ids]
        EventDelivery.objects.filter(pk__in=[row.pk for row in dropped]).delete()
        return keep, Counter(
            target_ref for target_ref, _key in {(row.target_ref, digest_line_key(row)) for row in dropped}
        )

    @staticmethod
    def wanted_event_keys(user: User, event_keys: set[str]) -> set[str]:
        """Which of ``event_keys`` ``user`` still has their email switched on for (read only).

        The send drops the rest at 8 AM; the Email Queue tab asks the same question so the
        two cannot disagree.
        """
        from core.events import preferences

        return {key for key in event_keys if preferences.wants(user, key, Channel.EMAIL)}

    @staticmethod
    def _drop_switched_off(user: User, rows: list[EventDelivery]) -> tuple[list[EventDelivery], int]:
        """Delete the rows for events ``user`` has since switched their email off for.

        Returns the rest, and how many requests (distinct links) that left out.
        """
        wanted_keys = DigestAdapter.wanted_event_keys(user, {row.event_key for row in rows})
        dropped = [row for row in rows if row.event_key not in wanted_keys]
        EventDelivery.objects.filter(pk__in=[row.pk for row in dropped]).delete()
        return [row for row in rows if row.event_key in wanted_keys], len({digest_line_key(row) for row in dropped})

    @staticmethod
    def _send(user: User, rows: list[EventDelivery], day: date, *, left_out: int) -> bool:
        """Send ``user`` the digest of ``rows`` unless they already had one on ``day``.

        A sent digest's ledger row records its subject and how many requests it carried and
        left out (:meth:`core.models.EventDelivery.record_digest`), for the Email Queue tab.

        The ``staff_digest`` ledger slot for the Pacific date is claimed before the send. A
        first failed send hands it back and records a ``digest:<date>:failed`` slot, so the
        next tick retries once; a second failure keeps the day's slot, so a rejecting address
        costs two attempts a day, not one every tick, and the lines wait for tomorrow's 8 AM.
        With no address nothing is claimed and the lines stay queued.
        """
        from django.db import IntegrityError
        from django.template.loader import render_to_string

        address = notification_email_for(user)
        if not address.strip():
            return False
        target_ref, period = f"user:{user.pk}", f"digest:{day.isoformat()}"
        try:
            row, created = EventDelivery.objects.get_or_create(
                event_key=DIGEST_TRIGGER_KIND, target_ref=target_ref, channel=Channel.EMAIL.value, period=period
            )
        except IntegrityError:
            return False
        if not created:
            return False
        context = digest_context(rows)
        log = send_email(
            to=address,
            subject=str(context["subject"]),
            trigger_kind=DIGEST_TRIGGER_KIND,
            text_body=render_to_string("core/emails/staff_digest.txt", context),
            html_body=render_to_string("core/emails/staff_digest.html", context),
            best_effort=True,
        )
        if log.status != TransactionalEmailLog.Status.SENT:
            _failed, first_failure = EventDelivery.objects.get_or_create(
                event_key=DIGEST_TRIGGER_KIND,
                target_ref=target_ref,
                channel=Channel.EMAIL.value,
                period=f"{period}:failed",
            )
            if first_failure:
                row.delete()
            return False
        row.record_digest(subject=str(context["subject"]), included=cast("int", context["count"]), left_out=left_out)
        return True


# A morning email holding only class reviews says so in its subject; anything else
# queued alongside them falls back to the general wording.
CLASS_REVIEW_EVENTS = frozenset({"class_review_requested", "class_validation_requested"})


def digest_line_key(row: EventDelivery) -> str:
    """Which morning email line a queued row belongs to: one line per request.

    Usually the request's link, so the original ask and a Remind lead (same link) make one
    line. A request nothing decides never repeats, and its link can be shared (a class change
    request links to the class), so each of its rows is its own line (#894 part 3).
    """
    from core.events.resolvers import is_always_included

    return row.period if is_always_included(row.event_key) else row.url


def digest_context(rows: list[EventDelivery]) -> dict[str, object]:
    """The staff digest template context for ``rows``, oldest first.

    One line per request (:func:`digest_line_key`): a request queued twice (the original ask
    and a Remind lead) shows once, with the newest row's line ("Reminded by ..."). Lines group under
    their event's label, in the order the first of each arrived.
    """
    from core.events.registry import get_event

    newest_by_url = {digest_line_key(row): row for row in rows}
    sections: dict[str, list[dict[str, str]]] = {}
    for row in newest_by_url.values():
        # Senders pass the bell's link, often a path; the email needs a host (#894 part 3).
        line = {"title": row.title, "body": row.body, "url": member_absolute_url(row.url)}
        sections.setdefault(get_event(row.event_key).label, []).append(line)
    count = len(newest_by_url)
    if {row.event_key for row in newest_by_url.values()} <= CLASS_REVIEW_EVENTS:
        noun = "Class" if count == 1 else "Classes"
        subject = f"Action Needed: {count} {noun} Awaiting Review"
    else:
        noun = "Request" if count == 1 else "Requests"
        subject = f"Action Needed: {count} {noun} Awaiting You"
    return {
        "subject": subject,
        "count": count,
        "sections": [{"label": label, "lines": lines} for label, lines in sections.items()],
    }


class DiscordAdapter:
    """Per-event BROADCAST to a configured Discord webhook (Decision 9).

    Posts ONE embed per event emission to the routed webhook
    (:func:`core.events.discord.webhook_for_event`, defaulting to
    ``DISCORD_NOTIFY_WEBHOOK_URL``) — not one message per recipient. It is a no-op
    when no webhook is configured (mirroring ``MailchimpClient.from_site_config``),
    and best-effort: failures are logged and never raised into ``emit()``.

    Because it is broadcast, :attr:`is_broadcast` is True and the spine invokes
    :meth:`broadcast` once per event, not :meth:`deliver` per recipient. ``deliver``
    is kept (interface compatibility) and routes to ``broadcast`` so an accidental
    per-recipient call still posts at most the same single embed payload.
    """

    channel = Channel.DISCORD
    is_broadcast = True

    def broadcast(self, message: Message) -> bool:
        """Post one embed for ``message`` to the event's routed webhook.

        Returns ``True`` when the post succeeded, ``False`` when disabled (blank
        webhook) or on any failure. Never raises.
        """
        webhook = discord_module.webhook_for_event(message.trigger_kind or "")
        return discord_module.post_embed(webhook, message)

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool:
        # Broadcast channel: the recipient is irrelevant. Defer to broadcast so a
        # stray per-recipient call still posts the single event-level embed.
        return self.broadcast(message)


class DiscordDMAdapter:
    """Per-recipient DM to a member's linked Discord account (opt-in, Decision 9 sibling).

    The mirror image of :class:`DiscordAdapter`: where that posts ONE webhook embed per
    event, this DMs each opted-in recipient through the FOG bot. It is per-recipient
    (``is_broadcast`` False), so the spine invokes :meth:`deliver` once per recipient.

    It is a no-op (never raises) when:

    * the recipient hasn't linked their Discord account (``discord_user_id`` blank), or
    * the bot token is blank (the channel is disabled).

    Otherwise it opens the DM channel and posts via :mod:`core.events.discord_dm`, which
    is best-effort — it logs and swallows any Discord failure so the fan-out continues.
    """

    channel = Channel.DISCORD_DM
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> bool:
        # Discord DMs carry no file attachments — the message is title/body/url text.
        # A disabled channel or an unlinked account spends the slot for the same reason
        # push does: a DM delivered on a later retry is stale, not helpful.
        if not discord_dm_module.bot_token():
            return True
        discord_user_id = discord_dm_module.discord_user_id_for(user)
        if not discord_user_id:
            return True
        discord_dm_module.post_dm(discord_user_id, message)
        return True


# --- Registry ----------------------------------------------------------------

_DIGEST_ADAPTER = DigestAdapter()

_ADAPTERS: dict[Channel, ChannelAdapter] = {
    Channel.IN_APP: InAppAdapter(),
    Channel.EMAIL: EmailAdapter(),
    Channel.PUSH: PushAdapter(),
    Channel.SCHEDULED_EMAIL: ScheduledEmailAdapter(),
    Channel.DIGEST: _DIGEST_ADAPTER,
    Channel.DISCORD: DiscordAdapter(),
    Channel.DISCORD_DM: DiscordDMAdapter(),
}


def get_adapter(channel: Channel) -> ChannelAdapter:
    """Return the adapter for ``channel``. Raises ``KeyError`` if unregistered."""
    return _ADAPTERS[channel]


def digest_adapter() -> DigestAdapter:
    """The registered :class:`DigestAdapter`, typed as itself so emit can call ``enqueue``."""
    return _DIGEST_ADAPTER


def is_implemented(channel: Channel) -> bool:
    """Whether ``channel``'s adapter is a live implementation (vs an unbuilt shell)."""
    return not isinstance(_ADAPTERS[channel], _ShellAdapter)


def broadcast(adapter: ChannelAdapter, message: Message) -> None:
    """Invoke a broadcast adapter's per-event post (Discord).

    Broadcast adapters expose ``broadcast(message)``; this indirection keeps the
    emit spine from importing the concrete :class:`DiscordAdapter` and lets the
    interface stay duck-typed. A non-broadcast adapter passed here is a caller bug.
    """
    adapter.broadcast(message)  # type: ignore[attr-defined]
