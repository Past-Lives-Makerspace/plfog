"""Tests for guild voting views."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from membership.models import Guild, GuildVote, VotingSession
from membership.vote_tokens import generate_vote_token
from tests.membership.factories import GuildFactory, VotingSessionFactory

User = get_user_model()

pytestmark = pytest.mark.django_db

MOCK_MEMBER = {
    "record_id": "recTEST001",
    "name": "Test Member",
    "email": "test@example.com",
    "status": "Active",
    "role": "Standard",
    "monthly_amount": 150,
}

MOCK_GUILDS = [
    {"record_id": "recG1", "name": "Ceramics"},
    {"record_id": "recG2", "name": "Glass"},
    {"record_id": "recG3", "name": "Wood"},
    {"record_id": "recG4", "name": "Metal"},
]

MOCK_MEMBERS = [
    {"record_id": "recTEST001", "name": "Test Member", "email": "test@example.com"},
    {"record_id": "recTEST002", "name": "Member Two", "email": "two@example.com"},
]


@pytest.fixture()
def client():
    return Client()


@pytest.fixture()
def admin_client():
    user = User.objects.create_superuser(username="vote-admin", password="pw", email="admin@example.com")
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture()
def open_session():
    today = timezone.now().date()
    return VotingSessionFactory(
        name="Test Session",
        status=VotingSession.Status.OPEN,
        open_date=today - timedelta(days=1),
        close_date=today + timedelta(days=5),
        eligible_member_count=10,
    )


@pytest.fixture()
def vote_token(open_session):
    return generate_vote_token("recTEST001", open_session.pk)


# ---------------------------------------------------------------------------
# Public: vote view
# ---------------------------------------------------------------------------


def describe_vote_view():
    @patch("membership.vote_views.airtable_sync")
    def it_renders_voting_form(mock_at, client, open_session, vote_token):
        mock_at.get_member.return_value = MOCK_MEMBER
        mock_at.get_voteable_guilds.return_value = MOCK_GUILDS
        resp = client.get(f"/voting/vote/{vote_token}/")
        assert resp.status_code == 200
        assert b"rank your top 3 guilds" in resp.content

    @patch("membership.vote_views.airtable_sync")
    def it_submits_vote_successfully(mock_at, client, open_session, vote_token):
        mock_at.get_member.return_value = MOCK_MEMBER
        mock_at.get_voteable_guilds.return_value = MOCK_GUILDS
        mock_at.sync_vote_to_airtable.return_value = "recVOTE1"

        resp = client.post(f"/voting/vote/{vote_token}/", {
            "guild_1st": "Ceramics",
            "guild_2nd": "Glass",
            "guild_3rd": "Wood",
        })
        assert resp.status_code == 200
        assert b"vote_success" in resp.content or b"Ceramics" in resp.content

        assert GuildVote.objects.filter(session=open_session).count() == 3
        assert Guild.objects.filter(name="Ceramics").exists()
        open_session.refresh_from_db()
        assert open_session.votes_cast == 1

    @patch("membership.vote_views.airtable_sync")
    def it_prevents_double_voting(mock_at, client, open_session, vote_token):
        mock_at.get_member.return_value = MOCK_MEMBER
        mock_at.get_voteable_guilds.return_value = MOCK_GUILDS
        mock_at.sync_vote_to_airtable.return_value = ""

        # First vote
        client.post(f"/voting/vote/{vote_token}/", {
            "guild_1st": "Ceramics",
            "guild_2nd": "Glass",
            "guild_3rd": "Wood",
        })

        # Second attempt
        resp = client.get(f"/voting/vote/{vote_token}/")
        assert resp.status_code == 200
        assert b"already" in resp.content.lower()

    def it_rejects_bad_token(client):
        resp = client.get("/voting/vote/bad-token/")
        assert resp.status_code == 400

    def it_rejects_expired_token(client, open_session):
        from django.test import override_settings

        with override_settings(VOTE_TOKEN_MAX_AGE=0):
            token = generate_vote_token("recTEST001", open_session.pk)
            resp = client.get(f"/voting/vote/{token}/")
        assert resp.status_code == 200
        assert b"expired" in resp.content.lower()

    def it_shows_closed_for_non_open_session(client):
        session = VotingSessionFactory(status=VotingSession.Status.CLOSED)
        token = generate_vote_token("recTEST001", session.pk)
        resp = client.get(f"/voting/vote/{token}/")
        assert resp.status_code == 200
        assert b"closed" in resp.content.lower()

    @patch("membership.vote_views.airtable_sync")
    def it_returns_503_on_airtable_failure(mock_at, client, open_session, vote_token):
        mock_at.get_member.side_effect = Exception("Airtable down")
        resp = client.get(f"/voting/vote/{vote_token}/")
        assert resp.status_code == 503

    @patch("membership.vote_views.airtable_sync")
    def it_rejects_inactive_member(mock_at, client, open_session, vote_token):
        mock_at.get_member.return_value = {**MOCK_MEMBER, "status": "Former"}
        resp = client.get(f"/voting/vote/{vote_token}/")
        assert resp.status_code == 400

    @patch("membership.vote_views.airtable_sync")
    def it_rejects_invalid_form_data(mock_at, client, open_session, vote_token):
        mock_at.get_member.return_value = MOCK_MEMBER
        mock_at.get_voteable_guilds.return_value = MOCK_GUILDS

        resp = client.post(f"/voting/vote/{vote_token}/", {
            "guild_1st": "Ceramics",
            "guild_2nd": "Ceramics",
            "guild_3rd": "Glass",
        })
        assert resp.status_code == 200
        # Should re-render form with errors, not create votes
        assert GuildVote.objects.filter(session=open_session).count() == 0

    def it_returns_404_for_nonexistent_session(client):
        token = generate_vote_token("recTEST001", 99999)
        resp = client.get(f"/voting/vote/{token}/")
        assert resp.status_code == 404

    @patch("membership.vote_views.airtable_sync")
    def it_handles_race_condition_on_post(mock_at, client, open_session):
        """Double-check guard (line 84) triggers on POST when vote was inserted concurrently."""
        mock_at.get_member.return_value = MOCK_MEMBER
        mock_at.get_voteable_guilds.return_value = MOCK_GUILDS

        token = generate_vote_token("recRACE001", open_session.pk)

        # We need the first filter().exists() to return False, then the second to return True.
        # Strategy: patch GuildVote.objects.filter so the first call for this member returns
        # an empty QS, but before the second call, real votes exist in DB.
        original_filter = GuildVote.objects.filter
        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            qs = original_filter(*args, **kwargs)
            if kwargs.get("member_airtable_id") == "recRACE001":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    # First check: pretend no votes exist yet
                    return GuildVote.objects.none()
                elif call_count["n"] == 2:
                    # Before second check, insert votes to simulate race
                    g1, _ = Guild.objects.get_or_create(name="Ceramics")
                    g2, _ = Guild.objects.get_or_create(name="Glass")
                    g3, _ = Guild.objects.get_or_create(name="Wood")
                    for pri, g in enumerate([g1, g2, g3], 1):
                        GuildVote.objects.create(
                            session=open_session,
                            member_airtable_id="recRACE001",
                            member_name="Race",
                            guild=g,
                            priority=pri,
                        )
                    # Now return the real queryset which will find the votes
                    return original_filter(*args, **kwargs)
            return qs

        with patch.object(type(GuildVote.objects), "filter", side_effect=side_effect_filter):
            resp = client.post(f"/voting/vote/{token}/", {
                "guild_1st": "Ceramics",
                "guild_2nd": "Glass",
                "guild_3rd": "Wood",
            })
        assert resp.status_code == 200
        assert b"already" in resp.content.lower()


# ---------------------------------------------------------------------------
# Public: voting_results view
# ---------------------------------------------------------------------------


def describe_voting_results():
    def it_shows_results_for_calculated_session(client):
        session = VotingSessionFactory(
            status=VotingSession.Status.CALCULATED,
            results_summary={
                "total_pool": 100,
                "total_weighted": 50,
                "non_vote_dollars": 50,
                "votes_cast": 5,
                "eligible_member_count": 10,
                "results": [
                    {"guild_name": "Ceramics", "votes_1st": 3, "votes_2nd": 1, "votes_3rd": 1,
                     "weighted_amount": 22, "disbursement": 44.0},
                ],
            },
        )
        resp = client.get(f"/voting/results/{session.pk}/")
        assert resp.status_code == 200
        assert b"Ceramics" in resp.content

    def it_blocks_results_for_non_calculated_session(client):
        session = VotingSessionFactory(status=VotingSession.Status.OPEN)
        resp = client.get(f"/voting/results/{session.pk}/")
        assert resp.status_code == 200
        assert b"not yet available" in resp.content.lower()

    def it_returns_404_for_nonexistent_session(client):
        resp = client.get("/voting/results/99999/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Admin: voting_dashboard
# ---------------------------------------------------------------------------


def describe_voting_dashboard():
    def it_requires_staff(client):
        resp = client.get("/voting/manage/")
        assert resp.status_code == 302  # redirects to login

    @patch("membership.vote_views.airtable_sync")
    def it_renders_for_staff(mock_at, admin_client):
        mock_at.get_eligible_members.return_value = MOCK_MEMBERS
        VotingSessionFactory()
        resp = admin_client.get("/voting/manage/")
        assert resp.status_code == 200

    @patch("membership.vote_views.airtable_sync")
    def it_shows_active_session_info(mock_at, admin_client, open_session):
        mock_at.get_eligible_members.return_value = MOCK_MEMBERS
        resp = admin_client.get("/voting/manage/")
        assert resp.status_code == 200

    @patch("membership.vote_views.airtable_sync")
    def it_handles_airtable_error_gracefully(mock_at, admin_client, open_session):
        mock_at.get_eligible_members.side_effect = Exception("Airtable down")
        resp = admin_client.get("/voting/manage/")
        assert resp.status_code == 200  # still renders, shows error


# ---------------------------------------------------------------------------
# Admin: voting_create_session
# ---------------------------------------------------------------------------


def describe_voting_create_session():
    def it_requires_staff(client):
        resp = client.get("/voting/manage/create-session/")
        assert resp.status_code == 302

    def it_renders_form_for_staff(admin_client):
        resp = admin_client.get("/voting/manage/create-session/")
        assert resp.status_code == 200

    @patch("membership.vote_views.airtable_sync")
    def it_creates_session_on_post(mock_at, admin_client):
        mock_at.get_eligible_members.return_value = MOCK_MEMBERS
        mock_at.sync_session_to_airtable.return_value = "recSESS1"

        today = date.today()
        resp = admin_client.post("/voting/manage/create-session/", {
            "name": "New Session",
            "open_date": today.isoformat(),
            "close_date": (today + timedelta(days=7)).isoformat(),
        })
        assert resp.status_code == 302
        session = VotingSession.objects.get(name="New Session")
        assert session.eligible_member_count == 2
        assert session.airtable_record_id == "recSESS1"

    def it_rerenders_form_on_invalid_post(admin_client):
        resp = admin_client.post("/voting/manage/create-session/", {
            "name": "",
            "open_date": "2026-03-10",
            "close_date": "2026-03-05",
        })
        assert resp.status_code == 200  # re-rendered form, not redirect

    @patch("membership.vote_views.airtable_sync")
    def it_handles_empty_airtable_record_id(mock_at, admin_client):
        mock_at.get_eligible_members.return_value = MOCK_MEMBERS
        mock_at.sync_session_to_airtable.return_value = ""

        today = date.today()
        resp = admin_client.post("/voting/manage/create-session/", {
            "name": "No AT ID",
            "open_date": today.isoformat(),
            "close_date": (today + timedelta(days=7)).isoformat(),
        })
        assert resp.status_code == 302
        session = VotingSession.objects.get(name="No AT ID")
        assert session.airtable_record_id == ""


# ---------------------------------------------------------------------------
# Admin: voting_send_emails
# ---------------------------------------------------------------------------


def describe_voting_send_emails():
    def it_requires_staff(client, open_session):
        resp = client.get(f"/voting/manage/send-emails/{open_session.pk}/")
        assert resp.status_code == 302

    @patch("membership.vote_views.airtable_sync")
    def it_renders_preview(mock_at, admin_client, open_session):
        mock_at.get_eligible_members.return_value = MOCK_MEMBERS
        resp = admin_client.get(f"/voting/manage/send-emails/{open_session.pk}/")
        assert resp.status_code == 200

    @patch("membership.vote_views.vote_emails")
    @patch("membership.vote_views.airtable_sync")
    def it_sends_emails_on_post(mock_at, mock_emails, admin_client):
        session = VotingSessionFactory(status=VotingSession.Status.DRAFT)
        mock_at.get_eligible_members.return_value = MOCK_MEMBERS
        mock_at.sync_session_to_airtable.return_value = ""
        mock_emails.send_voting_emails.return_value = {"sent_count": 2, "errors": []}

        resp = admin_client.post(f"/voting/manage/send-emails/{session.pk}/")
        assert resp.status_code == 302
        session.refresh_from_db()
        assert session.status == VotingSession.Status.OPEN

    @patch("membership.vote_views.vote_emails")
    @patch("membership.vote_views.airtable_sync")
    def it_reports_email_errors(mock_at, mock_emails, admin_client, open_session):
        mock_at.get_eligible_members.return_value = MOCK_MEMBERS
        mock_emails.send_voting_emails.return_value = {
            "sent_count": 1,
            "errors": ["Failed to send to Bob"],
        }
        resp = admin_client.post(f"/voting/manage/send-emails/{open_session.pk}/")
        assert resp.status_code == 302

    @patch("membership.vote_views.airtable_sync")
    def it_shows_members_without_email(mock_at, admin_client, open_session):
        members_mixed = MOCK_MEMBERS + [{"record_id": "rec003", "name": "No Email"}]
        mock_at.get_eligible_members.return_value = members_mixed
        resp = admin_client.get(f"/voting/manage/send-emails/{open_session.pk}/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Admin: voting_calculate
# ---------------------------------------------------------------------------


def describe_voting_calculate():
    def it_requires_staff(client, open_session):
        resp = client.get(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 302

    def it_renders_preview_with_no_votes(admin_client, open_session):
        resp = admin_client.get(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 200

    def it_skips_votes_with_unknown_priority(admin_client, open_session):
        guild = GuildFactory(name="Skip Priority Guild")
        # Create a vote with priority=4 which is not in {1,2,3}
        GuildVote.objects.create(
            session=open_session,
            member_airtable_id="recSKIP",
            member_name="Skip",
            guild=guild,
            priority=4,
        )
        resp = admin_client.get(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 200

    def it_renders_preview_with_votes(admin_client, open_session):
        g1 = GuildFactory(name="Ceramics")
        g2 = GuildFactory(name="Glass")
        g3 = GuildFactory(name="Wood")
        for priority, guild in enumerate([g1, g2, g3], start=1):
            GuildVote.objects.create(
                session=open_session,
                member_airtable_id="recTEST001",
                member_name="Test",
                guild=guild,
                priority=priority,
            )

        resp = admin_client.get(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 200

    @patch("membership.vote_views.airtable_sync")
    def it_saves_results_on_post(mock_at, admin_client, open_session):
        mock_at.sync_session_to_airtable.return_value = ""
        g1 = GuildFactory(name="Calc Ceramics")
        g2 = GuildFactory(name="Calc Glass")
        g3 = GuildFactory(name="Calc Wood")
        for priority, guild in enumerate([g1, g2, g3], start=1):
            GuildVote.objects.create(
                session=open_session,
                member_airtable_id="recCALC001",
                member_name="Calc Test",
                guild=guild,
                priority=priority,
            )

        resp = admin_client.post(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 302
        open_session.refresh_from_db()
        assert open_session.status == VotingSession.Status.CALCULATED
        assert open_session.results_summary["votes_cast"] == 1
        assert len(open_session.results_summary["results"]) == 3


# ---------------------------------------------------------------------------
# Admin: voting_email_results
# ---------------------------------------------------------------------------


def describe_voting_email_results():
    def it_requires_staff(client):
        session = VotingSessionFactory()
        resp = client.get(f"/voting/manage/email-results/{session.pk}/")
        assert resp.status_code == 302

    def it_redirects_if_no_results(admin_client):
        session = VotingSessionFactory(results_summary={})
        resp = admin_client.get(f"/voting/manage/email-results/{session.pk}/")
        assert resp.status_code == 302

    def it_renders_form_with_results(admin_client):
        session = VotingSessionFactory(
            status=VotingSession.Status.CALCULATED,
            results_summary={"total_pool": 100, "results": []},
        )
        resp = admin_client.get(f"/voting/manage/email-results/{session.pk}/")
        assert resp.status_code == 200

    @patch("membership.vote_views.vote_emails")
    def it_sends_results_email(mock_emails, admin_client):
        session = VotingSessionFactory(
            status=VotingSession.Status.CALCULATED,
            results_summary={"total_pool": 100, "results": [], "votes_cast": 5},
        )
        resp = admin_client.post(
            f"/voting/manage/email-results/{session.pk}/",
            {"recipients": "lead@example.com, other@example.com"},
        )
        assert resp.status_code == 302
        mock_emails.send_results_email.assert_called_once()

    @patch("membership.vote_views.vote_emails")
    def it_handles_send_failure(mock_emails, admin_client):
        mock_emails.send_results_email.side_effect = Exception("SMTP down")
        session = VotingSessionFactory(
            status=VotingSession.Status.CALCULATED,
            results_summary={"total_pool": 100, "results": [], "votes_cast": 5},
        )
        resp = admin_client.post(
            f"/voting/manage/email-results/{session.pk}/",
            {"recipients": "lead@example.com"},
        )
        assert resp.status_code == 302  # redirects with error message

    def it_rejects_empty_recipients(admin_client):
        session = VotingSessionFactory(
            status=VotingSession.Status.CALCULATED,
            results_summary={"total_pool": 100, "results": [], "votes_cast": 5},
        )
        resp = admin_client.post(
            f"/voting/manage/email-results/{session.pk}/",
            {"recipients": ""},
        )
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Admin: voting_set_status
# ---------------------------------------------------------------------------


def describe_voting_set_status():
    def it_requires_staff(client):
        session = VotingSessionFactory()
        resp = client.post(f"/voting/manage/set-status/{session.pk}/", {"status": "open"})
        assert resp.status_code == 302  # login redirect

    def it_transitions_draft_to_open(admin_client):
        session = VotingSessionFactory(status=VotingSession.Status.DRAFT)
        with patch("membership.vote_views.airtable_sync") as mock_at:
            mock_at.get_eligible_members.return_value = MOCK_MEMBERS
            mock_at.sync_session_to_airtable.return_value = ""
            resp = admin_client.post(
                f"/voting/manage/set-status/{session.pk}/",
                {"status": "open"},
            )
        assert resp.status_code == 302
        session.refresh_from_db()
        assert session.status == VotingSession.Status.OPEN

    def it_rejects_invalid_transition(admin_client):
        session = VotingSessionFactory(status=VotingSession.Status.DRAFT)
        with patch("membership.vote_views.airtable_sync") as mock_at:
            mock_at.sync_session_to_airtable.return_value = ""
            resp = admin_client.post(
                f"/voting/manage/set-status/{session.pk}/",
                {"status": "calculated"},
            )
        assert resp.status_code == 302
        session.refresh_from_db()
        assert session.status == VotingSession.Status.DRAFT  # unchanged

    def it_redirects_on_get(admin_client):
        session = VotingSessionFactory()
        resp = admin_client.get(f"/voting/manage/set-status/{session.pk}/")
        assert resp.status_code == 302

    def it_fetches_members_when_reopening_with_zero_count(admin_client):
        session = VotingSessionFactory(
            status=VotingSession.Status.CLOSED,
            eligible_member_count=0,
        )
        with patch("membership.vote_views.airtable_sync") as mock_at:
            mock_at.get_eligible_members.return_value = MOCK_MEMBERS
            mock_at.sync_session_to_airtable.return_value = ""
            resp = admin_client.post(
                f"/voting/manage/set-status/{session.pk}/",
                {"status": "open"},
            )
        assert resp.status_code == 302
        session.refresh_from_db()
        assert session.eligible_member_count == 2

    def it_skips_member_fetch_when_reopening_with_nonzero_count(admin_client):
        session = VotingSessionFactory(
            status=VotingSession.Status.CLOSED,
            eligible_member_count=15,
        )
        with patch("membership.vote_views.airtable_sync") as mock_at:
            mock_at.sync_session_to_airtable.return_value = ""
            resp = admin_client.post(
                f"/voting/manage/set-status/{session.pk}/",
                {"status": "open"},
            )
        assert resp.status_code == 302
        session.refresh_from_db()
        assert session.eligible_member_count == 15  # unchanged

    def it_handles_airtable_error_on_reopen(admin_client):
        session = VotingSessionFactory(
            status=VotingSession.Status.CLOSED,
            eligible_member_count=0,
        )
        with patch("membership.vote_views.airtable_sync") as mock_at:
            mock_at.get_eligible_members.side_effect = Exception("AT down")
            mock_at.sync_session_to_airtable.return_value = ""
            resp = admin_client.post(
                f"/voting/manage/set-status/{session.pk}/",
                {"status": "open"},
            )
        assert resp.status_code == 302
        session.refresh_from_db()
        assert session.status == VotingSession.Status.OPEN
        assert session.eligible_member_count == 0  # stays 0 on error
