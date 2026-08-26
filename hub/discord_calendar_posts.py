"""#public-calendar Discord posts: the weekly digest + the 15-minute new-event announcer.

Two skinny management commands (``post_weekly_calendar_digest`` / ``announce_calendar_events``)
delegate here. Both posts share one config gate — ``SiteConfiguration.discord_calendar_posts_enabled``
AND a non-blank ``discord_calendar_channel_id`` — and both no-op (return 0) when it's closed.

The digest is a pure builder (:func:`build_weekly_digest_embeds`) over the same deduped
grid union the Community Calendar shows (:func:`hub.calendar_entries.upcoming_calendar_events`:
feed + class + community events, echo-suppressed), limited to the next 7 days, grouped by
day, chunked under Discord's 4096-char embed-description limit.

The announcer posts one compact embed per *new* upcoming item — a feed/class
``CalendarEvent`` row from the nightly sync or a newly published ``CommunityEvent`` — and
stamps ``channel_announced_at`` so nothing is ever announced twice. Echoes of our own
Google-pushed events are excluded (same UID set the grid uses), and a run posts at most
:data:`ANNOUNCE_CAP` items; any overflow is stamped silently and logged.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from core.integrations.discord_channel import MAX_EMBEDS_PER_MESSAGE, post_channel_message

logger = logging.getLogger(__name__)

# Discord's hard cap on one embed's description.
EMBED_DESCRIPTION_MAX = 4096
# Discord's hard cap on one MESSAGE's combined embed text (titles + descriptions + …
# across every embed in the message) — separate from the per-description cap above.
MESSAGE_EMBED_TOTAL_MAX = 6000
# Max new-event announcements posted per run; the rest are stamped silently.
ANNOUNCE_CAP = 10
# New-event announcements are held until the event starts within this window. The nightly
# sync materializes one CalendarEvent row per occurrence out to a 180-day horizon, so a
# recurring series' leading edge keeps surfacing months out — a weekly meeting shows up as
# a February row in August. A row past this horizon is left UNstamped (not announced), so it
# announces once it rolls inside the window instead of being pre-announced a quarter early.
ANNOUNCE_HORIZON = timedelta(days=90)
DIGEST_WINDOW_DAYS = 7
# Persisted alongside channel_announced_at when a community event's RSVP announcement posts,
# so the hub refresh and the cancel button-strip can find the message it created.
_ANNOUNCE_ID_FIELDS = ["discord_announce_channel_id", "discord_announce_message_id"]
# The community-calendar blue (matches the calendar legend's community color).
_EMBED_COLOR = 0x3D8BD4
_DIGEST_TITLE = "This week at Past Lives"
_FOOTER_LABEL = "See the full calendar"


def _calendar_url() -> str:
    """Absolute URL of the Community Calendar page (the digest footer link)."""
    from django.urls import reverse

    return _absolute(reverse("hub_community_calendar"))


def _absolute(url: str) -> str:
    """Absolutize a path onto the member site — Discord renders URLs as-is, so a bare
    ``/classes/…`` path would be a dead link. Already-absolute URLs pass through."""
    if url.startswith("/"):
        return f"{settings.MEMBER_BASE_URL}{url}"
    return url


def _time_of(dt: datetime) -> str:
    return timezone.localtime(dt).strftime("%-I:%M %p")


def _when(start: datetime, end: datetime, all_day: bool) -> str:
    """Human date/time for one event, in local (Portland) time."""
    local_start = timezone.localtime(start)
    day = local_start.strftime("%A, %B %-d")
    if all_day:
        return f"{day} · All day"
    local_end = timezone.localtime(end)
    if local_end.date() == local_start.date():
        return f"{day} · {_time_of(start)} – {_time_of(end)}"
    return f"{day} {_time_of(start)} – {local_end.strftime('%A, %B %-d')} {_time_of(end)}"


# Studio hours are ambient noise, never events. A typed CommunityEvent carries the
# STUDIO_HOURS event type, but a guild's Google-calendar "open studio hours" block comes
# through as a plain feed event with no type — so match the title too.
_STUDIO_HOURS_TITLE = "studio hours"
_STUDIO_HOURS_TITLE_RE = re.compile(re.escape(_STUDIO_HOURS_TITLE), re.IGNORECASE)


def _is_studio_hours(item: Any) -> bool:
    """Whether a calendar entry is a standing studio-hours block — ambient noise the
    digest skips, exactly as the announcer (and the Discord Events sync) skip them.

    Catches the typed CommunityEvent (event_type == STUDIO_HOURS) and any feed event whose
    title reads as studio hours (feed rows carry no event type to key off).
    """
    from membership.models import CommunityEvent

    if _STUDIO_HOURS_TITLE_RE.search(getattr(item, "title", "") or ""):
        return True
    backing = getattr(item, "community_event", None)  # CalendarEvent rows have no such attr
    return backing is not None and backing.event_type == CommunityEvent.EventType.STUDIO_HOURS


def _digest_items(now: datetime) -> list[Any]:
    """The next-7-days slice of the events calendar (feed + general + community events),
    minus classes and standing studio-hours blocks.

    #public-calendar is events-only: classes have their own #classes digest, so
    class-sourced rows are dropped here even though they share the calendar union.
    """
    from hub.calendar_entries import upcoming_calendar_events
    from membership.models import CalendarEvent

    horizon = now + timedelta(days=DIGEST_WINDOW_DAYS)
    return [
        e
        for e in upcoming_calendar_events()
        if e.start_dt < horizon
        and e.end_dt >= now
        and e.source != CalendarEvent.Source.CLASSES
        and not _is_studio_hours(e)
    ]


def _digest_line(item: Any) -> str:
    """One digest bullet: time + linked title (linked only when the item has a page)."""
    time_part = "All day" if item.all_day else _time_of(item.start_dt)
    url = _absolute(item.url) if item.url else ""
    title_part = f"[{item.title}]({url})" if url else item.title
    return f"• {time_part} — {title_part}"


def _day_blocks(items: list[Any]) -> list[str]:
    """Group items by local day into ``**Day header**\\n• line…`` markdown blocks."""
    grouped: dict[Any, list[Any]] = {}
    for item in sorted(items, key=lambda e: e.start_dt):
        grouped.setdefault(timezone.localtime(item.start_dt).date(), []).append(item)
    blocks: list[str] = []
    for day, day_items in grouped.items():
        header = f"**{day.strftime('%A, %B %-d')}**"
        lines = [_digest_line(item) for item in day_items]
        blocks.append("\n".join([header, *lines]))
    return blocks


def _chunk_blocks(blocks: list[str]) -> list[str]:
    """Pack markdown blocks into descriptions that each fit Discord's 4096-char cap.

    Blocks are kept whole where possible (joined with a blank line); a single oversize
    block is split on its own lines so no description can ever exceed the cap.
    """
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= EMBED_DESCRIPTION_MAX:
            pieces.append(block)
        else:
            pieces.extend(_split_lines(block))
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) > EMBED_DESCRIPTION_MAX:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _split_lines(block: str) -> list[str]:
    """Split one oversize day block into cap-sized line runs (fallback path)."""
    runs: list[str] = []
    current = ""
    for line in block.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > EMBED_DESCRIPTION_MAX:
            runs.append(current)
            current = line
        else:
            current = candidate
    if current:
        runs.append(current)
    return runs


def build_weekly_digest_embeds(now: datetime) -> list[dict[str, Any]]:
    """Build the Monday-morning digest embeds for the 7 days starting at ``now``.

    Pure over :func:`hub.calendar_entries.upcoming_calendar_events` — no HTTP, so it's
    testable in isolation. Returns ``[]`` when the week is empty (the caller then posts
    nothing). Every embed description fits Discord's 4096-char cap; the last embed
    carries the "See the full calendar" footer link.
    """
    return _embeds_for_items(_digest_items(now), now)


def _embeds_for_items(items: list[Any], now: datetime) -> list[dict[str, Any]]:
    """The digest embeds for an already-computed item list (shared by build + post)."""
    if not items:
        return []
    blocks = _day_blocks(items)
    blocks.append(f"[{_FOOTER_LABEL} →]({_calendar_url()})")
    chunks = _chunk_blocks(blocks)
    local_start = timezone.localtime(now)
    local_end = timezone.localtime(now + timedelta(days=DIGEST_WINDOW_DAYS - 1))
    title = f"{_DIGEST_TITLE} · {local_start.strftime('%b %-d')} – {local_end.strftime('%b %-d')}"
    embeds: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        embeds.append(
            {
                "title": title if i == 0 else f"{_DIGEST_TITLE} (continued)",
                "description": chunk,
                "color": _EMBED_COLOR,
                "url": _calendar_url(),
            }
        )
    return embeds


def _embed_chars(embed: dict[str, Any]) -> int:
    """The characters this embed counts toward Discord's per-message 6,000 total
    (title + description; our digest embeds carry no footer/author/field text)."""
    return len(embed.get("title", "")) + len(embed.get("description", ""))


def _batch_embeds(embeds: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split embeds into per-message batches that respect BOTH message caps: at most
    :data:`MAX_EMBEDS_PER_MESSAGE` embeds AND :data:`MESSAGE_EMBED_TOTAL_MAX` combined
    characters. Two near-full 4,096-char embeds in one message would 400 on the 6,000
    combined cap even though each description is individually legal.
    """
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for embed in embeds:
        chars = _embed_chars(embed)
        if current and (len(current) >= MAX_EMBEDS_PER_MESSAGE or current_chars + chars > MESSAGE_EMBED_TOTAL_MAX):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(embed)
        current_chars += chars
    if current:
        batches.append(current)
    return batches


def _posting_channel_id() -> str:
    """The configured channel id, or ``""`` when posting is disabled/unconfigured."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    if not config.discord_calendar_posts_enabled:
        return ""
    return (config.discord_calendar_channel_id or "").strip()


def post_weekly_digest() -> int:
    """Post the weekly digest to #public-calendar; return the number of items listed.

    No-ops (returns 0) when posting is disabled, no channel id is set, or the coming
    week is empty — an empty digest is noise, not news.
    """
    channel_id = _posting_channel_id()
    if not channel_id:
        return 0
    now = timezone.now()
    items = _digest_items(now)
    if not items:
        return 0
    for batch in _batch_embeds(_embeds_for_items(items, now)):
        post_channel_message(channel_id, batch)
    return len(items)


def _announcement_embed(title: str, kind_label: str, when: str, url: str) -> dict[str, Any]:
    """One compact new-item embed: labelled kind, date/time, and a link when there is one."""
    lines = [f"*{kind_label}*", f"**When:** {when}"]
    if url:
        lines.append(f"[More info →]({url})")
    embed: dict[str, Any] = {"title": title, "description": "\n".join(lines), "color": _EMBED_COLOR}
    if url:
        embed["url"] = url
    return embed


def _stamp(objs: list[Any], now: datetime, *, extra_fields: list[str] | None = None) -> None:
    """Mark every row of one announced (or capped) item as handled.

    ``extra_fields`` are persisted in the same save as ``channel_announced_at`` — used for a
    community event whose stored announcement channel/message ids are set right before the
    stamp, so the RSVP embed, the hub refresh, and the cancel button-strip all find the message.
    """
    fields = ["channel_announced_at", *(extra_fields or [])]
    for obj in objs:
        obj.channel_announced_at = now
        obj.save(update_fields=fields)


def announce_new_events() -> int:
    """Announce unannounced upcoming items in #public-calendar; return how many posted.

    Pulls new feed/class ``CalendarEvent`` rows (future start, echoes of our own
    Google-pushed events excluded) and newly published non-studio-hours ``CommunityEvent``
    rows, oldest-starting first. A recurring feed series (and a multi-session class)
    materializes one row *per occurrence* sharing a uid — those collapse to a single
    announcement at the earliest upcoming occurrence, with every sibling row stamped in
    the same pass. A group whose earliest occurrence starts beyond :data:`ANNOUNCE_HORIZON`
    is held: it is left unstamped and re-checked each run, so it announces only once it
    rolls inside the window (this is what stops a recurring series' 180-day leading edge
    from being announced a quarter early). Posts up to :data:`ANNOUNCE_CAP`, stamping
    ``channel_announced_at`` right after each post; anything past the cap is stamped
    silently (and logged) so a backlog can never flood the channel later.
    """
    from membership.models import CalendarEvent, CommunityEvent

    from hub.calendar_service import _pushed_event_uids

    channel_id = _posting_channel_id()
    if not channel_id:
        return 0
    now = timezone.now()
    horizon = now + ANNOUNCE_HORIZON

    # (start of the announced occurrence, the embed, the button row or None, every row to
    # stamp, the CommunityEvent to store message ids on or None for a feed row)
    pending: list[tuple[datetime, dict[str, Any], list[dict[str, Any]] | None, list[Any], Any]] = []
    feed_rows = (
        CalendarEvent.objects.filter(channel_announced_at__isnull=True, start_dt__gt=now)
        # #public-calendar is events-only — classes announce to #classes, not here.
        .exclude(source=CalendarEvent.Source.CLASSES)
        # Feed-based "open studio hours" blocks carry no event type; drop them by title.
        .exclude(title__icontains=_STUDIO_HOURS_TITLE)
        .exclude(uid__in=_pushed_event_uids())
        .order_by("start_dt")
    )
    # Collapse per-occurrence rows of one series/class into a single announcement:
    # the query is start-ordered, so each group's first row is its earliest occurrence.
    series: dict[tuple[str, int | None, str], list[CalendarEvent]] = {}
    for row in feed_rows:
        series.setdefault((row.source, row.guild_id, row.uid), []).append(row)
    for rows in series.values():
        first = rows[0]
        # Hold far-future occurrences: the query is start-ordered, so `first` is the group's
        # earliest unannounced occurrence. Leave the whole group UNstamped when it's past the
        # horizon so it announces once its next occurrence rolls inside the window, not now.
        if first.start_dt > horizon:
            continue
        url = _absolute(first.url) if first.url else ""
        embed = _announcement_embed(
            first.title, "New on the calendar", _when(first.start_dt, first.end_dt, first.all_day), url
        )
        # Feed/class rows have no CommunityEvent to RSVP against: compact embed, no buttons.
        pending.append((first.start_dt, embed, None, list(rows), None))

    community_rows = (
        CommunityEvent.objects.published()
        .upcoming()
        .filter(channel_announced_at__isnull=True)
        .exclude(event_type=CommunityEvent.EventType.STUDIO_HOURS)
    )
    for event in community_rows:
        # One truth: the rich embed + RSVP/Manage buttons the model builds, also rendered by
        # the RSVP click's type-7 rebuild and the hub refresh.
        pending.append(
            (
                event.next_occurrence_start(),
                event.discord_announcement_embed(),
                event.discord_announcement_components(),
                [event],
                event,
            )
        )

    pending.sort(key=lambda item: item[0])
    posted = 0
    for _start, embed, components, objs, event in pending[:ANNOUNCE_CAP]:
        message = post_channel_message(channel_id, [embed], components=components)
        if event is not None:
            event.discord_announce_channel_id = channel_id
            event.discord_announce_message_id = str(message["id"])
            _stamp(objs, now, extra_fields=_ANNOUNCE_ID_FIELDS)
        else:
            _stamp(objs, now)
        posted += 1

    overflow = pending[ANNOUNCE_CAP:]
    for _start, _embed, _components, objs, _event in overflow:
        _stamp(objs, now)
    if overflow:
        logger.info(
            "announce_new_events: capped at %s posts; silently marked %s more event(s) announced.",
            ANNOUNCE_CAP,
            len(overflow),
        )
    return posted
