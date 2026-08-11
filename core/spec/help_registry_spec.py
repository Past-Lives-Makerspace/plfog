"""BDD specs for core.help_registry — the help-key spine Specs B/C/D resolve against.

Format checks (key grammar, short_text budget) run without the database;
``url_for`` resolution runs against real WikiArticle rows because it degrades
to ``/help/`` until an article is seeded and published (the no-deadlock rule).
"""

from __future__ import annotations

import pytest

from core.help_registry import HELP_KEYS, KEY_PATTERN, HelpKeyEntry, entry, url_for
from tests.membership.factories import HelpCategoryFactory, WikiArticleFactory

_ANNOTATION_ONLY: HelpKeyEntry = {
    "title": "The menu",
    "short_text": "Everything lives in the sidebar.",
    "article_slug": None,
    "anchor": None,
}


def describe_help_registry():
    def describe_HELP_KEYS():
        def it_matches_the_key_pattern_for_every_key():
            for key in HELP_KEYS:
                assert KEY_PATTERN.match(key), f"registry key {key!r} violates KEY_PATTERN"

        def it_keeps_every_short_text_nonempty_and_within_200_chars():
            for key, key_entry in HELP_KEYS.items():
                assert key_entry["short_text"].strip(), f"{key} has an empty short_text"
                assert len(key_entry["short_text"]) <= 200, f"{key} short_text exceeds 200 chars"

        def it_pairs_an_anchor_with_every_article_slug():
            # The TypedDict contract: anchor is None exactly when article_slug is None.
            for key, key_entry in HELP_KEYS.items():
                assert (key_entry["article_slug"] is None) == (key_entry["anchor"] is None), key

        def it_carries_every_anchor_as_a_heading_id_in_its_seeded_article_body():
            # Phase-6 content resolution: for entries pointing at a seeded article,
            # the anchor must exist as a {#anchor} heading id in that body — a hover
            # panel's "Learn more" link may never land on a missing anchor. Entries
            # pointing at pending/unseeded articles are tolerated (§5.1).
            from membership.help_content import ARTICLES

            bodies = {article["slug"]: article["body"] for article in ARTICLES}
            for key, key_entry in HELP_KEYS.items():
                slug = key_entry["article_slug"]
                if slug not in bodies:
                    continue
                assert f"{{#{key_entry['anchor']}}}" in bodies[slug], (
                    f"{key}: anchor {key_entry['anchor']!r} is not a heading id in article {slug!r}"
                )

    def describe_entry():
        def it_returns_the_registry_entry_for_a_known_key():
            assert entry("voting.rank-guilds")["article_slug"] == "guild-voting"

        def it_raises_key_error_on_an_unknown_key():
            with pytest.raises(KeyError):
                entry("voting.does-not-exist")

    def describe_url_for():
        def it_falls_back_to_help_root_for_annotation_only_entries(monkeypatch):
            monkeypatch.setitem(HELP_KEYS, "nav.sidebar", _ANNOTATION_ONLY)
            assert url_for("nav.sidebar") == "/help/"

        def it_falls_back_to_help_root_when_the_article_is_not_seeded(db):
            assert url_for("voting.rank-guilds") == "/help/"

        def it_falls_back_to_help_root_when_the_article_is_unpublished(db):
            WikiArticleFactory(slug="guild-voting", is_published=False)
            assert url_for("voting.rank-guilds") == "/help/"

        def it_returns_the_canonical_url_with_anchor_for_a_published_article(db):
            category = HelpCategoryFactory(name="Guilds")
            WikiArticleFactory(slug="guild-voting", category=category, is_published=True)
            assert url_for("voting.rank-guilds") == "/help/guilds/guild-voting/#voting-rank-guilds"

        def it_uses_the_more_segment_for_a_published_uncategorized_article(db):
            WikiArticleFactory(slug="guild-voting", is_published=True)
            assert url_for("voting.rank-guilds") == "/help/more/guild-voting/#voting-rank-guilds"

        def it_raises_key_error_on_an_unknown_key():
            with pytest.raises(KeyError):
                url_for("voting.does-not-exist")
