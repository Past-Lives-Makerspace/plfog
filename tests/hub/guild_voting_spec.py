"""BDD specs for the persistent guild voting feature."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from hub.forms import VotePreferenceForm
from membership.vote_calculator import compute_live_standings, compute_new_votes_since
from membership.cycle import get_cycle_context
from membership.models import Member, VotePreference
from tests.membership.factories import (
    FundingSnapshotFactory,
    GuildFactory,
    MemberFactory,
    VotePreferenceFactory,
)


# ---------------------------------------------------------------------------
# describe_get_cycle_context
# ---------------------------------------------------------------------------


def describe_get_cycle_context():
    def it_returns_correct_labels_for_a_regular_month():
        fixed = dt.datetime(2026, 3, 15, 12, 0, 0, tzinfo=dt.timezone.utc)
        with patch("membership.cycle.timezone") as mock_tz:
            mock_tz.now.return_value = fixed
            ctx = get_cycle_context()

        assert ctx["current_cycle_label"] == "March 2026"
        assert "March 31, 2026" in ctx["cycle_closes_on"]
        assert "April 1, 2026" in ctx["next_cycle_begins"]

    def it_handles_december_rollover_to_january():
        fixed = dt.datetime(2026, 12, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
        with patch("membership.cycle.timezone") as mock_tz:
            mock_tz.now.return_value = fixed
            ctx = get_cycle_context()

        assert ctx["current_cycle_label"] == "December 2026"
        assert "December 31, 2026" in ctx["cycle_closes_on"]
        assert "January 1, 2027" in ctx["next_cycle_begins"]


# ---------------------------------------------------------------------------
# describe_guild_voting_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_guild_voting_view():
    def it_requires_login(client: Client):
        response = client.get("/guilds/voting/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_shows_form_for_active_member(client: Client):
        User.objects.create_user(username="voter", password="pass")
        GuildFactory(name="Alpha")
        client.login(username="voter", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert response.context["form"] is not None
        assert response.context["member"] is not None

    def it_shows_message_when_no_member(client: Client):
        user = User.objects.create_user(username="nomember", password="pass")
        client.login(username="nomember", password="pass")
        Member.objects.filter(user=user).delete()

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert response.context["member"] is None
        assert response.context["form"] is None
        msgs = list(response.context["messages"])
        assert any("not linked" in str(m) for m in msgs)

    def it_submits_new_vote(client: Client):
        user = User.objects.create_user(username="newvoter", password="pass")
        member = user.member
        g1 = GuildFactory(name="Wood")
        g2 = GuildFactory(name="Metal")
        g3 = GuildFactory(name="Clay")
        client.login(username="newvoter", password="pass")

        response = client.post(
            "/guilds/voting/",
            {"guild_1st": g1.pk, "guild_2nd": g2.pk, "guild_3rd": g3.pk},
            follow=True,
        )

        assert response.status_code == 200
        assert VotePreference.objects.filter(member=member).exists()
        pref = VotePreference.objects.get(member=member)
        assert pref.guild_1st == g1
        assert pref.guild_2nd == g2
        assert pref.guild_3rd == g3
        msgs = list(response.context["messages"])
        assert any("submitted" in str(m) for m in msgs)

    def it_updates_existing_vote(client: Client):
        user = User.objects.create_user(username="updater", password="pass")
        member = user.member
        g1 = GuildFactory(name="Fiber")
        g2 = GuildFactory(name="Print")
        g3 = GuildFactory(name="Laser")
        g_new = GuildFactory(name="Ceramics")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        client.login(username="updater", password="pass")

        client.post(
            "/guilds/voting/",
            {"guild_1st": g_new.pk, "guild_2nd": g2.pk, "guild_3rd": g3.pk},
            follow=True,
        )

        pref = VotePreference.objects.get(member=member)
        assert pref.guild_1st == g_new
        assert VotePreference.objects.filter(member=member).count() == 1

    def it_shows_updated_message_when_preference_exists(client: Client):
        user = User.objects.create_user(username="updatemsg", password="pass")
        member = user.member
        g1 = GuildFactory(name="AA")
        g2 = GuildFactory(name="BB")
        g3 = GuildFactory(name="CC")
        g_new = GuildFactory(name="DD")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        client.login(username="updatemsg", password="pass")

        response = client.post(
            "/guilds/voting/",
            {"guild_1st": g_new.pk, "guild_2nd": g2.pk, "guild_3rd": g3.pk},
            follow=True,
        )

        msgs = list(response.context["messages"])
        assert any("updated" in str(m) for m in msgs)

    def it_rejects_duplicate_guilds(client: Client):
        User.objects.create_user(username="dupeuser", password="pass")
        g1 = GuildFactory(name="Same")
        g2 = GuildFactory(name="Other")
        client.login(username="dupeuser", password="pass")

        response = client.post(
            "/guilds/voting/",
            {"guild_1st": g1.pk, "guild_2nd": g1.pk, "guild_3rd": g2.pk},
        )

        assert response.status_code == 200
        assert response.context["form"].errors
        assert not VotePreference.objects.filter(member__user__username="dupeuser").exists()

    def it_shows_current_preferences(client: Client):
        user = User.objects.create_user(username="showpref", password="pass")
        member = user.member
        g1 = GuildFactory(name="Guild A")
        g2 = GuildFactory(name="Guild B")
        g3 = GuildFactory(name="Guild C")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        client.login(username="showpref", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert response.context["preference"] is not None
        assert response.context["preference"].guild_1st == g1

    def it_shows_latest_results(client: Client):
        User.objects.create_user(username="results_viewer", password="pass")
        snap = FundingSnapshotFactory(
            cycle_label="March 2026",
            funding_pool=Decimal("100.00"),
            results={"total_pool": 100, "results": []},
        )
        client.login(username="results_viewer", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert response.context["latest_snapshot"] == snap

    def it_does_not_render_contributor_count_on_results_section(client: Client):
        """Privacy: contributor count is admin-only and must not leak to members."""
        User.objects.create_user(username="results_privacy", password="pass")
        FundingSnapshotFactory(
            cycle_label="March 2026",
            funding_pool=Decimal("100.00"),
            contributor_count=7,
            results={"total_pool": 100, "results": []},
        )
        client.login(username="results_privacy", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert b"contributing member" not in response.content

    def it_includes_cycle_info_in_context(client: Client):
        User.objects.create_user(username="cycleuser", password="pass")
        client.login(username="cycleuser", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert "current_cycle_label" in response.context
        assert "cycle_closes_on" in response.context
        assert "next_cycle_begins" in response.context
        assert response.context["current_cycle_label"] != ""
        assert response.context["cycle_closes_on"] != ""
        assert response.context["next_cycle_begins"] != ""


# ---------------------------------------------------------------------------
# describe_snapshot_history_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_snapshot_history_view():
    def it_requires_login(client: Client):
        response = client.get("/guilds/voting/history/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_shows_list_of_snapshots(client: Client):
        User.objects.create_user(username="histuser", password="pass")
        snap1 = FundingSnapshotFactory(cycle_label="January 2026", funding_pool=Decimal("100.00"))
        snap2 = FundingSnapshotFactory(cycle_label="February 2026", funding_pool=Decimal("120.00"))
        client.login(username="histuser", password="pass")

        response = client.get("/guilds/voting/history/")

        assert response.status_code == 200
        snapshots = list(response.context["snapshots"])
        assert snap1 in snapshots
        assert snap2 in snapshots

    def it_shows_empty_state_when_no_snapshots(client: Client):
        User.objects.create_user(username="emptyuser", password="pass")
        client.login(username="emptyuser", password="pass")

        response = client.get("/guilds/voting/history/")

        assert response.status_code == 200
        assert list(response.context["snapshots"]) == []

    def it_is_accessible_to_non_staff_members(client: Client):
        User.objects.create_user(username="plainmember", password="pass", is_staff=False)
        client.login(username="plainmember", password="pass")

        response = client.get("/guilds/voting/history/")

        assert response.status_code == 200

    def it_does_not_render_contributor_count_column(client: Client):
        """Privacy: the history table must not expose contributor counts."""
        User.objects.create_user(username="hist_privacy", password="pass")
        FundingSnapshotFactory(
            cycle_label="January 2026",
            funding_pool=Decimal("100.00"),
            contributor_count=7,
        )
        client.login(username="hist_privacy", password="pass")

        response = client.get("/guilds/voting/history/")

        assert response.status_code == 200
        assert b"Contributors" not in response.content


# ---------------------------------------------------------------------------
# describe_snapshot_detail_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_snapshot_detail_view():
    def it_requires_login(client: Client):
        snap = FundingSnapshotFactory()
        response = client.get(f"/guilds/voting/history/{snap.pk}/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_shows_snapshot_data(client: Client):
        User.objects.create_user(username="detailuser", password="pass")
        snap = FundingSnapshotFactory(
            cycle_label="March 2026",
            funding_pool=Decimal("200.00"),
            contributor_count=5,
            results={"total_pool": 200, "results": []},
        )
        client.login(username="detailuser", password="pass")

        response = client.get(f"/guilds/voting/history/{snap.pk}/")

        assert response.status_code == 200
        assert response.context["snapshot"] == snap

    def it_does_not_render_contributor_count(client: Client):
        """Privacy: snapshot detail must not expose contributor counts to members."""
        User.objects.create_user(username="detail_privacy", password="pass")
        snap = FundingSnapshotFactory(
            cycle_label="March 2026",
            funding_pool=Decimal("200.00"),
            contributor_count=7,
            results={"total_pool": 200, "results": []},
        )
        client.login(username="detail_privacy", password="pass")

        response = client.get(f"/guilds/voting/history/{snap.pk}/")

        assert response.status_code == 200
        assert b"Contributors:" not in response.content

    def it_returns_404_for_invalid_pk(client: Client):
        User.objects.create_user(username="notfounduser", password="pass")
        client.login(username="notfounduser", password="pass")

        response = client.get("/guilds/voting/history/99999/")

        assert response.status_code == 404

    def it_is_accessible_to_non_staff_members(client: Client):
        User.objects.create_user(username="regularuser", password="pass", is_staff=False)
        snap = FundingSnapshotFactory(cycle_label="April 2026")
        client.login(username="regularuser", password="pass")

        response = client.get(f"/guilds/voting/history/{snap.pk}/")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# describe_vote_preference_model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_vote_preference_model():
    def it_has_str_representation():
        g1 = GuildFactory(name="Wood")
        g2 = GuildFactory(name="Metal")
        g3 = GuildFactory(name="Clay")
        member = MemberFactory(preferred_name="Alice")
        pref = VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        assert str(pref) == "Alice: Wood / Metal / Clay"

    def it_casts_a_ballot_with_only_a_first_choice():
        g1 = GuildFactory(name="Solo")
        member = MemberFactory()

        pref, created = VotePreference.objects.cast_ballot(member, guild_1st=g1)

        assert created
        assert pref.guild_1st == g1
        assert pref.guild_2nd is None
        assert pref.guild_3rd is None

    def it_str_omits_blank_choices():
        g1 = GuildFactory(name="Wood")
        member = MemberFactory(preferred_name="Bob")

        pref = VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=None, guild_3rd=None)

        assert str(pref) == "Bob: Wood"


@pytest.mark.django_db
def describe_funding_snapshot_model():
    def it_has_str_representation():
        snap = FundingSnapshotFactory(cycle_label="March 2026", funding_pool=Decimal("100.00"))
        assert "March 2026" in str(snap)
        assert "$100.00" in str(snap)


# ---------------------------------------------------------------------------
# describe_vote_preference_form
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_vote_preference_form():
    def it_validates_three_different_guilds():
        g1 = GuildFactory(name="Wood", is_active=True)
        g2 = GuildFactory(name="Metal", is_active=True)
        g3 = GuildFactory(name="Fiber", is_active=True)

        form = VotePreferenceForm(data={"guild_1st": g1.pk, "guild_2nd": g2.pk, "guild_3rd": g3.pk})

        assert form.is_valid()

    def it_rejects_duplicate_guild_selections():
        g1 = GuildFactory(name="Dup1", is_active=True)
        g2 = GuildFactory(name="Other1", is_active=True)

        form = VotePreferenceForm(data={"guild_1st": g1.pk, "guild_2nd": g1.pk, "guild_3rd": g2.pk})

        assert not form.is_valid()
        assert form.non_field_errors()

    def it_rejects_all_same_guilds():
        g1 = GuildFactory(name="AllSame", is_active=True)

        form = VotePreferenceForm(data={"guild_1st": g1.pk, "guild_2nd": g1.pk, "guild_3rd": g1.pk})

        assert not form.is_valid()

    def it_rejects_a_first_choice_only_ballot():
        g1 = GuildFactory(name="Only1", is_active=True)

        form = VotePreferenceForm(data={"guild_1st": g1.pk, "guild_2nd": "", "guild_3rd": ""})

        assert not form.is_valid()
        assert "guild_2nd" in form.errors
        assert "guild_3rd" in form.errors

    def it_rejects_a_ballot_missing_the_third_choice():
        g1 = GuildFactory(name="First", is_active=True)
        g2 = GuildFactory(name="Second", is_active=True)

        form = VotePreferenceForm(data={"guild_1st": g1.pk, "guild_2nd": g2.pk, "guild_3rd": ""})

        assert not form.is_valid()
        assert "guild_3rd" in form.errors

    def it_requires_a_first_choice():
        form = VotePreferenceForm(data={"guild_1st": "", "guild_2nd": "", "guild_3rd": ""})

        assert not form.is_valid()
        assert "guild_1st" in form.errors

    def it_rejects_a_third_choice_without_a_second():
        g1 = GuildFactory(name="First", is_active=True)
        g3 = GuildFactory(name="Third", is_active=True)

        form = VotePreferenceForm(data={"guild_1st": g1.pk, "guild_2nd": "", "guild_3rd": g3.pk})

        assert not form.is_valid()
        assert "guild_2nd" in form.errors

    def it_rejects_a_duplicate_across_two_chosen_tiers():
        g1 = GuildFactory(name="Dup", is_active=True)

        form = VotePreferenceForm(data={"guild_1st": g1.pk, "guild_2nd": g1.pk, "guild_3rd": ""})

        assert not form.is_valid()
        assert form.non_field_errors()

    def it_excludes_inactive_guilds():
        GuildFactory(name="Inactive", is_active=False)
        g_active = GuildFactory(name="Active", is_active=True)

        form = VotePreferenceForm()
        guild_choices = list(form.fields["guild_1st"].queryset)

        assert g_active in guild_choices
        assert all(g.is_active for g in guild_choices)


# ---------------------------------------------------------------------------
# describe_compute_live_standings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_compute_live_standings():
    def it_returns_empty_list_when_no_votes_exist():
        GuildFactory(name="Empty Guild")

        result = compute_live_standings()

        assert result == []

    def it_calculates_points_with_correct_weights():
        g1 = GuildFactory(name="First Pick")
        g2 = GuildFactory(name="Second Pick")
        g3 = GuildFactory(name="Third Pick")
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        result = compute_live_standings()

        by_name = {r["guild_name"]: r for r in result}
        assert by_name["First Pick"]["total_points"] == 5
        assert by_name["Second Pick"]["total_points"] == 3
        assert by_name["Third Pick"]["total_points"] == 2

    def it_sorts_by_total_points_descending():
        g1 = GuildFactory(name="Top")
        g2 = GuildFactory(name="Mid")
        g3 = GuildFactory(name="Low")
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        result = compute_live_standings()

        names = [r["guild_name"] for r in result]
        assert names == ["Top", "Mid", "Low"]

    def it_sets_bar_pct_relative_to_leader():
        g1 = GuildFactory(name="Leader")
        g2 = GuildFactory(name="Follower")
        g3 = GuildFactory(name="Trailing")
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        result = compute_live_standings()

        by_name = {r["guild_name"]: r for r in result}
        # Leader (5 pts) gets 100%, others scale proportionally
        assert by_name["Leader"]["bar_pct"] == 100.0
        assert by_name["Follower"]["bar_pct"] == round(3 / 5 * 100, 1)  # 60.0
        assert by_name["Trailing"]["bar_pct"] == round(2 / 5 * 100, 1)  # 40.0

    def it_excludes_guilds_with_zero_votes():
        g1 = GuildFactory(name="Voted")
        g2 = GuildFactory(name="Also Voted")
        g3 = GuildFactory(name="Also Also Voted")
        GuildFactory(name="No Votes")
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        result = compute_live_standings()

        names = [r["guild_name"] for r in result]
        assert "No Votes" not in names
        assert len(names) == 3

    def it_aggregates_points_across_multiple_voters():
        g1 = GuildFactory(name="Popular")
        g2 = GuildFactory(name="Moderate")
        g3 = GuildFactory(name="Niche")
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g3, guild_3rd=g2)

        result = compute_live_standings()

        by_name = {r["guild_name"]: r for r in result}
        # g1: 5+5=10, g2: 3+2=5, g3: 2+3=5
        assert by_name["Popular"]["total_points"] == 10
        assert by_name["Moderate"]["total_points"] == 5
        assert by_name["Niche"]["total_points"] == 5

    def it_is_used_for_context_under_new_vote_standings_key(client: Client):
        User.objects.create_user(username="ctxviewer", password="pass")
        g1 = GuildFactory(name="CtxA")
        g2 = GuildFactory(name="CtxB")
        g3 = GuildFactory(name="CtxC")
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        client.login(username="ctxviewer", password="pass")

        response = client.get("/guilds/voting/")

        assert "new_vote_standings" in response.context
        assert response.context["new_vote_standings"]

    def it_excludes_votes_from_members_without_linked_user():
        """Votes backfilled from Airtable for unlinked members must not inflate standings."""
        g1 = GuildFactory(name="Signed Up Pick")
        g2 = GuildFactory(name="Signed Up 2nd")
        g3 = GuildFactory(name="Signed Up 3rd")
        g_backfill = GuildFactory(name="Backfill Only")

        # One vote from a signed-up member (factory auto-links a User)
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        # One vote from an Airtable-imported member who never signed up
        unlinked = MemberFactory(user=None)
        VotePreferenceFactory(
            member=unlinked,
            guild_1st=g_backfill,
            guild_2nd=g_backfill,  # dup ok at the model level; validation is in the form
            guild_3rd=g_backfill,
            signed_up=False,
        )

        result = compute_live_standings()

        names = {r["guild_name"] for r in result}
        assert "Backfill Only" not in names
        assert names == {"Signed Up Pick", "Signed Up 2nd", "Signed Up 3rd"}

    def it_does_not_cross_multiply_counts_when_one_guild_appears_at_multiple_ranks():
        """Regression: three Count annotations cross-joined reverse FKs and multiplied.

        With 1 first-place, 2 second-place, and 3 third-place votes for the same
        guild, the raw JOIN produced 1*2*3 = 6 rows; each un-distinct Count
        returned 6, yielding 6*5 + 6*3 + 6*2 = 60 instead of 1*5 + 2*3 + 3*2 = 17.
        """
        target = GuildFactory(name="Popular Across Ranks")
        # Filler guilds so each VotePreference has three distinct slots.
        fillers = [GuildFactory(name=f"Filler {i}") for i in range(6)]

        # 1 first-place vote for target
        VotePreferenceFactory(guild_1st=target, guild_2nd=fillers[0], guild_3rd=fillers[1])
        # 2 second-place votes for target
        VotePreferenceFactory(guild_1st=fillers[0], guild_2nd=target, guild_3rd=fillers[1])
        VotePreferenceFactory(guild_1st=fillers[1], guild_2nd=target, guild_3rd=fillers[2])
        # 3 third-place votes for target
        VotePreferenceFactory(guild_1st=fillers[2], guild_2nd=fillers[3], guild_3rd=target)
        VotePreferenceFactory(guild_1st=fillers[3], guild_2nd=fillers[4], guild_3rd=target)
        VotePreferenceFactory(guild_1st=fillers[4], guild_2nd=fillers[5], guild_3rd=target)

        result = compute_live_standings()

        by_name = {r["guild_name"]: r for r in result}
        assert by_name["Popular Across Ranks"]["total_points"] == 1 * 5 + 2 * 3 + 3 * 2


# ---------------------------------------------------------------------------
# describe_compute_new_votes_since
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_compute_new_votes_since():
    def it_returns_all_signed_up_votes_when_since_is_none():
        g1 = GuildFactory(name="Fresh 1st")
        g2 = GuildFactory(name="Fresh 2nd")
        g3 = GuildFactory(name="Fresh 3rd")
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        result = compute_new_votes_since(None)

        names = {r["guild_name"] for r in result}
        assert names == {"Fresh 1st", "Fresh 2nd", "Fresh 3rd"}

    def it_excludes_votes_older_than_since():
        g1 = GuildFactory(name="Old Pick")
        g2 = GuildFactory(name="Shared")
        g3 = GuildFactory(name="Also Shared")
        old_vote = VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        # Force old_vote to look like it was cast before the cutoff
        old_ts = dt.datetime(2025, 12, 1, tzinfo=dt.timezone.utc)
        VotePreference.objects.filter(pk=old_vote.pk).update(updated_at=old_ts)

        cutoff = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        result = compute_new_votes_since(cutoff)

        assert result == []

    def it_includes_votes_newer_than_since():
        g1 = GuildFactory(name="New Top")
        g2 = GuildFactory(name="New Mid")
        g3 = GuildFactory(name="New Low")
        # New vote: auto_now sets updated_at=now → newer than the historic cutoff
        VotePreferenceFactory(guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        cutoff = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        result = compute_new_votes_since(cutoff)

        by_name = {r["guild_name"]: r for r in result}
        assert by_name["New Top"]["total_points"] == 5

    def it_excludes_votes_from_members_without_linked_user():
        unlinked = MemberFactory(user=None)
        g1 = GuildFactory(name="Unlinked Pick")
        VotePreferenceFactory(member=unlinked, guild_1st=g1, guild_2nd=g1, guild_3rd=g1, signed_up=False)

        result = compute_new_votes_since(None)

        assert result == []
