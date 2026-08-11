"""Template ↔ registry integrity for Info View help keys (Spec B §9).

Every ``data-help-key`` stamped in a template must resolve in Spec A's
``HELP_KEYS`` and obey its key grammar — a typo'd or orphaned key fails CI
here instead of silently no-op'ing in production. All app templates live under
the single ``templates/`` root (the teach pages are ``templates/classes/…``),
so one walk covers everything.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.help_registry import HELP_KEYS, KEY_PATTERN

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
HELP_KEY_ATTR = re.compile(r'data-help-key="([^"]*)"')


def _referenced_keys() -> list[tuple[str, str]]:
    """Every (template-relative-path, key) pair stamped under templates/."""
    return [
        (str(path.relative_to(TEMPLATES_DIR)), key)
        for path in sorted(TEMPLATES_DIR.rglob("*.html"))
        for key in HELP_KEY_ATTR.findall(path.read_text(encoding="utf-8"))
    ]


def describe_template_help_keys():
    def it_finds_the_phase_1_annotations():
        # The walk itself must be alive — zero hits would mean the regex or the
        # root went stale, and the two specs below would pass vacuously.
        assert len(_referenced_keys()) >= 30

    def it_only_references_registered_keys():
        orphans = [f"{path}: {key!r}" for path, key in _referenced_keys() if key not in HELP_KEYS]
        assert not orphans, "data-help-key values missing from core.help_registry.HELP_KEYS:\n  " + "\n  ".join(orphans)

    def it_uses_the_key_format_contract():
        # KEY_PATTERN is imported from core.help_registry — never restated here —
        # so the contract (including the exactly-one-dot rule) has a single home.
        bad = [f"{path}: {key!r}" for path, key in _referenced_keys() if not KEY_PATTERN.match(key)]
        assert not bad, "data-help-key values violating KEY_PATTERN:\n  " + "\n  ".join(bad)
