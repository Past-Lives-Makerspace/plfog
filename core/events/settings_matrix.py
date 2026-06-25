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
from core.events.registry import Channel, EventType, all_events
from core.models import NotificationPreference

if TYPE_CHECKING:
    from django.contrib.auth.models import User

# The per-recipient channels a user can hold a preference on, in display order.
# DISCORD is a per-event BROADCAST channel (event→webhook, not per-user) and is
# deliberately excluded — a member cannot opt into/out of a site-wide broadcast.
USER_CHANNELS: tuple[Channel, ...] = (
    Channel.IN_APP,
    Channel.EMAIL,
    Channel.PUSH,
    Channel.SCHEDULED_EMAIL,
    Channel.DIGEST,
)

# Human labels for the matrix column headers.
CHANNEL_LABELS: dict[Channel, str] = {
    Channel.IN_APP: "Bell",
    Channel.EMAIL: "Email",
    Channel.PUSH: "Push",
    Channel.SCHEDULED_EMAIL: "Scheduled",
    Channel.DIGEST: "Digest",
}

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
    this channel at all (an absent channel renders as an empty cell).
    """

    name: str
    channel: Channel
    enabled: bool
    forced: bool
    present: bool


@dataclass(frozen=True)
class Row:
    """One event row in the matrix: its label/description + a cell per user channel."""

    event_key: str
    label: str
    description: str
    cells: list[Cell]


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


def build_matrix(user: User) -> list[tuple[str, list[Row]]]:
    """Assemble the matrix for ``user`` — a list of ``(category, [Row, ...])``.

    For each visible event, one :class:`Row` with a :class:`Cell` per user channel:
    forced cells locked-on, opt-out-able cells reflecting the saved preference (or the
    event's channel default). Categories are returned in :data:`CATEGORY_ORDER`.
    """
    events = _visible_events(user)
    by_category: dict[str, list[Row]] = {}
    for event in events:
        cells: list[Cell] = []
        for channel in USER_CHANNELS:
            spec = event.channel(channel)
            if spec is None:
                cells.append(Cell(name="", channel=channel, enabled=False, forced=False, present=False))
                continue
            forced = spec.is_forced
            # IN_APP is always-on; show it locked-on like a forced channel.
            locked = forced or channel is Channel.IN_APP
            enabled = preferences.wants(user, event.key, channel)
            cells.append(
                Cell(
                    name=field_name(event.key, channel),
                    channel=channel,
                    enabled=enabled,
                    forced=locked,
                    present=True,
                )
            )
        row = Row(event_key=event.key, label=event.label, description=event.description, cells=cells)
        by_category.setdefault(event.category, []).append(row)
    return [(category, by_category[category]) for category in _ordered_categories(set(by_category))]


def save_matrix(user: User, posted: dict[str, str]) -> None:
    """Persist the matrix POST for ``user``.

    For each visible (event, opt-out-able channel) cell, upsert a
    :class:`core.models.NotificationPreference` row with ``enabled`` reflecting the
    checkbox. FORCED and IN_APP cells are skipped — they are always-on and have no
    user-controlled state to store, so we never write a row that pretends otherwise.
    A checked box is ``enabled=True``; an absent box (HTML omits unchecked checkboxes)
    is ``enabled=False``.
    """
    for event in _visible_events(user):
        for channel in USER_CHANNELS:
            spec = event.channel(channel)
            if spec is None or spec.is_forced or channel is Channel.IN_APP:
                continue
            NotificationPreference.objects.update_or_create(
                user=user,
                event_key=event.key,
                channel=channel.value,
                defaults={"enabled": posted.get(field_name(event.key, channel)) == "on"},
            )
