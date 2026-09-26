"""SiteSettingsForm and SlideshowSettingsForm — where the signage_* fields live now.

The three global signage fields LEFT SiteSettingsForm when the Slideshow admin became its
own page. They live on SlideshowSettingsForm alongside the self-building block switches,
and are deliberately not duplicated across the two forms.
"""

from __future__ import annotations

import pytest

from core.models import SiteConfiguration
from hub.forms import SiteSettingsForm, SlideshowSettingsForm

pytestmark = pytest.mark.django_db

_TIMING_FIELDS = [
    "signage_default_slide_seconds",
    "signage_event_days_ahead",
]

_BLOCK_SWITCHES = [
    "signage_show_events",
    "signage_show_classes",
    "signage_show_guilds",
    "signage_show_calendar",
    "signage_show_voting",
    "signage_show_directory",
    "signage_show_teach",
    "signage_show_tour",
]

# The tour block is the only one with a destination to type, so its URL rides with the
# switches rather than with the timing fields.
_DESTINATION_FIELDS = [
    "signage_tour_url",
]

# Still a column, deliberately on no form: event slides always carry a QR now, so the old
# per-block toggle is dead config rather than an admin control.
_RETIRED = {"signage_event_qr"}


def describe_SiteSettingsForm_features():
    def it_declares_the_public_member_directory_toggle():
        assert "member_directory_public" in SiteSettingsForm.Meta.fields


def describe_SiteSettingsForm_signage():
    def it_no_longer_carries_any_signage_field():
        for name in [*_TIMING_FIELDS, *_BLOCK_SWITCHES, *_DESTINATION_FIELDS]:
            assert name not in SiteSettingsForm.Meta.fields

    def it_carries_no_signage_field_this_spec_has_not_heard_of():
        # The lists above have to be edited by hand, and a signage field added to BOTH forms
        # is invisible: site_settings.html names its fields explicitly, so the duplicate binds
        # but never renders, and every Site Settings save posts nothing for it. A blankable
        # field is then silently overwritten with "" — which for signage_tour_url drops the
        # Book a Tour slide off every screen while its switch still reads on. Ask the model.
        declared = {f.name for f in SiteConfiguration._meta.get_fields() if f.name.startswith("signage_")}
        assert declared, "SiteConfiguration grew no signage_* fields — this guard is looking at the wrong model."
        assert declared & set(SiteSettingsForm.Meta.fields) == set()


def describe_SlideshowSettingsForm():
    def it_declares_the_timing_fields():
        for name in _TIMING_FIELDS:
            assert name in SlideshowSettingsForm.Meta.fields

    def it_declares_every_self_building_block_switch():
        for name in _BLOCK_SWITCHES:
            assert name in SlideshowSettingsForm.Meta.fields

    def it_declares_the_tour_destination():
        for name in _DESTINATION_FIELDS:
            assert name in SlideshowSettingsForm.Meta.fields

    def it_owns_every_signage_field_the_model_has_not_retired():
        # The other half of the guard above: a signage field on NEITHER form is unreachable,
        # stuck at its default with no admin able to change it. _RETIRED is the deliberate
        # exception list, so adding a field means choosing which side it lands on.
        declared = {f.name for f in SiteConfiguration._meta.get_fields() if f.name.startswith("signage_")}
        assert declared - _RETIRED <= set(SlideshowSettingsForm.Meta.fields)

    def it_does_not_expose_the_removed_event_qr_toggle():
        # Event slides always carry a QR now — the toggle is gone.
        assert "signage_event_qr" not in SlideshowSettingsForm.Meta.fields

    def it_does_not_expose_the_removed_emergency_alert_fields():
        # The emergency-takeover feature was removed — its fields are gone.
        for name in ("signage_alert_active", "signage_alert_heading", "signage_alert_message"):
            assert name not in SlideshowSettingsForm.Meta.fields

    def it_round_trips_a_save_onto_the_singleton():
        config = SiteConfiguration.load()
        data = {
            "signage_default_slide_seconds": "20",
            "signage_show_events": "on",
            "signage_event_days_ahead": "45",
        }
        form = SlideshowSettingsForm(data, instance=config)
        assert form.is_valid(), form.errors
        form.save()
        config.refresh_from_db()
        assert config.signage_default_slide_seconds == 20
        assert config.signage_event_days_ahead == 45
        assert config.signage_show_events is True
        # Unchecked switches post nothing, which a ModelForm reads as False.
        assert config.signage_show_classes is False


def describe_SiteSettingsForm_member_agreement():
    @pytest.fixture
    def required_settings() -> dict[str, str]:
        return {
            "org_name": "Past Lives Makerspace",
            "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
            "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
            "late_cancel_notice_hours": "24",
            "late_cancel_grace_hours": "2",
        }

    def it_fails_clean_if_required_but_no_url(required_settings: dict[str, str]) -> None:
        data = {
            **required_settings,
            "member_agreement_required": "on",
            "member_agreement_url": "",
        }
        form = SiteSettingsForm(data)
        assert not form.is_valid()
        assert set(form.errors) == {"member_agreement_url"}

    def it_passes_clean_if_required_and_url_provided(required_settings: dict[str, str]) -> None:
        data = {
            **required_settings,
            "member_agreement_required": "on",
            "member_agreement_url": "https://example.com",
        }
        form = SiteSettingsForm(data)
        assert form.is_valid(), form.errors

    def it_passes_clean_if_not_required(required_settings: dict[str, str]) -> None:
        data = {
            **required_settings,
            "member_agreement_required": "",
            "member_agreement_url": "",
        }
        form = SiteSettingsForm(data)
        assert form.is_valid(), form.errors


def describe_SiteSettingsForm_late_cancel_fees():
    """The switch and the window on the Features card (#456, part 1)."""

    def _data(**overrides: str) -> dict[str, str]:
        data = {
            "org_name": "Past Lives Makerspace",
            "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
            "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
            "late_cancel_notice_hours": "24",
            "late_cancel_grace_hours": "2",
        }
        data.update(overrides)
        return data

    def it_declares_the_switch_and_the_window():
        for name in ("late_cancel_fees_enabled", "late_cancel_notice_hours", "late_cancel_grace_hours"):
            assert name in SiteSettingsForm.Meta.fields

    def it_ships_off_with_a_day_of_notice_and_two_hours_of_grace():
        config = SiteConfiguration.load()
        assert config.late_cancel_fees_enabled is False
        assert config.late_cancel_notice_hours == 24
        assert config.late_cancel_grace_hours == 2

    def it_saves_the_three_onto_the_singleton():
        form = SiteSettingsForm(
            _data(late_cancel_fees_enabled="on", late_cancel_notice_hours="48", late_cancel_grace_hours="4"),
            instance=SiteConfiguration.load(),
        )
        assert form.is_valid(), form.errors
        form.save()
        config = SiteConfiguration.load()
        assert config.late_cancel_fees_enabled is True
        assert config.late_cancel_notice_hours == 48
        assert config.late_cancel_grace_hours == 4

    def it_reads_an_unchecked_switch_as_off():
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.save()
        form = SiteSettingsForm(_data(), instance=config)
        assert form.is_valid(), form.errors
        form.save()
        assert SiteConfiguration.load().late_cancel_fees_enabled is False

    def it_refuses_a_notice_under_an_hour():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="0", late_cancel_grace_hours="0"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_notice_hours"}
        assert "at least 1 hour" in str(form.errors["late_cancel_notice_hours"])

    def it_refuses_a_grace_equal_to_the_notice():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="24"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_grace_hours"}
        assert "shorter than the notice" in str(form.errors["late_cancel_grace_hours"])

    def it_refuses_a_grace_longer_than_the_notice():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="30"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_grace_hours"}

    def it_accepts_no_grace_at_all():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="0"))
        assert form.is_valid(), form.errors

    def it_requires_both_hours():
        # Blank hours are the field's own required error; the window check stays quiet.
        form = SiteSettingsForm(_data(late_cancel_notice_hours="", late_cancel_grace_hours=""))
        assert set(form.errors) == {"late_cancel_notice_hours", "late_cancel_grace_hours"}
        assert "required" in str(form.errors["late_cancel_grace_hours"])

    def it_requires_the_grace_without_judging_a_missing_one():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours=""))
        assert set(form.errors) == {"late_cancel_grace_hours"}
        assert "shorter than the notice" not in str(form.errors["late_cancel_grace_hours"])
