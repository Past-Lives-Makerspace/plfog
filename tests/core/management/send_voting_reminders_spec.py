"""send_voting_reminders drives the per-member voting reminders via the spine scheduler.

The command walks ``closing_soon_occurrences`` (members who voted) +
``vote_soon_occurrences`` (signed-in non-voters) the configured lead before close,
to each member, deduped on EventDelivery by the ``voting:YYYY-MM`` period.
"""

from datetime import datetime

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.models import EventDelivery, Notification
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _aware(y, m, d, h=9):
    return timezone.make_aware(datetime(y, m, d, h, 0))


def _voter(email):
    """A paying, active member with a linked, email-bearing User who has cast a vote."""
    member = MemberFactory()  # status ACTIVE, member_type STANDARD (paying) by default
    with mute_signals(post_save):
        user = User.objects.create_user(username=email.split("@")[0], email=email)
    member.user = user
    member.save(update_fields=["user"])
    g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
    VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
    return member


def describe_send_voting_reminders():
    def it_fires_inside_the_window_and_is_idempotent(monkeypatch):
        # June closes at midnight July 1; default 3-day lead = June 28 00:00. Tick exactly
        # there so the half-open [now, now+15m) window contains the fire time.
        fire = _aware(2026, 6, 28, 0)
        monkeypatch.setattr("core.management.commands.send_voting_reminders.timezone.now", lambda: fire)
        _voter("voter1@example.com")

        call_command("send_voting_reminders")
        call_command("send_voting_reminders")  # second run must dedupe

        assert Notification.objects.filter(trigger="voting.closing_soon").count() == 1
        assert EventDelivery.objects.filter(event_key="voting.closing_soon", period="voting:2026-06").exists()

    def it_does_nothing_outside_the_window(monkeypatch):
        _voter("voter2@example.com")
        monkeypatch.setattr(
            "core.management.commands.send_voting_reminders.timezone.now",
            lambda: _aware(2026, 6, 10, 9),  # far from the lead-before-close instant
        )
        call_command("send_voting_reminders")
        assert Notification.objects.filter(trigger="voting.closing_soon").count() == 0
