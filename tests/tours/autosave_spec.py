"""Tour settings save themselves as they are edited (#1279).

Google Calendar's picker (on My Hours since #1300) and Tour Settings post with the autosave header
(``static/js/guild_autosave.js``) and answer JSON through ``hub.autosave``: a 200, or a 422 with every
error keyed by its html field name. The pages carry the autosave markers and no Save button. Every
Week's autosave is My Hours' (``tests/hours/editing_spec.py``).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse

from membership.models import Member
from tests.tours.conftest import signed_in
from tests.tours.factories import TourCalendarConnectionFactory
from tours.models import FacilityTourSettings, TourCalendarConnection

pytestmark = pytest.mark.django_db

CHOOSE = reverse("tours:google_calendar")
SETTINGS = reverse("tours:settings")
AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}
TOURS_CAL = "tours@group.calendar.google.com"


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


def describe_confirming_bookings_is_gone():
    def it_has_no_save_url(guide: Member):
        # Confirm First was removed (#1309): every booking is booked at once.
        assert signed_in(guide).post("/facility-tours/hours/confirming/save/", {}).status_code == 404


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

    def it_shows_the_picker_on_my_hours_as_a_form_that_saves_itself(guide: Member):
        TourCalendarConnectionFactory(guide=guide)
        content = signed_in(guide).get(reverse("hours:my_hours")).content.decode()
        assert 'data-autosave data-autosave-key="gcal"' in content
        assert 'data-tour-save-status="gcal"' in content


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
    def it_marks_tour_settings_to_save_themselves(admin: Member):
        content = signed_in(admin).get(SETTINGS).content.decode()
        assert 'data-tour-settings-form data-autosave data-autosave-key="settings"' in content
        for key in ("availability", "booking", "defaults"):
            assert f'data-tour-save-status="{key}"' in content
        assert "data-tour-settings-save" not in content
        assert content.count("data-tour-noscript-save>Save</button></noscript>") == 1
        # Actions stay buttons.
        assert "data-tour-kiosk-create>Create Kiosk Link</button>" in content
