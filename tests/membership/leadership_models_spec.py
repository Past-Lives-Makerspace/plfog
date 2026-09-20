"""BDD specs for the Leadership Directory data (#464, part 1).

The page wording singleton, the curated listing rows and their role lines, plus the two
small helpers the page will read: ``Member.discord_profile_url`` and ``Guild.co_leads``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from membership.models import Guild, GuildStaffMembership, LeadershipListing, LeadershipPage, LeadershipRole
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db


def describe_LeadershipPage():
    def it_loads_one_row_with_the_default_wording():
        page = LeadershipPage.load()
        assert page.pk == 1
        assert page.hero_title == "Leadership Directory"
        assert page.team_heading == "Leadership & Admin Team"
        assert page.guilds_heading == "Guild Leaders"
        assert page.hero_lead and page.team_intro and page.guilds_intro
        assert LeadershipPage.load().pk == 1
        assert LeadershipPage.objects.count() == 1

    def it_pins_every_save_to_pk_1():
        LeadershipPage(hero_title="Who We Are").save()
        LeadershipPage(hero_title="Our Team").save()
        assert list(LeadershipPage.objects.values_list("pk", "hero_title")) == [(1, "Our Team")]

    def it_names_itself():
        assert str(LeadershipPage.load()) == "Leadership Directory"


def describe_LeadershipListing():
    def describe_listed():
        def it_returns_listed_rows_in_sort_order_with_members_and_roles_loaded(django_assert_num_queries):
            second = LeadershipListingFactory(is_listed=True, sort_order=2)
            first = LeadershipListingFactory(is_listed=True, sort_order=1)
            LeadershipListingFactory(is_listed=False, sort_order=0)
            LeadershipRoleFactory(listing=first, title="Founder")
            with django_assert_num_queries(2):  # the listings with their members, then the roles
                rows = list(LeadershipListing.objects.listed())
                names = [row.member.display_name for row in rows]
                titles = [[role.title for role in row.roles.all()] for row in rows]
            assert rows == [first, second]
            assert names == [first.member.display_name, second.member.display_name]
            assert titles == [["Founder"], []]

    def describe_last_updated():
        def it_is_none_with_no_listings():
            assert LeadershipListing.objects.last_updated() is None

        def it_picks_the_newer_of_the_listing_and_role_stamps():
            listing = LeadershipListingFactory()
            role = LeadershipRoleFactory(listing=listing)
            older = timezone.now() - timedelta(days=3)
            newer = timezone.now() - timedelta(days=1)
            LeadershipListing.objects.filter(pk=listing.pk).update(updated_at=older)
            LeadershipRole.objects.filter(pk=role.pk).update(updated_at=newer)
            assert LeadershipListing.objects.last_updated() == newer

            newest = newer + timedelta(hours=1)
            LeadershipListing.objects.filter(pk=listing.pk).update(updated_at=newest)
            assert LeadershipListing.objects.last_updated() == newest

        def it_counts_a_listing_that_has_no_roles():
            listing = LeadershipListingFactory()
            assert LeadershipListing.objects.last_updated() == listing.updated_at

        def it_counts_an_unlisted_row_because_hiding_a_card_changes_the_page():
            hidden = LeadershipListingFactory(is_listed=False)
            assert LeadershipListing.objects.last_updated() == hidden.updated_at

    def describe_for_member():
        def it_returns_the_saved_row():
            listing = LeadershipListingFactory()
            assert LeadershipListing.objects.for_member(listing.member) == listing

        def it_returns_an_unsaved_stand_in_without_writing_one():
            member = MemberFactory()
            stand_in = LeadershipListing.objects.for_member(member)
            assert stand_in.pk is None
            assert stand_in.member == member
            assert LeadershipListing.objects.count() == 0

    def describe___str__():
        def it_names_the_member_and_whether_they_are_listed():
            member = MemberFactory(full_legal_name="Ada Lovelace")
            listing = LeadershipListingFactory(member=member, is_listed=True)
            assert str(listing) == "Ada Lovelace (listed)"
            listing.is_listed = False
            assert str(listing) == "Ada Lovelace (unlisted)"


def describe_LeadershipRole():
    def it_names_the_title_and_the_member():
        member = MemberFactory(full_legal_name="Ada Lovelace")
        role = LeadershipRoleFactory(listing__member=member, title="Founder")
        assert str(role) == "Founder (Ada Lovelace)"

    def it_orders_by_sort_order_then_id():
        listing = LeadershipListingFactory()
        late = LeadershipRoleFactory(listing=listing, sort_order=1)
        early = LeadershipRoleFactory(listing=listing, sort_order=0)
        assert list(listing.roles.all()) == [early, late]


def describe_Member_discord_profile_url():
    def it_links_the_verified_discord_id():
        member = MemberFactory(discord_user_id="1106993495580868728")
        assert member.discord_profile_url == "https://discord.com/users/1106993495580868728"

    def it_is_blank_for_a_typed_handle_alone():
        member = MemberFactory(discord_handle="@lee", discord_user_id="")
        assert member.discord_profile_url == ""


def describe_Guild_co_leads():
    def it_returns_co_lead_rows_only():
        guild = GuildFactory()
        co_lead = GuildStaffMembershipFactory(guild=guild, role=GuildStaffMembership.Role.CO_LEAD).member
        GuildStaffMembershipFactory(guild=guild, role=GuildStaffMembership.Role.SECRETARY)
        GuildStaffMembershipFactory(guild=guild, custom=True, custom_title="Vice Guild Leader")
        assert guild.co_leads == [co_lead]

    def it_does_not_repeat_a_lead_who_also_holds_a_co_lead_row():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=lead, role=GuildStaffMembership.Role.CO_LEAD)
        other = GuildStaffMembershipFactory(guild=guild, role=GuildStaffMembership.Role.CO_LEAD).member
        assert guild.co_leads == [other]

    def it_reads_a_prefetch_without_another_query(django_assert_num_queries):
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, role=GuildStaffMembership.Role.CO_LEAD)
        loaded = Guild.objects.prefetch_related("staff_memberships__member").get(pk=guild.pk)
        with django_assert_num_queries(0):
            assert len(loaded.co_leads) == 1
