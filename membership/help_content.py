"""Help-center content data.

Phase 6 fills in ``CATEGORIES`` / ``ARTICLES`` / ``LEGACY_SLUG_MAP`` (the seeded
category table, the 29 article bodies with their screenshot lists, and the old
landing-anchor → new-article map). Until then this module carries only the
constants the views and model logic already need.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class ShotSpec(TypedDict):
    """One screenshot request in an article's ``screenshots`` list (§9).

    The capture harness (``tests/e2e/help_screenshots_spec.py``) reads these to
    regenerate ``static/help/<article-slug>/<file>``; the drift guard
    (``tests/membership/help_content_spec.py``) keeps body images, specs, and
    files on disk in lockstep.
    """

    file: str  # "02-pick-your-guilds.png" → static/help/<article-slug>/02-pick-your-guilds.png
    page: str  # URL name ("hub_guild_voting") or literal path ("/guilds/voting/")
    # CSS selector to crop to — e.g. "[data-help-key='voting.rank-guilds']" once
    # Spec B lands, plain CSS until then. None = framed viewport shot.
    selector: str | None
    caption: str  # becomes the image's alt text in the article body
    as_role: str  # "member" | "guild_lead" | "instructor" | "admin" — whose UI to capture
    full_page: NotRequired[bool]  # default False


# Articles that exist at a URL without appearing in any browsing surface —
# excluded from search, the landing page, and category pages, but their
# canonical URL resolves normally (registry keys / tours may deep-link them).
UNLISTED_SLUGS: frozenset[str] = frozenset({"instructor-orientation"})

# Old /help/#slug landing anchors → new article slugs. Phase 6 fills in all
# 8 legacy slugs (identity entries included); empty until then, so the
# landing's legacy-anchor filter yields {}.
LEGACY_SLUG_MAP: dict[str, str] = {}
