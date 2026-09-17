"""Route gating for the seven feature switches (#405).

Two jobs here, and the first is the one that matters most.

``hub/urls.py`` and ``classes/urls.py`` fence each family by URL-name prefix rather than by
listing every route, which is what lets a route added to the family later be gated for free.
The cost of that convenience is that a careless prefix could silently swallow a neighbour —
``teach_`` would take the entire instructor portal with it. So the exact set each prefix
selects is pinned here. **If you change a prefix, this spec is the thing that tells you what
you actually caught.**

The second job is the states: Coming soon 404s exactly as Hidden does, because the difference
between them is a nav affordance, not an access level.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from classes.urls import GATED_ROUTE_NAMES as TEACH_GATED
from core.features import FEATURES, FeatureState, feature_required, is_on
from hub.urls import GATED_ROUTE_NAMES as HUB_GATED
from tests.features import coming_soon, hide, turn_on

pytestmark = pytest.mark.django_db


def _login_admin(client: Client, username: str = "gateadmin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def describe_the_url_name_fences():
    def it_gates_exactly_the_meetings_family():
        assert HUB_GATED["meetings"][0] == "hub_meetings"
        assert len(HUB_GATED["meetings"]) == 28
        assert all(name.startswith("hub_meeting") for name in HUB_GATED["meetings"])
        # The per-guild meeting NOTES are a different model and a different screen.
        assert "hub_guild_meeting_notes" not in HUB_GATED["meetings"]

    def it_gates_both_halves_of_voting():
        names = HUB_GATED["voting"]
        assert len(names) == 11
        # The member destination and the admin destination are different URLs behind one switch
        # (base.html points the same feature at each). Missing either leaves half the roster in.
        assert "hub_guild_voting" in names
        assert "hub_admin_voting_overview" in names
        # Member administration is not voting, and shares no prefix with it.
        assert not any(name.startswith("hub_admin_members") for name in names)

    def it_gates_only_the_directory_route():
        assert HUB_GATED["directory"] == ["hub_member_directory"]

    def it_gates_the_spaces_family_including_the_legacy_redirect():
        names = HUB_GATED["spaces"]
        assert len(names) == 17
        assert "hub_spaces" in names
        # /info/ permanently redirects onto hub_spaces. Gated with the family so a switched-off
        # Spaces answers 404 directly instead of bouncing a visitor into one.
        assert "hub_org_info_legacy" in names

    def it_gates_the_recruiting_routes_and_never_the_teaching_portal():
        assert TEACH_GATED == ["teach_why", "teach_apply", "teach_orientation"]
        # The portal is the whole point of the carve-out: an instructor's own classes must
        # survive all three states, so no teach_classes/teach_dashboard route may appear here.
        assert not any(name.startswith("teach_class") for name in TEACH_GATED)
        assert "teach_dashboard" not in TEACH_GATED
        assert "teach_overview" not in TEACH_GATED


def describe_the_gate_decorator():
    def it_refuses_an_unknown_feature_key():
        # Fail loudly at import, not silently at request time: a typo in a fence would
        # otherwise produce a gate that never fires and a family that is never protected.
        with pytest.raises(KeyError):
            feature_required("not_a_feature")


def describe_a_route_in_a_switched_off_family():
    @pytest.fixture()
    def admin(client: Client) -> User:
        return _login_admin(client)

    def it_answers_200_while_the_feature_is_on(client: Client, admin: User):
        turn_on("spaces")
        assert client.get(reverse("hub_spaces")).status_code == 200

    def it_404s_when_hidden(client: Client, admin: User):
        hide("spaces")
        assert client.get(reverse("hub_spaces")).status_code == 404

    def it_404s_when_coming_soon_exactly_as_when_hidden(client: Client, admin: User):
        coming_soon("spaces", "Launching Sept 30th!")
        assert client.get(reverse("hub_spaces")).status_code == 404

    def it_404s_every_route_in_the_family_not_just_the_index(client: Client, admin: User):
        hide("meetings")
        for name in ("hub_meetings", "hub_meeting_create"):
            assert client.get(reverse(name)).status_code == 404, name

    def it_404s_write_posts_too(client: Client, admin: User):
        # A dark feature is dark for writes as well, or a crafted POST still changes data
        # behind a switch an admin believes is off.
        hide("meetings")
        assert client.post(reverse("hub_meeting_create")).status_code == 404


def describe_nobody_bypasses():
    def it_404s_the_admin_voting_screens_for_an_admin(client: Client):
        """Settled decision 1: off is off for everyone, admins included.

        The consequence is deliberate and worth seeing in a test — an admin who hides Voting
        loses the admin voting screens too. It is recoverable because Site Settings is never
        gated, which the next spec asserts.
        """
        _login_admin(client)
        hide("voting")
        assert client.get(reverse("hub_admin_voting_overview")).status_code == 404

    def it_leaves_site_settings_reachable_so_the_switch_can_be_undone(client: Client):
        _login_admin(client)
        for feature in FEATURES:
            hide(feature.key)
        assert client.get(reverse("hub_admin_site_settings") + "?tab=features").status_code == 200


def describe_the_default_state():
    def it_treats_every_feature_as_on_when_no_row_exists(client: Client):
        from core.models import FeatureSwitch

        FeatureSwitch.objects.all().delete()
        assert [f.key for f in FEATURES if not is_on(f.key)] == []
        _login_admin(client)
        assert client.get(reverse("hub_spaces")).status_code == 200

    def it_keeps_the_three_states_distinct():
        assert FeatureState.ON != FeatureState.SOON != FeatureState.HIDDEN
