"""BDD specs for the guild card's link on the public class detail page.

The class site (classes.pastlives.space) does not serve guild pages, so the link must
point at the guilds site rather than a path on whichever host rendered the class.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

ON_CLASS_SITE = dict(
    ALLOWED_HOSTS=["classes.pastlives.space", "testserver"],
    PUBLIC_HOSTS=["classes.pastlives.space"],
    GUILDS_BASE_URL="https://guilds.example.test",
)


def describe_guild_card_link():
    @override_settings(**ON_CLASS_SITE)
    def it_links_to_the_guild_page_on_the_guilds_site(client: Client):
        guild = GuildFactory(name="Glass Guild", slug="glass-guild")
        offering = ClassOfferingFactory(
            category=CategoryFactory(name="Glass", guild=guild), status=ClassOffering.Status.PUBLISHED
        )

        response = client.get(f"/classes/{offering.slug}/", HTTP_HOST="classes.pastlives.space")

        assert response.status_code == 200
        assert (
            b'class="cp-detail__guild-link" href="https://guilds.example.test/guilds/glass-guild/"' in response.content
        )
