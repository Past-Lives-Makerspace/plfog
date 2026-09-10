"""Deck builder for the signage slideshow.

A small service, because it orchestrates three models (``SlideshowSlide`` +
``CommunityEvent`` + ``SiteConfiguration``) into an ordered list of render-ready
view-models — cross-model orchestration belongs in a service, not a view or
template.

The site-wide blocks are the privacy-safe part: the events block and the What's On
list use site-wide events ONLY (``guild__isnull=True``), expanded via the same
occurrence logic the home feed uses. Both deliberately never touch
``_get_calendar_context`` (a confirmed private-guild leak) — they query
``CommunityEvent`` directly.

Guild slides are the one deliberate exception, added 2026-09-09 for the lobby kiosk:
each names its own guild's next PUBLISHED meeting or public class. That is wider than
``for_member`` scoping, which shows a member only the meetings of guilds they have
joined. It was a decision, not an oversight — see ``_guild_slides``. Nothing unpublished
reaches a screen, and no other block names a guild's events.

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
from django.utils.text import Truncator

if TYPE_CHECKING:
    from core.models import SiteConfiguration
    from membership.models import CommunityEvent, Guild, SlideshowSlide, SlideshowZone

SIGNAGE_EVENT_CAP = 8
# "This week" is a fixed seven days: the ask is the week, and the events block already
# owns the configurable look-ahead. No admin field (YAGNI).
SIGNAGE_CLASS_DAYS = 7
SIGNAGE_CLASS_CAP = 6
# The What's On slide holds six named lines at a size readable across a lobby. More rows means
# smaller type, and the QR carries anyone who wants the full month.
SIGNAGE_AGENDA_CAP = 6
# How far ahead a guild slide looks for something to announce. A monthly guild's next meeting
# can be three weeks out and still worth naming; four months out is not.
SIGNAGE_GUILD_HORIZON_DAYS = 60
SIGNAGE_GUILD_ABOUT_CHARS = 180


@dataclass(frozen=True)
class SignageAgendaEntry:
    """One named line on the What's On slide: when it is, what it is, what time.

    The date and the time are separate fields rather than one string so the template can
    size them apart — the date anchors the row, the time is secondary.
    """

    when_display: str
    title: str
    time_display: str


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
    meta_lead: str = ""  # small eyebrow above ``meta`` — e.g. "Next up" over a guild's next event
    entries: tuple[SignageAgendaEntry, ...] = ()  # non-empty only on the What's On slide


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
                    vm.meta_lead,
                    vm.image_url or "",
                    "q" if vm.qr_svg else "",
                    vm.url_display,
                    str(vm.duration_seconds),
                    # The month grid folds in here rather than as its own top-level part, so
                    # the per-slide grouping this function documents survives. Without it a
                    # day gaining its first event never changes the hash and never swaps.
                    # ``is_today`` needs no digest — ``parts[0]`` is already today's date.
                    ",".join(f"{e.when_display} {e.time_display} {e.title}" for e in vm.entries),
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


def _guild_slides(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """One slide per visible guild, each carrying that guild's next real thing.

    This replaced a single slide that joined every guild name into one middot-separated
    run-on sentence. Centered and wrapped at 32ch it read as a paragraph blob from across a
    room — it named the guilds without telling anyone anything about them.

    A guild's next meeting or class is now named on the wall. That is a DELIBERATE widening
    of who can see a guild meeting: :meth:`CommunityEventQuerySet.for_member` scopes meetings
    to the guilds a member has joined, so before this a visitor could not discover one at all.
    Chosen on 2026-09-09 for the lobby kiosk, on the reasoning that the wall stands inside the
    building the guild meets in, and a meeting nobody outside the guild can find cannot
    recruit. Only PUBLISHED events qualify, so a pending member proposal or a parked draft
    still never reaches a screen.

    A guild with nothing coming up falls back to its own ``about`` copy, so a quiet guild
    keeps its name and its QR on the wall instead of dropping out of the rotation.
    """
    from membership.models import Guild

    now = timezone.localtime()
    slides: list[SignageSlideVM] = []
    for guild in Guild.objects.visible().order_by("name"):
        url = settings.GUILDS_BASE_URL + reverse("hub_guild_detail", args=[guild.slug])
        upcoming = _guild_next_item(guild, now)
        if upcoming is None:
            meta_lead = ""
            meta = ""
            body = Truncator(guild.about).chars(SIGNAGE_GUILD_ABOUT_CHARS) if guild.about else ""
        else:
            when, meta = upcoming
            # Without the eyebrow the gold line reads as a tagline, not as something with a
            # date attached. Two words is the difference between "nice name" and "be there".
            meta_lead = "Next up"
            body = date_format(timezone.localtime(when), "D, M j · g:i A")
        slides.append(
            SignageSlideVM(
                kind="guild",
                title=guild.name,
                body=body,
                image_url=None,
                qr_svg=_qr_svg(url),
                duration_seconds=default,
                meta=meta,
                meta_lead=meta_lead,
                url_display=_friendly_url(url),
            )
        )
    return slides


def _guild_next_item(guild: Guild, now: datetime_type) -> tuple[datetime_type, str] | None:
    """The soonest published meeting or public class belonging to ``guild``, or ``None``.

    Meetings come from ``CommunityEvent`` (expanded through ``occurrences_in``, so a monthly
    series contributes its next concrete date rather than its original anchor). Classes reach
    a guild through their category, and ``upcoming_public()`` already enforces published +
    non-private + the demo gate, so a draft or private class can never surface here.

    Bounded by :data:`SIGNAGE_GUILD_HORIZON_DAYS` — a guild whose next meeting is four months
    out is better served by its ``about`` copy than by a date nobody will act on.
    """
    from classes.models import ClassSession
    from membership.models import CommunityEvent

    horizon = (now + timedelta(days=SIGNAGE_GUILD_HORIZON_DAYS)).date()
    soonest: tuple[datetime_type, str] | None = None
    for event in CommunityEvent.objects.published().for_guild(guild).candidates_for_window(now.date(), horizon):
        for occurrence in event.occurrences_in(now.date(), horizon):
            if occurrence >= now and (soonest is None or occurrence < soonest[0]):
                soonest = (occurrence, event.title)
    session = (
        ClassSession.objects.upcoming_public()
        .filter(class_offering__category__guild=guild, starts_at__date__lte=horizon)
        .select_related("class_offering")
        .order_by("starts_at")
        .first()
    )
    if session is not None and (soonest is None or session.starts_at < soonest[0]):
        soonest = (session.starts_at, session.class_offering.title)
    return soonest


def _calendar_slide(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """What is still to come this month, BY NAME — one line each, soonest first.

    This replaced a month grid of anonymous dots. The grid answered "is something happening?"
    — on a live month nearly every day carried a dot, so it discriminated nothing — and never
    answered "what is it?", which is the only question a passer-by actually has.

    PUBLIC sources only: site-wide published community events plus public class sessions. It
    never calls ``hub.calendar_entries.community_event_entries`` or ``_get_calendar_context``:
    with ``guild=None`` those return every guild's meetings, and a guild meeting belongs on
    that guild's own slide where it is named and attributed, not in an unattributed site-wide
    list. ``site_wide()`` does include Guild Lead Meetings, which is intended — they are
    makerspace-wide business.

    Future-only, and empty late in a quiet month rather than padded: the classes block already
    names everything in the next seven days, so this slide's job is the rest of the month.
    """
    from classes.models import ClassSession
    from membership.models import CommunityEvent

    now = timezone.localtime()
    today = now.date()
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    dated: list[tuple[datetime_type, str]] = []
    for event in CommunityEvent.objects.published().site_wide().candidates_for_window(today, last):
        dated.extend((occurrence, event.title) for occurrence in event.occurrences_in(today, last) if occurrence >= now)
    window_end = timezone.make_aware(datetime_type.combine(last, time_type.max))
    for session in ClassSession.objects.public_between(now, window_end).select_related("class_offering"):
        dated.append((session.starts_at, session.class_offering.title))

    if not dated:
        return []
    dated.sort(key=lambda pair: pair[0])
    # One line per (instant, title). A class that a lead ALSO put on the calendar by hand
    # arrives from both sources, and the same row twice on a wall just reads as broken.
    entries: tuple[SignageAgendaEntry, ...] = ()
    seen: set[tuple[datetime_type, str]] = set()
    for when, title in dated:
        local = timezone.localtime(when)
        key = (local, title)
        if key in seen:
            continue
        seen.add(key)
        entries += (
            SignageAgendaEntry(
                when_display=date_format(local, "D j M"),
                title=title,
                time_display=date_format(local, "g:i A"),
            ),
        )
        if len(entries) == SIGNAGE_AGENDA_CAP:
            break
    url = settings.MEMBER_BASE_URL + reverse("hub_community_calendar")
    return [
        SignageSlideVM(
            kind="calendar",
            title="What's On This Month",
            body="",
            image_url=None,
            qr_svg=_qr_svg(url),
            duration_seconds=default,
            url_display=_friendly_url(url),
            entries=entries,
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


def _tour_slide(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """The Book a Tour invitation, with a QR to the booking page.

    The only generated slide whose URL is not an internal ``reverse()``: tours are booked on
    the marketing site, so the destination is an admin-editable field rather than a constant.
    Empty when an admin has blanked it, the same way a blanked Host a Workshop CTA drops out.
    """
    url = config.signage_tour_url
    if not url:
        return []
    return [
        SignageSlideVM(
            kind="tour",
            title="Book a Tour",
            body="New here? Book a walkthrough and a member will show you the shops, the tools, and how to join.",
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
    ("signage_show_guilds", _guild_slides),
    ("signage_show_calendar", _calendar_slide),
    ("signage_show_voting", _voting_slide),
    ("signage_show_directory", _directory_slide),
    ("signage_show_teach", _teach_slide),
    ("signage_show_tour", _tour_slide),
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
