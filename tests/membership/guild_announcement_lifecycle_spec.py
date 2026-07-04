"""BDD specs for the GuildAnnouncement review lifecycle: submit / approve / request-changes
/ decline / withdraw, their guards, and the workflow notifications they emit.

A member's proposal starts Pending and posts nothing until a lead/admin approves it; approval
fires ``GuildAnnouncement.notify_members`` (the guild-page post + opt-out member email + guild
Discord) and tells the proposer it's up.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import EventDelivery
from membership.models import GuildAnnouncement, InvalidAnnouncementTransition
from tests.membership.factories import (
    GuildAnnouncementFactory,
    GuildFactory,
    GuildMembershipFactory,
    MembershipPlanFactory,
)


def _user(username: str, email: str = "") -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=email or f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _fresh(guild) -> GuildAnnouncement:
    """An unsaved announcement, ready to be proposed."""
    return GuildAnnouncement(guild=guild, title="New anvil Saturday", body="Come help install it.")


def _delivery_count(event_key: str) -> int:
    return EventDelivery.objects.filter(event_key=event_key, channel="in_app").count()


@pytest.mark.django_db
def describe_submit_for_review():
    def it_sets_pending_records_the_proposer_as_author_and_notifies_leadership():
        lead = _user("lead", "lead@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop", "prop@example.com")
        announcement = _fresh(guild)
        announcement.submit_for_review(submitted_by=proposer)
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PENDING
        assert announcement.submitted_by == proposer
        assert announcement.author == proposer
        assert _delivery_count("guild_announcement.submitted") == 1

    def it_clears_a_prior_verdict_on_resubmit():
        lead = _user("lead2", "lead2@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop2", "prop2@example.com")
        announcement = _fresh(guild)
        announcement.submit_for_review(submitted_by=proposer)
        announcement.request_changes(reviewer=lead, notes="Add the time.")
        announcement.submit_for_review(submitted_by=proposer)
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PENDING
        assert announcement.review_notes == ""
        assert announcement.reviewed_by is None

    def it_re_notifies_leadership_on_resubmit_via_a_distinct_period():
        lead = _user("lead3", "lead3@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop3", "prop3@example.com")
        announcement = _fresh(guild)
        announcement.submit_for_review(submitted_by=proposer)
        announcement.request_changes(reviewer=lead, notes="Tweak it.")
        announcement.submit_for_review(submitted_by=proposer)
        assert _delivery_count("guild_announcement.submitted") == 2

    def it_raises_from_a_published_announcement():
        announcement = GuildAnnouncementFactory()  # PUBLISHED
        with pytest.raises(InvalidAnnouncementTransition):
            announcement.submit_for_review(submitted_by=_user("p"))


@pytest.mark.django_db
def describe_approve():
    def it_publishes_records_the_reviewer_and_notifies_the_proposer():
        reviewer = _user("rev", "rev@example.com")
        proposer = _user("subm", "subm@example.com")
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=proposer)
        announcement.approve(reviewer=reviewer)
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PUBLISHED
        assert announcement.reviewed_by == reviewer
        assert announcement.reviewed_at is not None
        assert announcement in GuildAnnouncement.objects.published()
        assert _delivery_count("guild_announcement.approved") == 1

    def it_posts_to_the_guilds_members_on_approval():
        reviewer = _user("rev2", "rev2@example.com")
        proposer = _user("subm2", "subm2@example.com")
        guild = GuildFactory()
        member = _user("gm", "gm@example.com")
        GuildMembershipFactory(guild=guild, member=member.member)
        announcement = GuildAnnouncementFactory(guild=guild, pending=True, submitted_by=proposer)
        announcement.approve(reviewer=reviewer)
        # notify_members fired the guild.announcement broadcast to the joined member.
        assert _delivery_count("guild_announcement") == 1

    def it_dates_the_post_from_approval_not_from_drafting():
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=_user("s3"))
        before = timezone.now()
        announcement.approve(reviewer=_user("r3"))
        announcement.refresh_from_db()
        assert announcement.published_at >= before

    def it_raises_from_a_declined_announcement():
        announcement = GuildAnnouncementFactory(declined=True)
        with pytest.raises(InvalidAnnouncementTransition):
            announcement.approve(reviewer=_user("r"))


@pytest.mark.django_db
def describe_request_changes():
    def it_sets_changes_requested_with_the_note_and_notifies_the_proposer():
        reviewer = _user("rc", "rc@example.com")
        proposer = _user("submrc", "submrc@example.com")
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=proposer)
        announcement.request_changes(reviewer=reviewer, notes="Add a start time.")
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.CHANGES_REQUESTED
        assert announcement.review_notes == "Add a start time."
        assert _delivery_count("guild_announcement.changes_requested") == 1

    def it_requires_a_note():
        announcement = GuildAnnouncementFactory(pending=True)
        with pytest.raises(ValueError):
            announcement.request_changes(reviewer=_user("r"), notes="   ")

    def it_raises_from_a_non_pending_announcement():
        announcement = GuildAnnouncementFactory(changes_requested=True)
        with pytest.raises(InvalidAnnouncementTransition):
            announcement.request_changes(reviewer=_user("r"), notes="x")


@pytest.mark.django_db
def describe_decline():
    def it_sets_declined_with_the_note_and_notifies_the_proposer():
        reviewer = _user("dc", "dc@example.com")
        proposer = _user("submdc", "submdc@example.com")
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=proposer)
        announcement.decline(reviewer=reviewer, notes="Already announced this one.")
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.DECLINED
        assert _delivery_count("guild_announcement.declined") == 1
        assert announcement not in GuildAnnouncement.objects.published()

    def it_requires_a_note():
        announcement = GuildAnnouncementFactory(pending=True)
        with pytest.raises(ValueError):
            announcement.decline(reviewer=_user("r"), notes="")

    def it_raises_from_a_published_announcement():
        announcement = GuildAnnouncementFactory()  # PUBLISHED
        with pytest.raises(InvalidAnnouncementTransition):
            announcement.decline(reviewer=_user("r"), notes="nope")


@pytest.mark.django_db
def describe_withdraw():
    def it_deletes_a_pending_proposal():
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=_user("w1"))
        pk = announcement.pk
        announcement.withdraw(by=announcement.submitted_by)
        assert not GuildAnnouncement.objects.filter(pk=pk).exists()

    def it_raises_from_a_published_announcement():
        announcement = GuildAnnouncementFactory()  # PUBLISHED
        with pytest.raises(InvalidAnnouncementTransition):
            announcement.withdraw(by=_user("w"))


@pytest.mark.django_db
def describe_querysets():
    def it_separates_published_from_awaiting_review():
        published = GuildAnnouncementFactory()
        pending = GuildAnnouncementFactory(pending=True)
        assert published in GuildAnnouncement.objects.published()
        assert published not in GuildAnnouncement.objects.awaiting_review()
        assert pending in GuildAnnouncement.objects.awaiting_review()
        assert pending not in GuildAnnouncement.objects.published()
