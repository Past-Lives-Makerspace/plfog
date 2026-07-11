"""Deck builder for the signage slideshow.

A small service, because it orchestrates three models (``SlideshowSlide`` +
``CommunityEvent`` + ``SiteConfiguration``) into an ordered list of render-ready
view-models — cross-model orchestration belongs in a service, not a view or
template. The emergency-alert takeover is decided in the VIEW/template (so it also
swaps in on the next poll), never here.

Event slides are the privacy-safe part: site-wide events ONLY
(``guild__isnull=True``), expanded via the same occurrence logic the home feed
uses. This deliberately never touches ``_get_calendar_context`` (a confirmed
private-guild leak) — it queries ``CommunityEvent`` directly.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime as datetime_type
from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from core.models import SiteConfiguration
    from membership.models import CommunityEvent, SlideshowSlide, SlideshowZone

SIGNAGE_EVENT_CAP = 8


@dataclass(frozen=True)
class SignageSlideVM:
    """A single render-ready slide for the player template."""

    kind: str  # "custom" | "announcement" | "event" | "holding"
    title: str
    body: str
    image_url: str | None
    qr_svg: str | None  # inline SVG when a QR should render
    duration_seconds: int
    meta: str = ""  # e.g. an event's when_display / location


def build_deck(zone: SlideshowZone) -> list[SignageSlideVM]:
    """Ordered slides for one zone: configured slides (by ``sort_order``) then generated
    event slides (soonest first, capped). A branded holding slide guarantees the screen
    is never blank. The emergency alert is handled in the view, not here."""
    from core.models import SiteConfiguration
    from membership.models import SlideshowSlide

    config = SiteConfiguration.load()
    default = config.signage_default_slide_seconds

    deck: list[SignageSlideVM] = []
    for slide in SlideshowSlide.objects.for_zone(zone).visible().select_related("zone", "announcement"):
        deck.append(_slide_vm(slide, default))

    if config.signage_show_events:
        deck.extend(_event_slides(config, default))

    if not deck:
        deck.append(_holding_vm(default))
    return deck


def deck_hash(deck: list[SignageSlideVM], config: SiteConfiguration) -> str:
    """A stable hash of the alert state + each slide's identity/content + today's local
    date. The player renders it as ``data-deck-hash``; the poll uses it to skip a no-op
    swap. Today's date is included so a scheduled slide dropping in or out changes it."""
    parts: list[str] = [
        str(timezone.localdate()),
        "1" if config.signage_alert_active else "0",
        config.signage_alert_heading,
        config.signage_alert_message,
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
                    str(vm.duration_seconds),
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

    duration = slide.duration_seconds or default
    if slide.kind == Slide.Kind.ANNOUNCEMENT:
        ann = slide.announcement
        return SignageSlideVM(
            kind="announcement",
            title=ann.title if ann else "",
            body=ann.body if ann else "",
            image_url=None,
            qr_svg=None,
            duration_seconds=duration,
        )
    image_url = slide.image.url if slide.image else None
    qr = _qr_svg(slide.link_url) if slide.show_qr and slide.link_url else None
    return SignageSlideVM(
        kind="custom",
        title=slide.title,
        body=slide.body,
        image_url=image_url,
        qr_svg=qr,
        duration_seconds=duration,
    )


def _event_slides(config: SiteConfiguration, default: int) -> list[SignageSlideVM]:
    """Generated slides for upcoming SITE-WIDE events (``guild__isnull=True`` — never a
    private-guild meeting), soonest first, capped. Mirrors the home feed's occurrence
    expansion; never calls ``_get_calendar_context``."""
    from membership.models import CommunityEvent

    now = timezone.now()
    today = now.date()
    horizon = today + timedelta(days=config.signage_event_days_ahead)
    dated: list[tuple[datetime_type, SignageSlideVM]] = []
    for event in CommunityEvent.objects.published().upcoming().filter(guild__isnull=True):
        occ = _next_occurrence(event, today, horizon, now)
        if occ is None:
            continue
        qr = _qr_svg(event.absolute_url) if config.signage_event_qr else None
        meta = event.when_display + (f" · {event.location}" if event.location else "")
        vm = SignageSlideVM(
            kind="event",
            title=event.title,
            body="",
            image_url=None,
            qr_svg=qr,
            duration_seconds=default,
            meta=meta,
        )
        dated.append((occ, vm))
    dated.sort(key=lambda pair: pair[0])
    return [vm for _occ, vm in dated[:SIGNAGE_EVENT_CAP]]


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
    """Inline SVG QR of ``url`` (segno — pure Python, no Pillow), the same approach as
    ``Guild.qr_svg``."""
    import segno

    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=1, xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")
