#!/usr/bin/env python3
"""Fail if a Django template puts an inline <style> block inside an extra_head block.

Why this lint exists: hx-boost swaps <body>, not <head>. Without the head-support
extension, page-specific <style> tags placed inside `{% block extra_head %}`
silently disappear when a user navigates via a boosted link — the CSS never
re-renders. We *do* now load the head-support extension, but the safer pattern
is to keep page-specific CSS in static files referenced by <link>, so this
class of bug can't recur even if the extension is ever removed.

Allowed: `<link rel="stylesheet" href="...">` in extra_head blocks.
Disallowed: `<style>...</style>` blocks in extra_head blocks.

A baseline of pre-existing offenders is grandfathered in BASELINE below — the
lint only fails when *new* violations appear. As you migrate baseline files to
external CSS, delete them from BASELINE; the lint will then block regressions.

Run: python scripts/check_no_inline_style_in_extra_head.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BLOCK_RE = re.compile(
    r"\{%\s*block\s+(\w*extra_head)\s*%\}(?P<body>.*?)\{%\s*endblock\s*(?:\w+)?\s*%\}",
    re.DOTALL,
)
INLINE_STYLE_RE = re.compile(r"<style[\s>]", re.IGNORECASE)

# Templates that contain inline <style> in extra_head and are grandfathered until migrated.
# DO NOT add to this list. The right fix is to move the styles into static/css/*.css and
# reference them with a <link> tag — see templates/classes/public/register.html for the pattern.
BASELINE: frozenset[str] = frozenset(
    {
        "templates/hub/admin/members.html",
        "templates/hub/admin/site_settings.html",
        "templates/hub/community_calendar.html",
    }
)


def find_violations(template_root: Path) -> list[tuple[Path, str, int]]:
    """Return (file, block_name, line) tuples for any inline <style> in extra_head blocks."""
    violations: list[tuple[Path, str, int]] = []
    for path in sorted(template_root.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in BLOCK_RE.finditer(text):
            block_body = match.group("body")
            style_match = INLINE_STYLE_RE.search(block_body)
            if style_match is None:
                continue
            absolute_offset = match.start("body") + style_match.start()
            line_number = text.count("\n", 0, absolute_offset) + 1
            violations.append((path, match.group(1), line_number))
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    template_root = repo_root / "templates"
    if not template_root.exists():
        print(f"templates/ not found under {repo_root}", file=sys.stderr)
        return 2

    all_violations = find_violations(template_root)
    new_violations: list[tuple[Path, str, int]] = []
    seen_baseline_files: set[str] = set()

    for path, block_name, line in all_violations:
        rel = str(path.relative_to(repo_root))
        if rel in BASELINE:
            seen_baseline_files.add(rel)
            continue
        new_violations.append((path, block_name, line))

    stale_baseline = BASELINE - seen_baseline_files

    failed = False

    if new_violations:
        failed = True
        print("New inline <style> blocks inside extra_head blocks (NOT allowed):")
        print()
        for path, block_name, line in new_violations:
            rel = str(path.relative_to(repo_root))
            print(f"  {rel}:{line}  inside `{{% block {block_name} %}}`")
        print()
        print(
            "Move these styles into a static .css file and reference them with\n"
            '  <link rel="stylesheet" href="{% static \'css/your-file.css\' %}">.\n'
            "See templates/classes/public/register.html for the pattern."
        )

    if stale_baseline:
        failed = True
        print()
        print("Stale baseline entries (file no longer contains inline styles — please remove from BASELINE):")
        for entry in sorted(stale_baseline):
            print(f"  {entry}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
