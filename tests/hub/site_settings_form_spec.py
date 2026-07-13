"""SiteSettingsForm — the signage_* fields ride the shared singleton form."""

from __future__ import annotations

import pytest

from core.models import SiteConfiguration
from hub.forms import SiteSettingsForm

pytestmark = pytest.mark.django_db

_SIGNAGE_FIELDS = [
    "signage_default_slide_seconds",
    "signage_show_events",
    "signage_event_days_ahead",
]


def describe_SiteSettingsForm_signage():
    def it_declares_all_signage_fields():
        for name in _SIGNAGE_FIELDS:
            assert name in SiteSettingsForm.Meta.fields

    def it_does_not_expose_the_removed_event_qr_toggle():
        # Event slides always carry a QR now — the toggle is gone.
        assert "signage_event_qr" not in SiteSettingsForm.Meta.fields

    def it_does_not_expose_the_removed_emergency_alert_fields():
        # The emergency-takeover feature was removed — its fields are gone.
        for name in ("signage_alert_active", "signage_alert_heading", "signage_alert_message"):
            assert name not in SiteSettingsForm.Meta.fields

    def it_round_trips_a_save_onto_the_singleton():
        config = SiteConfiguration.load()
        data = {
            "registration_mode": SiteConfiguration.RegistrationMode.INVITE_ONLY,
            "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
            "signage_default_slide_seconds": "20",
            "signage_show_events": "on",
            "signage_event_days_ahead": "45",
        }
        form = SiteSettingsForm(data, instance=config)
        assert form.is_valid(), form.errors
        form.save()
        config.refresh_from_db()
        assert config.signage_default_slide_seconds == 20
        assert config.signage_event_days_ahead == 45
