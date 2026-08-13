"""The event-category × channel preference matrix for the settings page (§2.7).

The unified preferences page is an **event × channel** grid sourced from the event
registry: one row per event the user may control, one cell per *per-recipient*
channel that event declares. A ``FORCED`` cell renders locked-on (the user can't opt
out — essentials + operational mail, Decision 1); an opt-out-able cell reflects the
user's saved :class:`core.models.NotificationPreference` row (or the event's channel
default when none exists).

Broadcast channels (Discord) are **not** per-user — they post once per event to a
configured webhook (admin-configured, §2.4) — so they never appear as a user
preference column. Channels the user has no agency over (IN_APP is always on) still
appear, rendered locked-on, so the page honestly shows every way an event reaches
them.

This is the model/service layer for the page (CLAUDE.md: logic out of views). The
view calls :func:`build_matrix` for the GET context and :func:`save_matrix` for the
POST.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.events import preferences
from core.events.registry import Channel, EventType, Recipients, all_events
from core.models import NotificationPreference

if TYPE_CHECKING:
    from django.contrib.auth.models import User

# The per-recipient channels a user can hold a preference on, in display order.
# DISCORD is a per-event BROADCAST channel (event→webhook, not per-user) and is
# deliberately excluded — a member cannot opt into/out of a site-wide broadcast.
# DISCORD_DM, by contrast, IS per-user (the bot DMs the individual member), so it is
# included; its column appears once at least one event declares it (visible_channels).
USER_CHANNELS: tuple[Channel, ...] = (
    Channel.IN_APP,
    Channel.EMAIL,
    Channel.PUSH,
    Channel.DISCORD_DM,
    Channel.SCHEDULED_EMAIL,
    Channel.DIGEST,
)

# Human labels for the matrix column headers.
CHANNEL_LABELS: dict[Channel, str] = {
    Channel.IN_APP: "In-app (Bell)",
    Channel.EMAIL: "Email",
    Channel.PUSH: "Push (Browser)",
    Channel.DISCORD_DM: "Discord",
    Channel.SCHEDULED_EMAIL: "Scheduled",
    Channel.DIGEST: "Digest",
}

# Shown on a disabled DISCORD_DM toggle when the member hasn't linked Discord yet.
_DISCORD_LINK_HINT = "Connect your Discord account first to receive DMs."

# Stable category display order; any category not listed falls to the end, alpha.
CATEGORY_ORDER: tuple[str, ...] = (
    "Classes",
    "Teaching",
    "Voting",
    "Guilds",
    "Billing",
    "Membership",
    "Spaces",
    "Announcements",
    "Security",
)


@dataclass(frozen=True)
class Cell:
    """One (event, channel) checkbox in the matrix.

    ``name`` is the form field name (``pref__<event_key>__<channel>``) the POST reads.
    ``enabled`` is the current checked state; ``forced`` renders it locked-on
    (always-on, the user can't change it); ``present`` is True when the event declares
    this channel at all (an absent channel renders as an empty cell). ``available`` is
    False when the user can't use this channel yet (the Discord DM channel before they
    link Discord) — the box renders disabled with ``hint`` as its tooltip, distinct
    from the locked-on ``forced`` state.
    """

    name: str
    channel: Channel
    enabled: bool
    forced: bool
    present: bool
    available: bool = True
    hint: str = ""
    badge: str = ""


@dataclass(frozen=True)
class Row:
    """One event row in the matrix: its label/description + a cell per user channel.

    ``badge`` is a short note explaining a non-obvious reason the user receives this
    event — set when they're a default recipient via an admin capability (e.g. "You get
    this as a Class Administrator") rather than by a plain opt-in.
    """

    event_key: str
    label: str
    description: str
    cells: list[Cell]
    badge: str = ""


def field_name(event_key: str, channel: Channel) -> str:
    """The POST field name for an (event, channel) checkbox."""
    return f"pref__{event_key}__{channel.value}"


def _visible_events(user: User) -> list[EventType]:
    """Events whose preference row should be shown to ``user``.

    Every registered event that declares at least one *user* channel is shown — the
    page is the full catalogue of how the app can reach you. (Audience scoping is the
    resolver's job at send time; the page lists every event so a user always sees,
    e.g., the teaching events even before they teach. Forced rows make the operational
    ones visible-but-locked rather than hidden.)
    """
    out: list[EventType] = []
    for event in all_events():
        if any(event.has_channel(channel) for channel in USER_CHANNELS):
            out.append(event)
    return out


def _ordered_categories(categories: set[str]) -> list[str]:
    ranked = [c for c in CATEGORY_ORDER if c in categories]
    rest = sorted(c for c in categories if c not in CATEGORY_ORDER)
    return ranked + rest


def _member_discord_linked(user: User) -> bool:
    """Whether ``user``'s member has a verified Discord account linked for DMs.

    ``Member`` is imported lazily to avoid a model-layer import at module load. A user
    with no linked member is treated as not linked (the column renders disabled).
    """
    from membership.models import Member

    member = Member.objects.filter(user=user).only("discord_user_id").first()
    return bool(member and member.discord_user_id)


def channel_availability(user: User, channel: Channel, *, discord_linked: bool) -> tuple[bool, str]:
    """Whether ``user`` can use ``channel`` right now, and a hint to show when not.

    Every channel is available except the per-member Discord DM channel, which needs the
    member to have linked their Discord account first; until then its cells render
    disabled with a hint. ``discord_linked`` is passed in (computed once per page) so the
    matrix doesn't re-query the member for every cell.
    """
    if channel is Channel.DISCORD_DM and not discord_linked:
        return False, _DISCORD_LINK_HINT
    return True, ""


def visible_channels(user: User) -> list[Channel]:
    """The user channels at least one visible event actually offers, in display order.

    A channel no event declares (today: the unbuilt Scheduled-email and Digest shells)
    would render as an entire column of empty '—' cells — dead, confusing UI. We drop
    such columns until something uses them; the column reappears automatically the day
    an event starts declaring that channel.
    """
    events = _visible_events(user)
    return [channel for channel in USER_CHANNELS if any(event.channel(channel) is not None for event in events)]


# Each capability-scoped recipient maps to the capability whose holders receive the
# event by default; a member holding it gets a "You get this as a …" badge on the row.
_CAPABILITY_BY_RECIPIENT: dict[Recipients, str] = {
    Recipients.CLASS_APPROVERS: "class_approver",
    Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS: "class_approver",
    Recipients.SPACE_APPROVERS: "space_approver",
    Recipients.DISCOUNT_APPROVERS: "discount_approver",
    Recipients.EVENTS_APPROVERS: "events_approver",
    Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS: "events_approver",
    Recipients.BILLING_APPROVERS: "billing_approver",
}


def _capability_badges(user: User, events: list[EventType]) -> dict[str, str]:
    """Map each visible event the user receives via a held capability to its row badge.

    One bounded query for the member's capabilities (not a resolver call per row): a row
    is badged only when the event routes to a capability the member actually holds.
    """
    from membership.models import AdminCapability, Member

    member = Member.objects.filter(user=user).only("id").first()
    if member is None:
        return {}
    held = set(member.admin_capabilities.values_list("capability", flat=True))
    if not held:
        return {}
    labels = {choice.value: choice.label for choice in AdminCapability.Capability}
    badges: dict[str, str] = {}
    for event in events:
        capability = _CAPABILITY_BY_RECIPIENT.get(event.recipient)
        if capability is not None and capability in held:
            badges[event.key] = f"You get this as a {labels[capability]}"
    return badges


def build_matrix(user: User) -> list[tuple[str, list[Row]]]:
    """Assemble the matrix for ``user`` — a list of ``(category, [Row, ...])``.

    For each visible event, one :class:`Row` with a :class:`Cell` per user channel:
    forced cells locked-on, opt-out-able cells reflecting the saved preference (or the
    event's channel default). A row the user receives via an admin capability carries a
    badge. Categories are returned in :data:`CATEGORY_ORDER`.
    """
    events = _visible_events(user)
    channels = visible_channels(user)
    # Compute per-channel availability once (the Discord-linked lookup is a single
    # query) rather than re-deriving it for every cell.
    discord_linked = _member_discord_linked(user)
    availability = {channel: channel_availability(user, channel, discord_linked=discord_linked) for channel in channels}
    capability_badges = _capability_badges(user, events)
    by_category: dict[str, list[Row]] = {}
    for event in events:
        cells: list[Cell] = []
        for channel in channels:
            spec = event.channel(channel)
            if spec is None:
                cells.append(Cell(name="", channel=channel, enabled=False, forced=False, present=False))
                continue
            forced = spec.is_forced
            # IN_APP is always-on; show it locked-on like a forced channel.
            locked = forced or channel is Channel.IN_APP
            enabled = preferences.wants(user, event.key, channel)
            available, hint = availability[channel]
            cells.append(
                Cell(
                    name=field_name(event.key, channel),
                    channel=channel,
                    enabled=enabled,
                    forced=locked,
                    present=True,
                    available=available,
                    hint=hint,
                )
            )
        row = Row(
            event_key=event.key,
            label=event.label,
            description=event.description,
            cells=cells,
            badge=capability_badges.get(event.key, ""),
        )
        by_category.setdefault(event.category, []).append(row)
    return [(category, by_category[category]) for category in _ordered_categories(set(by_category))]


def save_matrix(user: User, posted: dict[str, str]) -> None:
    """Persist the matrix POST for ``user``.

    For each visible (event, opt-out-able channel) cell, upsert a
    :class:`core.models.NotificationPreference` row with ``enabled`` reflecting the
    checkbox. FORCED and IN_APP cells are skipped — they are always-on and have no
    user-controlled state to store, so we never write a row that pretends otherwise.
    A checked box is ``enabled=True``; an absent box (HTML omits unchecked checkboxes)
    is ``enabled=False``. A channel the user can't use yet (the Discord DM channel
    before they link Discord) is skipped — its box renders disabled, so the browser
    omits it, and writing ``enabled=False`` would silently wipe a preference they set
    while linked. Skipping preserves their choice across an unlink/relink.
    """
    discord_linked = _member_discord_linked(user)
    availability = {
        channel: channel_availability(user, channel, discord_linked=discord_linked) for channel in USER_CHANNELS
    }
    for event in _visible_events(user):
        for channel in USER_CHANNELS:
            spec = event.channel(channel)
            if spec is None or spec.is_forced or channel is Channel.IN_APP:
                continue
            if not availability[channel][0]:
                continue
            NotificationPreference.objects.update_or_create(
                user=user,
                event_key=event.key,
                channel=channel.value,
                defaults={"enabled": posted.get(field_name(event.key, channel)) == "on"},
            )
