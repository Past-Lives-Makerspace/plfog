"""SiteSettingsForm — the seven signage_* fields ride the shared singleton form."""

from __future__ import annotations

import pytest
from django import forms

from core.models import SiteConfiguration
from hub.forms import SiteSettingsForm

pytestmark = pytest.mark.django_db

_SIGNAGE_FIELDS = [
    "signage_default_slide_seconds",
    "signage_show_events",
    "signage_event_days_ahead",
    "signage_event_qr",
    "signage_alert_active",
    "signage_alert_heading",
    "signage_alert_message",
]


def describe_SiteSettingsForm_signage():
    def it_declares_all_seven_signage_fields():
        for name in _SIGNAGE_FIELDS:
            assert name in SiteSettingsForm.Meta.fields

    def it_uses_a_textarea_for_the_alert_message():
        form = SiteSettingsForm()
        assert isinstance(form.fields["signage_alert_message"].widget, forms.Textarea)

    def it_round_trips_a_save_onto_the_singleton():
        config = SiteConfiguration.load()
        data = {
            "registration_mode": SiteConfiguration.RegistrationMode.INVITE_ONLY,
            "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
            "signage_default_slide_seconds": "20",
            "signage_show_events": "on",
            "signage_event_days_ahead": "45",
            "signage_event_qr": "on",
            "signage_alert_active": "on",
            "signage_alert_heading": "Building Closed",
            "signage_alert_message": "Back tomorrow.",
        }
        form = SiteSettingsForm(data, instance=config)
        assert form.is_valid(), form.errors
        form.save()
        config.refresh_from_db()
        assert config.signage_default_slide_seconds == 20
        assert config.signage_event_days_ahead == 45
        assert config.signage_alert_active is True
        assert config.signage_alert_heading == "Building Closed"
        assert config.signage_alert_message == "Back tomorrow."
