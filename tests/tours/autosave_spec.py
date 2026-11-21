"""The Tours page's settings forms save themselves as they are edited (#1279).

Every Week, Confirming Bookings, Google Calendar's picker and Tour Settings post with the autosave
header (``static/js/guild_autosave.js``) and answer JSON through ``hub.autosave``: a 200 with each
hours row's pk in posted order, or a 422 with every error keyed by its html field name (an overlap
on its row). The pages carry the autosave markers and no Save button.
"""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any
from unittest import mock

import pytest
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourCalendarConnectionFactory, TourHoursFactory
from tours.forms import TourHoursForm
from tours.models import FacilityTourSettings, TourCalendarConnection, TourGuideSettings, TourHours

pytestmark = pytest.mark.django_db

HOURS = reverse("tours:hours")
SAVE = reverse("tours:hours_save")
CONFIRM_MODE = reverse("tours:confirm_mode_save")
CHOOSE = reverse("tours:google_calendar")
SETTINGS = reverse("tours:settings")
AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}
TOURS_CAL = "tours@group.calendar.google.com"


def _hours_post(guide: Member, *rows: dict[str, str], initial: int = 0) -> dict[str, str]:
    data = {
        "guide": str(guide.pk),
        "hours-TOTAL_FORMS": str(len(rows)),
        "hours-INITIAL_FORMS": str(initial),
        "hours-MIN_NUM_FORMS": "0",
        "hours-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        values = {"weekday": "1", "start_time": "16:00", "end_time": "18:00", "tour_minutes": "60", "seats": "4"}
        values.update(row)
        data.update({f"hours-{index}-{name}": value for name, value in values.items()})
    return data


def _settings_post(**values: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "accepting_bookings": "on",
        "closed_message": "",
        "booking_horizon_days": "30",
        "min_notice_hours": "24",
        "cancel_cutoff_hours": "24",
        "default_tour_minutes": "60",
        "default_seats": "6",
    }
    data.update(values)
    return {key: value for key, value in data.items() if value is not None}


def describe_every_week_autosave():
    def it_answers_each_rows_pk_in_posted_order(guide: Member):
        kept = TourHoursFactory(guide=guide)
        gone = TourHoursFactory(guide=guide, weekday=TourHours.Weekday.WEDNESDAY)
        data = _hours_post(
            guide,
            {"id": str(kept.pk), "seats": "5"},
            {"id": str(gone.pk), "weekday": "2", "DELETE": "on"},
            {"weekday": "4"},
            initial=2,
        )
        response = signed_in(guide).post(SAVE, data, **AUTOSAVE)
        assert response.status_code == 200
        added = TourHours.objects.get(weekday=TourHours.Weekday.FRIDAY)
        assert response.json() == {"saved": True, "rows": {"hours": [kept.pk, None, added.pk]}}
        assert TourHours.objects.get(pk=kept.pk).seats == 5
        assert not TourHours.objects.filter(pk=gone.pk).exists()

    def it_skips_a_new_row_posted_as_rendered_while_the_rest_saves(guide: Member):
        kept = TourHoursFactory(guide=guide)
        # A + Add hours row with Day, From and Until still blank: the script posts it as it rendered.
        data = _hours_post(
            guide,
            {"id": str(kept.pk), "seats": "7"},
            {"weekday": "", "start_time": "", "end_time": "", "seats": "6"},
            initial=1,
        )
        response = signed_in(guide).post(SAVE, data, **AUTOSAVE)
        assert response.json() == {"saved": True, "rows": {"hours": [kept.pk, None]}}
        assert list(TourHours.objects.values_list("seats", flat=True)) == [7]

    def it_puts_an_overlap_on_each_row_and_saves_nothing(guide: Member):
        data = _hours_post(guide, {}, {"start_time": "17:00", "end_time": "19:00"})
        response = signed_in(guide).post(SAVE, data, **AUTOSAVE)
        assert response.status_code == 422
        assert response.json() == {
            "errors": {
                "hours-0-__all__": [
                    "These hours overlap your Tuesday hours, 5:00 PM to 7:00 PM. A guide gives one tour at a time."
                ],
                "hours-1-__all__": [
                    "These hours overlap your Tuesday hours, 4:00 PM to 6:00 PM. A guide gives one tour at a time."
                ],
            }
        }
        assert "Couldn't save that." in response["HX-Trigger"]
        assert not TourHours.objects.exists()

    def it_names_one_clash_once_on_a_row_that_overlaps_two_alike(guide: Member):
        data = _hours_post(
            guide, {}, {"start_time": "17:00", "end_time": "19:00"}, {"start_time": "17:00", "end_time": "19:00"}
        )
        errors = signed_in(guide).post(SAVE, data, **AUTOSAVE).json()["errors"]
        assert errors["hours-0-__all__"] == [
            "These hours overlap your Tuesday hours, 5:00 PM to 7:00 PM. A guide gives one tour at a time."
        ]

    def it_puts_a_clash_with_dated_hours_on_the_row(guide: Member):
        today = timezone.localdate()
        saturday = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
        TourHoursFactory(
            guide=guide, weekday=TourHours.Weekday.SATURDAY, date=saturday, start_time=time(12), end_time=time(14)
        )
        data = _hours_post(guide, {"weekday": "5", "start_time": "11:00", "end_time": "16:00"})
        errors = signed_in(guide).post(SAVE, data, **AUTOSAVE).json()["errors"]
        assert list(errors) == ["hours-0-__all__"]
        assert errors["hours-0-__all__"][0].startswith("Every Saturday 11:00 AM to 4:00 PM overlaps your hours on ")

    def it_keys_a_field_error_by_its_name(guide: Member):
        data = _hours_post(guide, {"end_time": "16:30"})
        response = signed_in(guide).post(SAVE, data, **AUTOSAVE)
        assert response.status_code == 422
        assert response.json()["errors"] == {"hours-0-tour_minutes": ["These hours are shorter than one tour."]}

    def it_checks_and_saves_under_the_guides_hours_lock(guide: Member):
        seen: list[bool] = []

        def lock(target: Member) -> None:
            seen.append(target == guide and transaction.get_connection().in_atomic_block)

        with mock.patch("tours.views.lock_guide_hours", side_effect=lock):
            signed_in(guide).post(SAVE, _hours_post(guide, {}), **AUTOSAVE)
        assert seen == [True]

    def it_saves_another_guides_hours_for_an_admin(admin: Member):
        other = make_guide()
        response = signed_in(admin).post(SAVE, _hours_post(other, {}), **AUTOSAVE)
        assert response.status_code == 200
        assert TourHours.objects.get().guide == other

    def it_refuses_a_guide_posting_another_guides_hours(guide: Member):
        other = make_guide()
        assert signed_in(guide).post(SAVE, _hours_post(other, {}), **AUTOSAVE).status_code == 403
        assert not TourHours.objects.exists()


def describe_new_hours_row():
    def it_opens_with_day_from_and_until_blank(guide: Member):
        form = TourHoursForm(defaults=FacilityTourSettings.load())
        assert form.fields["start_time"].choices[0] == ("", "—")
        assert form.fields["end_time"].choices[0] == ("", "—")
        assert form.fields["start_time"].required

    def it_keeps_a_saved_rows_times_without_a_blank(guide: Member):
        form = TourHoursForm(instance=TourHoursFactory(guide=guide), defaults=FacilityTourSettings.load())
        assert form.fields["start_time"].choices[0] == ("06:00", "6:00 AM")


def describe_confirming_bookings_autosave():
    def it_saves_the_choice_and_answers_json(guide: Member):
        data = {"guide": str(guide.pk), "confirm_mode": "first", "min_notice_hours": "72"}
        response = signed_in(guide).post(CONFIRM_MODE, data, **AUTOSAVE)
        assert response.status_code == 200
        assert response.json() == {"saved": True, "rows": {}}
        saved = TourGuideSettings.objects.get(guide=guide)
        assert (saved.confirm_mode, saved.min_notice_hours) == ("first", 72)

    def it_refuses_a_notice_under_the_house_minimum_under_the_field(guide: Member):
        TourGuideSettings.objects.create(guide=guide, confirm_mode="first", min_notice_hours=48)
        data = {"guide": str(guide.pk), "confirm_mode": "first", "min_notice_hours": "2"}
        response = signed_in(guide).post(CONFIRM_MODE, data, **AUTOSAVE)
        assert response.status_code == 422
        assert response.json()["errors"] == {
            "min_notice_hours": ["At least 24 hours: the minimum notice in Tour Settings."]
        }
        assert TourGuideSettings.objects.get(guide=guide).min_notice_hours == 48


@pytest.mark.usefixtures("google_on")
def describe_google_calendar_autosave():
    def it_saves_the_picked_calendar_and_answers_json(guide: Member):
        TourCalendarConnectionFactory(guide=guide)
        response = signed_in(guide).post(CHOOSE, {"calendar": TOURS_CAL}, **AUTOSAVE)
        assert response.json() == {"saved": True, "rows": {}}
        assert TourCalendarConnection.objects.get(guide=guide).calendar_id == TOURS_CAL

    def it_refuses_a_calendar_google_never_listed_under_the_picker(guide: Member):
        TourCalendarConnectionFactory(guide=guide)
        response = signed_in(guide).post(CHOOSE, {"calendar": "someone-else@group"}, **AUTOSAVE)
        assert response.status_code == 422
        assert list(response.json()["errors"]) == ["calendar"]
        assert TourCalendarConnection.objects.get(guide=guide).calendar_id == "primary"

    def it_shows_the_picker_as_a_form_that_saves_itself(guide: Member):
        TourCalendarConnectionFactory(guide=guide)
        content = signed_in(guide).get(HOURS).content.decode()
        assert 'data-tour-gcal-form data-autosave data-autosave-key="gcal"' in content
        assert 'data-tour-save-status="gcal"' in content
        assert "data-tour-gcal-save" not in content


def describe_tour_settings_autosave():
    def it_saves_and_answers_json(admin: Member):
        response = signed_in(admin).post(SETTINGS, _settings_post(booking_horizon_days="45"), **AUTOSAVE)
        assert response.status_code == 200
        assert response.json() == {"saved": True, "rows": {}}
        assert FacilityTourSettings.load().booking_horizon_days == 45

    def it_refuses_a_bad_value_and_keeps_the_last_good_one(admin: Member):
        response = signed_in(admin).post(SETTINGS, _settings_post(booking_horizon_days="0"), **AUTOSAVE)
        assert response.status_code == 422
        assert list(response.json()["errors"]) == ["booking_horizon_days"]
        assert FacilityTourSettings.load().booking_horizon_days == 30

    def it_asks_for_the_closed_message_when_bookings_go_off(admin: Member):
        response = signed_in(admin).post(SETTINGS, _settings_post(accepting_bookings=None), **AUTOSAVE)
        assert response.json()["errors"] == {"closed_message": ["Write what visitors read while bookings are closed."]}
        assert FacilityTourSettings.load().accepting_bookings

    def it_keeps_a_guide_out(guide: Member):
        assert signed_in(guide).post(SETTINGS, _settings_post(), **AUTOSAVE).status_code == 403


def describe_pages_carry_no_save_button():
    def it_marks_my_hours_forms_to_save_themselves(guide: Member):
        content = signed_in(guide).get(HOURS).content.decode()
        assert "x-data=\"plGuildAutosave({ section: 'hours' })\"" in content
        assert 'data-autosave data-autosave-key="hours" data-formset="hours"' in content
        assert 'data-formset-required="weekday start_time end_time tour_minutes seats"' in content
        assert 'data-tour-confirm-mode data-autosave data-autosave-key="confirm"' in content
        assert "A change could not be saved. Leave anyway, or stay and try again?" in content
        for gone in ("data-tour-hours-save", "data-tour-confirm-save", "pl-tours-save-row"):
            assert gone not in content

    def it_applies_the_admins_guide_picker_on_change_with_show_only_without_script(admin: Member):
        make_guide()
        content = signed_in(admin).get(HOURS).content.decode()
        assert 'onchange="this.form.requestSubmit()"' in content
        assert "pl-tours-picker__go" not in content
        assert (
            '<noscript><button type="submit" class="pl-btn pl-btn--secondary pl-btn--sm">Show</button></noscript>'
            in content
        )

    def it_marks_tour_settings_to_save_themselves(admin: Member):
        content = signed_in(admin).get(SETTINGS).content.decode()
        assert 'data-tour-settings-form data-autosave data-autosave-key="settings"' in content
        for key in ("availability", "booking", "defaults"):
            assert f'data-tour-save-status="{key}"' in content
        assert "data-tour-settings-save" not in content
        # Actions stay buttons.
        assert "data-tour-kiosk-create>Create Kiosk Link</button>" in content
