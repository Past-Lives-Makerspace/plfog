"""BDD specs for the persistent guild voting feature."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from hub.forms import VotePreferenceForm
from hub.views import _get_cycle_context
from membership.models import VotePreference
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
        with patch("hub.views.timezone") as mock_tz:
            mock_tz.now.return_value = fixed
            ctx = _get_cycle_context()

        assert ctx["current_cycle_label"] == "March 2026"
        assert "March 31, 2026" in ctx["cycle_closes_on"]
        assert "April 1, 2026" in ctx["next_cycle_begins"]

    def it_handles_december_rollover_to_january():
        fixed = dt.datetime(2026, 12, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
        with patch("hub.views.timezone") as mock_tz:
            mock_tz.now.return_value = fixed
            ctx = _get_cycle_context()

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
        user = User.objects.create_user(username="voter", password="pass")
        MemberFactory(user=user)
        GuildFactory(name="Alpha")
        client.login(username="voter", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert response.context["form"] is not None
        assert response.context["member"] is not None

    def it_shows_message_when_no_member(client: Client):
        User.objects.create_user(username="nomember", password="pass")
        client.login(username="nomember", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert response.context["member"] is None
        assert response.context["form"] is None
        msgs = list(response.context["messages"])
        assert any("not linked" in str(m) for m in msgs)

    def it_submits_new_vote(client: Client):
        user = User.objects.create_user(username="newvoter", password="pass")
        member = MemberFactory(user=user)
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
        member = MemberFactory(user=user)
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
        member = MemberFactory(user=user)
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
        user = User.objects.create_user(username="dupeuser", password="pass")
        MemberFactory(user=user)
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
        member = MemberFactory(user=user)
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
        user = User.objects.create_user(username="results_viewer", password="pass")
        MemberFactory(user=user)
        snap = FundingSnapshotFactory(
            cycle_label="March 2026",
            funding_pool=Decimal("100.00"),
            results={"total_pool": 100, "results": []},
        )
        client.login(username="results_viewer", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert response.context["latest_snapshot"] == snap

    def it_includes_cycle_info_in_context(client: Client):
        user = User.objects.create_user(username="cycleuser", password="pass")
        MemberFactory(user=user)
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
        user = User.objects.create_user(username="histuser", password="pass")
        MemberFactory(user=user)
        snap1 = FundingSnapshotFactory(cycle_label="January 2026", funding_pool=Decimal("100.00"))
        snap2 = FundingSnapshotFactory(cycle_label="February 2026", funding_pool=Decimal("120.00"))
        client.login(username="histuser", password="pass")

        response = client.get("/guilds/voting/history/")

        assert response.status_code == 200
        snapshots = list(response.context["snapshots"])
        assert snap1 in snapshots
        assert snap2 in snapshots

    def it_shows_empty_state_when_no_snapshots(client: Client):
        user = User.objects.create_user(username="emptyuser", password="pass")
        MemberFactory(user=user)
        client.login(username="emptyuser", password="pass")

        response = client.get("/guilds/voting/history/")

        assert response.status_code == 200
        assert list(response.context["snapshots"]) == []

    def it_is_accessible_to_non_staff_members(client: Client):
        user = User.objects.create_user(username="plainmember", password="pass", is_staff=False)
        MemberFactory(user=user)
        client.login(username="plainmember", password="pass")

        response = client.get("/guilds/voting/history/")

        assert response.status_code == 200


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
        user = User.objects.create_user(username="detailuser", password="pass")
        MemberFactory(user=user)
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

    def it_returns_404_for_invalid_pk(client: Client):
        user = User.objects.create_user(username="notfounduser", password="pass")
        MemberFactory(user=user)
        client.login(username="notfounduser", password="pass")

        response = client.get("/guilds/voting/history/99999/")

        assert response.status_code == 404

    def it_is_accessible_to_non_staff_members(client: Client):
        user = User.objects.create_user(username="regularuser", password="pass", is_staff=False)
        MemberFactory(user=user)
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

    def it_rejects_missing_guild_selection():
        g1 = GuildFactory(name="Only1", is_active=True)

        form = VotePreferenceForm(data={"guild_1st": g1.pk, "guild_2nd": "", "guild_3rd": ""})

        assert not form.is_valid()

    def it_excludes_inactive_guilds():
        GuildFactory(name="Inactive", is_active=False)
        g_active = GuildFactory(name="Active", is_active=True)

        form = VotePreferenceForm()
        guild_choices = list(form.fields["guild_1st"].queryset)

        assert g_active in guild_choices
        assert all(g.is_active for g in guild_choices)
