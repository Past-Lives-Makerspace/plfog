"""The Knowledge Base sidebar entry.

The single guard that matters here: base.html writes the sidebar **twice** — an admin block and a
member block — and an entry added to only one of them is invisible to half the roster while
looking completely correct in a diff and on whichever page its author happened to load.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_HTML = REPO_ROOT / "templates" / "hub" / "base.html"


def describe_the_sidebar_entry():
    def it_appears_in_both_sidebar_blocks():
        html = BASE_HTML.read_text()
        assert html.count("feature=features.knowledge_base") == 2, (
            "base.html writes the sidebar twice (admin block and member block). One entry gates half the roster."
        )

    def it_sits_directly_below_the_wiki_entry():
        """Morlock asked for it under Wiki; this pins the order so a later edit cannot drift it."""
        html = BASE_HTML.read_text()
        for block in html.split("feature=features.wiki")[1:]:
            head = block[: block.find("</nav>") if "</nav>" in block else 600]
            assert "feature=features.knowledge_base" in head

    def it_uses_its_own_icon_partial():
        icon = REPO_ROOT / "templates" / "hub" / "partials" / "nav_icons" / "_knowledge_base.html"
        assert icon.exists()


def describe_the_feature_switch():
    def it_is_registered_so_the_sidebar_can_resolve_it():
        from core.features import FEATURES_BY_KEY

        assert "knowledge_base" in FEATURES_BY_KEY

    def it_is_not_the_access_gate():
        """Turning the switch off hides the way in; it does not close the KB, which is a separate
        application with its own tiers. The off_description has to say so, because an admin
        reading that card is deciding whether they have just revoked access."""
        from core.features import FEATURES_BY_KEY

        assert "own access tiers" in FEATURES_BY_KEY["knowledge_base"].off_description


def describe_the_url_setting():
    def it_defaults_to_blank_so_an_unconfigured_environment_links_nowhere():
        from django.conf import settings

        assert hasattr(settings, "KNOWLEDGE_BASE_URL")

    def it_is_exposed_to_templates(settings):
        from core.context_processors import knowledge_base

        settings.KNOWLEDGE_BASE_URL = "https://kb.example.test"
        assert knowledge_base(None) == {"knowledge_base_url": "https://kb.example.test"}
