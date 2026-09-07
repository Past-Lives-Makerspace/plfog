"""Phase 6 — the six net-new user-facing events on the spine (design §4).

Covers, per event: correct recipients (incl. guild scope), correct channels,
opt-out respected, scheduled-event dedupe, and release-command idempotency.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.events.emit import emit
from core.events.registry import Channel, Recipients, get_event
from core.models import EventDelivery, Notification, NotificationPreference, SiteActivity, TransactionalEmailLog
from membership.models import GuildAnnouncement
from plfog.version import CHANGELOG
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    VotePreferenceFactory,
)

pytestmark = pytest.mark.django_db


# --- 1. member.invited -------------------------------------------------------


def describe_member_invited():
    def it_is_a_forced_email_only_event():
        event = get_event("member.invited")
        assert event.channel_list == [Channel.EMAIL]
        assert event.channel(Channel.EMAIL).is_forced

    def it_emails_the_invitee_address(linked_member):
        from core.models import Invite

        admin = User.objects.create_user(username="inv_admin", email="admin@example.com")
        invite = Invite.objects.create(email="invitee@example.com", invited_by=admin)
        invite.send_invite_email()
        log = TransactionalEmailLog.objects.get(trigger_kind="member.invited")
        assert log.to_email == "invitee@example.com"
        # No in-app row — the invitee has no account, and the event declares no in-app.
        assert Notification.objects.count() == 0


# --- 2. guild.announcement ---------------------------------------------------


def describe_guild_announcement():
    def it_routes_to_guild_members_only_not_the_whole_site(linked_member):
        guild = GuildFactory(name="Metal")
        in_guild = linked_member()
        out_guild = linked_member()  # an active member NOT in the guild
        GuildMembershipFactory(guild=guild, member=in_guild)
        announcement = GuildAnnouncement.objects.create(guild=guild, title="Anvil day", body="Come help.")

        announcement.notify_members()

        notified = set(Notification.objects.values_list("user_id", flat=True))
        assert in_guild.user_id in notified
        assert out_guild.user_id not in notified

    def it_respects_email_opt_out(linked_member):
        # Email defaults ON (opt-out): a member who has NOT opted out is emailed; a
        # member with an explicit email_enabled=False preference is not.
        guild = GuildFactory(name="Fiber")
        default_member = linked_member()
        opted_out = linked_member()
        GuildMembershipFactory(guild=guild, member=default_member)
        GuildMembershipFactory(guild=guild, member=opted_out)
        NotificationPreference.objects.create(
            user=opted_out.user, event_key="guild_announcement", channel="email", enabled=False
        )
        announcement = GuildAnnouncement.objects.create(guild=guild, title="News", body="Body.")

        announcement.notify_members()

        emailed = set(TransactionalEmailLog.objects.values_list("to_email", flat=True))
        assert default_member.user.email in emailed
        assert opted_out.user.email not in emailed

    def it_broadcasts_to_discord_once(linked_member):
        # The channel picker owns the single Discord post: the default "Our Guild Channel"
        # resolves to the guild's own webhook and posts exactly once (no central double-post).
        guild = GuildFactory(name="Print", discord_webhook_url="https://discord.com/api/webhooks/1/guild")
        m1 = linked_member()
        m2 = linked_member()
        GuildMembershipFactory(guild=guild, member=m1)
        GuildMembershipFactory(guild=guild, member=m2)
        announcement = GuildAnnouncement.objects.create(guild=guild, title="T", body="B")
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            announcement.notify_members()
        assert mock_post.call_count == 1

    def it_logs_a_guild_announcement_activity(linked_member):
        guild = GuildFactory(name="Glass")
        announcement = GuildAnnouncement.objects.create(guild=guild, title="T", body="B")
        announcement.notify_members()
        assert SiteActivity.objects.filter(kind="guild_announcement").exists()


# --- 3. site.announcement ----------------------------------------------------


def describe_site_announcement():
    def it_reaches_every_active_member(linked_member):
        a = linked_member()
        b = linked_member()
        emit(
            "site_announcement",
            context={
                "member_name": "there",
                "announcement_title": "Hours",
                "announcement_body": "Closed.",
                "site_url": "/",
            },
        )
        notified = set(Notification.objects.values_list("user_id", flat=True))
        assert {a.user_id, b.user_id} <= notified

    def it_honors_email_opt_out(linked_member):
        default_member = linked_member()
        opted_out = linked_member()
        NotificationPreference.objects.create(
            user=opted_out.user, event_key="site_announcement", channel="email", enabled=False
        )
        emit(
            "site_announcement",
            context={"member_name": "there", "announcement_title": "Hi", "announcement_body": "B", "site_url": "/"},
        )
        emailed = set(TransactionalEmailLog.objects.values_list("to_email", flat=True))
        assert default_member.user.email in emailed
        assert opted_out.user.email not in emailed

    def it_declares_discord_broadcast():
        assert get_event("site_announcement").has_channel(Channel.DISCORD)


# --- 4. voting.closing_soon (scheduled, per-member) --------------------------


def _voter(email):
    """A paying, active member with a linked, email-bearing User (an eligible voter)."""
    from tests.membership.factories import MemberFactory

    member = MemberFactory()  # ACTIVE + STANDARD (paying) by default
    with mute_signals(post_save):
        user = User.objects.create_user(username=email.split("@")[0], email=email)
    member.user = user
    member.save(update_fields=["user"])
    return member


def describe_voting_closing_soon():
    def it_fires_once_per_cycle_per_voter_via_the_scheduler():
        from core.events.scheduler import run_sources
        from membership.voting import closing_soon_occurrences

        g1, g2, g3 = GuildFactory(name="A1"), GuildFactory(name="B1"), GuildFactory(name="C1")
        member = _voter("v1@example.com")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
        # Default lead is 3 days → June close July 1, fire June 28 00:00.
        now = timezone.make_aware(datetime(2026, 6, 28, 0, 0))

        first = run_sources([closing_soon_occurrences], now=now)
        second = run_sources([closing_soon_occurrences], now=now)
        assert first == 1
        assert second == 0  # deduped on EventDelivery period voting:2026-06
        assert Notification.objects.filter(trigger="voting.closing_soon").count() == 1

    def it_only_targets_members_who_voted():
        from core.events.scheduler import run_sources
        from membership.voting import closing_soon_occurrences

        g1, g2, g3 = GuildFactory(name="A2"), GuildFactory(name="B2"), GuildFactory(name="C2")
        voter = _voter("voted@example.com")
        VotePreferenceFactory(member=voter, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
        novote = _voter("novote@example.com")  # paying + signed in but no vote

        now = timezone.make_aware(datetime(2026, 6, 28, 0, 0))
        run_sources([closing_soon_occurrences], now=now)
        notified = set(Notification.objects.filter(trigger="voting.closing_soon").values_list("user_id", flat=True))
        assert voter.user_id in notified
        assert novote.user_id not in notified

    def it_does_not_fire_outside_the_window():
        from core.events.scheduler import run_sources
        from membership.voting import closing_soon_occurrences

        g1, g2, g3 = GuildFactory(name="A3"), GuildFactory(name="B3"), GuildFactory(name="C3")
        member = _voter("early@example.com")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
        now = timezone.make_aware(datetime(2026, 6, 10, 9, 0))
        assert run_sources([closing_soon_occurrences], now=now) == 0


# --- 5. voting.results_published (admin-confirmed send) ----------------------


def describe_voting_results_published():
    def it_emails_each_voter_only_on_send_results():
        from membership.models import FundingSnapshot

        g1, g2, g3 = GuildFactory(name="A"), GuildFactory(name="B"), GuildFactory(name="C")
        member = _voter("results@example.com")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
        NotificationPreference.objects.create(
            user=member.user, event_key="voting.results_published", channel="email", enabled=True
        )

        snapshot = FundingSnapshot.take()
        assert snapshot is not None
        # take() does NOT email members — only the admin-confirmed send_results() does.
        assert not TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").exists()

        snapshot.send_results()
        log = TransactionalEmailLog.objects.get(trigger_kind="voting.results_published")
        assert log.to_email == member.user.email
        assert "$" in snapshot.allocation_summary()

    def it_dedupes_the_same_send_but_resends_with_resend():
        from membership.models import FundingSnapshot

        g1, g2, g3 = GuildFactory(name="D"), GuildFactory(name="E"), GuildFactory(name="F")
        member = _voter("dedupe@example.com")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
        snapshot = FundingSnapshot.take()
        assert snapshot is not None

        snapshot.send_results()
        snapshot.send_results(resend=True)  # fresh period → a second delivery
        assert Notification.objects.filter(trigger="voting.results_published", user=member.user).count() == 2


# --- 6. release.published ----------------------------------------------------


def describe_release_published():
    # These test the announce FAN OUT, not how a version is resolved. Calling the command bare
    # makes them depend on VERSION happening to have a CHANGELOG entry, and a release that
    # carries nothing member-facing legitimately has none (see CLAUDE.md) - which turned this
    # block red on main the moment a test-only PR bumped VERSION. Pinning to the newest entry
    # keeps the subject under test the fan out, and never goes stale.
    announced_version = str(CHANGELOG[0]["version"])

    def it_announces_to_everyone_with_a_login(linked_member):
        member = linked_member()
        call_command("announce_release", release_version=announced_version)
        assert Notification.objects.filter(trigger="release.published", user=member.user).exists()

    def it_is_idempotent_per_version(linked_member):
        linked_member()
        call_command("announce_release", release_version=announced_version)
        call_command("announce_release", release_version=announced_version)  # same version must no-op
        assert EventDelivery.objects.filter(event_key="release.published", channel="in_app").count() == 1

    def it_honors_email_opt_out(linked_member):
        default_member = linked_member()
        opted_out = linked_member()
        NotificationPreference.objects.create(
            user=opted_out.user, event_key="release.published", channel="email", enabled=False
        )
        call_command("announce_release", release_version=announced_version)
        emailed = set(TransactionalEmailLog.objects.values_list("to_email", flat=True))
        assert default_member.user.email in emailed
        assert opted_out.user.email not in emailed


# --- 7. orientation.completed ------------------------------------------------


def describe_orientation_completed():
    def it_registers_orientation_completed_with_guild_members_and_discord():
        event = get_event("orientation.completed")
        assert event.recipient is Recipients.GUILD_MEMBERS
        assert event.has_channel(Channel.IN_APP)
        assert event.has_channel(Channel.DISCORD)
        # No email channel — a light social nudge to the guild, not an inbox item.
        assert not event.has_channel(Channel.EMAIL)
        # activity_kind stays None: complete_orientation logs the SiteActivity itself.
        assert event.activity_kind is None

    def it_keeps_orientation_completed_placeholders_and_sample_context_in_lockstep():
        from core.events.copy import COPY_CHANNELS, default_copy_for, placeholders_for, sample_context_for
        from core.events.rendering import placeholders_in

        placeholders = set(placeholders_for("orientation.completed"))
        assert placeholders == set(sample_context_for("orientation.completed"))
        for channel in COPY_CHANNELS:
            copy = default_copy_for("orientation.completed", channel)
            for fragment in (copy.subject, copy.body_text, copy.body_html):
                for name in placeholders_in(fragment):
                    assert name in placeholders
