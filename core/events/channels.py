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

``ScheduledEmailAdapter``, ``DigestAdapter`` and ``DiscordAdapter`` are registered
**shells** — they implement the interface but defer real work to Phase 2 (Discord
is BUILT in Phase 2 from ``DISCORD_NOTIFY_WEBHOOK_URL``; scheduled-email + digest
ride the scheduler). They raise :class:`ChannelNotImplemented` if invoked so a
premature call fails loudly rather than silently dropping a message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from core.email import send as send_email
from core.events.registry import Channel
from core.models import Notification, PushSubscription
from core.push import send_web_push

if TYPE_CHECKING:
    from django.contrib.auth.models import User


class ChannelNotImplemented(NotImplementedError):
    """Raised when a registered-but-unbuilt channel adapter is invoked (Phase 2)."""


@dataclass(frozen=True)
class Message:
    """A rendered message ready to hand to a channel adapter.

    One ``Message`` is built per event emission and reused across recipients and
    channels. ``html_body`` is optional (text-only channels ignore it); ``url`` is
    where an in-app / push click navigates; ``trigger_kind`` is the audit label
    passed to the email choke-point.
    """

    title: str
    body: str
    url: str = ""
    html_body: str | None = None
    trigger_kind: str = ""


@runtime_checkable
class ChannelAdapter(Protocol):
    """The interface every channel implements.

    ``channel`` identifies the adapter in the registry; ``deliver`` hands one
    message to one user. Adapters are best-effort and must not raise on ordinary
    delivery failure (the spine keeps fanning out to other recipients/channels).
    """

    channel: Channel

    def deliver(self, user: User, message: Message) -> None: ...


class InAppAdapter:
    """Writes one in-app bell :class:`core.models.Notification` row.

    Identical to the row ``dispatch()`` creates today (same ``trigger`` column =
    the event key, same title/body/url).
    """

    channel = Channel.IN_APP

    def deliver(self, user: User, message: Message) -> None:
        Notification.objects.create(
            user=user,
            trigger=message.trigger_kind or "",
            title=message.title,
            body=message.body,
            url=message.url,
        )


class EmailAdapter:
    """Sends through the ``core.email.send`` choke-point (audited, best-effort).

    Mirrors the email path inside ``dispatch()`` exactly: subject = title, body =
    text/html, ``best_effort=True`` so an SMTP failure is logged but never raised.
    Skips users with no usable email (defensive — resolvers already filter these).
    """

    channel = Channel.EMAIL

    def deliver(self, user: User, message: Message) -> None:
        if not (user.email or "").strip():
            return
        send_email(
            to=user.email,
            subject=message.title,
            trigger_kind=message.trigger_kind or "notification",
            text_body=message.body,
            html_body=message.html_body,
            best_effort=True,
        )


class PushAdapter:
    """Sends a web push to every one of the user's subscriptions via ``core/push.py``."""

    channel = Channel.PUSH

    def deliver(self, user: User, message: Message) -> None:
        for sub in PushSubscription.objects.filter(user=user):
            send_web_push(sub, title=message.title, body=message.body, url=message.url)


class _ShellAdapter:
    """Base for registered-but-unbuilt adapters (Phase 2 drop-ins).

    Present so the channel registry is complete and the interface is exercised,
    but raises :class:`ChannelNotImplemented` if actually invoked — a premature
    call is a bug, not a silent no-op.
    """

    channel: Channel

    def deliver(self, user: User, message: Message) -> None:
        raise ChannelNotImplemented(
            f"The {self.channel.value!r} channel is registered but not implemented until Phase 2."
        )


class ScheduledEmailAdapter(_ShellAdapter):
    """Timed email (orientation 48h, class reminders, voting 48h). Built in Phase 2."""

    channel = Channel.SCHEDULED_EMAIL


class DigestAdapter(_ShellAdapter):
    """Buffers events into a batched digest email (#9). Built in a follow-on PR."""

    channel = Channel.DIGEST


class DiscordAdapter(_ShellAdapter):
    """Per-event broadcast to a configured Discord webhook (Decision 9).

    BUILT IN PHASE 2 from the ``DISCORD_NOTIFY_WEBHOOK_URL`` env var (already
    present in ``.env``): posts an embed to the configured webhook; routing is
    event → webhook, configured in the admin area, not per-user. For Phase 1 this
    is a registered shell only.
    """

    channel = Channel.DISCORD


# --- Registry ----------------------------------------------------------------

_ADAPTERS: dict[Channel, ChannelAdapter] = {
    Channel.IN_APP: InAppAdapter(),
    Channel.EMAIL: EmailAdapter(),
    Channel.PUSH: PushAdapter(),
    Channel.SCHEDULED_EMAIL: ScheduledEmailAdapter(),
    Channel.DIGEST: DigestAdapter(),
    Channel.DISCORD: DiscordAdapter(),
}


def get_adapter(channel: Channel) -> ChannelAdapter:
    """Return the adapter for ``channel``. Raises ``KeyError`` if unregistered."""
    return _ADAPTERS[channel]


def is_implemented(channel: Channel) -> bool:
    """Whether ``channel``'s adapter is a live implementation (vs a Phase-2 shell)."""
    return not isinstance(_ADAPTERS[channel], _ShellAdapter)
