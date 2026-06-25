"""send_voting_reminders now drives voting.closing_48h via the spine scheduler.

The command fires the ``voting.closing_48h`` event when the cycle close (month end)
is ~48h away, to all voters, deduped on EventDelivery by the ``voting:YYYY-MM`` period.
It supersedes the old month-end−3-days, in-app-only, ScheduledNotificationMarker path.
"""

from datetime import datetime

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.models import EventDelivery, Notification
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _aware(y, m, d, h=9):
    return timezone.make_aware(datetime(y, m, d, h, 0))


def _voter(email):
    """A paying, active member with a linked, email-bearing User (an eligible voter)."""
    member = MemberFactory()  # status ACTIVE, member_type STANDARD (paying) by default
    with mute_signals(post_save):
        user = User.objects.create_user(username=email.split("@")[0], email=email)
    member.user = user
    member.save(update_fields=["user"])
    return member


def describe_send_voting_reminders():
    def it_fires_inside_the_48h_window_and_is_idempotent(monkeypatch):
        # June closes at midnight July 1; 48h before = June 29 00:00. Tick exactly at
        # that instant so the half-open [now, now+15m) window contains the fire time.
        fire = _aware(2026, 6, 29, 0)
        monkeypatch.setattr("core.management.commands.send_voting_reminders.timezone.now", lambda: fire)
        _voter("voter1@example.com")

        call_command("send_voting_reminders")
        call_command("send_voting_reminders")  # second run must dedupe

        assert Notification.objects.filter(trigger="voting.closing_48h").count() == 1
        assert EventDelivery.objects.filter(event_key="voting.closing_48h", period="voting:2026-06").exists()

    def it_does_nothing_outside_the_window(monkeypatch):
        _voter("voter2@example.com")
        monkeypatch.setattr(
            "core.management.commands.send_voting_reminders.timezone.now",
            lambda: _aware(2026, 6, 10, 9),  # far from the 48h-before-close instant
        )
        call_command("send_voting_reminders")
        assert Notification.objects.filter(trigger="voting.closing_48h").count() == 0
