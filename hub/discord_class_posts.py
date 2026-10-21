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
announce twice. All links are absolute onto the public class site (``BOOK_BASE_URL``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from core.integrations.discord_channel import post_channel_message
from hub.discord_calendar_posts import (
    ANNOUNCE_CAP,
    DIGEST_WINDOW_DAYS,
    _chunk_blocks,
    _post_unposted_batches,
    _time_of,
)

if TYPE_CHECKING:
    from classes.models import ClassOffering, ClassSession

logger = logging.getLogger(__name__)

# The classes calendar purple (SiteConfiguration.classes_calendar_color's default).
_EMBED_COLOR = 0x7C5CBF
_DIGEST_TITLE = "Classes this week at Past Lives"
_FLEXIBLE_HEADER = "**Flexible scheduling — book anytime**"
_FLEXIBLE_WHEN = "Flexible scheduling — arrange with the instructor"
_FOOTER_LABEL = "Browse all classes"
_CLASS_FOOTER = "Sign up on the class page"
_MORE_SUFFIX = "… more on the class page"
# The #calendar card's description cut (membership.models._DISCORD_DESCRIPTION_MAX), the same here.
_DISCORD_DESCRIPTION_MAX = 600
# The hero plus three: Discord's client merges up to four same-url embeds into one image grid.
_GALLERY_PHOTOS_MAX = 3
# Discord's embed caps for a title and a field value. With these and the 600 cut, a post's
# text stays far under the 6,000 per message cap (the photo embeds carry none).
_TITLE_MAX = 256
_FIELD_VALUE_MAX = 1024


def _catalog_url() -> str:
    """The public class catalog, absolute: Discord renders URLs as-is, so a bare path is a dead link."""
    from django.urls import reverse

    from core.urls_util import book_absolute_url

    return book_absolute_url(reverse("classes:public_list"))


def _posting_channel_id() -> str:
    """The configured #classes channel id, or ``""`` when posting is disabled/unconfigured."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    if not config.discord_classes_posts_enabled:
        return ""
    return (config.discord_classes_channel_id or "").strip()


def _class_line(offering: ClassOffering, *, time_prefix: str = "") -> str:
    """One digest bullet: optional time, linked title, and the instructor's name."""
    title_part = f"[{offering.title}]({offering.public_url})"
    line = f"• {time_prefix}{title_part}"
    if offering.instructor_names:
        line += f" · with {offering.instructor_names}"
    return line


def _digest_blocks(now: datetime) -> list[str]:
    """The digest's day blocks (public classes' sessions in the next 7 days, grouped by
    local day) plus one flexible-scheduling block for currently-bookable flexible classes."""
    from classes.models import ClassOffering, ClassSession

    horizon = now + timedelta(days=DIGEST_WINDOW_DAYS)
    public_ids = ClassOffering.objects.public().values_list("pk", flat=True)
    sessions = (
        ClassSession.objects.filter(class_offering_id__in=public_ids, starts_at__gte=now, starts_at__lt=horizon)
        .select_related("class_offering")
        .prefetch_related("class_offering__instructor_rows")
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

    # bookable() is the one rule: a flexible class is listed through its last day on the
    # site's local date and gone the day after, exactly as the catalog has it (#545).
    flexible = (
        ClassOffering.objects.bookable()
        .filter(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        .select_related("instructor")
    )
    flexible_lines = [_class_line(offering, time_prefix=_window_prefix(offering)) for offering in flexible]
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
    blocks.append(f"[{_FOOTER_LABEL} →]({_catalog_url()})")
    chunks = _chunk_blocks(blocks)
    local_start = timezone.localtime(now)
    local_end = timezone.localtime(now + timedelta(days=DIGEST_WINDOW_DAYS - 1))
    title = f"{_DIGEST_TITLE} · {local_start.strftime('%b %-d')} – {local_end.strftime('%b %-d')}"
    return [
        {
            "title": title if i == 0 else f"{_DIGEST_TITLE} (continued)",
            "description": chunk,
            "color": _EMBED_COLOR,
            "url": _catalog_url(),
        }
        for i, chunk in enumerate(chunks)
    ]


def post_weekly_classes_digest() -> int:
    """Post the weekly classes digest to #classes; return the number of blocks listed.

    No-ops (returns 0) when posting is disabled, no channel id is set, or the coming week
    has neither class sessions nor bookable flexible classes — an empty digest is noise —
    or this week's digest is already in the channel (a timed-out post that landed anyway).
    """
    channel_id = _posting_channel_id()
    if not channel_id:
        return 0
    now = timezone.now()
    embeds = build_weekly_classes_digest_embeds(now)
    if not embeds:
        return 0
    if not _post_unposted_batches(channel_id, embeds):
        logger.info("Weekly classes digest already in #classes; not posting it again.")
        return 0
    return len(embeds)


def _window_prefix(offering: ClassOffering) -> str:
    """A flexible class's date window as a line prefix ("Nov 2 to Dec 1, 2026 — "), or "" with no window."""
    label = offering.flexible_window_label
    return f"{label} — " if label else ""


def _class_when(offering: ClassOffering, sessions: list[ClassSession]) -> str:
    """The **When** value: the next upcoming session, or the flexible line with its window.

    ``bookable()`` guarantees a FIXED offering's first session is still upcoming, so the
    earliest session IS the next one. ``sessions`` is the prefetched list, earliest first.
    """
    if offering.is_flexible:
        label = offering.flexible_window_label
        return f"{_FLEXIBLE_WHEN} · {label}" if label else _FLEXIBLE_WHEN
    if not sessions:  # defensive: bookable() excludes session-less FIXED offerings
        return _FLEXIBLE_WHEN
    first = sessions[0]
    local = timezone.localtime(first.starts_at)
    return f"{local.strftime('%A, %B %-d')} · {_time_of(first.starts_at)}"


def _absolute_photo_url(url: str) -> str:
    """A photo URL Discord can fetch, or ``""``.

    Discord fetches the picture from its own servers, so a relative ``/media/...`` or proxy
    path is no use to it. Object storage (R2) already returns an absolute URL and is kept as
    is; anything relative is made absolute on the public class site, as
    ``Event.photo_url`` does on the members site. What still is not absolute is left out.
    """
    from core.urls_util import book_absolute_url

    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    absolute = book_absolute_url(url)
    return absolute if absolute.startswith(("http://", "https://")) else ""


def _class_photo_urls(offering: ClassOffering) -> list[str]:
    """The post's photos: the hero, then up to :data:`_GALLERY_PHOTOS_MAX` gallery photos, absolute.

    No category fallback: a class with no photo of its own posts as a card with no picture.
    """
    gallery = [_absolute_photo_url(image["url"]) for image in offering.gallery_display_images]
    candidates = [_absolute_photo_url(offering.hero_image_url), *gallery[:_GALLERY_PHOTOS_MAX]]
    photos: list[str] = []
    for url in candidates:
        if url and url not in photos:
            photos.append(url)
    return photos


def _class_fields(offering: ClassOffering, sessions: list[ClassSession]) -> list[dict[str, Any]]:
    """The bold labelled rows, each present only when it has a value, capped at Discord's 1,024."""
    from classes.templatetags.classes_tags import cents_as_price, session_duration_words

    rows: list[tuple[str, str]] = [("When", _class_when(offering, sessions))]
    if len(sessions) > 1:
        rows.append(("Sessions", str(len(sessions))))
    if sessions:
        rows.append(("Duration", session_duration_words(sessions[0])))
    if offering.area is not None:
        rows.append(("Location", offering.area.name))
    rows.append(("Instructor", offering.instructor_names))
    rows.append(("Price", cents_as_price(offering.price_cents)))
    if not offering.is_flexible:
        rows.append(("Spots", str(offering.capacity)))
    return [{"name": name, "value": _clip(value, _FIELD_VALUE_MAX)} for name, value in rows if value and value.strip()]


def _clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, ending in an ellipsis when it was cut."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def class_announcement_embeds(offering: ClassOffering) -> list[dict[str, Any]]:
    """The new class post, in the shape of the #calendar card (``Event.discord_announcement_embed``).

    The main embed: the title linked to the class page, the description cut at 600
    characters, the labelled rows, the footer and the hero as its image. Each further
    gallery photo rides in an embed carrying only the same ``url`` and an ``image``, which
    Discord's client merges with the hero into one image grid (where it does not, they show
    as stacked picture cards). With no photo the ``image`` key is absent: a plain card.

    Reads only what :func:`_pending_offerings` loads, so it runs no query of its own.
    """
    sessions = list(offering.sessions.all())
    link = offering.public_url
    embed: dict[str, Any] = {
        "title": _clip(offering.title, _TITLE_MAX),
        "url": link,
        "color": _EMBED_COLOR,
        "fields": _class_fields(offering, sessions),
        "footer": {"text": _CLASS_FOOTER},
    }
    description = offering.description_text.strip()
    if description:
        if len(description) > _DISCORD_DESCRIPTION_MAX:
            description = description[:_DISCORD_DESCRIPTION_MAX].rstrip() + _MORE_SUFFIX
        embed["description"] = description
    photos = _class_photo_urls(offering)
    if not photos:
        return [embed]
    embed["image"] = {"url": photos[0]}
    return [embed, *({"url": link, "image": {"url": photo}} for photo in photos[1:])]


def _pending_offerings() -> list[ClassOffering]:
    """Unannounced publicly bookable classes, soonest first, with everything the post reads loaded."""
    from classes.models import ClassOffering

    return list(
        ClassOffering.objects.bookable()
        .filter(channel_announced_at__isnull=True)
        .select_related("category", "instructor", "area")
        .prefetch_related("sessions", "gallery_images", "instructor_rows")
    )


def announce_new_classes() -> int:
    """Announce unannounced publicly-bookable classes in #classes; return how many posted.

    Posts up to :data:`ANNOUNCE_CAP` (soonest-starting first — ``bookable()``'s own
    ordering), stamping ``channel_announced_at`` right after each post; anything past the
    cap is stamped silently (and logged) so a backlog can never flood the channel later.
    The stamp is permanent, so a republished class never re-announces.
    """
    channel_id = _posting_channel_id()
    if not channel_id:
        return 0
    now = timezone.now()
    pending = _pending_offerings()
    posted = 0
    for offering in pending[:ANNOUNCE_CAP]:
        post_channel_message(channel_id, class_announcement_embeds(offering))
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
