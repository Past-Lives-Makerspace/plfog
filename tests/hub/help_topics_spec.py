"""BDD specs for /help/topics.json — the Info View's registry-as-JSON endpoint (Spec B §5).

The view serializes a constant (Spec A's ``HELP_KEYS``), so the specs check the
contract: shape, per-key URL resolution, public read, the feature-flag 404, and
the VERSION-keyed ETag / Cache-Control pair that lets browsers 304 it.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.help_registry import HELP_KEYS
from core.models import SiteConfiguration
from plfog.version import VERSION
from tests.membership.factories import HelpCategoryFactory, WikiArticleFactory

pytestmark = pytest.mark.django_db


def describe_help_topics_json():
    def it_returns_404_when_help_page_disabled(client: Client):
        config = SiteConfiguration.load()
        config.help_page_enabled = False
        config.save()
        resp = client.get(reverse("hub_help_topics_json"))
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not found."}

    def it_serves_every_registry_key_with_title_short_text_and_url(client: Client):
        resp = client.get(reverse("hub_help_topics_json"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == VERSION
        assert set(data["topics"]) == set(HELP_KEYS)
        for key, entry in HELP_KEYS.items():
            topic = data["topics"][key]
            assert topic["title"] == entry["title"]
            assert topic["short_text"] == entry["short_text"]
            assert topic["url"].startswith("/help/")

    def it_resolves_urls_to_the_kb_article_anchor(client: Client):
        # A published article behind one key resolves to its canonical URL +
        # anchor; annotation-only keys (article_slug=None) degrade to /help/.
        category = HelpCategoryFactory(slug="guilds")
        article = WikiArticleFactory(slug="guild-voting", category=category, is_published=True)
        data = client.get(reverse("hub_help_topics_json")).json()
        entry = HELP_KEYS["voting.rank-guilds"]
        assert data["topics"]["voting.rank-guilds"]["url"] == f"{article.get_absolute_url()}#{entry['anchor']}"
        assert data["topics"]["settings.profile"]["url"] == "/help/"

    def it_is_public_read(client: Client):
        # Anonymous parity with help_page: org-wide reference content, no PII.
        assert client.get(reverse("hub_help_topics_json")).status_code == 200

    def it_sends_cache_headers_and_honors_etag(client: Client):
        first = client.get(reverse("hub_help_topics_json"))
        assert first.status_code == 200
        assert set(first["Cache-Control"].split(", ")) == {"public", "max-age=3600"}
        etag = first["ETag"]
        assert etag
        second = client.get(reverse("hub_help_topics_json"), HTTP_IF_NONE_MATCH=etag)
        assert second.status_code == 304

    def it_does_not_serve_a_cached_304_when_the_flag_is_off(client: Client):
        # The ETag function returns None when the feature is off, so a stale
        # If-None-Match can never turn the 404 into a 304.
        etag = client.get(reverse("hub_help_topics_json"))["ETag"]
        config = SiteConfiguration.load()
        config.help_page_enabled = False
        config.save()
        resp = client.get(reverse("hub_help_topics_json"), HTTP_IF_NONE_MATCH=etag)
        assert resp.status_code == 404

    def it_rejects_non_get_methods(client: Client):
        assert client.post(reverse("hub_help_topics_json")).status_code == 405
