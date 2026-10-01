"""BDD specs for the Leadership Directory roster seed (#464): matching, seeding and its report.

Names here are invented; the real roster lives outside the repo and is handed to the
``seed_leadership_roster`` command at run time.
"""

from __future__ import annotations

import pytest

from membership.leadership_roster import RosterReport, find_guild, find_member, seed_roster
from membership.models import Guild, LeadershipListing, LeadershipRole
from tests.membership.factories import GuildFactory, LeadershipListingFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _roster(team=None, guild_channels=None) -> dict:
    return {"team": team or [], "guild_channels": guild_channels or []}


def _person(name: str, discord: str = "", roles=None) -> dict:
    return {"name": name, "discord": discord, "roles": roles or [{"title": "Founder", "email": "founder@example.org"}]}


def describe_find_member():
    def it_matches_every_word_of_the_name_anywhere_in_the_legal_name():
        ada = MemberFactory(full_legal_name="Augusta Ada King Lovelace ")
        MemberFactory(full_legal_name="Ada Byron")
        assert find_member("Ada Lovelace", "") == ada

    def it_treats_two_name_matches_as_ambiguous_even_with_a_handle():
        MemberFactory(full_legal_name="Ada Lovelace", discord_handle="@ada")
        MemberFactory(full_legal_name="Ada Lovelace Jr")
        assert find_member("Ada Lovelace", "@ada") is None

    def it_falls_back_to_the_handle_case_insensitively_when_the_name_matches_nobody():
        ada = MemberFactory(full_legal_name="A. L.", discord_handle="@Ada_L")
        assert find_member("Ada Lovelace", "@ada_l") == ada

    def it_matches_a_discord_mention_on_the_verified_id():
        ada = MemberFactory(full_legal_name="A. L.", discord_user_id="712772431961653299")
        assert find_member("Ada Lovelace", "<@712772431961653299>") == ada

    def it_is_none_when_the_handle_matches_two_members():
        MemberFactory(full_legal_name="A. L.", discord_handle="@ada")
        MemberFactory(full_legal_name="B. L.", discord_handle="@ada")
        assert find_member("Ada Lovelace", "@ada") is None

    def it_is_none_with_no_name_match_and_no_discord():
        MemberFactory(full_legal_name="Grace Hopper")
        assert find_member("Ada Lovelace", "") is None


def describe_find_guild():
    def it_matches_a_fragment_of_one_active_guild():
        wood = GuildFactory(name="Woodworking Guild")
        assert find_guild("Woodwork") == wood

    def it_is_none_when_the_fragment_matches_two_active_guilds():
        GuildFactory(name="Tech Guild")
        GuildFactory(name="Tech Guild Annex")
        assert find_guild("Tech") is None

    def it_ignores_inactive_guilds():
        GuildFactory(name="Writers Guild", is_active=False)
        assert find_guild("Writers") is None


def describe_seed_roster():
    def it_lists_matched_people_in_roster_order_with_their_roles():
        MemberFactory(full_legal_name="Grace Hopper")
        MemberFactory(full_legal_name="Ada Lovelace")
        roster = _roster(
            team=[
                _person("Ada Lovelace"),
                _person("Nobody Here"),
                _person(
                    "Grace Hopper",
                    roles=[
                        {"title": "Membership Director", "email": "membership@example.org"},
                        {"title": "Class Administrator", "email": ""},
                    ],
                ),
            ]
        )
        report = seed_roster(roster)
        rows = LeadershipListing.objects.listed()
        assert [(row.member.full_legal_name, row.sort_order) for row in rows] == [
            ("Ada Lovelace", 0),
            ("Grace Hopper", 2),
        ]
        grace_roles = LeadershipRole.objects.filter(listing__member__full_legal_name="Grace Hopper")
        assert list(grace_roles.values_list("title", "email", "sort_order")) == [
            ("Membership Director", "membership@example.org", 0),
            ("Class Administrator", "", 1),
        ]
        assert report.listed == ["Ada Lovelace", "Grace Hopper"]
        assert report.unmatched_people == ["Nobody Here"]

    def it_leaves_a_member_who_already_has_a_listing_alone():
        existing = LeadershipListingFactory(member__full_legal_name="Ada Lovelace", is_listed=False, sort_order=7)
        report = seed_roster(_roster(team=[_person("Ada Lovelace")]))
        existing.refresh_from_db()
        assert (existing.is_listed, existing.sort_order, existing.roles.count()) == (False, 7, 0)
        assert report.already_listed == ["Ada Lovelace"]
        assert report.listed == []

    def it_fills_blank_channel_names_and_keeps_set_ones():
        wood = GuildFactory(name="Woodworking Guild")
        glass = GuildFactory(name="Glass Guild", discord_channel_name="#glass-live")
        lead = MemberFactory()
        leather = GuildFactory(name="Leatherwork Guild", guild_lead=lead)
        report = seed_roster(
            _roster(
                guild_channels=[
                    {"guild": "Woodwork", "channel": "#woodworkers"},
                    {"guild": "Glass", "channel": "#glass-post"},
                    {"guild": "Leather", "channel": "#leather"},
                    {"guild": "Nowhere", "channel": "#nowhere"},
                ]
            )
        )
        assert Guild.objects.get(pk=wood.pk).discord_channel_name == "#woodworkers"
        assert Guild.objects.get(pk=glass.pk).discord_channel_name == "#glass-live"
        leather.refresh_from_db()
        assert (leather.discord_channel_name, leather.guild_lead) == ("#leather", lead)
        assert report.channels_set == ["Woodworking Guild", "Leatherwork Guild"]
        assert report.channels_kept == ["Glass Guild"]
        assert report.unmatched_guilds == ["Nowhere"]

    def it_is_idempotent():
        MemberFactory(full_legal_name="Ada Lovelace")
        GuildFactory(name="Woodworking Guild")
        roster = _roster(team=[_person("Ada Lovelace")], guild_channels=[{"guild": "Woodwork", "channel": "#w"}])
        seed_roster(roster)
        second = seed_roster(roster)
        assert (LeadershipListing.objects.count(), LeadershipRole.objects.count()) == (1, 1)
        assert (second.listed, second.already_listed, second.channels_set, second.channels_kept) == (
            [],
            ["Ada Lovelace"],
            [],
            ["Woodworking Guild"],
        )

    def it_reports_the_same_on_a_dry_run_and_writes_nothing():
        MemberFactory(full_legal_name="Ada Lovelace")
        wood = GuildFactory(name="Woodworking Guild")
        roster = _roster(team=[_person("Ada Lovelace")], guild_channels=[{"guild": "Woodwork", "channel": "#w"}])
        report = seed_roster(roster, dry_run=True)
        assert (report.listed, report.channels_set) == (["Ada Lovelace"], ["Woodworking Guild"])
        assert LeadershipListing.objects.count() == 0
        assert Guild.objects.get(pk=wood.pk).discord_channel_name == ""

    def it_fails_loudly_on_a_roster_missing_a_key():
        with pytest.raises(KeyError):
            seed_roster({"team": [{"name": "Ada Lovelace"}], "guild_channels": []})


def describe_RosterReport_lines():
    def it_always_says_what_was_done_and_names_leftovers_only_when_there_are_any():
        assert RosterReport().lines() == ["Listed: nobody new", "Channel names set: none"]
        full = RosterReport(
            listed=["Ada"],
            already_listed=["Grace"],
            unmatched_people=["Nobody"],
            channels_set=["Wood"],
            channels_kept=["Glass"],
            unmatched_guilds=["Nowhere"],
        )
        assert full.lines() == [
            "Listed: Ada",
            "Already listed, left alone: Grace",
            "No single member matched, add by hand: Nobody",
            "Channel names set: Wood",
            "Channel names already set, kept: Glass",
            "No single active guild matched, set by hand: Nowhere",
        ]
