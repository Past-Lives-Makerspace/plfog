"""Shared QR-code rendering for the guild, signage-zone, and slideshow surfaces.

One helper so every QR is generated the same way — and, importantly, scales to fill
its container instead of sitting tiny in a fixed white box.
"""

from __future__ import annotations

import io
import re

import segno

# segno's SVG writer stamps a fixed pixel ``width``/``height`` (e.g. ``width="37"``) and
# NO ``viewBox``, so the QR always draws at its intrinsic ~37px however large its container
# is — the "tiny QR in a big white box" bug. Swapping that fixed size for an equal-extent
# ``viewBox`` lets the SVG scale to its box (every QR container styles ``svg { width/height: 100% }``).
# Match each dimension independently (order- and whitespace-tolerant) so a future segno
# formatting change degrades to the untouched SVG rather than silently reverting to fixed size.
_QR_WIDTH = re.compile(r'\s+width="(\d+)"')
_QR_HEIGHT = re.compile(r'\s+height="(\d+)"')


def qr_svg(url: str) -> str:
    """Return inline, CSS-scalable SVG markup for a QR code of ``url``.

    Args:
        url: The absolute URL the QR encodes.

    Returns:
        SVG markup carrying a ``viewBox`` and no fixed pixel size, so it scales to
        whatever box its CSS gives it. Drop into a template with ``|safe``.
    """
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=1, xmldecl=False, svgns=True)
    svg = buf.getvalue().decode("utf-8")
    width = _QR_WIDTH.search(svg)
    height = _QR_HEIGHT.search(svg)
    if width is None or height is None:
        return svg  # unexpected segno output — leave it untouched rather than mangle it
    svg = _QR_HEIGHT.sub("", _QR_WIDTH.sub("", svg, count=1), count=1)
    return svg.replace("<svg", f'<svg viewBox="0 0 {width.group(1)} {height.group(1)}"', 1)


def qr_png_bytes(url: str) -> bytes:
    """PNG bytes of a QR code for ``url`` (segno's native writer — no Pillow).

    A raster download for print/handout, alongside the scalable SVG from :func:`qr_svg`.
    """
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=10, border=2)
    return buf.getvalue()
