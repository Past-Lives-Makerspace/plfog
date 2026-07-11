"""BDD specs for the class QR helpers — public_url + scalable SVG + PNG."""

from __future__ import annotations

import pytest
from django.test import override_settings

from classes.factories import ClassOfferingFactory

pytestmark = pytest.mark.django_db


def describe_class_qr():
    @override_settings(BOOK_BASE_URL="https://book.pastlives.app")
    def it_builds_an_absolute_public_url_on_the_book_host():
        offering = ClassOfferingFactory(slug="intro-to-resin")
        assert offering.public_url.startswith("https://book.pastlives.app")
        assert "intro-to-resin" in offering.public_url

    def it_returns_a_scalable_svg_qr():
        offering = ClassOfferingFactory(slug="intro-to-resin")
        svg = offering.qr_svg()
        assert "<svg" in svg
        assert "viewBox" in svg  # scales to its box, not a fixed tiny size

    def it_builds_a_stable_slug_independent_permalink():
        offering = ClassOfferingFactory(slug="intro-to-resin")
        assert f"/c/{offering.pk}/" in offering.qr_url
        assert "intro-to-resin" not in offering.qr_url  # slug-proof

    def it_encodes_the_stable_permalink_not_the_slug_url():
        from membership.qr import qr_svg

        offering = ClassOfferingFactory(slug="intro-to-resin")
        # The QR encodes the slug-proof permalink so a printed code survives a rename.
        assert offering.qr_svg() == qr_svg(offering.qr_url)
        assert offering.qr_svg() != qr_svg(offering.public_url)

    def it_returns_png_bytes_with_the_magic_header():
        offering = ClassOfferingFactory(slug="intro-to-resin")
        png = offering.qr_png_bytes()
        assert isinstance(png, bytes)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")

    def it_differs_for_a_different_class():
        one = ClassOfferingFactory(slug="one")
        two = ClassOfferingFactory(slug="two")
        assert one.qr_svg() != two.qr_svg()
