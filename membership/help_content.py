"""Help-center content data.

Phase 6 fills in ``CATEGORIES`` / ``ARTICLES`` / ``LEGACY_SLUG_MAP`` (the seeded
category table, the 29 article bodies with their screenshot lists, and the old
landing-anchor → new-article map). Until then this module carries only the
constants the views and model logic already need.
"""

from __future__ import annotations

# Articles that exist at a URL without appearing in any browsing surface —
# excluded from search, the landing page, and category pages, but their
# canonical URL resolves normally (registry keys / tours may deep-link them).
UNLISTED_SLUGS: frozenset[str] = frozenset({"instructor-orientation"})

# Old /help/#slug landing anchors → new article slugs. Phase 6 fills in all
# 8 legacy slugs (identity entries included); empty until then, so the
# landing's legacy-anchor filter yields {}.
LEGACY_SLUG_MAP: dict[str, str] = {}
