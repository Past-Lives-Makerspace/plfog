"""Deck builder for the signage slideshow.

A small service, because it orchestrates three models (``SlideshowSlide`` +
``CommunityEvent`` + ``SiteConfiguration``) into an ordered list of render-ready
view-models — cross-model orchestration belongs in a service, not a view or
template.

Event slides are the privacy-safe part: site-wide events ONLY
(``guild__isnull=True``), expanded via the same occurrence logic the home feed
uses. This deliberately never touches ``_get_calendar_context`` (a confirmed
private-guild leak) — it queries ``CommunityEvent`` directly. The month-calendar
grid holds to the same rule: it counts site-wide events and public class sessions
only, and renders a dot rather than a title.

Every generated block is computed **per request** from live models — there is no
cron, no persisted deck and no "regenerate" command. The player re-polls every
300s and hard-reloads at 04:00, so a screen pointed at its zone URL once stays
correct on its own.

All bucketing, formatting and boundary decisions use local time. Stored datetimes
are UTC-aware, so a naive ``.day``/``.date()`` drops a 6pm-Portland event (01:00Z
the next day) on the wrong calendar cell.
"""

from __future__ import annotations

import calendar
import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime as datetime_type
from datetime import time as time_type
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

if TYPE_CHECKING:
    from core.models import SiteConfiguration
    from membership.models import CommunityEvent, SlideshowSlide, SlideshowZone

SIGNAGE_EVENT_CAP = 8
# "This week" is a fixed seven days: the ask is the week, and the events block already
# owns the configurable look-ahead. No admin field (YAGNI).
SIGNAGE_CLASS_DAYS = 7
SIGNAGE_CLASS_CAP = 6


@dataclass(frozen=True)
class SignageCalendarDay:
    """One cell of the signage month grid. ``day`` is 0 for padding cells outside the month."""

    day: int
    event_count: int
    is_today: bool


@dataclass(frozen=True)
class SignageSlideVM:
    """A single render-ready slide for the player template."""

    kind: str  # "custom" | "announcement" | "event" | "holding" | one of the generated block kinds
    title: str
    body: str
    image_url: str | None
    qr_svg: str | None  # inline SVG when a QR should render
    duration_seconds: int
    meta: str = ""  # e.g. an event's when_display / location
    url_display: str = ""  # a human-friendly "learn more" URL shown under the slide (paired with the QR)
    calendar_days: tuple[SignageCalendarDay, ...] = ()  # non-empty only on the month-calendar slide

    @property
    def calendar_weeks(self) -> int:
        """How many week rows the month grid renders; 0 when this is not a calendar slide.

        The template needs this to size the grid: cells are square, so six-week months are
        a whole row taller and are the only ones that have to be bounded on viewport
        height (see ``.pl-sign-calendar--six-weeks``).
        """
        return len(self.calendar_days) // 7


def build_deck(zone: SlideshowZone) -> list[SignageSlideVM]:
    """Ordered slides for one zone: the admin's configured slides (by ``sort_order``) then
    each self-building block that is switched on, in a fixed order. A branded holding slide
    guarantees the screen is never blank. The emergency alert is handled in the view, not here."""
    from core.models import SiteConfiguration
    from membership.models import SlideshowSlide

    config = SiteConfiguration.load()
    default = config.signage_default_slide_seconds

    deck: list[SignageSlideVM] = []
    for slide in SlideshowSlide.objects.for_zone(zone).visible().select_related("zone", "announcement"):
        deck.append(_slide_vm(slide, default))

    for flag, generator in _GENERATED_BLOCKS:
        if getattr(config, flag):
            deck.extend(generator(config, default))

    if not deck:
        deck.append(_holding_vm(default))
    return deck


def deck_hash(deck: list[SignageSlideVM], config: SiteConfiguration) -> str:
    """A stable hash of each slide's identity/content + today's local date. The player
    renders it as ``data-deck-hash``; the poll uses it to skip a no-op swap. Today's date
    is included so a scheduled slide dropping in or out changes it."""
    parts: list[str] = [
        str(timezone.localdate()),
    ]
    for vm in deck:
        parts.append(
            "|".join(
                [
                    vm.kind,
                    vm.title,
                    vm.body,
                    vm.meta,
                    vm.image_url or "",
                    "q" if vm.qr_svg else "",
                    vm.url_display,
                    str(vm.duration_seconds),
                    # The month grid folds in here rather than as its own top-level part, so
                    # the per-slide grouping this function documents survives. Without it a
                    # day gaining its first event never changes the hash and never swaps.
                    # ``is_today`` needs no digest — ``parts[0]`` is already today's date.
                    ",".join(f"{d.day}:{d.event_count}" for d in vm.calendar_days),
                ]
            )
        )
    raw = "\x1f".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _slide_vm(slide: SlideshowSlide, default: int) -> SignageSlideVM:
    """Turn one configured slide into a view-model. Announcement slides pull the linked
    announcement's live title/body (the FK is guaranteed published+active by
    ``visible()``); custom slides use their own fields and render a QR from ``link_url``."""
    from membership.models import SlideshowSlide as Slide

    if slide.kind == Slide.Kind.ANNOUNCEMENT:
        ann = slide.announcement
        return SignageSlideVM(
            kind="announcement",
            title=ann.title if ann else "",
            body=ann.body if ann else "",
            image_url=None,
            qr_svg=None,
            duration_seconds=default,
        )
    image_url = slide.image.url if slide.image else None
    qr = _qr_svg(slide.link_url) if slide.show_qr and slide.link_url else None
    return SignageSlideVM(
        kind="custom",
        title=slide.title,
        body=slide.body,
        image_url=image_url,
        qr_svg=qr,
        duration_seconds=default,
        url_display=_friendly_url(slide.link_url),
    )


def _event_slides(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """Generated slides for upcoming SITE-WIDE events (``guild__isnull=True`` — never a
    private-guild meeting), soonest first, capped. Mirrors the home feed's occurrence
    expansion; never calls ``_get_calendar_context``.

    The window bounds are LOCAL dates. ``occurrences_in`` compares them against
    ``timezone.localtime(...).date()``, so a UTC ``now().date()`` rolls the window
    forward at 5pm Portland and drops every site-wide event still to come that evening
    off the wall — exactly when the lobby has people in it.
    """
    from membership.models import CommunityEvent

    now = timezone.now()
    today = timezone.localdate()
    horizon = today + timedelta(days=config.signage_event_days_ahead)
    dated: list[tuple[datetime_type, SignageSlideVM]] = []
    for event in CommunityEvent.objects.published().upcoming().filter(guild__isnull=True):
        occ = _next_occurrence(event, today, horizon, now)
        if occ is None:
            continue
        # Every event slide carries a QR to its detail page — a member can always scan
        # to learn more or add it to their calendar. No toggle: it's free and useful.
        meta = event.when_display + (f" · {event.location}" if event.location else "")
        vm = SignageSlideVM(
            kind="event",
            title=event.title,
            body="",
            image_url=None,
            qr_svg=_qr_svg(event.absolute_url),
            duration_seconds=default,
            meta=meta,
            url_display=_friendly_url(event.absolute_url),
        )
        dated.append((occ, vm))
    dated.sort(key=lambda pair: pair[0])
    return [vm for _occ, vm in dated[:SIGNAGE_EVENT_CAP]]


def _class_slides(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """One slide per class or workshop starting in the next :data:`SIGNAGE_CLASS_DAYS` days.

    ``upcoming_public()`` already enforces published + non-private + the demo gate, so a
    private or draft class can never reach a wall. A multi-session class contributes one
    slide (its earliest session in the window), then the block is capped.
    """
    from classes.models import ClassSession

    horizon = timezone.now() + timedelta(days=SIGNAGE_CLASS_DAYS)
    sessions = (
        ClassSession.objects.upcoming_public()
        .filter(starts_at__lt=horizon)
        .select_related("class_offering")
        .order_by("starts_at")
    )
    seen: set[int] = set()
    slides: list[SignageSlideVM] = []
    for session in sessions:
        offering = session.class_offering
        if offering.pk in seen:
            continue
        seen.add(offering.pk)
        url = offering.public_url
        slides.append(
            SignageSlideVM(
                kind="class",
                title=offering.title,
                body="",
                image_url=None,
                qr_svg=_qr_svg(url),
                duration_seconds=default,
                meta=date_format(timezone.localtime(session.starts_at), "D, M j · g:i A"),
                url_display=_friendly_url(url),
            )
        )
        if len(slides) == SIGNAGE_CLASS_CAP:
            break
    return slides


def _guilds_slide(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """ONE slide listing every currently visible guild, with a QR to the guild directory.

    One slide, not one per guild: a passer-by wants the shape of the place, and the
    directory behind the QR carries the detail. Empty list when no guild is visible.
    """
    from membership.models import Guild

    names = list(Guild.objects.visible().order_by("name").values_list("name", flat=True))
    if not names:
        return []
    url = settings.GUILDS_BASE_URL + reverse("hub_guild_directory")
    return [
        SignageSlideVM(
            kind="guilds",
            title="The Guilds Of Past Lives",
            body=" · ".join(names),
            image_url=None,
            qr_svg=_qr_svg(url),
            duration_seconds=default,
            url_display=_friendly_url(url),
        )
    ]


def _calendar_slide(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """This month as a grid, with a dot on every day that has something public on it.

    Day counts come from PUBLIC sources only — site-wide published community events plus
    public class sessions. It never calls ``hub.calendar_entries.community_event_entries``
    or ``_get_calendar_context``: with ``guild=None`` those return every guild's private
    meetings, and a private meeting on a lobby wall is exactly the leak this file exists to
    avoid. ``site_wide()`` does include Guild Lead Meetings, which is intended — the grid
    renders a count, never a title, so a lead-meeting day looks like any other busy day.

    Class sessions come through ``public_between`` rather than ``upcoming_public``: the
    latter is future-only, which would leave the elapsed half of the month blank and read
    as broken.
    """
    from classes.models import ClassSession
    from membership.models import CommunityEvent

    today = timezone.localdate()
    first = today.replace(day=1)
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    # A Counter, not a dict, so a day with nothing on it reads as 0 without a fallback
    # default hiding a missing key (padding cells ask for day 0, which is never counted).
    counts: Counter[int] = Counter()
    for event in CommunityEvent.objects.published().site_wide():
        for occurrence in event.occurrences_in(first, last):
            counts[timezone.localtime(occurrence).day] += 1
    window_start = timezone.make_aware(datetime_type.combine(first, time_type.min))
    window_end = timezone.make_aware(datetime_type.combine(last, time_type.max))
    for session in ClassSession.objects.public_between(window_start, window_end):
        counts[timezone.localtime(session.starts_at).day] += 1

    days = tuple(
        SignageCalendarDay(day=day, event_count=counts[day], is_today=day == today.day)
        for week in calendar.Calendar(firstweekday=0).monthdayscalendar(today.year, today.month)
        for day in week
    )
    url = settings.MEMBER_BASE_URL + reverse("hub_community_calendar")
    return [
        SignageSlideVM(
            kind="calendar",
            title=date_format(today, "F Y"),
            body="",
            image_url=None,
            qr_svg=_qr_svg(url),
            duration_seconds=default,
            url_display=_friendly_url(url),
            calendar_days=days,
        )
    ]


def _voting_slide(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """The monthly guild funding vote and when it closes, with a QR to the ballot."""
    from membership.cycle import get_cycle_context

    cycle = get_cycle_context()
    url = settings.MEMBER_BASE_URL + reverse("hub_guild_voting")
    return [
        SignageSlideVM(
            kind="voting",
            title="Guild Funding Vote",
            body="Every member has a say in how this month's funding pool is split between the guilds.",
            image_url=None,
            qr_svg=_qr_svg(url),
            duration_seconds=default,
            meta=f"{cycle['current_cycle_label']} · Voting closes {cycle['cycle_closes_on']}",
            url_display=_friendly_url(url),
        )
    ]


def _directory_slide(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """A QR that opens the member directory. Members-only behind the scan, by design —
    the audience is members standing in the building and the login wall is one tap."""
    url = settings.MEMBER_BASE_URL + reverse("hub_member_directory")
    return [
        SignageSlideVM(
            kind="directory",
            title="Member Directory",
            body="See who else is in the space, what they make, and what they can teach you.",
            image_url=None,
            qr_svg=_qr_svg(url),
            duration_seconds=default,
            url_display=_friendly_url(url),
        )
    ]


def _teach_slide(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """The Host a Workshop invitation, in the copy admins already edit.

    Reuses the Host a Workshop page's own CTA fields (falling back to its title/lead) so
    there is no second place to keep the same sentence current. Empty when an admin has
    blanked both pairs.
    """
    from classes.models import ClassSettings

    class_settings = ClassSettings.load()
    title = class_settings.teach_page_cta_title or class_settings.teach_page_title
    body = class_settings.teach_page_cta_line or class_settings.teach_page_lead
    if not title and not body:
        return []
    url = settings.MEMBER_BASE_URL + reverse("classes:teach_overview")
    return [
        SignageSlideVM(
            kind="teach",
            title=title,
            body=body,
            image_url=None,
            qr_svg=_qr_svg(url),
            duration_seconds=default,
            url_display=_friendly_url(url),
        )
    ]


# The self-building blocks, in the fixed order they append after the admin's own slides.
# Each pairs its SiteConfiguration switch with its generator; the uniform
# ``(config, default) -> list`` signature is what lets build_deck drive them in one loop.
_GENERATED_BLOCKS: tuple[tuple[str, Callable[[SiteConfiguration, int], list[SignageSlideVM]]], ...] = (
    ("signage_show_classes", _class_slides),
    ("signage_show_events", _event_slides),
    ("signage_show_guilds", _guilds_slide),
    ("signage_show_calendar", _calendar_slide),
    ("signage_show_voting", _voting_slide),
    ("signage_show_directory", _directory_slide),
    ("signage_show_teach", _teach_slide),
)


def _next_occurrence(event: CommunityEvent, frm: date_type, to: date_type, now: datetime_type) -> datetime_type | None:
    """The soonest still-future start of ``event`` within ``[frm, to]``, or ``None``.

    Self-contained so signage doesn't import a private ``hub/home.py`` helper — it
    iterates ``event.occurrences_in`` and returns the first at/after ``now``.
    """
    for occurrence in event.occurrences_in(frm, to):
        if occurrence >= now:
            return occurrence
    return None


def _holding_vm(default: int) -> SignageSlideVM:
    """The branded fallback slide, so an empty deck still shows something."""
    return SignageSlideVM(
        kind="holding",
        title="Past Lives Makerspace",
        body="",
        image_url=None,
        qr_svg=None,
        duration_seconds=default,
    )


def _qr_svg(url: str) -> str:
    """Inline, CSS-scalable SVG QR of ``url`` — delegates to the shared ``membership.qr`` helper."""
    from membership.qr import qr_svg

    return qr_svg(url)


def _friendly_url(url: str) -> str:
    """A room-legible version of ``url`` for the "learn more" caption: drop the scheme and
    any trailing slash so ``https://pastlives.app/calendar/`` reads as ``pastlives.app/calendar``.
    Returns ``""`` for a blank url."""
    if not url:
        return ""
    return url.split("://", 1)[-1].rstrip("/")
