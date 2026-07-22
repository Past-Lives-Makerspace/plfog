"""Specs for classes/management/commands/download_legacy_images.py."""

from __future__ import annotations

import io
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.management.commands.download_legacy_images import legacy_image_filename
from classes.models import CLASS_IMAGE_PREFIX, ClassOffering
from core.images import is_content_addressed

pytestmark = pytest.mark.django_db


def _jpeg_bytes(size: tuple[int, int] = (4000, 3000)) -> bytes:
    """A real JPEG, oversized so normalization has something to do."""
    img = Image.new("RGB", size)
    pixels = img.load()
    for y in range(0, size[1], 7):
        for x in range(0, size[0], 7):
            pixels[x, y] = ((x * 7) % 256, (y * 13) % 256, (x + y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=100)
    return buf.getvalue()


def _mock_response(content: bytes) -> MagicMock:
    mock = MagicMock()
    mock.read.return_value = content
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def describe_legacy_image_filename():
    def it_percent_decodes_the_path_and_drops_the_query():
        url = "https://classes.pastlives.space/sites/default/files/Closeup%20Hands%202_11.JPG?itok=abc"

        assert legacy_image_filename(url) == "Closeup Hands 2_11.JPG"


def describe_download_legacy_images_command():
    def it_prints_no_pending_message_when_no_offerings():
        out = StringIO()
        call_command("download_legacy_images", stdout=out)
        assert "No offerings" in out.getvalue()

    def it_downloads_image_and_clears_legacy_url():
        offering = ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/img.jpg",
            image="",
        )

        out = StringIO()
        with patch("urllib.request.urlopen", return_value=_mock_response(_jpeg_bytes((400, 300)))):
            call_command("download_legacy_images", stdout=out, stderr=StringIO())

        offering.refresh_from_db()
        assert offering.legacy_image_url == ""
        assert offering.image.name
        assert "Downloaded 1" in out.getvalue()

    def it_normalizes_the_downloaded_image():
        raw = _jpeg_bytes()
        offering = ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/huge.jpg",
            image="",
        )

        with patch("urllib.request.urlopen", return_value=_mock_response(raw)):
            call_command("download_legacy_images", stdout=StringIO(), stderr=StringIO())

        offering.refresh_from_db()
        assert default_storage.size(offering.image.name) < len(raw)
        with default_storage.open(offering.image.name, "rb") as handle:
            assert max(Image.open(handle).size) <= 2400

    def it_stores_the_image_under_a_content_addressed_key():
        offering = ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/mycustom.png",
            image="",
        )

        with patch("urllib.request.urlopen", return_value=_mock_response(_jpeg_bytes((400, 300)))):
            call_command("download_legacy_images", stdout=StringIO(), stderr=StringIO())

        offering.refresh_from_db()
        assert is_content_addressed(offering.image.name, prefix=CLASS_IMAGE_PREFIX)

    def it_stores_one_object_for_offerings_that_share_a_legacy_url():
        shared_url = "https://classes.pastlives.space/sites/default/files/2024-03/pattern.jpeg"
        first = ClassOfferingFactory(legacy_image_url=shared_url, image="")
        second = ClassOfferingFactory(legacy_image_url=shared_url, image="")
        raw = _jpeg_bytes((500, 400))

        out = StringIO()
        with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: _mock_response(raw)):
            call_command("download_legacy_images", stdout=out, stderr=StringIO())

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.image.name == second.image.name
        assert "into 1 stored object(s)" in out.getvalue()

    def it_keeps_the_original_bytes_when_normalization_fails():
        offering = ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/broken.jpg",
            image="",
        )

        err = StringIO()
        with patch("urllib.request.urlopen", return_value=_mock_response(b"not really an image")):
            call_command("download_legacy_images", stdout=StringIO(), stderr=err)

        offering.refresh_from_db()
        assert offering.image.name
        assert "Could not normalize" in err.getvalue()

    def it_skips_untrusted_urls():
        ClassOfferingFactory(legacy_image_url="https://evil.example.com/img.jpg", image="")

        err = StringIO()
        with pytest.raises(CommandError, match="failure ceiling"):
            call_command("download_legacy_images", stdout=StringIO(), stderr=err)

        assert "untrusted" in err.getvalue().lower()

    def it_exits_non_zero_when_most_downloads_fail():
        ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/notfound.jpg",
            image="",
        )

        err = StringIO()
        out = StringIO()
        with (
            patch("urllib.request.urlopen", side_effect=OSError("Not found")),
            pytest.raises(CommandError, match="failure ceiling"),
        ):
            call_command("download_legacy_images", stdout=out, stderr=err)

        assert "Downloaded 0" in out.getvalue()
        assert "Failed for offering" in err.getvalue()

    def it_tolerates_a_minority_of_failures():
        ClassOfferingFactory(legacy_image_url="https://classes.pastlives.space/sites/default/files/a.jpg", image="")
        ClassOfferingFactory(legacy_image_url="https://classes.pastlives.space/sites/default/files/b.jpg", image="")
        ClassOfferingFactory(legacy_image_url="https://evil.example.com/c.jpg", image="")

        out = StringIO()
        with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: _mock_response(_jpeg_bytes((300, 200)))):
            call_command("download_legacy_images", stdout=out, stderr=StringIO())

        assert "Downloaded 2" in out.getvalue()
        assert "1 failed" in out.getvalue()

    def it_skips_offerings_that_already_have_images():
        ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/img.jpg",
            image="existing/image.jpg",
        )
        out = StringIO()
        call_command("download_legacy_images", stdout=out)
        assert "No offerings" in out.getvalue()

    def it_skips_offerings_with_empty_legacy_image_url():
        ClassOfferingFactory(legacy_image_url="", image="")
        out = StringIO()
        call_command("download_legacy_images", stdout=out)
        assert "No offerings" in out.getvalue()

    def it_does_not_re_download_on_a_second_run():
        offering = ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/once.jpg",
            image="",
        )

        with patch("urllib.request.urlopen", return_value=_mock_response(_jpeg_bytes((400, 300)))) as opener:
            call_command("download_legacy_images", stdout=StringIO(), stderr=StringIO())
            assert opener.call_count == 1

            out = StringIO()
            call_command("download_legacy_images", stdout=out, stderr=StringIO())
            assert opener.call_count == 1
            assert "No offerings" in out.getvalue()

        offering.refresh_from_db()
        assert ClassOffering.objects.filter(pk=offering.pk, legacy_image_url="").exists()
