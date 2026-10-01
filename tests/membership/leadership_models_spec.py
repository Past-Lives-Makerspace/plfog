"""BDD specs for the Leadership Directory data (#464, tabs #564).

The page wording singleton, the tabs (People tabs admins add, and the one Guild Leads tab),
the cards on each tab and their role lines, plus the two small helpers the page reads:
``Member.discord_profile_url`` and ``Guild.co_leads``.

The data migration makes two tabs in every migrated test database, so each spec here starts
from none (``_no_tabs``) and builds exactly what it needs.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from membership.models import (
    Guild,
    GuildStaffMembership,
    LeadershipListing,
    LeadershipPage,
    LeadershipRole,
    LeadershipRowGoneError,
    LeadershipTab,
    Member,
)
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_tabs() -> None:
    LeadershipTab.objects.all().delete()


def describe_LeadershipPage():
    def it_loads_one_row_with_the_default_wording():
        page = LeadershipPage.load()
        assert page.pk == 1
        assert page.hero_title == "Leadership Directory"
        assert page.hero_lead
        assert LeadershipPage.load().pk == 1
        assert LeadershipPage.objects.count() == 1

    def it_pins_every_save_to_pk_1():
        LeadershipPage(hero_title="Who We Are").save()
        LeadershipPage(hero_title="Our Team").save()
        assert list(LeadershipPage.objects.values_list("pk", "hero_title")) == [(1, "Our Team")]

    def it_names_itself():
        assert str(LeadershipPage.load()) == "Leadership Directory"


def describe_LeadershipTab():
    def it_names_itself_by_its_title():
        assert str(LeadershipTabFactory(title="Council")) == "Council"

    def it_orders_by_sort_order_then_id():
        late = LeadershipTabFactory(sort_order=1)
        early = LeadershipTabFactory(sort_order=0)
        tie = LeadershipTabFactory(sort_order=1)
        assert list(LeadershipTab.objects.all()) == [early, late, tie]

    def it_knows_the_guild_leads_tab():
        assert LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS).is_guild_leads is True
        assert LeadershipTabFactory().is_guild_leads is False

    def it_allows_only_one_guild_leads_tab():
        LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS)
        with pytest.raises(IntegrityError), transaction.atomic():
            LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS)

    def it_allows_any_number_of_people_tabs():
        LeadershipTabFactory()
        LeadershipTabFactory()
        assert LeadershipTab.objects.people().count() == 2

    def describe_people():
        def it_leaves_out_guild_leads():
            people = LeadershipTabFactory()
            LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS)
            assert list(LeadershipTab.objects.people()) == [people]

    def describe_pick():
        def it_opens_the_tab_the_query_names():
            first, second = LeadershipTabFactory(), LeadershipTabFactory()
            assert LeadershipTab.pick([first, second], str(second.pk)) == second

        def it_falls_back_to_the_first_for_a_missing_or_unknown_id():
            first, second = LeadershipTabFactory(), LeadershipTabFactory()
            assert LeadershipTab.pick([first, second], None) == first
            assert LeadershipTab.pick([first, second], "999999") == first
            assert LeadershipTab.pick([first, second], "not-a-number") == first

        def it_is_none_with_no_tabs():
            assert LeadershipTab.pick([], "1") is None

    def describe_add_people_tab():
        def it_puts_the_new_tab_last():
            LeadershipTabFactory(sort_order=4)
            tab = LeadershipTab.objects.add_people_tab("Board", "Who advises the council.")
            assert (tab.title, tab.intro, tab.kind, tab.sort_order) == (
                "Board",
                "Who advises the council.",
                LeadershipTab.Kind.PEOPLE,
                5,
            )

        def it_starts_at_zero_with_no_tabs():
            assert LeadershipTab.objects.add_people_tab("Board", "").sort_order == 0

    def describe_reorder():
        def it_sets_each_named_tab_to_its_index():
            a, b, c = LeadershipTabFactory(), LeadershipTabFactory(), LeadershipTabFactory()
            LeadershipTab.objects.reorder([c.pk, a.pk, b.pk])
            assert list(LeadershipTab.objects.all()) == [c, a, b]

        def it_refuses_an_id_that_is_gone_and_moves_nothing():
            a, b = LeadershipTabFactory(sort_order=0), LeadershipTabFactory(sort_order=1)
            with pytest.raises(LeadershipRowGoneError):
                LeadershipTab.objects.reorder([b.pk, 999999, a.pk])
            assert list(LeadershipTab.objects.all()) == [a, b]

    def describe_with_listed():
        def it_loads_listed_cards_members_and_lines_in_three_queries(django_assert_num_queries):
            first = LeadershipTabFactory()
            second = LeadershipTabFactory()
            LeadershipRoleFactory(listing=LeadershipListingFactory(tab=first), title="Founder")
            LeadershipListingFactory(tab=first, is_listed=False)
            LeadershipRoleFactory(listing=LeadershipListingFactory(tab=second), title="Advisor")
            with django_assert_num_queries(3):
                tabs = list(LeadershipTab.objects.with_listed())
                lines = [[[role.title for role in row.roles.all()] for row in tab.listed_listings] for tab in tabs]
                names = [[row.member.display_name for row in tab.listed_listings] for tab in tabs]
            assert lines == [[["Founder"]], [["Advisor"]]]
            assert all(len(per_tab) == 1 for per_tab in names)

    def describe_for_directory():
        def it_leaves_an_empty_people_tab_out_of_the_member_view():
            full = LeadershipTabFactory()
            LeadershipListingFactory(tab=full)
            empty = LeadershipTabFactory()
            hidden_only = LeadershipTabFactory()
            LeadershipListingFactory(tab=hidden_only, is_listed=False)
            guild_leads = LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS)
            assert LeadershipTab.objects.for_directory(include_empty=False) == [full, guild_leads]
            assert LeadershipTab.objects.for_directory(include_empty=True) == [full, empty, hidden_only, guild_leads]

    def describe_list_member():
        def it_puts_the_member_last_on_the_tab_with_the_line():
            tab = LeadershipTabFactory()
            LeadershipListingFactory(tab=tab, sort_order=4)
            LeadershipListingFactory(tab=tab, is_listed=False, sort_order=9)  # an unlisted row never sets the pace
            LeadershipListingFactory(sort_order=20)  # nor does a card on another tab
            listing = tab.list_member(MemberFactory(), "Council Secretary", "sec@x.com")
            assert (listing.tab, listing.is_listed, listing.sort_order) == (tab, True, 5)
            assert list(listing.roles.values_list("title", "email", "sort_order")) == [
                ("Council Secretary", "sec@x.com", 0)
            ]

        def it_starts_at_zero_on_an_empty_tab():
            assert LeadershipTabFactory().list_member(MemberFactory(), "Founder", "").sort_order == 0

        def it_relists_a_member_taken_off_this_tab_and_adds_only_a_title_they_do_not_hold():
            tab = LeadershipTabFactory()
            LeadershipListingFactory(tab=tab, sort_order=0)
            removed = LeadershipListingFactory(tab=tab, is_listed=False, sort_order=0)
            LeadershipRoleFactory(listing=removed, title="Old Title", email="old@x.com")
            again = tab.list_member(removed.member, "Old Title", "")
            assert again.pk == removed.pk
            assert (again.is_listed, again.sort_order) == (True, 1)
            assert list(again.roles.values_list("title", "email")) == [("Old Title", "old@x.com")]
            tab.list_member(removed.member, "New Title", "new@x.com")
            assert list(again.roles.values_list("title", "email", "sort_order")) == [
                ("Old Title", "old@x.com", 0),
                ("New Title", "new@x.com", 1),
            ]

        def it_gives_a_member_on_another_tab_a_second_card_with_separate_lines():
            leadership, board = LeadershipTabFactory(title="Leadership"), LeadershipTabFactory(title="Board")
            morlock = MemberFactory(full_legal_name="Morlock")
            first = leadership.list_member(morlock, "Guild Executor", "")
            second = board.list_member(morlock, "Board Advisor", "board@x.com")
            assert first.pk != second.pk
            assert list(first.roles.values_list("title", flat=True)) == ["Guild Executor"]
            assert list(second.roles.values_list("title", flat=True)) == ["Board Advisor"]
            assert morlock.leadership_listings.count() == 2

    def describe_reorder_listings():
        def it_moves_only_the_cards_whose_place_changed_and_stamps_them():
            tab = LeadershipTabFactory()
            a = LeadershipListingFactory(tab=tab, sort_order=0)
            b = LeadershipListingFactory(tab=tab, sort_order=1)
            c = LeadershipListingFactory(tab=tab, sort_order=2)
            stamp = timezone.now() - timedelta(days=3)
            LeadershipListing.objects.update(updated_at=stamp)
            tab.reorder_listings([b.pk, a.pk, c.pk])
            rows = {row.pk: row for row in LeadershipListing.objects.all()}
            assert [rows[a.pk].sort_order, rows[b.pk].sort_order, rows[c.pk].sort_order] == [1, 0, 2]
            assert rows[c.pk].updated_at == stamp
            assert rows[a.pk].updated_at > stamp and rows[b.pk].updated_at > stamp

        def it_refuses_a_card_from_another_tab():
            tab = LeadershipTabFactory()
            mine = LeadershipListingFactory(tab=tab, sort_order=0)
            elsewhere = LeadershipListingFactory(sort_order=5)
            with pytest.raises(LeadershipRowGoneError):
                tab.reorder_listings([elsewhere.pk, mine.pk])
            mine.refresh_from_db()
            elsewhere.refresh_from_db()
            assert (mine.sort_order, elsewhere.sort_order) == (0, 5)


def describe_LeadershipListing():
    def it_allows_one_card_per_tab_and_member():
        listing = LeadershipListingFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            LeadershipListingFactory(tab=listing.tab, member=listing.member)

    def it_deletes_a_tabs_cards_and_lines_with_the_tab_and_keeps_the_others():
        member = MemberFactory()
        gone = LeadershipListingFactory(member=member)
        kept = LeadershipListingFactory(member=member)
        LeadershipRoleFactory(listing=gone, title="Going")
        LeadershipRoleFactory(listing=kept, title="Staying")
        gone.tab.delete()
        assert list(LeadershipListing.objects.values_list("pk", flat=True)) == [kept.pk]
        assert list(LeadershipRole.objects.values_list("title", flat=True)) == ["Staying"]

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

    def describe_on_tabs_for():
        def it_lists_the_members_cards_on_show_in_tab_order_with_tab_and_lines(django_assert_num_queries):
            member = MemberFactory()
            later = LeadershipListingFactory(member=member, tab=LeadershipTabFactory(sort_order=5))
            earlier = LeadershipListingFactory(member=member, tab=LeadershipTabFactory(sort_order=1))
            LeadershipRoleFactory(listing=earlier, title="Guild Executor")
            LeadershipListingFactory(member=member, is_listed=False)
            LeadershipListingFactory()  # someone else
            with django_assert_num_queries(2):
                rows = list(LeadershipListing.objects.on_tabs_for(member))
                seen = [(row.tab.title, [role.title for role in row.roles.all()]) for row in rows]
            assert rows == [earlier, later]
            assert seen == [(earlier.tab.title, ["Guild Executor"]), (later.tab.title, [])]

    def describe_add_role():
        def it_adds_the_line_under_the_last_one():
            listing = LeadershipListingFactory()
            LeadershipRoleFactory(listing=listing, sort_order=4)
            assert listing.add_role("Advisor", "a@x.com").sort_order == 5
            assert LeadershipListingFactory().add_role("First", "").sort_order == 0

    def describe_remove_from_tab():
        def it_hides_the_card_keeps_the_lines_and_moves_the_stamp():
            listing = LeadershipListingFactory()
            LeadershipRoleFactory(listing=listing, title="Founder")
            stamp = timezone.now() - timedelta(days=3)
            LeadershipListing.objects.filter(pk=listing.pk).update(updated_at=stamp)
            listing.refresh_from_db()
            listing.remove_from_tab()
            listing.refresh_from_db()
            assert listing.is_listed is False
            assert listing.updated_at > stamp
            assert list(listing.roles.values_list("title", flat=True)) == ["Founder"]

    def describe___str__():
        def it_names_the_member_the_tab_and_whether_they_are_listed():
            member = MemberFactory(full_legal_name="Ada Lovelace")
            listing = LeadershipListingFactory(member=member, tab=LeadershipTabFactory(title="Council"))
            assert str(listing) == "Ada Lovelace on Council (listed)"
            listing.is_listed = False
            assert str(listing) == "Ada Lovelace on Council (unlisted)"


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


def describe_Member_leadership_candidates():
    def it_offers_everyone_not_listed_on_the_tab_by_name():
        tab = LeadershipTabFactory()
        LeadershipListingFactory(tab=tab, member=MemberFactory(full_legal_name="Listed Lou"))
        LeadershipListingFactory(tab=tab, is_listed=False, member=MemberFactory(full_legal_name="Zed Removed"))
        LeadershipListingFactory(member=MemberFactory(full_legal_name="Other Tab Olive"))
        MemberFactory(full_legal_name="Ada Never")
        names = list(Member.objects.leadership_candidates(tab).values_list("full_legal_name", flat=True))
        assert "Listed Lou" not in names
        assert names.index("Ada Never") < names.index("Other Tab Olive") < names.index("Zed Removed")

    def it_tests_the_tab_and_the_listed_flag_on_the_same_card():
        """Listed on another tab and unlisted here is still a candidate here: one subquery, not two joins."""
        tab = LeadershipTabFactory()
        member = MemberFactory(full_legal_name="Split Sam")
        LeadershipListingFactory(tab=tab, member=member, is_listed=False)
        LeadershipListingFactory(member=member, is_listed=True)
        assert member in Member.objects.leadership_candidates(tab)
