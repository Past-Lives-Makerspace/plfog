"""BDD specs for the Emails-tab catalogue service (core.events.email_catalogue)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.events.email_catalogue import EmailRow, build_email_catalogue
from membership.models import VotingSettings

pytestmark = pytest.mark.django_db


def _rows() -> dict[str, EmailRow]:
    """Flatten the grouped catalogue into a key → row map."""
    out: dict[str, EmailRow] = {}
    for _category, rows in build_email_catalogue():
        for row in rows:
            out[row.key] = row
    return out


def describe_build_email_catalogue():
    def it_includes_only_events_that_send_email():
        rows = _rows()
        # These declare an email channel...
        assert "voting.closing_soon" in rows
        assert "registration_confirmed" in rows
        # ...while a push/in-app-only event (no email channel) never appears.
        assert "class_reminder" in rows  # sanity: an emailing event is present
        assert all(("Email" in r.channels or "Scheduled email" in r.channels) for r in rows.values())

    def it_groups_by_category_in_registry_order():
        catalogue = build_email_catalogue()
        categories = [category for category, _rows in catalogue]
        # No category repeats (each appears once, grouped).
        assert len(categories) == len(set(categories))
        # Voting is one of the groups and carries the three reminder emails.
        voting = dict(catalogue)["Voting"]
        keys = {r.key for r in voting}
        assert {"voting.closing_soon", "voting.vote_soon", "voting.officers_closing_soon"} <= keys

    def describe_automatic_emails():
        def it_marks_the_voting_reminders_automatic():
            row = _rows()["voting.officers_closing_soon"]
            assert row.is_automatic
            assert row.kind_label == "Automatic"

        def it_reflects_the_live_reminder_lead_days_in_the_schedule_note():
            settings = VotingSettings.load()
            settings.reminder_lead_days = 5
            settings.save()
            note = _rows()["voting.closing_soon"].schedule_note
            assert "5 days before the monthly guild vote closes" in note

        def it_singularizes_a_one_day_lead():
            settings = VotingSettings.load()
            settings.reminder_lead_days = 1
            settings.save()
            assert "1 day before" in _rows()["voting.vote_soon"].schedule_note

        def it_links_voting_reminders_to_the_voting_settings_page():
            row = _rows()["voting.closing_soon"]
            assert row.adjust_url == reverse("hub_admin_voting_settings")
            assert "Voting settings" in row.adjust_label

        def it_links_other_scheduled_emails_to_the_automations_tab():
            row = _rows()["class_reminder"]
            assert row.is_automatic
            assert "tab=automations" in row.adjust_url
            assert "Automations" in row.adjust_label
            assert row.schedule_note == "Sent automatically before each class starts."

    def describe_triggered_emails():
        def it_marks_a_transactional_email_triggered_with_a_sent_when_note():
            row = _rows()["registration_confirmed"]
            assert not row.is_automatic
            assert row.kind_label == "Triggered"
            assert row.schedule_note.startswith("Sent when ")

        def it_offers_no_adjust_link_for_a_triggered_email():
            row = _rows()["registration_confirmed"]
            assert row.adjust_url == ""
            assert row.adjust_label == ""

    def it_exposes_an_edit_url_to_the_email_copy_editor():
        row = _rows()["voting.closing_soon"]
        assert row.edit_url == reverse("hub_admin_notification_edit", args=["voting.closing_soon", "email"])

    def it_carries_the_resolved_audience_and_channel_labels():
        row = _rows()["voting.officers_closing_soon"]
        assert row.audience  # non-empty resolved description
        assert "Email" in row.channels
        assert "In-app" in row.channels
