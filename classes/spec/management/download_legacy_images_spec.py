"""Specs for classes/management/commands/download_legacy_images.py."""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from classes.factories import ClassOfferingFactory

pytestmark = pytest.mark.django_db


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

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"fake-image-bytes"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        out = StringIO()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            call_command("download_legacy_images", stdout=out)

        offering.refresh_from_db()
        assert offering.legacy_image_url == ""
        assert "Downloaded 1" in out.getvalue()

    def it_skips_untrusted_urls():
        ClassOfferingFactory(
            legacy_image_url="https://evil.example.com/img.jpg",
            image="",
        )
        err = StringIO()
        call_command("download_legacy_images", stderr=err)
        assert "untrusted" in err.getvalue().lower()

    def it_reports_failures_separately():
        ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/notfound.jpg",
            image="",
        )

        err = StringIO()
        out = StringIO()
        with patch("urllib.request.urlopen", side_effect=ValueError("Not found")):
            call_command("download_legacy_images", stdout=out, stderr=err)

        # Should still report 0 downloaded, 1 failed
        assert "Downloaded 0" in out.getvalue()
        assert "Failed" in err.getvalue()

    def it_saves_image_with_original_filename():
        offering = ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/mycustom.png",
            image="",
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"fake-image-bytes"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            call_command("download_legacy_images", stdout=StringIO())

        offering.refresh_from_db()
        # Image should be saved with a name based on the original filename
        assert offering.image.name
        assert "mycustom" in offering.image.name

    def it_skips_offerings_that_already_have_images():
        ClassOfferingFactory(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/img.jpg",
            image="existing/image.jpg",  # Already has an image
        )
        out = StringIO()
        call_command("download_legacy_images", stdout=out)
        assert "No offerings" in out.getvalue()

    def it_skips_offerings_with_empty_legacy_image_url():
        ClassOfferingFactory(
            legacy_image_url="",  # No legacy URL
            image="",
        )
        out = StringIO()
        call_command("download_legacy_images", stdout=out)
        assert "No offerings" in out.getvalue()
