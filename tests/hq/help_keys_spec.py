"""The hq.* help keys (Track C, part 4, and Track D): registered against the Holiday Bizarre HQ article's anchors,
and stamped on the seeded HQ's door grid, goal band, shift list, bottle form, the marketing log form, the add a sponsor
form and the card edit page's audience block."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from core.help_registry import HELP_KEYS, KEY_PATTERN
from hq.holiday_bizarre import HUB
from hq.models import EventHub, HubCard
from membership.help_content import ARTICLES
from tests.membership.factories import CommunityEventFactory

ARTICLE_SLUG = "holiday-bizarre-hq"

# Key → the anchor in the article it deep-links to.
HQ_KEYS = {
    "hq.door-cards": "hq-door-cards",
    "hq.goal-tracker": "hq-goal-tracker",
    "hq.volunteer-shifts": "hq-volunteer-shifts",
    "hq.wine-wall": "hq-wine-wall",
    "hq.marketing": "hq-marketing",
    "hq.sponsorship": "hq-sponsorship",
    "hq.edit-card": "hq-edit",
}


def describe_the_registry():
    def _body() -> str:
        return next(article["body"] for article in ARTICLES if article["slug"] == ARTICLE_SLUG)

    def it_points_every_hq_key_at_an_anchor_in_the_article():
        body = _body()
        for key, anchor in HQ_KEYS.items():
            entry = HELP_KEYS[key]
            assert KEY_PATTERN.match(key), key
            assert entry["article_slug"] == ARTICLE_SLUG, key
            assert entry["anchor"] == anchor, key
            assert f"{{#{anchor}}}" in body, key

    def it_keeps_every_hover_text_short():
        for key in HQ_KEYS:
            assert len(HELP_KEYS[key]["short_text"]) <= 200, key
            assert HELP_KEYS[key]["title"], key

    def it_files_the_article_after_the_calendar_in_events_and_community():
        article = next(article for article in ARTICLES if article["slug"] == ARTICLE_SLUG)
        calendar = next(article for article in ARTICLES if article["slug"] == "community-calendar")
        assert article["category"] == calendar["category"] == "events-community"
        assert calendar["sort_order"] < article["sort_order"]
        assert "community-calendar" in article["related"]
        assert article["screenshots"] == []


@pytest.mark.django_db
def describe_the_stamps():
    @pytest.fixture
    def hub(db) -> EventHub:
        """Lee's HQ as the command seeds it, for the published Holiday Bizarre."""
        event = CommunityEventFactory(community=True, title="Holiday Bizarre")
        call_command("seed_holiday_bizarre", "--event", str(event.pk), stdout=StringIO())
        return EventHub.objects.get(slug=HUB.slug)

    def it_marks_the_door_grid_and_the_goal_band_on_the_hq_page(hub, admin_client: Client):
        body = admin_client.get(hub.get_absolute_url()).content.decode()

        assert 'class="pl-hq-grid" data-help-key="hq.door-cards"' in body
        assert 'class="pl-hq-goals" aria-labelledby="hq-goals-title" data-help-key="hq.goal-tracker"' in body

    def it_marks_the_shift_list_on_the_volunteer_page(hub, admin_client: Client):
        body = admin_client.get(reverse("hq:volunteers", args=[hub.slug])).content.decode()

        assert 'data-hq-vol-shifts data-help-key="hq.volunteer-shifts"' in body

    def it_marks_the_bottle_form_on_the_wine_wall(hub, admin_client: Client):
        body = admin_client.get(reverse("hq:wine", args=[hub.slug])).content.decode()

        assert 'data-hq-wine-log data-help-key="hq.wine-wall"' in body

    def it_marks_the_log_form_on_the_marketing_tracker(hub, admin_client: Client):
        body = admin_client.get(reverse("hq:marketing", args=[hub.slug])).content.decode()

        assert 'aria-labelledby="hq-mk-form-title" data-help-key="hq.marketing"' in body

    def it_marks_the_add_form_on_the_sponsorship_tracker(hub, admin_client: Client):
        body = admin_client.get(reverse("hq:sponsors", args=[hub.slug])).content.decode()

        assert 'data-hq-sp-add data-help-key="hq.sponsorship"' in body

    def it_marks_who_may_open_it_on_the_card_edit_page(hub, admin_client: Client):
        card = hub.cards.get(kind=HubCard.Kind.VOLUNTEERS)

        body = admin_client.get(reverse("hq:card_edit", args=[hub.slug, card.pk])).content.decode()

        assert 'class="hub-card pl-hq-edit__card" data-help-key="hq.edit-card"' in body
