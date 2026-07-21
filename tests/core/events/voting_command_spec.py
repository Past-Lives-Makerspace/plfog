"""Specs for the ``/voting`` slash command handler (membership.discord_commands)."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

from core.events.discord_replies import hub_url
from membership.discord_commands import _BAR_WIDTH, _STANDINGS_CAP, VOTING, _bar, _voting
from membership.models import VotePreference
from tests.membership.factories import GuildFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _reply(member) -> dict:
    return _voting({}, member)


def _description(member) -> str:
    return _reply(member)["data"]["embeds"][0]["description"]


def describe_voting_command_definition():
    def it_is_linked_only_ephemeral_and_immediate():
        assert VOTING.name == "voting"
        assert (VOTING.requires_link, VOTING.ephemeral, VOTING.defer, VOTING.scope) == (True, True, False, "guild")


def describe_bar():
    def it_renders_the_leader_as_all_filled_blocks():
        assert _bar(100.0) == "█" * _BAR_WIDTH

    def it_renders_half_as_six_filled_six_empty():
        assert _bar(50.0) == "█" * 6 + "░" * 6

    def it_never_renders_an_all_empty_bar_for_a_nonzero_guild():
        bar = _bar(0.5)
        assert bar.startswith("█")
        assert len(bar) == _BAR_WIDTH


def describe_voting():
    def it_shows_standings_ballot_cycle_and_the_voting_page_button(linked_member, settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        member = linked_member()
        g1 = GuildFactory(name="Fiber Arts")
        g2 = GuildFactory(name="Woodshop")
        g3 = GuildFactory(name="Ceramics")
        # Three voters: g1 = 5+5+5 = 15 pts, g2 = 3+3+3 = 9 pts, g3 = 2+2+2 = 6 pts.
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        fixed = dt.datetime(2026, 7, 14, 14, 0, 0, tzinfo=dt.timezone.utc)
        with patch("membership.cycle.timezone") as mock_tz:
            mock_tz.now.return_value = fixed
            result = _reply(member)

        embed = result["data"]["embeds"][0]
        assert embed["title"] == "Guild funding — July 2026"
        assert "This cycle closes **July 31, 2026**." in embed["description"]
        assert "🥇 `" + "█" * _BAR_WIDTH + "` **Fiber Arts** — 15 pts" in embed["description"]
        assert "**Woodshop** — 9 pts" in embed["description"]
        assert "**Ceramics** — 6 pts" in embed["description"]
        assert embed["footer"]["text"] == "Weighting: 1st = 5 pts · 2nd = 3 pts · 3rd = 2 pts"
        button = result["data"]["components"][0]["components"][0]
        assert button["style"] == 5
        assert button["label"] == "Open the voting page"
        assert button["url"] == hub_url("hub_guild_voting")
        assert button["url"] == "https://members.example/guilds/voting/"
        assert result["data"]["flags"] == 64  # ephemeral

    def it_scales_bars_relative_to_the_leader(linked_member):
        member = linked_member()
        g1 = GuildFactory(name="Leader Guild")
        g2 = GuildFactory(name="Runner Up")
        g3 = GuildFactory(name="Trailing Guild")
        # g1 = 5 pts (100% → 12 blocks), g2 = 3 pts (60% → 7 blocks), g3 = 2 pts (40% → 5 blocks).
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        description = _description(member)

        assert f"`{'█' * 12}` **Leader Guild**" in description
        assert f"`{'█' * 7 + '░' * 5}` **Runner Up**" in description
        assert f"`{'█' * 5 + '░' * 7}` **Trailing Guild**" in description

    def it_shows_the_members_own_ballot_with_rank_points_and_updated_at(linked_member):
        member = linked_member()
        g1 = GuildFactory(name="My First")
        g2 = GuildFactory(name="My Second")
        g3 = GuildFactory(name="My Third")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        fixed = dt.datetime(2026, 7, 14, 21, 0, 0, tzinfo=dt.timezone.utc)  # 2:00 PM in America/Los_Angeles
        VotePreference.objects.filter(member=member).update(updated_at=fixed)
        member.refresh_from_db()

        description = _description(member)

        assert "**Your ballot**" in description
        assert "1st — My First · 5 pts" in description
        assert "2nd — My Second · 3 pts" in description
        assert "3rd — My Third · 2 pts" in description
        assert "_Last updated Tue Jul 14, 2:00 PM_" in description

    def it_never_leaks_another_members_ballot(linked_member):
        member = linked_member()
        other = linked_member()
        g1 = GuildFactory(name="Their First")
        g2 = GuildFactory(name="Their Second")
        g3 = GuildFactory(name="Their Third")
        VotePreferenceFactory(member=other, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        description = _description(member)

        # The other member's vote shapes the standings but never the ballot block.
        assert "1st — Their First" not in description
        assert "You haven't voted yet" in description

    def describe_when_the_member_has_not_voted():
        def it_shows_the_nudge_and_still_offers_the_button(linked_member):
            member = linked_member()
            g1 = GuildFactory(name="Someone Elses Pick")
            g2 = GuildFactory(name="Another Pick")
            g3 = GuildFactory(name="Third Pick")
            VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

            result = _reply(member)

            description = result["data"]["embeds"][0]["description"]
            assert "You haven't voted yet" in description
            assert "It takes 30 seconds on the voting page below." in description
            assert "1st —" not in description
            button = result["data"]["components"][0]["components"][0]
            assert button["style"] == 5

    def describe_when_no_votes_have_been_cast():
        def it_says_the_standings_are_wide_open_and_keeps_the_frame(linked_member):
            member = linked_member()
            GuildFactory(name="Quiet Guild")

            fixed = dt.datetime(2026, 7, 14, 14, 0, 0, tzinfo=dt.timezone.utc)
            with patch("membership.cycle.timezone") as mock_tz:
                mock_tz.now.return_value = fixed
                result = _reply(member)

            embed = result["data"]["embeds"][0]
            assert "No votes yet this cycle — the standings are wide open. Be the first!" in embed["description"]
            assert embed["title"] == "Guild funding — July 2026"
            assert "This cycle closes **July 31, 2026**." in embed["description"]
            assert "Weighting:" in embed["footer"]["text"]
            assert result["data"]["components"][0]["components"][0]["style"] == 5

    def describe_with_more_guilds_than_the_cap():
        def it_collapses_the_overflow_and_stays_under_the_embed_limit(linked_member):
            member = linked_member()
            shared_2nd = GuildFactory(name="Shared Second")
            shared_3rd = GuildFactory(name="Shared Third")
            # 16 distinct first-choice guilds + the two shared ones = 18 guilds with points.
            for i in range(16):
                VotePreferenceFactory(
                    guild_1st=GuildFactory(name=f"Overflow Guild {i}"),
                    guild_2nd=shared_2nd,
                    guild_3rd=shared_3rd,
                )

            description = _description(member)

            bar_rows = [line for line in description.splitlines() if "█" in line or "░" in line]
            assert len(bar_rows) == _STANDINGS_CAP
            assert f"…and {18 - _STANDINGS_CAP} more on the voting page" in description
            assert len(description) <= 4096
