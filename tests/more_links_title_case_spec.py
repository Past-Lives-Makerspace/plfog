"""The arrow links that close a card are Title Case (FRONTEND.md rule 22).

"See past polls →" reached Home in #925 because nothing checked it. Every anchor whose text ends in "→" in a
template must capitalize each word but the small ones, which stay lowercase unless first.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMALL = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "at", "by", "with", "as", "per", "vs"}
LINK = re.compile(r"<a\b[^>]*>([^<{]*?)\s*(?:→|&rarr;)\s*</a>")


def _lowercase_words(text: str) -> list[str]:
    words = text.split()
    return [w for i, w in enumerate(words) if w[:1].islower() and (i == 0 or w not in SMALL)]


def describe_arrow_links():
    def it_finds_some_to_check():
        found = [m for path in (ROOT / "templates").rglob("*.html") for m in LINK.finditer(path.read_text())]

        assert len(found) > 3

    def it_keeps_every_one_in_title_case():
        offenders = [
            f"{path.relative_to(ROOT)}: {m.group(1).strip()!r}"
            for path in (ROOT / "templates").rglob("*.html")
            for m in LINK.finditer(path.read_text())
            if _lowercase_words(m.group(1))
        ]

        assert offenders == []

    def it_lets_small_words_stay_lowercase():
        assert _lowercase_words("Open the Class Queue") == []
        assert _lowercase_words("See past polls") == ["past", "polls"]
