"""BDD specs for the seed_notification_templates command (idempotency + override safety)."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command

from core.events.copy import COPY_CHANNELS
from core.events.registry import all_events
from core.models import NotificationTemplate

pytestmark = pytest.mark.django_db


def _seed() -> str:
    out = io.StringIO()
    call_command("seed_notification_templates", stdout=out)
    return out.getvalue()


def describe_seed_notification_templates():
    def it_creates_one_row_per_event_and_copy_channel():
        _seed()
        assert NotificationTemplate.objects.count() == len(all_events()) * len(COPY_CHANNELS)

    def it_seeds_the_default_copy_for_a_known_event():
        _seed()
        row = NotificationTemplate.objects.get(event_key="registration_confirmed", channel="email")
        assert "{{ class_title }}" in row.subject
        assert row.is_overridden is False

    def describe_run_twice():
        def it_is_idempotent_and_does_not_duplicate_rows():
            _seed()
            count_after_first = NotificationTemplate.objects.count()
            _seed()
            assert NotificationTemplate.objects.count() == count_after_first

        def it_reports_refreshed_not_created_on_the_second_run():
            _seed()
            output = _seed()
            assert "0 created" in output
            assert "refreshed" in output


def describe_override_preservation():
    def it_never_clobbers_an_admin_overridden_row():
        _seed()
        row = NotificationTemplate.objects.get(event_key="registration_confirmed", channel="email")
        row.subject = "COPY TEAM WORDING"
        row.is_overridden = True
        row.save(update_fields=["subject", "is_overridden"])

        _seed()  # re-seed must leave the override alone

        row.refresh_from_db()
        assert row.subject == "COPY TEAM WORDING"

    def it_reports_preserved_overridden_rows_in_the_summary():
        _seed()
        row = NotificationTemplate.objects.first()
        row.is_overridden = True
        row.save(update_fields=["is_overridden"])
        output = _seed()
        assert "preserved" in output

    def it_refreshes_a_non_overridden_row_to_the_current_default():
        _seed()
        row = NotificationTemplate.objects.get(event_key="registration_confirmed", channel="email")
        row.subject = "stale local edit"
        row.is_overridden = False  # not an override → fair game to refresh
        row.save(update_fields=["subject", "is_overridden"])

        _seed()

        row.refresh_from_db()
        assert "{{ class_title }}" in row.subject  # back to the default
