"""Facility tour forms (#947): Tour Settings. A guide's hours and days off are My Hours' forms since #1300 (``tests/hours/``)."""

from __future__ import annotations

from typing import Any

import pytest

from tours.forms import FacilityTourSettingsForm
from tours.models import DEFAULT_CLOSED_MESSAGE, FacilityTourSettings

pytestmark = pytest.mark.django_db


def describe_facility_tour_settings_form():
    def _post(**overrides: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "accepting_bookings": "on",
            "closed_message": "Back soon.",
            "booking_horizon_days": "30",
            "min_notice_hours": "24",
            "cancel_cutoff_hours": "24",
            "default_tour_minutes": "60",
            "default_seats": "6",
        }
        data.update(overrides)
        return {name: value for name, value in data.items() if value is not None}

    def _form(**overrides: Any) -> FacilityTourSettingsForm:
        return FacilityTourSettingsForm(_post(**overrides), instance=FacilityTourSettings.load())

    def it_saves_every_rule():
        form = _form(booking_horizon_days="7", min_notice_hours="0", cancel_cutoff_hours="336", default_seats="50")
        assert form.is_valid(), form.errors
        form.save()
        saved = FacilityTourSettings.load()
        assert (saved.booking_horizon_days, saved.min_notice_hours, saved.cancel_cutoff_hours, saved.default_seats) == (
            7,
            0,
            336,
            50,
        )

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("booking_horizon_days", "0", "Use 1 to 365 days."),
            ("booking_horizon_days", "366", "Use 1 to 365 days."),
            ("min_notice_hours", "337", "Use 0 to 336 hours."),
            ("cancel_cutoff_hours", "337", "Use 0 to 336 hours."),
            ("default_seats", "0", "Use 1 to 50 seats."),
            ("default_seats", "51", "Use 1 to 50 seats."),
        ],
    )
    def it_refuses_a_value_out_of_range(field: str, value: str, message: str):
        form = _form(**{field: value})
        assert not form.is_valid()
        assert message in form.errors[field]

    def it_refuses_a_negative_notice():
        assert "min_notice_hours" in _form(min_notice_hours="-1").errors

    def it_asks_for_the_closed_message_only_while_closed():
        form = _form(accepting_bookings=None, closed_message="  ")
        assert form.errors["closed_message"] == ["Write what visitors read while bookings are closed."]

    def it_keeps_the_saved_closed_message_when_left_blank_while_open():
        form = _form(closed_message="")
        assert form.is_valid(), form.errors
        assert form.save().closed_message == DEFAULT_CLOSED_MESSAGE

    def it_saves_a_trimmed_closed_message_when_closing():
        form = _form(accepting_bookings=None, closed_message="  Closed for the holidays.  ")
        assert form.is_valid(), form.errors
        saved = form.save()
        assert (saved.accepting_bookings, saved.closed_message) == (False, "Closed for the holidays.")

    def it_builds_without_a_saved_row():
        assert ("75", "75 minutes") not in FacilityTourSettingsForm().fields["default_tour_minutes"].choices

    def it_keeps_an_off_list_default_length_selectable():
        tour_settings = FacilityTourSettings.load()
        tour_settings.default_tour_minutes = 75
        tour_settings.save()
        form = FacilityTourSettingsForm(instance=tour_settings)
        assert ("75", "75 minutes") in form.fields["default_tour_minutes"].choices
