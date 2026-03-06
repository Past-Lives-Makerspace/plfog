"""Tests for VotingSession model."""

from datetime import timedelta

import pytest
from django.utils import timezone

from membership.models import VotingSession
from tests.membership.factories import VotingSessionFactory

pytestmark = pytest.mark.django_db


def describe_VotingSession():
    def it_creates_with_factory():
        session = VotingSessionFactory(name="Test Session")
        assert session.pk is not None
        assert session.name == "Test Session"

    def it_has_str_representation():
        session = VotingSessionFactory(name="March 2026")
        assert str(session) == "March 2026"

    def it_defaults_to_draft_status():
        session = VotingSessionFactory()
        assert session.status == VotingSession.Status.DRAFT

    def it_defaults_to_zero_votes():
        session = VotingSessionFactory()
        assert session.votes_cast == 0
        assert session.eligible_member_count == 0

    def it_stores_results_summary_as_json():
        session = VotingSessionFactory(results_summary={"total_pool": 100})
        session.refresh_from_db()
        assert session.results_summary == {"total_pool": 100}

    def it_orders_by_open_date_descending():
        today = timezone.now().date()
        s1 = VotingSessionFactory(name="Older", open_date=today - timedelta(days=10))
        s2 = VotingSessionFactory(name="Newer", open_date=today)
        sessions = list(VotingSession.objects.all())
        assert sessions == [s2, s1]


def describe_is_open_for_voting():
    def it_returns_true_when_open_and_within_dates():
        today = timezone.now().date()
        session = VotingSessionFactory(
            status=VotingSession.Status.OPEN,
            open_date=today - timedelta(days=1),
            close_date=today + timedelta(days=1),
        )
        assert session.is_open_for_voting is True

    def it_returns_true_on_open_date():
        today = timezone.now().date()
        session = VotingSessionFactory(
            status=VotingSession.Status.OPEN,
            open_date=today,
            close_date=today + timedelta(days=1),
        )
        assert session.is_open_for_voting is True

    def it_returns_true_on_close_date():
        today = timezone.now().date()
        session = VotingSessionFactory(
            status=VotingSession.Status.OPEN,
            open_date=today - timedelta(days=1),
            close_date=today,
        )
        assert session.is_open_for_voting is True

    def it_returns_false_when_not_open_status():
        today = timezone.now().date()
        session = VotingSessionFactory(
            status=VotingSession.Status.DRAFT,
            open_date=today - timedelta(days=1),
            close_date=today + timedelta(days=1),
        )
        assert session.is_open_for_voting is False

    def it_returns_false_before_open_date():
        today = timezone.now().date()
        session = VotingSessionFactory(
            status=VotingSession.Status.OPEN,
            open_date=today + timedelta(days=1),
            close_date=today + timedelta(days=5),
        )
        assert session.is_open_for_voting is False

    def it_returns_false_after_close_date():
        today = timezone.now().date()
        session = VotingSessionFactory(
            status=VotingSession.Status.OPEN,
            open_date=today - timedelta(days=10),
            close_date=today - timedelta(days=1),
        )
        assert session.is_open_for_voting is False


def describe_can_transition_to():
    def it_allows_draft_to_open():
        session = VotingSessionFactory(status=VotingSession.Status.DRAFT)
        assert session.can_transition_to("open") is True

    def it_disallows_draft_to_closed():
        session = VotingSessionFactory(status=VotingSession.Status.DRAFT)
        assert session.can_transition_to("closed") is False

    def it_allows_open_to_closed():
        session = VotingSessionFactory(status=VotingSession.Status.OPEN)
        assert session.can_transition_to("closed") is True

    def it_disallows_open_to_calculated():
        session = VotingSessionFactory(status=VotingSession.Status.OPEN)
        assert session.can_transition_to("calculated") is False

    def it_allows_closed_to_open():
        session = VotingSessionFactory(status=VotingSession.Status.CLOSED)
        assert session.can_transition_to("open") is True

    def it_allows_closed_to_calculated():
        session = VotingSessionFactory(status=VotingSession.Status.CLOSED)
        assert session.can_transition_to("calculated") is True

    def it_disallows_transition_from_calculated():
        session = VotingSessionFactory(status=VotingSession.Status.CALCULATED)
        assert session.can_transition_to("open") is False
        assert session.can_transition_to("closed") is False
        assert session.can_transition_to("draft") is False
