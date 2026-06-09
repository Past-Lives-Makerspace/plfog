"""send_voting_reminders fires once per cycle, 3 days before month end."""

from datetime import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from classes.factories import UserFactory
from core.models import Notification, ScheduledNotificationMarker

pytestmark = pytest.mark.django_db


def describe_send_voting_reminders():
    def it_dispatches_inside_the_window_and_is_idempotent(monkeypatch):
        fixed = timezone.make_aware(datetime(2026, 6, 27, 9, 0))  # June has 30 days; 30 - 3 = 27
        monkeypatch.setattr("core.management.commands.send_voting_reminders.timezone.now", lambda: fixed)

        # UserFactory triggers the ensure_user_has_member signal → ACTIVE Member auto-created.
        UserFactory()

        call_command("send_voting_reminders")
        call_command("send_voting_reminders")  # second run must no-op

        assert Notification.objects.filter(trigger="voting_closing_soon").count() == 1
        assert ScheduledNotificationMarker.objects.filter(key="voting_closing:2026-06").exists()

    def it_does_nothing_outside_the_window(monkeypatch):
        fixed = timezone.make_aware(datetime(2026, 6, 10, 9, 0))
        monkeypatch.setattr("core.management.commands.send_voting_reminders.timezone.now", lambda: fixed)
        call_command("send_voting_reminders")
        assert Notification.objects.filter(trigger="voting_closing_soon").count() == 0
