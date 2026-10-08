"""Specs for classes.import_service.sync_legacy_gallery."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.storage import default_storage
from PIL import Image

from classes.factories import ClassImageFactory, ClassOfferingFactory
from classes.import_service import (
    LEGACY_CMS_BASE,
    LEGACY_CMS_GALLERY_API_URL,
    LegacyGalleryImportError,
    sync_legacy_gallery,
)
from classes.models import CLASS_IMAGE_PREFIX, MAX_GALLERY_IMAGES, ClassImage, ClassOffering
from core.images import is_content_addressed

pytestmark = pytest.mark.django_db

NEXT_URL = f"{LEGACY_CMS_BASE}/jsonapi/node/class?include=field_additional_images&page%5Boffset%5D=50"


def _jpeg_bytes(size: tuple[int, int] = (300, 200), seed: int = 0) -> bytes:
    """A real JPEG; ``seed`` changes the pixels so two files never share a content key."""
    img = Image.new("RGB", size, color=(seed % 256, (seed * 7) % 256, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _node(node_id: str, files: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """A legacy class node whose gallery references ``files`` as ``(file id, alt)``."""
    return {
        "id": node_id,
        "attributes": {"title": f"Class {node_id}"},
        "relationships": {
            "field_additional_images": {
                "data": [{"type": "file--file", "id": fid, "meta": {"alt": alt}} for fid, alt in (files or [])]
            }
        },
    }


def _file(file_id: str, path: str) -> dict[str, Any]:
    return {"type": "file--file", "id": file_id, "attributes": {"uri": {"url": path}}}


def _page(nodes: list[dict], included: list[dict], next_url: str | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {"data": nodes, "included": included, "links": {}}
    if next_url:
        page["links"]["next"] = {"href": next_url}
    return page


def _response(content: bytes) -> MagicMock:
    mock = MagicMock()
    mock.read.return_value = content
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _opener(pages: dict[str, dict], files: dict[str, bytes]) -> tuple[Any, list[str]]:
    """A fake ``urlopen``: feed pages by URL, file bytes by URL, and the URLs it was asked for."""
    asked: list[str] = []

    def opener(target: Any, timeout: float | None = None) -> MagicMock:
        url = target.full_url if hasattr(target, "full_url") else target
        asked.append(url)
        if url in pages:
            return _response(json.dumps(pages[url]).encode())
        if url in files:
            return _response(files[url])
        raise OSError(f"no such file: {url}")

    return opener, asked


def _legacy(node_id: str, **kwargs):
    """An imported offering with an empty gallery (the factory seeds one photo by default)."""
    return ClassOfferingFactory(legacy_cms_id=node_id, gallery=0, **kwargs)


def _run(pages: dict[str, dict], files: dict[str, bytes]):
    opener, asked = _opener(pages, files)
    with patch("urllib.request.urlopen", side_effect=opener):
        result = sync_legacy_gallery()
    return result, asked


A_PATH = "/sites/default/files/2024-02/a.jpg"
B_PATH = "/sites/default/files/2024-02/b%20two.jpg"
A_URL = f"{LEGACY_CMS_BASE}{A_PATH}"
B_URL = f"{LEGACY_CMS_BASE}{B_PATH}"


def describe_sync_legacy_gallery():
    def it_adds_a_gallery_row_per_legacy_file_in_drupal_order():
        offering = _legacy("node-1")
        pages = {
            LEGACY_CMS_GALLERY_API_URL: _page(
                [_node("node-1", [("f-a", "Hands"), ("f-b", "")])], [_file("f-a", A_PATH), _file("f-b", B_PATH)]
            )
        }

        result, _asked = _run(pages, {A_URL: _jpeg_bytes(seed=1), B_URL: _jpeg_bytes(seed=2)})

        rows = list(offering.gallery_images.all())
        assert [(row.legacy_source_url, row.alt_text, row.sort_order) for row in rows] == [
            (A_URL, "Hands", 0),
            (B_URL, "", 1),
        ]
        assert all(is_content_addressed(row.image.name, prefix=CLASS_IMAGE_PREFIX) for row in rows)
        assert (result.created, result.downloaded, result.reused, result.failed) == (2, 2, 0, 0)
        assert "Added 2 gallery photo(s): 2 downloaded" in result.summary()

    def it_sends_a_class_listed_on_eventbrite_back_for_a_resync_only_when_photos_were_added():
        listed = {"eventbrite_event_id": "ev-9", "eventbrite_sync_state": ClassOffering.EventbriteSyncState.LISTED}
        gaining = _legacy("node-1", **listed)
        unchanged = _legacy("node-2", **listed)
        ClassImageFactory(class_offering=unchanged, legacy_source_url=B_URL)
        pages = {
            LEGACY_CMS_GALLERY_API_URL: _page(
                [_node("node-1", [("f-a", "")]), _node("node-2", [("f-b", "")])],
                [_file("f-a", A_PATH), _file("f-b", B_PATH)],
            )
        }

        _run(pages, {A_URL: _jpeg_bytes(seed=1), B_URL: _jpeg_bytes(seed=2)})

        gaining.refresh_from_db()
        unchanged.refresh_from_db()
        assert gaining.eventbrite_sync_state == ClassOffering.EventbriteSyncState.PENDING
        assert gaining.eventbrite_sync_error == "The gallery changed."
        assert unchanged.eventbrite_sync_state == ClassOffering.EventbriteSyncState.LISTED

    def it_normalizes_each_file_to_the_gallery_ceiling(settings):
        settings.IMAGE_MAX_LONG_EDGE_GALLERY = 120
        offering = _legacy("node-1")
        pages = {LEGACY_CMS_GALLERY_API_URL: _page([_node("node-1", [("f-a", "")])], [_file("f-a", A_PATH)])}

        _run(pages, {A_URL: _jpeg_bytes((400, 300))})

        with default_storage.open(offering.gallery_images.get().image.name, "rb") as handle:
            assert max(Image.open(handle).size) <= 120

    def it_appends_after_the_photos_the_class_already_holds():
        offering = _legacy("node-1")
        ClassImageFactory(class_offering=offering, sort_order=4)
        pages = {LEGACY_CMS_GALLERY_API_URL: _page([_node("node-1", [("f-a", "")])], [_file("f-a", A_PATH)])}

        _run(pages, {A_URL: _jpeg_bytes()})

        assert list(offering.gallery_images.values_list("sort_order", flat=True)) == [4, 5]

    def it_counts_a_legacy_class_with_no_offering_here():
        pages = {LEGACY_CMS_GALLERY_API_URL: _page([_node("node-gone", [("f-a", "")])], [_file("f-a", A_PATH)])}

        result, asked = _run(pages, {A_URL: _jpeg_bytes()})

        assert result.unmatched == 1
        assert A_URL not in asked
        assert ClassImage.objects.count() == 0

    def it_ignores_a_node_with_no_gallery():
        _legacy("node-1")
        pages = {LEGACY_CMS_GALLERY_API_URL: _page([_node("node-1", [])], [])}

        result, _asked = _run(pages, {})

        assert (result.created, result.unmatched, result.failed) == (0, 0, 0)

    def it_is_idempotent_across_runs():
        offering = _legacy("node-1")
        pages = {LEGACY_CMS_GALLERY_API_URL: _page([_node("node-1", [("f-a", "")])], [_file("f-a", A_PATH)])}
        files = {A_URL: _jpeg_bytes()}

        _run(pages, files)
        result, asked = _run(pages, files)

        assert offering.gallery_images.count() == 1
        assert result.created == 0
        assert A_URL not in asked

    def it_downloads_a_file_shared_by_two_classes_once_and_stores_one_object():
        first = _legacy("node-1")
        second = _legacy("node-2")
        pages = {
            LEGACY_CMS_GALLERY_API_URL: _page(
                [_node("node-1", [("f-a", "")]), _node("node-2", [("f-a", "")])], [_file("f-a", A_PATH)]
            )
        }

        result, asked = _run(pages, {A_URL: _jpeg_bytes()})

        assert asked.count(A_URL) == 1
        assert first.gallery_images.get().image.name == second.gallery_images.get().image.name
        assert (result.created, result.downloaded, result.reused) == (2, 1, 1)

    def it_reuses_a_file_another_class_imported_on_an_earlier_run():
        earlier = _legacy("node-1")
        ClassImageFactory(class_offering=earlier, legacy_source_url=A_URL)
        stored = earlier.gallery_images.get().image.name
        later = _legacy("node-2")
        pages = {LEGACY_CMS_GALLERY_API_URL: _page([_node("node-2", [("f-a", "")])], [_file("f-a", A_PATH)])}

        result, asked = _run(pages, {})

        assert A_URL not in asked
        assert later.gallery_images.get().image.name == stored
        assert (result.downloaded, result.reused) == (0, 1)

    def it_stops_at_the_gallery_cap_and_counts_the_rest():
        offering = _legacy("node-1")
        for n in range(MAX_GALLERY_IMAGES - 1):
            ClassImageFactory(class_offering=offering, sort_order=n)
        pages = {
            LEGACY_CMS_GALLERY_API_URL: _page(
                [_node("node-1", [("f-a", ""), ("f-b", "")])], [_file("f-a", A_PATH), _file("f-b", B_PATH)]
            )
        }

        result, asked = _run(pages, {A_URL: _jpeg_bytes(seed=1), B_URL: _jpeg_bytes(seed=2)})

        assert offering.gallery_images.count() == MAX_GALLERY_IMAGES
        assert (result.created, result.over_cap) == (1, 1)
        assert B_URL not in asked

    def it_keeps_the_original_bytes_when_the_file_is_not_an_image_it_can_read():
        offering = _legacy("node-1")
        path = "/sites/default/files/odd.png"
        pages = {LEGACY_CMS_GALLERY_API_URL: _page([_node("node-1", [("f-a", "")])], [_file("f-a", path)])}

        result, _asked = _run(pages, {f"{LEGACY_CMS_BASE}{path}": b"not really an image"})

        row = offering.gallery_images.get()
        assert row.image.name.endswith(".png")
        with default_storage.open(row.image.name, "rb") as handle:
            assert handle.read() == b"not really an image"
        assert result.created == 1

    def it_skips_a_reference_the_feed_did_not_include():
        _legacy("node-1")
        pages = {LEGACY_CMS_GALLERY_API_URL: _page([_node("node-1", [("f-missing", "")])], [])}

        result, _asked = _run(pages, {})

        assert (result.created, result.failed) == (0, 0)

    def it_never_fetches_a_file_off_the_legacy_host():
        offering = _legacy("node-1")
        evil = "https://evil.example.com/x.jpg"
        pages = {
            LEGACY_CMS_GALLERY_API_URL: _page(
                [_node("node-1", [("f-x", ""), ("f-a", ""), ("f-b", "")])],
                [_file("f-x", evil), _file("f-a", A_PATH), _file("f-b", B_PATH)],
            )
        }
        files = {evil: _jpeg_bytes(seed=1), A_URL: _jpeg_bytes(seed=2), B_URL: _jpeg_bytes(seed=3)}

        result, asked = _run(pages, files)

        assert evil not in asked
        assert list(offering.gallery_images.values_list("legacy_source_url", flat=True)) == [A_URL, B_URL]
        assert (result.created, result.failed) == (2, 1)

    def it_raises_when_most_downloads_fail_but_keeps_what_landed():
        offering = _legacy("node-1")
        pages = {
            LEGACY_CMS_GALLERY_API_URL: _page(
                [_node("node-1", [("f-a", ""), ("f-b", "")])], [_file("f-a", A_PATH), _file("f-b", B_PATH)]
            )
        }

        opener, _asked = _opener(pages, {A_URL: _jpeg_bytes()})
        with (
            patch("urllib.request.urlopen", side_effect=opener),
            pytest.raises(LegacyGalleryImportError, match="1 of 2"),
        ):
            sync_legacy_gallery()

        assert list(offering.gallery_images.values_list("legacy_source_url", flat=True)) == [A_URL]

    def it_tolerates_a_minority_of_failures():
        _legacy("node-1")
        paths = [f"/sites/default/files/{n}.jpg" for n in range(3)]
        pages = {
            LEGACY_CMS_GALLERY_API_URL: _page(
                [_node("node-1", [(f"f-{n}", "") for n in range(3)])], [_file(f"f-{n}", p) for n, p in enumerate(paths)]
            )
        }
        files = {f"{LEGACY_CMS_BASE}{p}": _jpeg_bytes(seed=n + 1) for n, p in enumerate(paths[:2])}

        result, _asked = _run(pages, files)

        assert (result.created, result.failed) == (2, 1)

    def it_follows_the_feed_to_its_next_page():
        first = _legacy("node-1")
        second = _legacy("node-2")
        pages = {
            LEGACY_CMS_GALLERY_API_URL: _page(
                [_node("node-1", [("f-a", "")])], [_file("f-a", A_PATH)], next_url=NEXT_URL
            ),
            NEXT_URL: _page([_node("node-2", [("f-b", "")])], [_file("f-b", B_PATH)]),
        }

        result, _asked = _run(pages, {A_URL: _jpeg_bytes(seed=1), B_URL: _jpeg_bytes(seed=2)})

        assert first.gallery_images.count() == 1 and second.gallery_images.count() == 1
        assert result.created == 2
