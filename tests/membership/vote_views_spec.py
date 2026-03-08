"""Tests for guild voting views."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from membership.models import GuildVote, VotingSession
from tests.membership.factories import GuildFactory, MemberFactory, VotingSessionFactory

User = get_user_model()

pytestmark = pytest.mark.django_db

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
def member_user():
    """Create a user with an active member profile."""
    user = User.objects.create_user(username="voter", password="pw", email="voter@example.com")
    member = MemberFactory(user=user, full_legal_name="Test Voter")
    return user, member


@pytest.fixture()
def member_client(member_user):
    """Logged-in client for a member."""
    user, _ = member_user
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture()
def guilds():
    """Create guilds for voting."""
    return [
        GuildFactory(name="Ceramics", is_active=True),
        GuildFactory(name="Glass", is_active=True),
        GuildFactory(name="Wood", is_active=True),
        GuildFactory(name="Metal", is_active=True),
    ]


# ---------------------------------------------------------------------------
# Member: vote view
# ---------------------------------------------------------------------------


def describe_vote_view():
    def it_requires_login(client):
        resp = client.get("/voting/")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url or "/login/" in resp.url

    def it_renders_voting_form(member_client, open_session, guilds):
        resp = member_client.get("/voting/")
        assert resp.status_code == 200
        assert b"rank your top 3 guilds" in resp.content

    def it_submits_vote_successfully(member_client, member_user, open_session, guilds):
        _, member = member_user
        with patch("membership.vote_views.airtable_sync") as mock_at:
            mock_at.sync_vote_to_airtable.return_value = "recVOTE1"
            resp = member_client.post(
                "/voting/",
                {
                    "guild_1st": "Ceramics",
                    "guild_2nd": "Glass",
                    "guild_3rd": "Wood",
                },
            )
        assert resp.status_code == 200
        assert GuildVote.objects.filter(session=open_session, member=member).count() == 3
        open_session.refresh_from_db()
        assert open_session.votes_cast == 1

    def it_prevents_double_voting(member_client, member_user, open_session, guilds):
        _, member = member_user
        with patch("membership.vote_views.airtable_sync") as mock_at:
            mock_at.sync_vote_to_airtable.return_value = ""
            member_client.post(
                "/voting/",
                {
                    "guild_1st": "Ceramics",
                    "guild_2nd": "Glass",
                    "guild_3rd": "Wood",
                },
            )
        resp = member_client.get("/voting/")
        assert resp.status_code == 200
        assert b"already" in resp.content.lower()

    def it_shows_closed_when_no_open_session(member_client):
        resp = member_client.get("/voting/")
        assert resp.status_code == 200
        assert b"no voting session" in resp.content.lower()

    def it_redirects_user_without_member(client, open_session):
        user = User.objects.create_user(username="nomember", password="pw")
        client.force_login(user)
        resp = client.get("/voting/")
        assert resp.status_code == 302

    def it_rejects_inactive_member(client, open_session):
        user = User.objects.create_user(username="former", password="pw")
        MemberFactory(user=user, status="former")
        client.force_login(user)
        resp = client.get("/voting/")
        assert resp.status_code == 302

    def it_rejects_invalid_form_data(member_client, open_session, guilds):
        resp = member_client.post(
            "/voting/",
            {
                "guild_1st": "Ceramics",
                "guild_2nd": "Ceramics",
                "guild_3rd": "Glass",
            },
        )
        assert resp.status_code == 200
        assert GuildVote.objects.filter(session=open_session).count() == 0


# ---------------------------------------------------------------------------
# Public: voting_results view
# ---------------------------------------------------------------------------


def describe_voting_results():
    def it_shows_results_for_calculated_session(client):
        session = VotingSessionFactory(
            status=VotingSession.Status.CALCULATED,
            results_summary={
                "total_pool": 50,
                "total_points": 50,
                "votes_cast": 5,
                "results": [
                    {
                        "guild_name": "Ceramics",
                        "votes_1st": 3,
                        "votes_2nd": 1,
                        "votes_3rd": 1,
                        "total_points": 22,
                        "share_pct": 44.0,
                        "funding": 22.0,
                    },
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
        resp = admin_client.post(
            "/voting/manage/create-session/",
            {
                "name": "New Session",
                "open_date": today.isoformat(),
                "close_date": (today + timedelta(days=7)).isoformat(),
            },
        )
        assert resp.status_code == 302
        session = VotingSession.objects.get(name="New Session")
        assert session.eligible_member_count == 2
        assert session.airtable_record_id == "recSESS1"

    def it_rerenders_form_on_invalid_post(admin_client):
        resp = admin_client.post(
            "/voting/manage/create-session/",
            {
                "name": "",
                "open_date": "2026-03-10",
                "close_date": "2026-03-05",
            },
        )
        assert resp.status_code == 200  # re-rendered form, not redirect

    @patch("membership.vote_views.airtable_sync")
    def it_handles_empty_airtable_record_id(mock_at, admin_client):
        mock_at.get_eligible_members.return_value = MOCK_MEMBERS
        mock_at.sync_session_to_airtable.return_value = ""

        today = date.today()
        resp = admin_client.post(
            "/voting/manage/create-session/",
            {
                "name": "No AT ID",
                "open_date": today.isoformat(),
                "close_date": (today + timedelta(days=7)).isoformat(),
            },
        )
        assert resp.status_code == 302
        session = VotingSession.objects.get(name="No AT ID")
        assert session.airtable_record_id == ""


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
        member = MemberFactory()
        guild = GuildFactory(name="Skip Priority Guild")
        GuildVote.objects.create(
            session=open_session,
            member=member,
            guild=guild,
            priority=4,
        )
        resp = admin_client.get(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 200

    def it_renders_preview_with_votes(admin_client, open_session):
        member = MemberFactory()
        g1 = GuildFactory(name="Ceramics")
        g2 = GuildFactory(name="Glass")
        g3 = GuildFactory(name="Wood")
        for priority, guild in enumerate([g1, g2, g3], start=1):
            GuildVote.objects.create(
                session=open_session,
                member=member,
                guild=guild,
                priority=priority,
            )

        resp = admin_client.get(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 200

    @patch("membership.vote_views.airtable_sync")
    def it_saves_results_on_post(mock_at, admin_client, open_session):
        mock_at.sync_session_to_airtable.return_value = ""
        member = MemberFactory()
        g1 = GuildFactory(name="Calc Ceramics")
        g2 = GuildFactory(name="Calc Glass")
        g3 = GuildFactory(name="Calc Wood")
        for priority, guild in enumerate([g1, g2, g3], start=1):
            GuildVote.objects.create(
                session=open_session,
                member=member,
                guild=guild,
                priority=priority,
            )

        resp = admin_client.post(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 302
        open_session.refresh_from_db()
        assert open_session.status == VotingSession.Status.CALCULATED
        assert open_session.results_summary["votes_cast"] == 1
        assert len(open_session.results_summary["results"]) == 3

    @patch("membership.vote_views.airtable_sync")
    def it_excludes_free_members_from_pool(mock_at, admin_client, open_session):
        """Non-paying members vote but don't contribute to funding pool."""
        from decimal import Decimal

        from tests.membership.factories import MembershipPlanFactory

        mock_at.sync_session_to_airtable.return_value = ""
        free_plan = MembershipPlanFactory(name="Work-Trade", monthly_price=Decimal("0.00"))
        paying_member = MemberFactory()
        free_member = MemberFactory(membership_plan=free_plan)
        g1 = GuildFactory(name="Pool Ceramics")
        g2 = GuildFactory(name="Pool Glass")
        g3 = GuildFactory(name="Pool Wood")
        for member in [paying_member, free_member]:
            for priority, guild in enumerate([g1, g2, g3], start=1):
                GuildVote.objects.create(session=open_session, member=member, guild=guild, priority=priority)

        resp = admin_client.post(f"/voting/manage/calculate/{open_session.pk}/")
        assert resp.status_code == 302
        open_session.refresh_from_db()
        assert open_session.results_summary["votes_cast"] == 2
        # Pool = 1 paying × $10 = $10, not 2 × $10 = $20
        assert open_session.results_summary["total_pool"] == 10


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
