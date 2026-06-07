"""Import service — syncs ClassOffering records from classes.pastlives.space."""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from typing import Any

from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify

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


def _get_or_create_category(class_type: str) -> Any:
    from classes.models import Category

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


def _find_instructor(name: str) -> Any | None:
    from classes.models import Instructor

    return Instructor.objects.filter(display_name__icontains=name, is_active=True).first()


def sync_legacy_cms() -> int:
    """Sync ClassOffering records from classes.pastlives.space.

    Upserts offerings keyed on Drupal node UUID. Always syncs core fields (title,
    description, price, capacity, status, sessions, image URL). Never overwrites
    locally-set fields (slug after first import, instructor once set).

    Returns:
        Number of offerings upserted.
    """
    from classes.models import ClassOffering, ClassSession
    from core.models import SiteConfiguration

    now = timezone.now()
    seen_ids: list[str] = []

    next_url: str | None = LEGACY_CMS_API_URL
    while next_url:
        data = _fetch_json(next_url)

        for item in data.get("data") or []:
            node_id: str = item.get("id") or ""
            if not node_id:
                continue

            attrs = item.get("attributes") or {}

            title = attrs.get("title") or "(Untitled)"
            body = attrs.get("body") or {}
            description = strip_tags(body.get("processed") or body.get("value") or "")
            price_cents = int(float(attrs.get("field_price") or "0") * 100)
            capacity = attrs.get("field_max_students") or 0
            status = (
                ClassOffering.Status.PUBLISHED if attrs.get("status") else ClassOffering.Status.ARCHIVED
            )
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

            # Slug: set only on first import
            if created:
                slug = raw_slug
                if ClassOffering.objects.filter(slug=slug).exclude(pk=offering.pk).exists():
                    slug = f"{slug}-legacy"
                offering.slug = slug

            # Legacy image: only set when no real image is uploaded
            if image_url and not offering.image:
                offering.legacy_image_url = image_url

            # Instructor: only set when not already assigned
            if not offering.instructor_id:
                name = extract_instructor_name(title)
                if name:
                    instructor = _find_instructor(name)
                    if instructor:
                        offering.instructor = instructor

            offering.save()

            # Sessions: full replace
            ClassSession.objects.filter(class_offering=offering).delete()
            for i, session in enumerate(attrs.get("field_dates") or []):
                start_str = session.get("value")
                end_str = session.get("end_value") or start_str
                if not start_str:
                    continue
                ClassSession.objects.create(
                    class_offering=offering,
                    starts_at=datetime.fromisoformat(start_str),
                    ends_at=datetime.fromisoformat(end_str),
                    sort_order=i,
                )

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
