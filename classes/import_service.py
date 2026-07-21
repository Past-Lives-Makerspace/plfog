"""Import service — syncs ClassOffering records from classes.pastlives.space."""

from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from typing import TYPE_CHECKING, Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags
from django.utils.text import slugify

if TYPE_CHECKING:
    from classes.models import Category
    from membership.models import Member

LEGACY_CMS_BASE = "https://classes.pastlives.space"
LEGACY_CMS_API_URL = f"{LEGACY_CMS_BASE}/jsonapi/node/class"

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
    from django.db.models import Q

    from membership.models import Member

    return Member.objects.filter(
        Q(preferred_name__icontains=name) | Q(full_legal_name__icontains=name),
        status=Member.Status.ACTIVE,
    ).first()


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

    if image_url and not offering.image:
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

    # Archive offerings no longer present in the API
    ClassOffering.objects.filter(legacy_cms_id__gt="").exclude(legacy_cms_id__in=seen_ids).update(
        status=ClassOffering.Status.ARCHIVED
    )

    # Sanitize: collapse the same class posted on many dates into one catalog group.
    from classes.grouping import regroup_offerings

    regroup_offerings()

    config = SiteConfiguration.load()
    config.legacy_cms_last_synced_at = now
    config.legacy_cms_last_sync_duration = time.monotonic() - started
    config.save(update_fields=["legacy_cms_last_synced_at", "legacy_cms_last_sync_duration"])

    return len(seen_ids)
