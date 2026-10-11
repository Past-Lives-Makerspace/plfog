"""BDD specs for the hub sidebar's Manage Bookings entry (#768), in the shape of ``teach_sidebar_spec``.

Who gets the entry (a lead, a staffer, an equipment manager, an admin whatever the switches;
nobody else), where it sits (right after Reservations, in both sidebar blocks), where it lands,
when it lights, and what it costs on a page for a member with nothing to run.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.urls import reverse

from hub.context_processors import _manage_bookings_nav, hub_sidebar
from hub.view_as import ROLE_MEMBER, ViewAs, compute_actual_roles
from membership.models import GuildStaffMembership, Member
from tests.features import hide
from tests.membership.factories import (
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    UserFactory,
)

ENTRY = 'data-nav="manage-bookings"'
HOME = "/manage/bookings/"


@pytest.fixture
def plain_user(db):
    MembershipPlanFactory()
    return UserFactory(username="bookings-sidebar-member@example.com")


@pytest.fixture
def admin_user(db):
    MembershipPlanFactory()
    user = UserFactory(username="bookings-sidebar-admin@example.com")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _sidebar(client, url: str = "") -> str:
    html = client.get(url or reverse("hub_home")).content.decode()
    start = html.index('aria-label="Hub navigation"')
    return html[start : html.index("</nav>", start)]


def _entry(nav: str) -> str:
    """The Manage Bookings anchor's opening tag."""
    start = nav.rindex("<a ", 0, nav.index(ENTRY))
    return nav[start : nav.index(">", nav.index(ENTRY)) + 1]


def _nav_request(rf, user, path: str, picked: str | None = None) -> HttpRequest:
    request = rf.get(path)
    request.user = user
    request.view_as = ViewAs(compute_actual_roles(user), picked)
    return request


def describe_the_entry():
    def it_shows_for_a_guild_lead_right_after_reservations(plain_user, client):
        GuildFactory(guild_lead=plain_user.member)
        client.force_login(plain_user)
        nav = _sidebar(client)
        assert ENTRY in nav
        assert nav.index(ENTRY) > nav.index(reverse("hub_equipment_index"))
        assert nav.index(ENTRY) < nav.index(reverse("hub_help"))
        assert f'href="{HOME}"' in _entry(nav)

    def it_shows_for_a_guild_staffer(plain_user, client):
        GuildStaffMembershipFactory(member=plain_user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.force_login(plain_user)
        assert ENTRY in _sidebar(client)

    def it_shows_for_an_equipment_manager(plain_user, client):
        EquipmentStaffMembershipFactory(member=plain_user.member)
        client.force_login(plain_user)
        assert ENTRY in _sidebar(client)

    def it_is_absent_for_a_member_who_runs_nothing(plain_user, client):
        GuildFactory()  # somebody else's guild, nobody leads it
        client.force_login(plain_user)
        assert ENTRY not in _sidebar(client)

    def it_shows_for_an_admin_with_the_orientations_and_reservations_switches_off(admin_user, client):
        hide("orientations")
        hide("equipment")
        client.force_login(admin_user)
        nav = _sidebar(client)
        assert ENTRY in nav
        assert f'href="{reverse("hub_orientations")}"' not in nav
        assert f'href="{reverse("hub_equipment_index")}"' not in nav
        # The admin block, not the member block: the voting entry below it is the admin destination.
        assert nav.index(ENTRY) < nav.index(reverse("hub_admin_voting_overview"))

    def it_lands_on_the_home_directly(plain_user, client):
        GuildFactory(guild_lead=plain_user.member)
        client.force_login(plain_user)
        response = client.get(HOME)
        assert response.status_code == 200
        assert "hub/manage_bookings.html" in [t.name for t in response.templates]


def describe_active_states():
    def it_lights_on_the_home_and_nowhere_else(plain_user, client):
        GuildFactory(guild_lead=plain_user.member)
        client.force_login(plain_user)
        assert "active" in _entry(_sidebar(client, HOME))
        assert "active" not in _entry(_sidebar(client))
        assert "active" not in _entry(_sidebar(client, reverse("hub_orientations")))


def describe_context_processor():
    def it_costs_one_query_for_a_member_with_nothing_to_run(plain_user, rf, django_assert_num_queries):
        request = _nav_request(rf, plain_user, "/")
        # hub_sidebar reads user.member before the entry does, so the entry's own cost is what is counted.
        assert request.user.member is not None
        with django_assert_num_queries(1):
            assert _manage_bookings_nav(request) is None

    def it_costs_no_query_for_an_admin(admin_user, rf, django_assert_num_queries):
        request = _nav_request(rf, admin_user, "/manage/bookings/")
        with django_assert_num_queries(0):
            assert _manage_bookings_nav(request) == {"url": HOME, "is_active": True, "active_class": "active"}

    def it_marks_the_entry_inactive_off_its_path(admin_user, rf):
        ctx = hub_sidebar(_nav_request(rf, admin_user, "/orientations/manage/"))
        assert ctx["manage_bookings_nav"] == {"url": HOME, "is_active": False, "active_class": ""}

    def it_gives_anonymous_visitors_no_entry(rf):
        request = rf.get("/")
        request.user = AnonymousUser()
        assert hub_sidebar(request)["manage_bookings_nav"] is None

    def it_gives_an_admin_previewing_as_a_member_the_members_sidebar(admin_user, rf):
        # Ruling 9: an admin on no leadership previewing as a member sees no entry, like that member.
        assert hub_sidebar(_nav_request(rf, admin_user, "/", ROLE_MEMBER))["manage_bookings_nav"] is None
        GuildFactory(guild_lead=admin_user.member)
        assert hub_sidebar(_nav_request(rf, admin_user, "/", ROLE_MEMBER))["manage_bookings_nav"] is not None

    def it_is_absent_on_a_request_that_never_reached_the_view_as_middleware(plain_user, rf):
        # A themed 404 renders every context processor before ViewAsMiddleware has run.
        GuildFactory(guild_lead=plain_user.member)
        request = rf.get("/")
        request.user = plain_user
        assert hub_sidebar(request)["manage_bookings_nav"] is None
