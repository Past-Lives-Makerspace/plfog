"""Import service — syncs ClassOffering records from classes.pastlives.space."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import TYPE_CHECKING, Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags
from django.utils.text import slugify

if TYPE_CHECKING:
    from classes.models import Category, Instructor

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


def _find_instructor(name: str) -> "Instructor | None":
    from classes.models import Instructor

    return Instructor.objects.filter(display_name__icontains=name, is_active=True).first()


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

    node_id: str = item.get("id") or ""
    if not node_id:
        return None

    attrs = item.get("attributes") or {}

    title = attrs.get("title") or "(Untitled)"
    body = attrs.get("body") or {}
    description = strip_tags(body.get("processed") or body.get("value") or "")
    price_cents = int(float(attrs.get("field_price") or "0") * 100)
    capacity = attrs.get("field_max_students") or 0
    status = ClassOffering.Status.PUBLISHED if attrs.get("status") else ClassOffering.Status.ARCHIVED
    image_url = _get_image_url(item)
    class_type = attrs.get("field_class_type") or "class"
    category = _get_or_create_category(class_type)

    path_alias: str = (attrs.get("path") or {}).get("alias") or ""
    raw_slug = path_alias.replace("/class/", "").strip("/") or node_id[:20]

    offering, created = ClassOffering.objects.update_or_create(
        legacy_cms_id=node_id,
        defaults={
            "title": title,
            "description": description,
            "price_cents": price_cents,
            "capacity": capacity,
            "status": status,
            "category": category,
        },
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
    _sync_sessions(offering, attrs.get("field_dates") or [])
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

    config = SiteConfiguration.load()
    config.legacy_cms_last_synced_at = now
    config.save(update_fields=["legacy_cms_last_synced_at"])

    return len(seen_ids)
