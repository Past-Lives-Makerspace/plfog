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
from core.events.registry import Channel, get_event
from core.models import EventDelivery, Notification, NotificationPreference, SiteActivity, TransactionalEmailLog
from membership.models import GuildAnnouncement, Member
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
        NotificationPreference.objects.create(user=opted_out.user, trigger="guild_announcement", email_enabled=False)
        announcement = GuildAnnouncement.objects.create(guild=guild, title="News", body="Body.")

        announcement.notify_members()

        emailed = set(TransactionalEmailLog.objects.values_list("to_email", flat=True))
        assert default_member.user.email in emailed
        assert opted_out.user.email not in emailed

    def it_broadcasts_to_discord_once(linked_member):
        guild = GuildFactory(name="Print")
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
        NotificationPreference.objects.create(user=opted_out.user, trigger="site_announcement", email_enabled=False)
        emit(
            "site_announcement",
            context={"member_name": "there", "announcement_title": "Hi", "announcement_body": "B", "site_url": "/"},
        )
        emailed = set(TransactionalEmailLog.objects.values_list("to_email", flat=True))
        assert default_member.user.email in emailed
        assert opted_out.user.email not in emailed

    def it_declares_discord_broadcast():
        assert get_event("site_announcement").has_channel(Channel.DISCORD)


# --- 4. voting.closing_48h (scheduled) ---------------------------------------


def _voter(email):
    """A paying, active member with a linked, email-bearing User (an eligible voter)."""
    from tests.membership.factories import MemberFactory

    member = MemberFactory()  # ACTIVE + STANDARD (paying) by default
    with mute_signals(post_save):
        user = User.objects.create_user(username=email.split("@")[0], email=email)
    member.user = user
    member.save(update_fields=["user"])
    return member


def describe_voting_closing_48h():
    def it_fires_once_per_cycle_via_the_scheduler():
        from membership.voting import closing_48h_occurrences

        _voter("v1@example.com")
        # 48h before the July-1 close = June 29 00:00; tick exactly there.
        now = timezone.make_aware(datetime(2026, 6, 29, 0, 0))
        from core.events.scheduler import run_sources

        first = run_sources([closing_48h_occurrences], now=now)
        second = run_sources([closing_48h_occurrences], now=now)
        assert first == 1
        assert second == 0  # deduped on EventDelivery period voting:2026-06
        assert Notification.objects.filter(trigger="voting.closing_48h").count() == 1

    def it_targets_paying_active_voters_only():
        from tests.membership.factories import MemberFactory

        voter = _voter("payer@example.com")
        # A non-paying member (member_type != STANDARD) is NOT a voter.
        nonpayer = MemberFactory(member_type=Member.MemberType.WORK_TRADE)
        with mute_signals(post_save):
            nonpayer_user = User.objects.create_user(username="np", email="np@example.com")
        nonpayer.user = nonpayer_user
        nonpayer.save(update_fields=["user"])

        from membership.voting import closing_48h_occurrences
        from core.events.scheduler import run_sources

        now = timezone.make_aware(datetime(2026, 6, 29, 0, 0))
        run_sources([closing_48h_occurrences], now=now)
        notified = set(Notification.objects.values_list("user_id", flat=True))
        assert voter.user_id in notified
        assert nonpayer.user_id not in notified

    def it_does_not_fire_outside_the_window():
        from membership.voting import closing_48h_occurrences
        from core.events.scheduler import run_sources

        _voter("early@example.com")
        now = timezone.make_aware(datetime(2026, 6, 10, 9, 0))
        assert run_sources([closing_48h_occurrences], now=now) == 0


# --- 5. voting.results_published ---------------------------------------------


def describe_voting_results_published():
    def it_emails_voters_the_allocation_breakdown():
        from membership.models import FundingSnapshot

        g1, g2, g3 = GuildFactory(name="A"), GuildFactory(name="B"), GuildFactory(name="C")
        member = _voter("results@example.com")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
        NotificationPreference.objects.create(user=member.user, trigger="voting.results_published", email_enabled=True)

        snapshot = FundingSnapshot.take()
        assert snapshot is not None
        log = TransactionalEmailLog.objects.get(trigger_kind="voting.results_published")
        assert log.to_email == member.user.email
        # The allocation summary names a guild that received funding.
        assert "$" in snapshot.allocation_summary()

    def it_dedupes_re_publishing_the_same_snapshot():
        from membership.models import FundingSnapshot

        g1, g2, g3 = GuildFactory(name="D"), GuildFactory(name="E"), GuildFactory(name="F")
        member = _voter("dedupe@example.com")
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
        snapshot = FundingSnapshot.take()
        assert snapshot is not None
        snapshot.publish_results()  # second call — same snapshot pk
        assert Notification.objects.filter(trigger="voting.results_published", user=member.user).count() == 1


# --- 6. release.published ----------------------------------------------------


def describe_release_published():
    def it_announces_to_everyone_with_a_login(linked_member):
        member = linked_member()
        call_command("announce_release")
        assert Notification.objects.filter(trigger="release.published", user=member.user).exists()

    def it_is_idempotent_per_version(linked_member):
        linked_member()
        call_command("announce_release")
        call_command("announce_release")  # second run for the same version must no-op
        assert EventDelivery.objects.filter(event_key="release.published", channel="in_app").count() == 1

    def it_honors_email_opt_out(linked_member):
        default_member = linked_member()
        opted_out = linked_member()
        NotificationPreference.objects.create(user=opted_out.user, trigger="release.published", email_enabled=False)
        call_command("announce_release")
        emailed = set(TransactionalEmailLog.objects.values_list("to_email", flat=True))
        assert default_member.user.email in emailed
        assert opted_out.user.email not in emailed
