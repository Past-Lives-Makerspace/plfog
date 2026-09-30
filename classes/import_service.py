"""Import service — syncs ClassOffering records from classes.pastlives.space."""

from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags
from django.utils.text import slugify

if TYPE_CHECKING:
    from classes.models import Category
    from membership.models import Member

LEGACY_CMS_BASE = "https://classes.pastlives.space"
LEGACY_CMS_API_URL = f"{LEGACY_CMS_BASE}/jsonapi/node/class"
# The same feed with each node's gallery files (``field_additional_images``) included, so one
# pull carries the file URLs; Drupal keeps the ``include`` on its ``links.next`` pages.
LEGACY_CMS_GALLERY_API_URL = f"{LEGACY_CMS_API_URL}?include=field_additional_images"
# Only files on the legacy host are ever fetched (SSRF guard: the feed names the URL).
LEGACY_FILE_PREFIX = f"{LEGACY_CMS_BASE}/"
# Fraction of attempted gallery downloads that may fail before the run reports failure. A few
# dead files are routine; half of them failing means the host or storage is wrong.
GALLERY_FAILURE_ABORT_FRACTION = 0.5

logger = logging.getLogger(__name__)

_CLASS_TYPE_MAP = {
    "workshop": "Workshop",
    "class": "Class",
    "open_studio": "Open Studio",
}

_WITH_NAME_RE = re.compile(r"\bwith\s+(\w+)", re.IGNORECASE)


def _fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.api+json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read())


def _get_or_create_category(class_type: str) -> "Category":
    from classes.models import Category

    # Unknown types get a humanised fallback so an unexpected value doesn't abort the whole sync
    name = _CLASS_TYPE_MAP.get(class_type, class_type.replace("_", " ").title())
    category, _ = Category.objects.get_or_create(name=name, defaults={"slug": slugify(name)})
    return category


def _get_image_url(item: dict[str, Any]) -> str:
    for tag in (item.get("attributes") or {}).get("metatag") or []:
        tag_attrs = tag.get("attributes") or {}
        if tag_attrs.get("property") == "og:image":
            return tag_attrs.get("content") or ""
    return ""


def extract_instructor_name(title: str) -> str | None:
    """Extract a name from a title like 'Blacksmithing 101 with Billy'. Public for admin UI use."""
    match = _WITH_NAME_RE.search(title)
    return match.group(1) if match else None


def _html_to_text(raw: str) -> str:
    """Convert Drupal HTML body to clean plain text with paragraph breaks."""
    text = re.sub(r"</p>\s*<p[^>]*>", "\n\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = strip_tags(text)
    return html.unescape(text).strip()


def _find_instructor(name: str) -> "Member | None":
    """Resolve an instructor name pulled from a class title to an active Member.

    Prefers an exact (case-insensitive) match on preferred or legal name. Falls back to a
    substring match ONLY when it is unambiguous: if a fragment like "Billy" matches more
    than one active member, return None and leave the instructor unset rather than silently
    linking an arbitrary same-named person. Auto-attaching the wrong member's name to a
    class is worse than attaching none — a human assigns the right instructor instead.
    """
    from django.db.models import Q

    from membership.models import Member

    active = Member.objects.filter(status=Member.Status.ACTIVE)
    # A unique exact match is the strongest signal — take it even if others contain the name.
    exact = list(active.filter(Q(preferred_name__iexact=name) | Q(full_legal_name__iexact=name))[:2])
    if len(exact) == 1:
        return exact[0]
    # Otherwise only auto-link when the substring match resolves to exactly one member.
    fuzzy = list(active.filter(Q(preferred_name__icontains=name) | Q(full_legal_name__icontains=name))[:2])
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def _sync_sessions(offering: Any, date_items: list[dict[str, Any]]) -> None:
    """Replace all sessions for an offering with the supplied date list."""
    from classes.models import ClassSession

    ClassSession.objects.filter(class_offering=offering).delete()
    for i, session in enumerate(date_items):
        start_str = session.get("value")
        end_str = session.get("end_value") or start_str
        if not start_str:
            continue
        start = parse_datetime(start_str)
        end = parse_datetime(end_str) if end_str else start
        if not start or not end:
            continue
        ClassSession.objects.create(
            class_offering=offering,
            starts_at=start,
            ends_at=end,
            sort_order=i,
        )


def _upsert_offering(item: dict[str, Any]) -> str | None:
    """Upsert a single API node item. Returns the node UUID, or None if skipped."""
    from classes.models import ClassOffering
    from classes.templatetags.classes_tags import strip_date_suffix

    node_id: str = item.get("id") or ""
    if not node_id:
        return None

    attrs = item.get("attributes") or {}

    title = strip_date_suffix(attrs.get("title") or "(Untitled)")
    body = attrs.get("body") or {}
    description = _html_to_text(body.get("processed") or body.get("value") or "")
    price_cents = int(float(attrs.get("field_price") or "0") * 100)
    capacity = attrs.get("field_max_students") or 0
    status = ClassOffering.Status.PUBLISHED if attrs.get("status") else ClassOffering.Status.ARCHIVED
    image_url = _get_image_url(item)
    class_type = attrs.get("field_class_type") or "class"
    category = _get_or_create_category(class_type)

    path_alias: str = (attrs.get("path") or {}).get("alias") or ""
    raw_slug = path_alias.replace("/class/", "").strip("/") or node_id[:20]

    # A legacy node carries every date of the class in one ``field_dates`` array.
    # More than one date means it's a multi-session series (one enrollment covers
    # all the dates), not a pick-one single. A single date is a one-off. We persist
    # this so a re-sync corrects rows imported before the rule existed — the daily
    # sync (and the admin Sync Now button) flips mislabeled multi-date offerings.
    date_items = attrs.get("field_dates") or []
    scheduling_type = (
        ClassOffering.SchedulingType.SERIES_PACKAGE
        if len(date_items) > 1
        else ClassOffering.SchedulingType.SINGLE_SESSION
    )

    # Category is create-only: the legacy field_class_type only knows the generic
    # Workshop/Class/Open Studio buckets, while staff re-file offerings into
    # guild-owned categories after import. Updating it here would wipe that
    # curation on every nightly sync.
    shared_defaults = {
        "title": title,
        "description": description,
        "price_cents": price_cents,
        "capacity": capacity,
        "status": status,
        "scheduling_type": scheduling_type,
    }
    # ``image`` appears in neither dict and is never assigned below: once a picture lives
    # in our own storage the feed must not be able to touch it.
    offering, created = ClassOffering.objects.update_or_create(
        legacy_cms_id=node_id,
        defaults=shared_defaults,
        create_defaults={**shared_defaults, "category": category},
    )

    if created:
        slug = raw_slug
        if ClassOffering.objects.filter(slug=slug).exclude(pk=offering.pk).exists():
            slug = f"{slug}-legacy"
        offering.slug = slug

    # Image ownership. Once an offering has a local image — migrated off the legacy CMS by
    # ``download_legacy_images`` or uploaded by staff — the Drupal URL is never written
    # back, and any URL left over from an earlier sync is cleared. Otherwise the next run
    # would silently re-point the site at the server we are decommissioning for a picture
    # we already host, and the request-time proxy would start fetching it all over again.
    # A genuinely new class with no local image still picks up its feed image URL on first
    # import, ready for the next ``download_legacy_images`` run.
    if offering.image:
        offering.legacy_image_url = ""
    elif image_url:
        offering.legacy_image_url = image_url

    if not offering.instructor_id:
        name = extract_instructor_name(title)
        if name:
            instructor = _find_instructor(name)
            if instructor:
                offering.instructor = instructor

    offering.save()
    _sync_sessions(offering, date_items)
    return node_id


def sync_legacy_cms() -> int:
    """Sync ClassOffering records from classes.pastlives.space.

    Upserts offerings keyed on Drupal node UUID. Always syncs core fields (title,
    description, price, capacity, status, sessions, image URL). Never overwrites
    locally-set fields (slug after first import, instructor once set).

    Returns:
        Number of offerings upserted.
    """
    from classes.models import ClassOffering
    from core.models import SiteConfiguration

    now = timezone.now()
    started = time.monotonic()
    seen_ids: list[str] = []

    next_url: str | None = LEGACY_CMS_API_URL
    while next_url:
        data = _fetch_json(next_url)

        for item in data.get("data") or []:
            node_id = _upsert_offering(item)
            if node_id:
                seen_ids.append(node_id)

        next_url = ((data.get("links") or {}).get("next") or {}).get("href")

    # Archive offerings no longer present in the API — guarded against a feed that
    # succeeds but returns nothing, which would otherwise archive the whole catalog.
    ClassOffering.objects.archive_missing_from_legacy_feed(seen_ids)

    # Sanitize: collapse the same class posted on many dates into one catalog group.
    from classes.grouping import regroup_offerings

    regroup_offerings()

    config = SiteConfiguration.load()
    config.legacy_cms_last_synced_at = now
    config.legacy_cms_last_sync_duration = time.monotonic() - started
    config.save(update_fields=["legacy_cms_last_synced_at", "legacy_cms_last_sync_duration"])

    return len(seen_ids)


class LegacyGalleryImportError(Exception):
    """Most gallery downloads failed; whatever succeeded has been saved."""


@dataclass(frozen=True)
class GalleryImportResult:
    """What one :func:`sync_legacy_gallery` run did, for the command's summary and the cron log."""

    created: int
    downloaded: int
    reused: int
    over_cap: int
    unmatched: int
    failed: int

    def summary(self) -> str:
        return (
            f"Added {self.created} gallery photo(s): {self.downloaded} downloaded, {self.reused} re-used "
            f"an object already stored. {self.over_cap} skipped at the gallery cap, {self.unmatched} legacy "
            f"class(es) have no offering here, {self.failed} failed."
        )


def _iter_legacy_pages(url: str | None):
    """Yield ``(items, included)`` per page of the legacy feed, following ``links.next``."""
    while url:
        data = _fetch_json(url)
        included = {inc["id"]: inc for inc in data.get("included") or [] if inc.get("id")}
        yield (data.get("data") or []), included
        url = ((data.get("links") or {}).get("next") or {}).get("href")


def _legacy_gallery_files(item: dict[str, Any], included: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """The ``(absolute url, alt text)`` of a node's gallery files, in the order Drupal shows them.

    A reference whose file entity the feed did not include is logged and skipped: the row can
    only be made from a URL, and the next run sees the reference again.
    """
    refs = ((item.get("relationships") or {}).get("field_additional_images") or {}).get("data") or []
    files: list[tuple[str, str]] = []
    for ref in refs:
        entity = included.get(ref.get("id") or "")
        path = (((entity or {}).get("attributes") or {}).get("uri") or {}).get("url") or ""
        if not path:
            logger.warning(
                "Legacy gallery: node %s references file %s the feed did not include", item.get("id"), ref.get("id")
            )
            continue
        files.append((urljoin(LEGACY_CMS_BASE, path), (ref.get("meta") or {}).get("alt") or ""))
    return files


def _fetch_legacy_file(url: str) -> bytes:
    """Download one file from the legacy host. Raises ``ValueError`` for any other host."""
    if not url.startswith(LEGACY_FILE_PREFIX):
        raise ValueError(f"untrusted URL: {url}")
    with urllib.request.urlopen(url, timeout=15) as resp:
        return resp.read()


def _store_legacy_gallery_file(url: str) -> str:
    """Download a legacy gallery file and store it normalized under a content-addressed key.

    A file that is not an image we can read is stored as its original bytes under its own
    extension, so the photo is kept rather than lost and nothing claims to be a JPEG.
    """
    from PIL import UnidentifiedImageError

    from classes.models import CLASS_IMAGE_PREFIX
    from core.images import normalize_image, store_content_addressed

    content = _fetch_legacy_file(url)
    filename = Path(url).name
    ext = "jpg"
    try:
        data = normalize_image(
            ContentFile(content, name=filename), max_long_edge=settings.IMAGE_MAX_LONG_EDGE_GALLERY
        ).read()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("Legacy gallery: could not normalize %s (%s); keeping the original bytes", filename, exc)
        data = content
        ext = (Path(filename).suffix.lstrip(".").lower() or "bin")[:10]
    return store_content_addressed(data, prefix=CLASS_IMAGE_PREFIX, ext=ext)


def _import_offering_gallery(
    offering: Any, files: list[tuple[str, str]], keys_by_url: dict[str, str]
) -> tuple[int, int, int, int, int]:
    """Add ``files`` to one offering's gallery; returns ``(created, downloaded, reused, over_cap, failed)``.

    ``keys_by_url`` is the run's memory of stored keys, read and extended here so a file two
    classes share is fetched once per run.
    """
    from classes.models import MAX_GALLERY_IMAGES, ClassImage

    created = downloaded = reused = over_cap = failed = 0
    existing = list(offering.gallery_images.all())
    held = {row.legacy_source_url for row in existing if row.legacy_source_url}
    slots = MAX_GALLERY_IMAGES - len(existing)
    next_sort = max((row.sort_order for row in existing), default=-1) + 1
    for url, alt in files:
        if url in held:
            continue
        if slots <= 0:
            over_cap += 1
            continue
        key = keys_by_url.get(url)
        if key is None:
            key = (
                ClassImage.objects.filter(legacy_source_url=url)
                .exclude(image="")
                .values_list("image", flat=True)
                .first()
                or ""
            )
        if key:
            reused += 1
        else:
            try:
                key = _store_legacy_gallery_file(url)
            except (OSError, ValueError) as exc:
                logger.warning("Legacy gallery: %s for offering %s: %s", url, offering.pk, exc)
                failed += 1
                continue
            downloaded += 1
        keys_by_url[url] = key
        ClassImage.objects.create(
            class_offering=offering, image=key, alt_text=alt[:255], sort_order=next_sort, legacy_source_url=url
        )
        held.add(url)
        next_sort += 1
        slots -= 1
        created += 1
    return created, downloaded, reused, over_cap, failed


def sync_legacy_gallery() -> GalleryImportResult:
    """Copy each legacy class's gallery photos into its offering's gallery.

    The nightly catalog sync carries only the hero (``legacy_image_url``); the gallery lives in
    Drupal's ``field_additional_images``. This walks the feed with those files included and adds
    a ``ClassImage`` row per file to the offering with the matching ``legacy_cms_id``, in Drupal's
    order, after whatever the gallery already holds.

    Idempotent: a row remembers its source in ``legacy_source_url``, so a file the class already
    holds is never added twice, and a file any class already imported is re-used from storage
    without a download (the key is content-addressed, so one stored object serves every class
    that shares the picture). Uploads made here are left alone; when they fill the gallery to
    ``MAX_GALLERY_IMAGES`` the legacy files past the cap are counted, not added.

    Returns:
        The run's counts.

    Raises:
        LegacyGalleryImportError: when at least :data:`GALLERY_FAILURE_ABORT_FRACTION` of the
            attempted downloads failed. Everything that succeeded is already saved.
    """
    from classes.models import ClassOffering

    created = downloaded = reused = over_cap = unmatched = failed = 0
    keys_by_url: dict[str, str] = {}

    for items, included in _iter_legacy_pages(LEGACY_CMS_GALLERY_API_URL):
        wanted = {item["id"]: _legacy_gallery_files(item, included) for item in items if item.get("id")}
        wanted = {node_id: files for node_id, files in wanted.items() if files}
        offerings = {
            offering.legacy_cms_id: offering
            for offering in ClassOffering.objects.filter(legacy_cms_id__in=list(wanted)).prefetch_related(
                "gallery_images"
            )
        }
        for node_id, files in wanted.items():
            offering = offerings.get(node_id)
            if offering is None:
                unmatched += 1
                continue
            counts = _import_offering_gallery(offering, files, keys_by_url)
            created, downloaded, reused, over_cap, failed = (
                created + counts[0],
                downloaded + counts[1],
                reused + counts[2],
                over_cap + counts[3],
                failed + counts[4],
            )

    result = GalleryImportResult(
        created=created, downloaded=downloaded, reused=reused, over_cap=over_cap, unmatched=unmatched, failed=failed
    )
    attempted = downloaded + failed
    if failed and failed >= attempted * GALLERY_FAILURE_ABORT_FRACTION:
        raise LegacyGalleryImportError(
            f"{failed} of {attempted} legacy gallery download(s) failed, at or above the "
            f"{GALLERY_FAILURE_ABORT_FRACTION:.0%} ceiling. This usually means the legacy host is unreachable "
            f"or storage is misconfigured, not that the photos are gone. {result.summary()}"
        )
    return result
