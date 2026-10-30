"""Template ↔ registry integrity for guidance keys (#1007, part 3), as ``help_keys_spec`` holds help keys.

Every key a template hands ``{% new_badge %}`` or ``{% whats_new_button %}``, or a partial through
its ``new_key=`` parameter (``_sidebar_feature_link.html``), must be a feature in
``core.guidance.GUIDANCE`` or an area with one under it; a typo'd key fails CI here instead of
logging and rendering nothing in production. One walk of ``templates/`` covers everything.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.guidance import registered

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
TAG_KEY = re.compile(r'\{%\s*(?:new_badge|whats_new_button)\s+"([^"]*)"')
PARAM_KEY = re.compile(r'\bnew_key="([^"{}]*)"')


def _referenced_keys() -> list[tuple[str, str]]:
    """Every (template-relative-path, key) pair a template names."""
    return [
        (str(path.relative_to(TEMPLATES_DIR)), key)
        for path in sorted(TEMPLATES_DIR.rglob("*.html"))
        for text in [path.read_text(encoding="utf-8")]
        for key in TAG_KEY.findall(text) + PARAM_KEY.findall(text)
    ]


def describe_template_guidance_keys():
    def it_finds_the_part_3_placements():
        # The walk itself must be alive: zero hits would mean the regexes or the root went stale.
        assert len(_referenced_keys()) >= 30

    def it_only_references_registered_keys_or_areas_of_them():
        orphans = [f"{path}: {key!r}" for path, key in _referenced_keys() if not registered(key)]
        assert not orphans, "guidance keys missing from core.guidance.GUIDANCE:\n  " + "\n  ".join(orphans)
