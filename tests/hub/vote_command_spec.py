"""Specs for the hub ``/vote`` slash command — the ranked-ballot options + the handler.

The handler's whole promise is *parity with the hub voting page*: the same
``VotePreferenceForm`` validation and the same ``cast_ballot`` manager save, so the
side-effect cases here assert the shared path actually ran (activity signal + Airtable
push) rather than re-testing the form rules exhaustively (the form has its own specs).
"""

from __future__ import annotations

import datetime as dt
from unittest import mock
from unittest.mock import patch

import pytest

from airtable_sync import service as airtable_service
from core.models import SiteActivity
from hub.discord_commands import VOTE, _ballot_options, _vote
from membership.models import VotePreference
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _interaction(first: str, second: str, third: str) -> dict:
    """A ``/vote`` interaction carrying the three ranked guild slugs."""
    return {
        "data": {
            "name": "vote",
            "options": [
                {"name": "first", "value": first},
                {"name": "second", "value": second},
                {"name": "third", "value": third},
            ],
        }
    }


def describe_vote_command_definition():
    def it_is_linked_only_ephemeral_and_immediate():
        assert VOTE.name == "vote"
        assert (VOTE.requires_link, VOTE.ephemeral, VOTE.defer, VOTE.scope) == (True, True, False, "guild")

    def it_builds_three_required_guild_pickers_with_slug_values():
        guild = GuildFactory(name="Alpha Fiber")

        options = _ballot_options()

        assert [option["name"] for option in options] == ["first", "second", "third"]
        assert all(option["required"] for option in options)
        for option in options:
            assert {"name": "Alpha Fiber", "value": guild.slug} in option["choices"]


def describe_vote():
    def it_casts_a_first_ballot_and_confirms_it(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        member = MemberFactory()
        g1 = GuildFactory(name="Fiber Arts")
        g2 = GuildFactory(name="Woodshop")
        g3 = GuildFactory(name="Ceramics")

        fixed = dt.datetime(2026, 7, 14, 14, 0, 0, tzinfo=dt.timezone.utc)
        with patch("membership.cycle.timezone") as mock_tz:
            mock_tz.now.return_value = fixed
            result = _vote(_interaction(g1.slug, g2.slug, g3.slug), member)

        preference = VotePreference.objects.get(member=member)
        assert (preference.guild_1st, preference.guild_2nd, preference.guild_3rd) == (g1, g2, g3)
        embed = result["data"]["embeds"][0]
        assert embed["title"] == "Your ballot is in — July 2026 ✅"
        assert "This cycle closes **July 31, 2026**." in embed["description"]
        assert "1st — Fiber Arts · 5 pts" in embed["description"]
        assert "2nd — Woodshop · 3 pts" in embed["description"]
        assert "3rd — Ceramics · 2 pts" in embed["description"]
        assert "See the live standings anytime with `/voting`." in embed["description"]
        button = result["data"]["components"][0]["components"][0]
        assert button["style"] == 5
        assert button["label"] == "Open the voting page"
        assert button["url"] == "https://members.example/guilds/voting/"
        assert result["data"]["flags"] == 64  # ephemeral

    def it_overwrites_an_existing_ballot_in_place(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        member = MemberFactory()
        old1 = GuildFactory(name="Old First")
        old2 = GuildFactory(name="Old Second")
        old3 = GuildFactory(name="Old Third")
        VotePreferenceFactory(member=member, guild_1st=old1, guild_2nd=old2, guild_3rd=old3)
        new1 = GuildFactory(name="New First")

        result = _vote(_interaction(new1.slug, old2.slug, old3.slug), member)

        assert VotePreference.objects.filter(member=member).count() == 1
        preference = VotePreference.objects.get(member=member)
        assert (preference.guild_1st, preference.guild_2nd, preference.guild_3rd) == (new1, old2, old3)
        assert "Your ballot is updated" in result["data"]["embeds"][0]["title"]

    def it_runs_the_shared_hub_save_path_with_all_its_side_effects(monkeypatch):
        """Parity proof: the Airtable push in ``VotePreference.save()`` and the vote-activity
        signal both fire — exactly what a hub-page submission triggers, nothing bespoke."""
        sync_spy = mock.MagicMock(name="sync_vote_to_airtable", return_value=None)
        monkeypatch.setattr(airtable_service, "sync_vote_to_airtable", sync_spy)
        member = MemberFactory()
        g1 = GuildFactory(name="Sync First")
        g2 = GuildFactory(name="Sync Second")
        g3 = GuildFactory(name="Sync Third")

        _vote(_interaction(g1.slug, g2.slug, g3.slug), member)

        preference = VotePreference.objects.get(member=member)
        sync_spy.assert_called_once_with(preference)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.VOTE_SUBMITTED).exists()

        another = GuildFactory(name="Sync Fourth")
        _vote(_interaction(another.slug, g2.slug, g3.slug), member)

        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.VOTE_CHANGED).exists()
        assert sync_spy.call_count == 2

    def describe_with_duplicate_guilds():
        def it_rejects_with_the_forms_own_message_and_saves_nothing():
            member = MemberFactory()
            g1 = GuildFactory(name="Dupe Pick")
            g2 = GuildFactory(name="Other Pick")

            result = _vote(_interaction(g1.slug, g1.slug, g2.slug), member)

            assert "Please select three different guilds." in result["data"]["content"]
            assert "Nothing was changed" in result["data"]["content"]
            assert not VotePreference.objects.filter(member=member).exists()

    def describe_with_an_unknown_slug():
        def it_names_the_bad_pick_and_saves_nothing():
            member = MemberFactory()
            g2 = GuildFactory(name="Known Second")
            g3 = GuildFactory(name="Known Third")

            result = _vote(_interaction("no-such-guild", g2.slug, g3.slug), member)

            assert "`no-such-guild`" in result["data"]["content"]
            assert not VotePreference.objects.filter(member=member).exists()

    def describe_with_an_inactive_guild():
        def it_treats_it_like_an_unknown_guild():
            member = MemberFactory()
            inactive = GuildFactory(name="Retired Guild", is_active=False)
            g2 = GuildFactory(name="Live Second")
            g3 = GuildFactory(name="Live Third")

            result = _vote(_interaction(inactive.slug, g2.slug, g3.slug), member)

            assert f"`{inactive.slug}`" in result["data"]["content"]
            assert not VotePreference.objects.filter(member=member).exists()
