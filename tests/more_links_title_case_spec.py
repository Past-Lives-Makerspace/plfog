"""The arrow links that close a card are Title Case (FRONTEND.md rule 22).

"See past polls →" reached Home in #925 because nothing checked it. Every anchor whose text ends in "→" in a
template must capitalize each word but the small ones, which stay lowercase unless first.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMALL = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "at", "by", "with", "as", "per", "vs"}
ANCHOR = re.compile(r"<a\b[^>]*>(.*?)</a>", re.S)
# Inside an anchor: a template variable reads as one capitalized word, a template tag or an element splits the text.
VARIABLE = re.compile(r"\{\{.*?\}\}", re.S)
SPLIT = re.compile(r"\{%.*?%\}|\{#.*?#\}|<[^>]+>", re.S)
ARROW = re.compile(r"([^\n]*?)\s*(?:→|&rarr;)\s*$")


def arrow_labels(html: str) -> list[str]:
    """The label of every anchor whose text, or a piece of it, ends in an arrow."""
    labels = []
    for anchor in ANCHOR.finditer(html):
        inner = SPLIT.sub("\n", VARIABLE.sub("Value", anchor.group(1)))
        for piece in inner.split("\n"):
            match = ARROW.match(piece.strip())
            if match and match.group(1).strip():
                labels.append(match.group(1).strip())
    return labels


def _lowercase_words(text: str) -> list[str]:
    words = text.split()
    return [w for i, w in enumerate(words) if w[:1].islower() and (i == 0 or w not in SMALL)]


def describe_arrow_links():
    def it_finds_some_to_check():
        found = [label for path in (ROOT / "templates").rglob("*.html") for label in arrow_labels(path.read_text())]

        assert len(found) > 3

    def it_keeps_every_one_in_title_case():
        offenders = [
            f"{path.relative_to(ROOT)}: {label!r}"
            for path in (ROOT / "templates").rglob("*.html")
            for label in arrow_labels(path.read_text())
            if _lowercase_words(label)
        ]

        assert offenders == []

    def it_reads_through_template_variables_and_child_elements():
        html = '<a href="#">See all {{ n }} &rarr;</a> <a href="#">\n  <span>Name</span>\n  <span>Open list &rarr;</span>\n</a>'

        assert arrow_labels(html) == ["See all Value", "Open list"]

    def it_lets_small_words_stay_lowercase():
        assert _lowercase_words("Open the Class Queue") == []
        assert _lowercase_words("See past polls") == ["past", "polls"]
