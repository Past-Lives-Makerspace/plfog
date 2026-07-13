"""BDD specs for the CommunityEvent review lifecycle: submit / approve / request-changes
/ decline / withdraw / publish, their guards, and the workflow notifications they emit."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import EventDelivery
from membership.models import CommunityEvent, InvalidEventTransition
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory


def _user(username: str, email: str = "") -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=email or f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _fresh_event(guild) -> CommunityEvent:
    """An unsaved guild event, ready to be proposed."""
    return CommunityEvent(
        title="Forge Night",
        event_type=CommunityEvent.EventType.GUILD_MEETING,
        guild=guild,
        starts_at=timezone.now() + timedelta(days=10),
        ends_at=timezone.now() + timedelta(days=10, hours=2),
    )


def _delivery_count(event_key: str) -> int:
    return EventDelivery.objects.filter(event_key=event_key, channel="in_app").count()


@pytest.mark.django_db
def describe_submit_for_review():
    def it_sets_pending_records_the_proposer_and_notifies_reviewers():
        lead = _user("lead", "lead@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop", "prop@example.com")
        event = _fresh_event(guild)
        event.submit_for_review(submitted_by=proposer)
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.submitted_by == proposer
        assert _delivery_count("event.submitted") == 1

    def it_clears_a_prior_review_verdict_on_resubmit():
        lead = _user("lead2", "lead2@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop2", "prop2@example.com")
        event = _fresh_event(guild)
        event.submit_for_review(submitted_by=proposer)
        event.request_changes(reviewer=lead, notes="Please move it later.")
        event.submit_for_review(submitted_by=proposer)
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.review_notes == ""
        assert event.reviewed_by is None

    def it_re_notifies_reviewers_on_resubmit_via_a_distinct_period():
        lead = _user("lead3", "lead3@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop3", "prop3@example.com")
        event = _fresh_event(guild)
        event.submit_for_review(submitted_by=proposer)
        event.request_changes(reviewer=lead, notes="Tweak the time.")
        event.submit_for_review(submitted_by=proposer)
        # Two distinct submit rounds → two reviewer notifications (not deduped away).
        assert _delivery_count("event.submitted") == 2

    def it_raises_from_a_published_event():
        event = CommunityEventFactory()  # PUBLISHED
        with pytest.raises(InvalidEventTransition):
            event.submit_for_review(submitted_by=_user("p"))


@pytest.mark.django_db
def describe_approve():
    def it_publishes_records_the_reviewer_and_notifies_the_proposer():
        reviewer = _user("rev", "rev@example.com")
        proposer = _user("subm", "subm@example.com")
        event = CommunityEventFactory(pending=True, submitted_by=proposer)
        event.approve(reviewer=reviewer)
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.reviewed_by == reviewer
        assert event.reviewed_at is not None
        # publish() marked it as needing a Google push (Wave 2 wiring), without pushing.
        assert event.sync_state == CommunityEvent.SyncState.PENDING
        assert _delivery_count("event.approved") == 1

    def it_raises_from_a_declined_event():
        event = CommunityEventFactory(declined=True)
        with pytest.raises(InvalidEventTransition):
            event.approve(reviewer=_user("r"))


@pytest.mark.django_db
def describe_request_changes():
    def it_sets_changes_requested_with_the_note_and_notifies_the_proposer():
        reviewer = _user("rc", "rc@example.com")
        proposer = _user("submrc", "submrc@example.com")
        event = CommunityEventFactory(pending=True, submitted_by=proposer)
        event.request_changes(reviewer=reviewer, notes="Add a location.")
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.CHANGES_REQUESTED
        assert event.review_notes == "Add a location."
        assert _delivery_count("event.changes_requested") == 1

    def it_requires_a_note():
        event = CommunityEventFactory(pending=True)
        with pytest.raises(ValueError):
            event.request_changes(reviewer=_user("r"), notes="   ")

    def it_raises_from_a_non_pending_event():
        event = CommunityEventFactory(moderation_state=CommunityEvent.ModerationState.CHANGES_REQUESTED)
        with pytest.raises(InvalidEventTransition):
            event.request_changes(reviewer=_user("r"), notes="x")


@pytest.mark.django_db
def describe_decline():
    def it_sets_declined_with_the_note_and_notifies_the_proposer():
        reviewer = _user("dc", "dc@example.com")
        proposer = _user("submdc", "submdc@example.com")
        event = CommunityEventFactory(pending=True, submitted_by=proposer)
        event.decline(reviewer=reviewer, notes="Not a fit right now.")
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.DECLINED
        assert _delivery_count("event.declined") == 1
        assert event not in CommunityEvent.objects.published()

    def it_requires_a_note():
        event = CommunityEventFactory(pending=True)
        with pytest.raises(ValueError):
            event.decline(reviewer=_user("r"), notes="")

    def it_raises_from_a_published_event():
        event = CommunityEventFactory()  # PUBLISHED
        with pytest.raises(InvalidEventTransition):
            event.decline(reviewer=_user("r"), notes="nope")


@pytest.mark.django_db
def describe_withdraw():
    def it_deletes_a_pending_proposal():
        event = CommunityEventFactory(pending=True)
        pk = event.pk
        event.withdraw(by=event.submitted_by)
        assert not CommunityEvent.objects.filter(pk=pk).exists()

    def it_raises_from_a_published_event():
        event = CommunityEventFactory()  # PUBLISHED
        with pytest.raises(InvalidEventTransition):
            event.withdraw(by=_user("w"))


@pytest.mark.django_db
def describe_publish():
    def it_flips_idle_to_pending_and_announces_once():
        member = _user("gm", "gm@example.com")
        guild = GuildFactory()
        from tests.membership.factories import GuildMembershipFactory

        GuildMembershipFactory(guild=guild, member=member.member)
        event = CommunityEventFactory(guild=guild)
        event.publish(actor=None)
        event.refresh_from_db()
        assert event.sync_state == CommunityEvent.SyncState.PENDING
        # A second publish neither double-announces (period dedup) nor re-flips.
        event.publish(actor=None)
        assert _delivery_count("event.guild_published") == 1
