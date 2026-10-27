"""Facility tour forms (#947): the hours rows, days off and Tour Settings."""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any

import pytest
from django.utils import timezone

from membership.models import Member
from tests.tours.factories import TourDayOffFactory, TourHoursFactory
from tours.forms import FacilityTourSettingsForm, TourDayOffForm, TourHoursForm, TourHoursFormSet
from tours.models import DEFAULT_CLOSED_MESSAGE, FacilityTourSettings, TourHours

pytestmark = pytest.mark.django_db


def _row(index: int, **values: Any) -> dict[str, Any]:
    row = {"weekday": "1", "start_time": "16:00", "end_time": "18:00", "tour_minutes": "60", "seats": "4"}
    row.update(values)
    return {f"hours-{index}-{name}": value for name, value in row.items()}


def _formset_data(*rows: dict[str, Any], initial: int = 0) -> dict[str, Any]:
    data: dict[str, Any] = {
        "hours-TOTAL_FORMS": str(len(rows)),
        "hours-INITIAL_FORMS": str(initial),
        "hours-MIN_NUM_FORMS": "0",
        "hours-MAX_NUM_FORMS": "1000",
    }
    for row in rows:
        data.update(row)
    return data


def _formset(guide: Member, data: dict[str, Any] | None = None) -> Any:
    return TourHoursFormSet(data, instance=guide, prefix="hours", form_kwargs={"defaults": FacilityTourSettings.load()})


def describe_tour_hours_form():
    def it_starts_a_new_row_from_the_tour_settings_defaults(guide: Member):
        tour_settings = FacilityTourSettings.load()
        tour_settings.default_tour_minutes = 75
        tour_settings.default_seats = 9
        form = TourHoursForm(defaults=tour_settings)
        assert (form.initial["tour_minutes"], form.initial["seats"]) == (75, 9)
        assert ("75", "75 minutes") in form.fields["tour_minutes"].choices

    def it_seeds_a_saved_rows_times_and_off_list_length(guide: Member):
        hours = TourHoursFactory(guide=guide, start_time=time(16, 15), tour_minutes=50)
        form = TourHoursForm(instance=hours, defaults=FacilityTourSettings.load())
        assert (form.initial["start_time"], form.initial["end_time"]) == ("16:15", "18:00")
        assert ("50", "50 minutes") in form.fields["tour_minutes"].choices

    def it_sums_up_a_saved_row_in_the_words_the_page_shows(guide: Member):
        hours = TourHoursFactory(guide=guide)
        form = TourHoursForm(instance=hours, defaults=FacilityTourSettings.load())
        assert form.summary == "Tuesday · 4:00 PM to 6:00 PM · 1 hour each · 4 seats"
        hours.seats = 1
        assert form.summary.endswith("· 1 seat")


def describe_tour_hours_formset():
    def it_saves_a_new_row_for_the_guide(guide: Member):
        formset = _formset(guide, _formset_data(_row(0)))
        assert formset.is_valid(), formset.errors
        formset.save()
        hours = TourHours.objects.get()
        assert (hours.guide, hours.weekday, hours.start_time, hours.end_time, hours.tour_minutes, hours.seats) == (
            guide,
            1,
            time(16, 0),
            time(18, 0),
            60,
            4,
        )

    def it_skips_a_row_the_post_carries_nothing_for(guide: Member):
        data = _formset_data(_row(0))
        data["hours-TOTAL_FORMS"] = "2"
        formset = _formset(guide, data)
        assert formset.is_valid(), formset.errors
        assert formset.forms[1].is_absent and not formset.forms[1].has_changed()
        assert not formset.forms[0].is_absent
        formset.save()
        assert TourHours.objects.filter(guide=guide).count() == 1

    def it_still_validates_a_row_the_post_carries(guide: Member):
        formset = _formset(guide, _formset_data(_row(0, seats="")))
        assert not formset.is_valid()
        assert "seats" in formset.forms[0].errors

    def it_refuses_two_rows_that_overlap(guide: Member):
        formset = _formset(guide, _formset_data(_row(0), _row(1, start_time="17:00", end_time="19:00")))
        assert not formset.is_valid()
        assert formset.non_form_errors() == ["Two of these rows overlap on Tuesday. A guide gives one tour at a time."]

    def it_saves_two_rows_that_do_not_overlap(guide: Member):
        formset = _formset(guide, _formset_data(_row(0), _row(1, start_time="18:00", end_time="20:00")))
        assert formset.is_valid(), formset.errors
        formset.save()
        assert TourHours.objects.filter(guide=guide).count() == 2

    def it_checks_new_rows_against_saved_rows_the_post_did_not_carry(guide: Member):
        TourHoursFactory(guide=guide)
        formset = _formset(guide, _formset_data(_row(0, start_time="17:00", end_time="19:00")))
        assert not formset.is_valid()
        assert formset.non_form_errors() == [
            "These hours overlap saved Tuesday hours of 4:00 PM to 6:00 PM that this page does not show. "
            "Reload the page and try again."
        ]

    def it_adds_beside_saved_rows_the_post_did_not_carry_when_they_do_not_overlap(guide: Member):
        TourHoursFactory(guide=guide)
        formset = _formset(guide, _formset_data(_row(0, start_time="18:00", end_time="20:00")))
        assert formset.is_valid(), formset.errors
        formset.save()
        assert TourHours.objects.filter(guide=guide).count() == 2

    def it_ignores_another_guides_saved_rows(guide: Member):
        TourHoursFactory()
        assert _formset(guide, _formset_data(_row(0))).is_valid()

    def it_lets_a_deleted_row_make_way(guide: Member):
        saved = TourHoursFactory(guide=guide)
        data = _formset_data(
            _row(0, id=str(saved.pk), DELETE="on"), _row(1, start_time="17:00", end_time="19:00"), initial=1
        )
        formset = _formset(guide, data)
        assert formset.is_valid(), formset.errors
        formset.save()
        assert list(TourHours.objects.values_list("start_time", flat=True)) == [time(17, 0)]

    def it_shows_a_row_error_for_hours_shorter_than_one_tour(guide: Member):
        formset = _formset(guide, _formset_data(_row(0, end_time="16:30")))
        assert not formset.is_valid()
        assert formset.forms[0].errors["tour_minutes"] == ["These hours are shorter than one tour."]

    def it_refuses_seats_out_of_range(guide: Member):
        formset = _formset(guide, _formset_data(_row(0, seats="0")))
        assert not formset.is_valid()
        assert formset.forms[0].errors["seats"] == ["Use 1 to 50 seats."]


def describe_tour_day_off_form():
    def _form(guide: Member, **values: Any) -> TourDayOffForm:
        return TourDayOffForm(
            {f"day_off-{name}": value for name, value in values.items()}, prefix="day_off", guide=guide
        )

    def it_saves_a_single_day_when_the_last_day_is_blank(guide: Member):
        day = timezone.localdate() + timedelta(days=3)
        form = _form(guide, starts_on=day.isoformat(), ends_on="", note="Dentist")
        assert form.is_valid(), form.errors
        day_off = form.save()
        assert (day_off.guide, day_off.starts_on, day_off.ends_on, day_off.note) == (guide, day, day, "Dentist")

    def it_saves_a_run_of_days(guide: Member):
        first = timezone.localdate() + timedelta(days=3)
        form = _form(guide, starts_on=first.isoformat(), ends_on=(first + timedelta(days=9)).isoformat())
        assert form.is_valid(), form.errors
        assert form.save().ends_on == first + timedelta(days=9)

    def it_refuses_a_past_first_day(guide: Member):
        form = _form(guide, starts_on=(timezone.localdate() - timedelta(days=1)).isoformat())
        assert form.errors["starts_on"] == ["Pick today or a day ahead."]

    def it_refuses_a_last_day_before_the_first(guide: Member):
        first = timezone.localdate() + timedelta(days=3)
        form = _form(guide, starts_on=first.isoformat(), ends_on=(first - timedelta(days=1)).isoformat())
        assert form.errors["ends_on"] == ["The last day must be on or after the first."]

    def it_refuses_a_run_longer_than_a_year(guide: Member):
        first = timezone.localdate() + timedelta(days=3)
        form = _form(guide, starts_on=first.isoformat(), ends_on=(first + timedelta(days=366)).isoformat())
        assert form.errors["ends_on"] == ["Pick a run of a year or less."]

    def it_refuses_a_run_that_overlaps_a_saved_day_off(guide: Member):
        first = timezone.localdate() + timedelta(days=3)
        saved = TourDayOffFactory(guide=guide, starts_on=first + timedelta(days=2))
        form = _form(guide, starts_on=first.isoformat(), ends_on=(first + timedelta(days=5)).isoformat())
        assert form.errors["starts_on"] == [f"Part of that is already off: {saved.label}."]

    def it_ignores_another_guides_days_off(guide: Member):
        first = timezone.localdate() + timedelta(days=3)
        TourDayOffFactory(starts_on=first)
        assert _form(guide, starts_on=first.isoformat()).is_valid()

    def it_needs_a_first_day(guide: Member):
        assert "starts_on" in _form(guide, starts_on="").errors


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
