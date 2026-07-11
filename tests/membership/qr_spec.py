"""BDD specs for the shared QR-code SVG helper."""

from __future__ import annotations

import re

from membership.qr import qr_svg


def describe_qr_svg():
    def it_returns_svg_markup():
        svg = qr_svg("https://pastlives.app/g/ceramics")
        assert svg.startswith("<svg")
        assert 'class="qrline"' in svg  # segno's path is present

    def it_scales_to_its_container():
        """The bug this guards: a fixed pixel size with no viewBox draws the QR tiny in a
        big box. The SVG must carry a viewBox and NOT a fixed pixel width/height."""
        svg = qr_svg("https://pastlives.app/g/ceramics")
        assert "viewBox=" in svg
        assert not re.search(r'\swidth="\d+"', svg)
        assert not re.search(r'\sheight="\d+"', svg)

    def it_viewbox_is_square_and_matches_the_module_extent():
        svg = qr_svg("https://pastlives.app/g/ceramics")
        m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
        assert m is not None
        assert m.group(1) == m.group(2)  # QR is square
        assert int(m.group(1)) > 0

    def it_encodes_different_urls_distinctly():
        assert qr_svg("https://pastlives.app/a") != qr_svg("https://pastlives.app/b")
