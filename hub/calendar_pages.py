"""The Orientations and Reservations calendars (#502 part 3).

Both pages gain a Calendar view that reuses the guild page's shell
(``hub/partials/guild_calendar_app.html`` around ``calendar_content.html``) with a legend of
their own instead of the guild legend. The window, the grid and the pagination come from
``hub.calendar_window``, the one place the calendars' date arithmetic lives;
the rows come from ``hub.calendar_entries``. The views only parse the navigation params and
render.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any, TypedDict, cast

from django.urls import reverse

from hub.calendar_entries import (
    MAKERSPACE_LEGEND_KEY,
    CalendarEntry,
    orientation_legend_key,
    orientation_page_entries,
    reservation_entries,
)
from hub.calendar_window import calendar_window, calendar_window_context

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Guild

#: Makerspace's chip on the Orientations calendar: a theme token, so it reads as "no guild".
MAKERSPACE_LEGEND_COLOR = "var(--hub-text-muted)"

#: The Reservations calendar's item colors, given out in name order and repeated past eight.
ITEM_LEGEND_COLORS = (
    "#e05d5d",
    "#c9823a",
    "#6f93d8",
    "#3aa883",
    "#8d6ad6",
    "#EEB44B",
    "#3d8bd4",
    "#a86b2d",
)


class LegendChip(TypedDict):
    """One filter button in the shell's legend row."""

    key: str
    label: str
    color: str
    logo_prefix: str | None


def calendar_nav_params(request: HttpRequest) -> tuple[int, int, int]:
    """``(week_offset, month_offset, page)`` from the query string, clamped as the guild calendar's are.

    Garbage in any of them resets all three, as ``guild_calendar_events_partial`` does.
    """
    try:
        week_offset = max(-52, min(52, int(request.GET.get("week_offset", 0))))
        month_offset = max(-24, min(24, int(request.GET.get("month_offset", 0))))
        event_page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        return 0, 0, 1
    return week_offset, month_offset, event_page


def entries_calendar_context(
    entries_for: Callable[[date, date], list[CalendarEntry]],
    *,
    week_offset: int,
    month_offset: int,
    event_page: int,
    legend: list[LegendChip],
    events_url: str,
) -> dict[str, Any]:
    """The shell's ``cal`` for a calendar fed only by ``entries_for(fetch_from, fetch_to)``.

    ``legend`` is the filter row, and its colors are the only source colors: every entry
    keys a legend chip (``CalendarEntry.legend_key``). ``entries_calendar`` tells the
    content partial to drop the guild calendar's notes about subscribed feeds.
    """
    window = calendar_window(week_offset, month_offset)
    entries = sorted(entries_for(window.fetch_from, window.fetch_to), key=lambda entry: entry.start_dt)
    return {
        **calendar_window_context(
            entries, window, week_offset=week_offset, month_offset=month_offset, event_page=event_page
        ),
        "legend": legend,
        "source_colors": {chip["key"]: chip["color"] for chip in legend},
        "events_url": events_url,
        "entries_calendar": True,
        "sync_flag_visible": False,
    }


def orientations_calendar_context(
    *, week_offset: int = 0, month_offset: int = 0, event_page: int = 1
) -> dict[str, Any]:
    """The Orientations calendar: every bookable time with a seat left, across every listed type.

    "Listed" is the Orientations page's own rule (``OrientationTypeQuerySet.listed_condition``),
    without the retired types a member's booking pins to their List view. The legend has one
    chip per guild that owns a listed type, its own or through its equipment, in name order,
    then Makerspace when an item no guild owns lists one. Three queries however many types:
    the site configuration (the demo guild switch), the types, and their slots.
    """
    from membership.models import OrientationType

    types = list(
        OrientationType.objects.filter(OrientationType.objects.listed_condition()).select_related(
            "guild", "equipment", "equipment__guild"
        )
    )
    guilds: dict[str, Guild] = {}
    has_makerspace = False
    for orientation_type in types:
        key = orientation_legend_key(orientation_type)
        if key == MAKERSPACE_LEGEND_KEY:
            has_makerspace = True
            continue
        # A key other than Makerspace always names a guild: the type's own or its equipment's.
        owner = orientation_type.guild if orientation_type.equipment is None else orientation_type.equipment.guild
        guilds[key] = cast("Guild", owner)
    legend: list[LegendChip] = [
        {"key": key, "label": guild.name, "color": guild.calendar_color, "logo_prefix": guild.logo_prefix}
        for key, guild in sorted(guilds.items(), key=lambda item: item[1].name.lower())
    ]
    if has_makerspace:
        legend.append(
            {"key": MAKERSPACE_LEGEND_KEY, "label": "Makerspace", "color": MAKERSPACE_LEGEND_COLOR, "logo_prefix": None}
        )
    return entries_calendar_context(
        lambda fetch_from, fetch_to: orientation_page_entries(types, fetch_from, fetch_to),
        week_offset=week_offset,
        month_offset=month_offset,
        event_page=event_page,
        legend=legend,
        events_url=reverse("hub_orientations_calendar_events"),
    )


def reservations_calendar_context(
    *, week_offset: int = 0, month_offset: int = 0, event_page: int = 1
) -> dict[str, Any]:
    """The Reservations calendar: what is taken, across every active item.

    One legend chip per active item (the Reservations page's base set, unfiltered by its
    chips; the legend filters on the page), colored from :data:`ITEM_LEGEND_COLORS` by the
    item's place in name order, so a given set of items keeps its colors. Three queries
    however many items: the items, their reservations, and their booked orientations.
    """
    from membership.models import Equipment

    items = list(Equipment.objects.active().select_related("guild").order_by("name", "pk"))
    legend: list[LegendChip] = [
        {
            "key": str(item.pk),
            "label": item.name,
            "color": ITEM_LEGEND_COLORS[index % len(ITEM_LEGEND_COLORS)],
            "logo_prefix": None,
        }
        for index, item in enumerate(items)
    ]
    return entries_calendar_context(
        lambda fetch_from, fetch_to: reservation_entries(items, fetch_from, fetch_to),
        week_offset=week_offset,
        month_offset=month_offset,
        event_page=event_page,
        legend=legend,
        events_url=reverse("hub_equipment_calendar_events"),
    )
