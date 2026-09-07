"""#classes Discord posts: the weekly classes digest + the 15-minute new-class announcer.

The classes-channel sibling of :mod:`hub.discord_calendar_posts`, sharing its embed
batching (Discord's 4,096-per-description and 6,000-per-message caps), its chunking,
and the same gate shape — ``SiteConfiguration.discord_classes_posts_enabled`` AND a
non-blank ``discord_classes_channel_id`` — with both entry points no-oping (return 0)
when the gate is closed.

Source of truth is :class:`classes.models.ClassOffering`, not the calendar cache: a
"new class" is an offering that is publicly bookable (``ClassOffering.objects.bookable()``
— published, non-private, flexible or first session still upcoming) and not yet stamped
``channel_announced_at``. The stamp is set when announced (or silently, past the per-run
cap) and never cleared, so a draft→published→unpublished→republished class can never
announce twice. For now all links point at the legacy Drupal site (still the public
sign-up surface) via :func:`_showcase_url`; locally-authored classes with no Drupal page
fall back to the new class site (``BOOK_BASE_URL``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from core.integrations.discord_channel import post_channel_message
from hub.discord_calendar_posts import ANNOUNCE_CAP, DIGEST_WINDOW_DAYS, _batch_embeds, _chunk_blocks, _time_of

if TYPE_CHECKING:
    from classes.models import ClassOffering

logger = logging.getLogger(__name__)

# The classes calendar purple (SiteConfiguration.classes_calendar_color's default).
_EMBED_COLOR = 0x7C5CBF
_DIGEST_TITLE = "Classes this week at Past Lives"
_FLEXIBLE_HEADER = "**Flexible scheduling — book anytime**"
_FLEXIBLE_WHEN = "Flexible scheduling — arrange with the instructor"
_FOOTER_LABEL = "Browse all classes"


def _showcase_url(offering: ClassOffering) -> str:
    """Where a #classes link sends members: the class's legacy Drupal page while that site
    is still the public sign-up surface, falling back to our own public page for
    locally-authored classes that never existed on Drupal. When the new class site takes
    over, this collapses back to ``offering.public_url``."""
    return offering.legacy_public_url or offering.public_url


def _legacy_site_root() -> str:
    """The legacy Drupal class site's root — the "Browse all classes" target for now."""
    from classes.import_service import LEGACY_CMS_BASE

    return LEGACY_CMS_BASE


def _posting_channel_id() -> str:
    """The configured #classes channel id, or ``""`` when posting is disabled/unconfigured."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    if not config.discord_classes_posts_enabled:
        return ""
    return (config.discord_classes_channel_id or "").strip()


def _class_line(offering: ClassOffering, *, time_prefix: str = "") -> str:
    """One digest bullet: optional time, linked title, and the instructor's name."""
    title_part = f"[{offering.title}]({_showcase_url(offering)})"
    line = f"• {time_prefix}{title_part}"
    if offering.instructor is not None:
        line += f" · with {offering.instructor.display_name}"
    return line


def _digest_blocks(now: datetime) -> list[str]:
    """The digest's day blocks (public classes' sessions in the next 7 days, grouped by
    local day) plus one flexible-scheduling block for currently-bookable flexible classes."""
    from classes.models import ClassOffering, ClassSession

    horizon = now + timedelta(days=DIGEST_WINDOW_DAYS)
    public_ids = ClassOffering.objects.public().values_list("pk", flat=True)
    sessions = (
        ClassSession.objects.filter(class_offering_id__in=public_ids, starts_at__gte=now, starts_at__lt=horizon)
        .select_related("class_offering", "class_offering__instructor")
        .order_by("starts_at")
    )
    grouped: dict[Any, list[ClassSession]] = {}
    for session in sessions:
        grouped.setdefault(timezone.localtime(session.starts_at).date(), []).append(session)
    blocks: list[str] = []
    for day, day_sessions in grouped.items():
        header = f"**{day.strftime('%A, %B %-d')}**"
        lines = [_class_line(s.class_offering, time_prefix=f"{_time_of(s.starts_at)} — ") for s in day_sessions]
        blocks.append("\n".join([header, *lines]))

    flexible = (
        ClassOffering.objects.bookable()
        .filter(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        .select_related("instructor")
    )
    flexible_lines = [_class_line(offering) for offering in flexible]
    if flexible_lines:
        blocks.append("\n".join([_FLEXIBLE_HEADER, *flexible_lines]))
    return blocks


def build_weekly_classes_digest_embeds(now: datetime) -> list[dict[str, Any]]:
    """Build the Monday-morning #classes digest embeds for the 7 days starting at ``now``.

    Pure over the class models — no HTTP, so it's testable in isolation. Returns ``[]``
    when there is nothing to say (no upcoming sessions AND no bookable flexible classes).
    Every embed description fits Discord's 4,096-char cap; the last embed carries the
    "Browse all classes" footer link.
    """
    blocks = _digest_blocks(now)
    if not blocks:
        return []
    blocks.append(f"[{_FOOTER_LABEL} →]({_legacy_site_root()})")
    chunks = _chunk_blocks(blocks)
    local_start = timezone.localtime(now)
    local_end = timezone.localtime(now + timedelta(days=DIGEST_WINDOW_DAYS - 1))
    title = f"{_DIGEST_TITLE} · {local_start.strftime('%b %-d')} – {local_end.strftime('%b %-d')}"
    return [
        {
            "title": title if i == 0 else f"{_DIGEST_TITLE} (continued)",
            "description": chunk,
            "color": _EMBED_COLOR,
            "url": _legacy_site_root(),
        }
        for i, chunk in enumerate(chunks)
    ]


def post_weekly_classes_digest() -> int:
    """Post the weekly classes digest to #classes; return the number of blocks listed.

    No-ops (returns 0) when posting is disabled, no channel id is set, or the coming week
    has neither class sessions nor bookable flexible classes — an empty digest is noise.
    """
    channel_id = _posting_channel_id()
    if not channel_id:
        return 0
    now = timezone.now()
    embeds = build_weekly_classes_digest_embeds(now)
    if not embeds:
        return 0
    for batch in _batch_embeds(embeds):
        post_channel_message(channel_id, batch)
    return len(embeds)


def _class_when(offering: ClassOffering) -> str:
    """The announcement's date line: the next upcoming session, or the flexible note.

    ``bookable()`` guarantees a FIXED offering's first session is still upcoming, so the
    earliest session IS the next one.
    """
    from classes.models import ClassOffering as CO

    if offering.scheduling_model == CO.SchedulingModel.FLEXIBLE:
        return _FLEXIBLE_WHEN
    first = offering.sessions.order_by("starts_at").first()
    if first is None:  # defensive: bookable() excludes session-less FIXED offerings
        return _FLEXIBLE_WHEN
    local = timezone.localtime(first.starts_at)
    return f"{local.strftime('%A, %B %-d')} · {_time_of(first.starts_at)}"


def _class_announcement_embed(offering: ClassOffering) -> dict[str, Any]:
    """One compact new-class embed: category, next date (or flexible), price, sign-up link."""
    from classes.templatetags.classes_tags import cents_as_price

    link = _showcase_url(offering)
    lines = [
        f"*New class in {offering.category.name}*",
        f"**When:** {_class_when(offering)}",
        f"**Price:** {cents_as_price(offering.price_cents)}",
        f"[Sign up →]({link})",
    ]
    return {
        "title": offering.title,
        "description": "\n".join(lines),
        "color": _EMBED_COLOR,
        "url": link,
    }


def announce_new_classes() -> int:
    """Announce unannounced publicly-bookable classes in #classes; return how many posted.

    Posts up to :data:`ANNOUNCE_CAP` (soonest-starting first — ``bookable()``'s own
    ordering), stamping ``channel_announced_at`` right after each post; anything past the
    cap is stamped silently (and logged) so a backlog can never flood the channel later.
    The stamp is permanent, so a republished class never re-announces.
    """
    from classes.models import ClassOffering

    channel_id = _posting_channel_id()
    if not channel_id:
        return 0
    now = timezone.now()
    pending = list(
        ClassOffering.objects.bookable()
        .filter(channel_announced_at__isnull=True)
        .select_related("category", "instructor")
    )
    posted = 0
    for offering in pending[:ANNOUNCE_CAP]:
        post_channel_message(channel_id, [_class_announcement_embed(offering)])
        offering.channel_announced_at = now
        offering.save(update_fields=["channel_announced_at"])
        posted += 1

    overflow = pending[ANNOUNCE_CAP:]
    for offering in overflow:
        offering.channel_announced_at = now
        offering.save(update_fields=["channel_announced_at"])
    if overflow:
        logger.info(
            "announce_new_classes: capped at %s posts; silently marked %s more class(es) announced.",
            ANNOUNCE_CAP,
            len(overflow),
        )
    return posted
