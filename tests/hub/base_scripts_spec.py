"""BDD specs for the scripts hub/base.html loads on every hub page."""

from __future__ import annotations

import pytest
from django.urls import reverse

from tests.membership.factories import MembershipPlanFactory, UserFactory


@pytest.fixture
def hub_user(db):
    MembershipPlanFactory()
    return UserFactory(username="base-scripts@example.com")


def describe_hub_base_scripts():
    def it_loads_the_help_tooltip_lift_on_every_hub_page(hub_user, client):
        client.force_login(hub_user)
        html = client.get(reverse("hub_home")).content.decode()
        assert '<script src="/static/js/pl_help.js"></script>' in html
        # It lands with the other page wide behaviors, after Alpine and before the calendar.
        assert html.index("js/hero_placement.js") < html.index("js/pl_help.js") < html.index("js/session_calendar.js")
