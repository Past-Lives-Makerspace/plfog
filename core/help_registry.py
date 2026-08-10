"""The help-key registry — the in-code spine of the help center.

Every hoverable Info View target, guided-tour step, and help deep link resolves
through this module. Keys are ``<area>.<action-slug>`` (lowercase, dots and
hyphens only — ``KEY_PATTERN`` is THE regex; companion specs import it rather
than restating their own). Entries may land before their articles ship:
``url_for`` degrades to ``/help/`` until the article is seeded and published.

Cross-app by design (hub renders it, core serves it as JSON, tours reference
it), so it lives in ``core`` — but the article lookup imports ``membership``
lazily to keep ``core`` import-clean.
"""

from __future__ import annotations

import re
from typing import TypedDict


class HelpKeyEntry(TypedDict):
    """One registry entry — the contract Specs B/C/D consume."""

    title: str  # short human label, e.g. "Rank your top 3"
    short_text: str  # 1-2 plain sentences for the Info View hover panel (<= 200 chars)
    article_slug: str | None  # WikiArticle.slug the key deep-links into; None = annotation-only key
    anchor: str | None  # heading id inside that article ([a-z0-9-]+; convention: key with "." -> "-");
    #   None when article_slug is None


HELP_KEYS: dict[str, HelpKeyEntry] = {
    "voting.rank-guilds": {
        "title": "Rank your top 3",
        "short_text": (
            "Pick your 1st, 2nd, and 3rd choice guilds. Your ballot sticks and counts every month until you change it."
        ),
        "article_slug": "guild-voting",
        "anchor": "voting-rank-guilds",
    },
    "orientation.book-slot": {
        "title": "Book an orientation",
        "short_text": (
            "Pick an open slot to get oriented on a guild's space and tools. "
            "Your booking counts once a guild lead confirms it."
        ),
        "article_slug": "getting-oriented",
        "anchor": "orientation-book-slot",
    },
    "teach.create-class": {
        "title": "Create a class",
        "short_text": (
            "Any active member can draft a class and submit it. A guild lead or admin reviews it before it goes live."
        ),
        "article_slug": "become-an-instructor",
        "anchor": "teach-create-class",
    },
    "guild.manage-staff": {
        "title": "Manage guild staff",
        "short_text": (
            "Add co-leads, secretaries, treasurers, and orienters on the Staff tab. "
            "Every staff role carries full lead authority."
        ),
        "article_slug": "guild-staff-roles",
        "anchor": "guild-manage-staff",
    },
    "calendar.subscribe": {
        "title": "Subscribe to the calendar",
        "short_text": (
            "Add the community calendar to your own calendar app with the .ics link. "
            "New events show up there automatically."
        ),
        "article_slug": "community-calendar",
        "anchor": "calendar-subscribe",
    },
    "directory.search-filter": {
        "title": "Search the directory",
        "short_text": (
            "Search the member directory by name or skill to find other members. "
            "Each member controls what their profile shows."
        ),
        "article_slug": "member-directory",
        "anchor": "directory-search-filter",
    },
}

# THE key regex — Specs B/C import this instead of restating their own.
KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.[a-z0-9]+(?:-[a-z0-9]+)*$")


def entry(key: str) -> HelpKeyEntry:
    """Return the registry entry for ``key``. Unknown key raises ``KeyError`` (fail loudly)."""
    return HELP_KEYS[key]


def url_for(key: str) -> str:
    """Resolve ``key`` to the URL a "Learn more" link should point at.

    Returns the article's canonical URL plus ``#anchor`` when the article exists
    and is published. Falls back to ``/help/`` for annotation-only entries
    (``article_slug is None``) and for entries whose article isn't seeded or
    published yet (§5.1's no-deadlock rule) — resolved lazily at call time.
    """
    key_entry = entry(key)
    article_slug = key_entry["article_slug"]
    if article_slug is None:
        return "/help/"
    # Lazy import: core must not import membership at module load (circular-import guard).
    from membership.models import WikiArticle

    article = WikiArticle.objects.published().filter(slug=article_slug).select_related("category").first()
    if article is None:
        return "/help/"
    category_segment = article.category.slug if article.category else "more"
    return f"/help/{category_segment}/{article_slug}/#{key_entry['anchor']}"
