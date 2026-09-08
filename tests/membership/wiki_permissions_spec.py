"""BDD specs for the wiki permission helpers.

Permissions here are FILTERS, not checks: a view asks for what it may show and renders
what comes back. These specs pin each helper's legs from both sides, including the two
the brief singled out — an Official page is never verifiable by anyone, and an admin
previewing as a member gets the member's answer.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from classes.factories import UserFactory
from hub.view_as import ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER, ViewAs
from membership.models import GuildStaffMembership, Member, WikiPage
from membership.permissions import (
    _can_moderate_wiki_page,
    can_edit_wiki_page,
    can_verify_wiki_page,
    editable_wiki_scopes,
    visible_wiki_pages,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    WikiPageFactory,
)

pytestmark = pytest.mark.django_db


def _request(user, *, roles: set[str] | None = None, picked: str | None = None):
    request = RequestFactory().get("/")
    request.user = user
    if roles is not None:
        request.view_as = ViewAs(actual=frozenset(roles), picked=picked)
    return request


def describe_can_edit_wiki_page():
    def it_allows_any_active_member():
        user = UserFactory(username="active@example.com")
        request = _request(user, roles={ROLE_MEMBER})
        assert can_edit_wiki_page(request, WikiPageFactory()) is True

    def it_denies_a_suspended_member():
        user = UserFactory(username="lapsed@example.com")
        user.member.status = Member.Status.SUSPENDED
        user.member.save(update_fields=["status"])
        request = _request(user, roles={ROLE_MEMBER})
        assert can_edit_wiki_page(request, WikiPageFactory()) is False

    def it_denies_an_anonymous_request():
        request = _request(AnonymousUser(), roles={ROLE_MEMBER})
        assert can_edit_wiki_page(request, WikiPageFactory()) is False

    def describe_an_official_page():
        def it_allows_staff():
            request = _request(UserFactory(username="admin@example.com"), roles={ROLE_ADMIN, ROLE_MEMBER})
            assert can_edit_wiki_page(request, WikiPageFactory(official=True)) is True

        def it_denies_a_plain_member():
            request = _request(UserFactory(username="member@example.com"), roles={ROLE_MEMBER})
            assert can_edit_wiki_page(request, WikiPageFactory(official=True)) is False

        def it_denies_the_guild_lead_of_its_own_guild():
            # Official is policy, safety, and money. A lead is not an officer.
            user = UserFactory(username="lead@example.com")
            guild = GuildFactory(guild_lead=user.member)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_edit_wiki_page(request, WikiPageFactory(official=True, guild=guild)) is False

    def describe_an_archived_page():
        def it_allows_effective_staff():
            request = _request(UserFactory(username="officer@example.com"), roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})
            assert can_edit_wiki_page(request, WikiPageFactory(archived=True)) is True

        def it_allows_the_guild_lead_of_its_own_guild():
            user = UserFactory(username="archlead@example.com")
            guild = GuildFactory(guild_lead=user.member)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_edit_wiki_page(request, WikiPageFactory(archived=True, guild=guild)) is True

        def it_denies_a_plain_active_member():
            request = _request(UserFactory(username="plain@example.com"), roles={ROLE_MEMBER})
            assert can_edit_wiki_page(request, WikiPageFactory(archived=True)) is False

        def it_still_denies_a_guild_lead_when_the_archived_page_is_official():
            # Official outranks archived. Checking archived first handed the guild's lead
            # an edit right on Official content, which the locked rule never grants.
            user = UserFactory(username="archofficial@example.com")
            guild = GuildFactory(guild_lead=user.member)
            page = WikiPageFactory(archived=True, official=True, guild=guild)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_edit_wiki_page(request, page) is False

        def it_allows_staff_on_an_archived_official_page():
            request = _request(UserFactory(username="archadmin@example.com"), roles={ROLE_ADMIN, ROLE_MEMBER})
            assert can_edit_wiki_page(request, WikiPageFactory(archived=True, official=True)) is True

    def it_gives_an_admin_previewing_as_a_member_the_members_answer():
        user = UserFactory(username="preview@example.com")
        request = _request(user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_edit_wiki_page(request, WikiPageFactory(official=True)) is False


def describe_can_verify_wiki_page():
    def describe_an_official_page():
        def it_is_never_verifiable_even_by_an_admin():
            # The guard runs first, before any authority test: Official already outranks
            # Guild verified, so a Verify control there could only quietly demote it.
            request = _request(UserFactory(username="admin2@example.com"), roles={ROLE_ADMIN, ROLE_MEMBER})
            assert can_verify_wiki_page(request, WikiPageFactory(official=True)) is False

    def describe_a_guild_scoped_page():
        def it_allows_the_guild_lead():
            user = UserFactory(username="gl@example.com")
            guild = GuildFactory(guild_lead=user.member)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_verify_wiki_page(request, WikiPageFactory(guild=guild)) is True

        @pytest.mark.parametrize("role", [r.value for r in GuildStaffMembership.Role])
        def it_allows_every_staff_role_including_orienter(role):
            user = UserFactory(username=f"staff-{role}@example.com")
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=user.member, role=role)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_verify_wiki_page(request, WikiPageFactory(guild=guild)) is True

        def it_denies_a_guild_member_holding_no_role():
            user = UserFactory(username="joined@example.com")
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=user.member)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_verify_wiki_page(request, WikiPageFactory(guild=guild)) is False

        def it_denies_the_lead_of_a_different_guild():
            user = UserFactory(username="otherlead@example.com")
            GuildFactory(guild_lead=user.member)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_verify_wiki_page(request, WikiPageFactory(guild=GuildFactory())) is False

    def describe_a_space_wide_page():
        def it_allows_effective_staff():
            request = _request(UserFactory(username="officer2@example.com"), roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})
            assert can_verify_wiki_page(request, WikiPageFactory()) is True

        def it_denies_a_plain_member():
            request = _request(UserFactory(username="plain2@example.com"), roles={ROLE_MEMBER})
            assert can_verify_wiki_page(request, WikiPageFactory()) is False

    def describe_an_equipment_page():
        def it_allows_the_tools_own_orienter_on_a_guildless_machine():
            # They teach the machine, so they know what is true about it, even when the
            # tool belongs to no guild at all.
            user = UserFactory(username="orienter@example.com")
            tool = EquipmentFactory(guild=None)
            EquipmentStaffMembershipFactory(equipment=tool, member=user.member)
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=tool)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_verify_wiki_page(request, page) is True

        def it_denies_a_member_with_no_role_on_the_tool():
            user = UserFactory(username="notorienter@example.com")
            tool = EquipmentFactory(guild=None)
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=tool)
            request = _request(user, roles={ROLE_MEMBER})
            assert can_verify_wiki_page(request, page) is False

        def it_denies_an_anonymous_request_on_an_equipment_page():
            tool = EquipmentFactory(guild=None)
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=tool)
            request = _request(AnonymousUser(), roles={ROLE_MEMBER})
            assert can_verify_wiki_page(request, page) is False


def describe_can_moderate_wiki_page():
    """The private stand-in spec D replaces with its public version, unchanged."""

    def it_allows_a_guild_lead_in_their_own_guild():
        user = UserFactory(username="mod@example.com")
        guild = GuildFactory(guild_lead=user.member)
        request = _request(user, roles={ROLE_MEMBER})
        assert _can_moderate_wiki_page(request, WikiPageFactory(guild=guild)) is True

    def it_denies_that_lead_in_another_guild():
        user = UserFactory(username="mod2@example.com")
        GuildFactory(guild_lead=user.member)
        request = _request(user, roles={ROLE_MEMBER})
        assert _can_moderate_wiki_page(request, WikiPageFactory(guild=GuildFactory())) is False

    def it_denies_a_lead_on_a_space_wide_page():
        user = UserFactory(username="mod3@example.com")
        GuildFactory(guild_lead=user.member)
        request = _request(user, roles={ROLE_MEMBER})
        assert _can_moderate_wiki_page(request, WikiPageFactory()) is False

    def it_allows_effective_staff_anywhere():
        request = _request(UserFactory(username="mod4@example.com"), roles={ROLE_ADMIN, ROLE_MEMBER})
        assert _can_moderate_wiki_page(request, WikiPageFactory()) is True


def describe_visible_wiki_pages():
    def it_delegates_to_the_queryset_filter():
        live = WikiPageFactory()
        WikiPageFactory(is_published=False)
        request = _request(UserFactory(username="reader@example.com"), roles={ROLE_MEMBER})
        assert list(visible_wiki_pages(request)) == [live]


def describe_editable_wiki_scopes():
    def it_gives_staff_every_guild_and_space_wide():
        GuildFactory(name="Woodworking")
        GuildFactory(name="Print")
        request = _request(UserFactory(username="scope-admin@example.com"), roles={ROLE_ADMIN, ROLE_MEMBER})
        guilds, space_wide = editable_wiki_scopes(request)
        assert [g.name for g in guilds] == ["Print", "Woodworking"]
        assert space_wide is True

    def it_gives_a_member_only_the_guilds_they_joined():
        user = UserFactory(username="scope-member@example.com")
        joined = GuildFactory(name="Woodworking")
        GuildFactory(name="Print")
        GuildMembershipFactory(guild=joined, member=user.member)
        request = _request(user, roles={ROLE_MEMBER})
        guilds, space_wide = editable_wiki_scopes(request)
        assert [g.name for g in guilds] == ["Woodworking"]
        assert space_wide is True

    def it_gives_a_suspended_member_nothing():
        user = UserFactory(username="scope-lapsed@example.com")
        user.member.status = Member.Status.SUSPENDED
        user.member.save(update_fields=["status"])
        GuildFactory()
        request = _request(user, roles={ROLE_MEMBER})
        assert editable_wiki_scopes(request) == ([], False)

    def it_gives_an_anonymous_request_nothing():
        GuildFactory()
        request = _request(AnonymousUser(), roles={ROLE_MEMBER})
        assert editable_wiki_scopes(request) == ([], False)

    def it_does_not_repeat_a_guild_a_member_joined_twice():
        user = UserFactory(username="scope-dupe@example.com")
        guild = GuildFactory(name="Woodworking")
        GuildMembershipFactory(guild=guild, member=user.member)
        GuildStaffMembershipFactory(guild=guild, member=user.member)
        request = _request(user, roles={ROLE_MEMBER})
        guilds, _ = editable_wiki_scopes(request)
        assert len(guilds) == 1
