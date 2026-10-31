"""Fragment ↔ template integrity for guidance targets (#1051), as ``help_keys_spec`` holds help keys.

A fragment's ``target`` (and a registry default) is a promise that the same PR placed
``data-guidance-target="<key>"`` on the control: Open <feature> lands on ``?show=<key>`` and
``guidance.js`` rings whatever carries it, silently doing nothing when nothing does. So every
target in ``changelog.d/``, in ``changelog/history.json`` and in ``core.guidance.GUIDANCE`` must
be a ``KEY_PATTERN`` key some template under ``templates/`` carries, and every key a template
carries must be in the grammar; a typo on either side fails CI here instead of ringing nothing in
production. One walk of ``templates/`` covers everything.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from core.guidance import GUIDANCE
from core.help_registry import KEY_PATTERN

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
# Literal keys only, as help_keys_spec has it: a brace-bearing value would be a parameter, and no
# template passes one today.
TARGET_ATTR = re.compile(r'data-guidance-target="([^"{}]*)"')


def _template_targets() -> list[tuple[str, str]]:
    """Every (template-relative-path, key) pair a template carries."""
    return [
        (str(path.relative_to(TEMPLATES_DIR)), key)
        for path in sorted(TEMPLATES_DIR.rglob("*.html"))
        for text in [path.read_text(encoding="utf-8")]
        for key in TARGET_ATTR.findall(text)
    ]


def _declared_targets() -> list[tuple[str, str]]:
    """Every (where, key) pair a fragment, a frozen entry or the registry names."""
    found = [
        (f"changelog.d/{path.name}", target)
        for path in sorted((REPO_ROOT / "changelog.d").glob("*.toml"))
        for target in [tomllib.loads(path.read_text(encoding="utf-8")).get("target", "")]
        if target
    ]
    history = json.loads((REPO_ROOT / "changelog" / "history.json").read_text(encoding="utf-8"))
    found += [
        (f"changelog/history.json: {entry['title']!r}", entry["target"]) for entry in history if entry.get("target")
    ]
    found += [
        (f"core/guidance.py: {guidance.key}", guidance.target) for guidance in GUIDANCE.values() if guidance.target
    ]
    return found


def describe_guidance_targets():
    def it_finds_the_1051_placement():
        # The walk itself must be alive: zero hits would mean the regex or the root went stale,
        # and the specs below would pass vacuously. The fragment this shipped with names one.
        assert ("hub/partials/_whats_new_button.html", "home.whats-new-button") in _template_targets()
        assert ("changelog.d/1051-open-it-destination.toml", "home.whats-new-button") in _declared_targets()

    def it_holds_every_declared_target_to_a_template():
        carried = {key for _, key in _template_targets()}
        orphans = [f"{where}: {key!r}" for where, key in _declared_targets() if key not in carried]
        assert not orphans, "targets no template carries as data-guidance-target:\n  " + "\n  ".join(orphans)

    def it_uses_the_key_format_contract_on_both_sides():
        # KEY_PATTERN is imported from core.help_registry, never restated here, so the contract
        # (including the exactly-one-dot rule) has a single home.
        bad = [
            f"{where}: {key!r}"
            for where, key in _declared_targets() + _template_targets()
            if not KEY_PATTERN.match(key)
        ]
        assert not bad, "guidance targets violating KEY_PATTERN:\n  " + "\n  ".join(bad)
