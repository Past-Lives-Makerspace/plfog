"""Specs for classes/management/commands/send_class_reminders.py."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def describe_send_class_reminders_command():
    def it_calls_send_due_class_reminders_with_default_window():
        with patch("classes.management.commands.send_class_reminders.send_due_class_reminders", return_value=3) as mock:
            call_command("send_class_reminders")

        mock.assert_called_once_with(window_minutes=15)

    def it_prints_the_count_of_sent_emails():
        stdout = StringIO()
        with patch("classes.management.commands.send_class_reminders.send_due_class_reminders", return_value=5):
            call_command("send_class_reminders", stdout=stdout)

        assert "5" in stdout.getvalue()

    def it_accepts_a_custom_window_minutes_argument():
        with patch("classes.management.commands.send_class_reminders.send_due_class_reminders", return_value=0) as mock:
            call_command("send_class_reminders", window_minutes=30)

        mock.assert_called_once_with(window_minutes=30)

    def it_prints_zero_when_no_reminders_were_sent():
        stdout = StringIO()
        with patch("classes.management.commands.send_class_reminders.send_due_class_reminders", return_value=0):
            call_command("send_class_reminders", stdout=stdout)

        assert "0" in stdout.getvalue()
