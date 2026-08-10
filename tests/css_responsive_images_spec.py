"""Repo-wide guard: every page-entry stylesheet carries the global responsive-image reset.

Without `img { max-width: 100% }`, a bare <img> with no scoped CSS class
renders at its native/attribute size and can overflow a mobile viewport —
guild logo SVGs in particular ship with no intrinsic width/height. See
FRONTEND.md and the v0.23.58 fix for the incident this guards against.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC_CSS_DIR = Path(__file__).resolve().parent.parent / "static" / "css"

ENTRY_STYLESHEETS = ["style.css", "hub.css", "cms-public.css"]

_IMG_RESET_RE = re.compile(r"(^|\})\s*img\s*\{[^}]*max-width\s*:\s*100%", re.MULTILINE)


def describe_responsive_image_reset():
    def it_defines_a_global_img_max_width_on_every_entry_stylesheet():
        missing = [
            name
            for name in ENTRY_STYLESHEETS
            if not _IMG_RESET_RE.search((STATIC_CSS_DIR / name).read_text(encoding="utf-8"))
        ]
        assert not missing, (
            "Missing the global `img { max-width: 100% }` reset — a bare <img> with no "
            "scoped CSS class will overflow mobile viewports:\n  " + "\n  ".join(missing)
        )
