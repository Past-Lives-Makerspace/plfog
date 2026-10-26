"""The Tours page's My Hours tab, its days off and Admin Tools > Tour Settings (#947)."""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.membership.factories import MemberFactory
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourDayOffFactory, TourHoursFactory
from tours.models import FacilityTourSettings, TourDayOff, TourHours

pytestmark = pytest.mark.django_db

HOURS = reverse("tours:hours")
SAVE = reverse("tours:hours_save")
DAY_OFF_SAVE = reverse("tours:day_off_save")
SETTINGS = reverse("tours:settings")


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


def _day_off_post(guide: Member, **values: str) -> dict[str, str]:
    return {"guide": str(guide.pk), **{f"day_off-{name}": value for name, value in values.items()}}


def _in(days: int) -> str:
    return (timezone.localdate() + timedelta(days=days)).isoformat()


def describe_access():
    def it_sends_a_signed_out_visitor_to_sign_in():
        response = Client().get(HOURS)
        assert response.status_code == 302
        assert "/login" in response["Location"] or "/accounts/" in response["Location"]

    def it_refuses_every_tours_url_to_a_member_without_the_permission(plain: Member):
        client = signed_in(plain)
        day_off = TourDayOffFactory(guide=plain)
        assert client.get(HOURS).status_code == 403
        assert client.post(SAVE, _hours_post(plain, {})).status_code == 403
        assert client.post(DAY_OFF_SAVE, _day_off_post(plain, starts_on=_in(3))).status_code == 403
        assert client.post(reverse("tours:day_off_delete", args=[day_off.pk])).status_code == 403
        assert client.get(SETTINGS).status_code == 403
        assert not TourHours.objects.exists()
        assert TourDayOff.objects.filter(pk=day_off.pk).exists()

    def it_opens_the_page_to_a_tour_guide_and_an_admin(guide: Member, admin: Member):
        assert signed_in(guide).get(HOURS).status_code == 200
        assert signed_in(admin).get(HOURS).status_code == 200

    def it_keeps_tour_settings_to_admins(guide: Member, admin: Member):
        assert signed_in(guide).get(SETTINGS).status_code == 403
        assert signed_in(admin).get(SETTINGS).status_code == 200

    def it_refuses_an_account_with_no_member_record():
        user = User.objects.create_superuser("rootless", "rootless@example.com", "pass")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)
        assert client.get(HOURS).status_code == 404


def _preview_as(client: Client, role: str) -> None:
    session = client.session
    session["view_as_role"] = role
    session.save()


def describe_sidebar():
    def it_shows_tours_to_a_guide_and_an_admin_only(guide: Member, admin: Member, plain: Member):
        assert 'data-nav="tours"' in signed_in(guide).get(reverse("hub_home")).content.decode()
        assert 'data-nav="tours"' in signed_in(admin).get(reverse("hub_home")).content.decode()
        assert 'data-nav="tours"' not in signed_in(plain).get(reverse("hub_home")).content.decode()

    def it_follows_an_admins_view_as_preview_while_the_page_stays_open(admin: Member):
        client = signed_in(admin)
        _preview_as(client, "member")
        assert 'data-nav="tours"' not in client.get(reverse("hub_home")).content.decode()
        # The page is gated on the actual role, so the admin still reaches it while previewing.
        assert client.get(HOURS).status_code == 200
        admin.set_admin_capability("tour_guide", True)
        assert 'data-nav="tours"' in client.get(reverse("hub_home")).content.decode()
        _preview_as(client, "guest")
        assert 'data-nav="tours"' not in client.get(HOURS).content.decode()

    def it_lights_the_entry_on_the_tours_page(guide: Member):
        content = signed_in(guide).get(HOURS).content.decode()
        assert 'class="hub-sidebar__link active" aria-current="page" data-nav="tours"' in content

    def it_shows_once_the_permission_is_granted(plain: Member):
        client = signed_in(plain)
        assert client.get(HOURS).status_code == 403
        plain.set_admin_capability("tour_guide", True)
        assert client.get(HOURS).status_code == 200
        assert 'data-nav="tours"' in client.get(reverse("hub_home")).content.decode()


def describe_my_hours():
    def it_shows_the_empty_state_before_any_hours(guide: Member):
        content = signed_in(guide).get(HOURS).content.decode()
        assert "data-tour-hours-empty>No tour hours yet. Add the times you can give tours.</p>" in content
        assert "My Tour Hours" in content
        assert "data-tour-guide-picker" not in content

    def it_saves_tuesday_afternoon_hours_and_shows_them_on_reload(guide: Member):
        client = signed_in(guide)
        response = client.post(SAVE, _hours_post(guide, {}))
        assert response.status_code == 302
        assert response["Location"] == HOURS
        hours = TourHours.objects.get()
        assert (hours.guide, hours.weekday, hours.start_time, hours.end_time, hours.tour_minutes, hours.seats) == (
            guide,
            TourHours.Weekday.TUESDAY,
            time(16, 0),
            time(18, 0),
            60,
            4,
        )
        content = client.get(HOURS).content.decode()
        assert "Tuesday · 4:00 PM to 6:00 PM · 1 hour each · 4 seats" in content
        assert "data-tour-hours-empty>" not in content
        assert "data-tour-hours-delete" in content

    def it_deletes_a_row_when_its_delete_flag_is_posted(guide: Member):
        hours = TourHoursFactory(guide=guide)
        response = signed_in(guide).post(SAVE, _hours_post(guide, {"id": str(hours.pk), "DELETE": "on"}, initial=1))
        assert response.status_code == 302
        assert not TourHours.objects.exists()

    def it_confirms_before_a_saved_row_is_deleted(guide: Member):
        hours = TourHoursFactory(guide=guide)
        content = signed_in(guide).get(HOURS).content.decode()
        assert f"open-confirm', 'tour-hours-del-{hours.pk}'" in content
        assert "Delete these tour hours?" in content

    def it_starts_a_new_row_from_the_tour_settings_defaults(guide: Member):
        tour_settings = FacilityTourSettings.load()
        tour_settings.default_seats = 9
        tour_settings.save()
        content = signed_in(guide).get(HOURS).content.decode()
        assert 'name="hours-__prefix__-seats" value="9"' in content

    def it_shows_the_errors_and_keeps_the_rows_open_when_a_save_is_refused(guide: Member):
        response = signed_in(guide).post(SAVE, _hours_post(guide, {}, {"start_time": "17:00", "end_time": "19:00"}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Two of these rows overlap on Tuesday. A guide gives one tour at a time." in content
        assert 'x-data="plTourHoursRow(true)"' in content
        assert not TourHours.objects.exists()

    def it_saves_the_real_row_when_a_removed_clone_left_total_forms_one_higher(guide: Member):
        # + Add hours twice, Remove the second: the page posts TOTAL_FORMS 2 and only row 0's fields.
        data = _hours_post(guide, {})
        data["hours-TOTAL_FORMS"] = "2"
        response = signed_in(guide).post(SAVE, data)
        assert response.status_code == 302
        assert list(TourHours.objects.values_list("guide_id", "weekday", "seats")) == [(guide.pk, 1, 4)]

    def it_says_at_the_top_when_a_save_is_refused_and_keeps_a_removed_clone_gone(guide: Member):
        data = _hours_post(guide, {"end_time": "16:30"})
        data["hours-TOTAL_FORMS"] = "2"
        response = signed_in(guide).post(SAVE, data)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'role="alert" data-tour-hours-refused' in content
        assert "These hours are shorter than one tour." in content
        assert 'name="hours-1-weekday"' not in content
        assert not TourHours.objects.exists()

    def it_shows_no_refusal_on_a_plain_visit(guide: Member):
        assert "data-tour-hours-refused" not in signed_in(guide).get(HOURS).content.decode()

    def it_refuses_a_stale_tab_whose_new_row_overlaps_saved_hours_it_never_showed(guide: Member):
        TourHoursFactory(guide=guide)  # Tuesday 4:00 to 6:00 PM
        response = signed_in(guide).post(SAVE, _hours_post(guide, {"start_time": "17:00", "end_time": "19:00"}))
        assert response.status_code == 200
        assert "These hours overlap saved Tuesday hours of 4:00 PM to 6:00 PM" in response.content.decode()
        assert list(TourHours.objects.values_list("start_time", flat=True)) == [time(16, 0)]

    def it_leaves_another_guides_hours_alone(guide: Member):
        other = TourHoursFactory(guide=make_guide())
        signed_in(guide).post(SAVE, _hours_post(guide, {"weekday": "3"}))
        assert TourHours.objects.filter(pk=other.pk, weekday=TourHours.Weekday.TUESDAY).exists()


def describe_another_guides_hours():
    def it_lets_an_admin_pick_a_guide_and_edit_their_hours(admin: Member, guide: Member):
        client = signed_in(admin)
        content = client.get(HOURS).content.decode()
        assert "data-tour-guide-picker" in content
        assert f'<option value="{guide.pk}">Gina Guide</option>' in content
        assert "data-tour-guides-empty" not in content
        content = client.get(f"{HOURS}?guide={guide.pk}").content.decode()
        assert "Gina Guide's Tour Hours" in content
        assert f'<input type="hidden" name="guide" value="{guide.pk}">' in content
        response = client.post(SAVE, _hours_post(guide, {}))
        assert response["Location"] == f"{HOURS}?guide={guide.pk}"
        assert TourHours.objects.get().guide == guide

    def it_lists_another_admin_with_no_hours_and_lets_them_be_given_some(admin: Member):
        other = MemberFactory(full_legal_name="Otto Admin", fog_role=Member.FogRole.ADMIN)
        client = signed_in(admin)
        assert f'<option value="{other.pk}">Otto Admin</option>' in client.get(HOURS).content.decode()
        assert client.post(SAVE, _hours_post(other, {})).status_code == 302
        assert TourHours.objects.get().guide == other

    def it_tells_an_admin_when_there_is_no_guide_to_pick(admin: Member):
        content = signed_in(admin).get(HOURS).content.decode()
        assert "No Tour Guides yet. Turn on Tour Guide on a member's Permissions tab to add one." in content

    def it_lists_a_former_guide_who_still_has_hours(admin: Member):
        former = MemberFactory(full_legal_name="Fern Former")
        TourHoursFactory(guide=former)
        client = signed_in(admin)
        assert f'<option value="{former.pk}">Fern Former</option>' in client.get(HOURS).content.decode()
        assert client.get(f"{HOURS}?guide={former.pk}").status_code == 200

    def it_refuses_a_guide_who_names_another_guide(guide: Member):
        other = make_guide()
        client = signed_in(guide)
        assert client.get(f"{HOURS}?guide={other.pk}").status_code == 403
        assert client.post(SAVE, _hours_post(other, {})).status_code == 403
        assert not TourHours.objects.exists()

    def it_lets_a_guide_name_themselves(guide: Member):
        assert signed_in(guide).get(f"{HOURS}?guide={guide.pk}").status_code == 200

    def it_refuses_an_admin_a_member_who_gives_no_tours(admin: Member, plain: Member):
        client = signed_in(admin)
        assert client.get(f"{HOURS}?guide={plain.pk}").status_code == 404
        assert client.get(f"{HOURS}?guide=nobody").status_code == 404

    def it_never_touches_a_row_id_outside_the_guides_own_hours(guide: Member):
        # The formset reads rows from this guide's hours only; Django skips a posted id outside them.
        theirs = TourHoursFactory(guide=make_guide())
        signed_in(guide).post(SAVE, _hours_post(guide, {"id": str(theirs.pk), "weekday": "4"}, initial=1))
        assert list(TourHours.objects.values_list("pk", "weekday")) == [(theirs.pk, TourHours.Weekday.TUESDAY)]


def describe_days_off():
    def it_shows_none_coming_up_at_first(guide: Member):
        content = signed_in(guide).get(HOURS).content.decode()
        assert "data-tour-days-off-empty>No days off coming up.</p>" in content

    def it_saves_a_run_of_days_off_over_htmx(guide: Member):
        client = signed_in(guide)
        response = client.post(
            DAY_OFF_SAVE, _day_off_post(guide, starts_on=_in(3), ends_on=_in(5), note="Away"), HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 204
        assert response["HX-Redirect"] == HOURS
        day_off = TourDayOff.objects.get()
        assert (day_off.guide, day_off.note) == (guide, "Away")
        content = client.get(HOURS).content.decode()
        assert f'data-tour-day-off="{day_off.pk}"' in content
        assert day_off.label in content

    def it_shows_the_errors_inside_the_modal_over_htmx(guide: Member):
        response = signed_in(guide).post(DAY_OFF_SAVE, _day_off_post(guide, starts_on=_in(-1)), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "Pick today or a day ahead." in response.content.decode()
        assert "<html" not in response.content.decode()
        assert not TourDayOff.objects.exists()

    def it_saves_and_redirects_without_htmx(admin: Member, guide: Member):
        response = signed_in(admin).post(DAY_OFF_SAVE, _day_off_post(guide, starts_on=_in(3)))
        assert response.status_code == 302
        assert response["Location"] == f"{HOURS}?guide={guide.pk}"
        assert TourDayOff.objects.get().guide == guide

    def it_reopens_the_modal_on_the_page_when_a_plain_post_is_refused(guide: Member):
        response = signed_in(guide).post(DAY_OFF_SAVE, _day_off_post(guide, starts_on=""))
        assert response.status_code == 200
        content = response.content.decode()
        assert "data-tour-day-off-reopen" in content
        assert "My Tour Hours" in content

    def it_refuses_a_guide_saving_another_guides_day_off(guide: Member):
        other = make_guide()
        assert signed_in(guide).post(DAY_OFF_SAVE, _day_off_post(other, starts_on=_in(3))).status_code == 403
        assert not TourDayOff.objects.exists()

    def it_deletes_a_guides_own_day_off(guide: Member):
        day_off = TourDayOffFactory(guide=guide)
        response = signed_in(guide).post(reverse("tours:day_off_delete", args=[day_off.pk]))
        assert response["Location"] == HOURS
        assert not TourDayOff.objects.exists()

    def it_refuses_a_guide_deleting_another_guides_day_off(guide: Member):
        day_off = TourDayOffFactory(guide=make_guide())
        assert signed_in(guide).post(reverse("tours:day_off_delete", args=[day_off.pk])).status_code == 403
        assert TourDayOff.objects.exists()

    def it_lets_an_admin_delete_anyones_day_off(admin: Member, guide: Member):
        day_off = TourDayOffFactory(guide=guide)
        response = signed_in(admin).post(reverse("tours:day_off_delete", args=[day_off.pk]))
        assert response["Location"] == f"{HOURS}?guide={guide.pk}"
        assert not TourDayOff.objects.exists()

    def it_refuses_a_get_on_the_write_urls(guide: Member):
        client = signed_in(guide)
        assert client.get(SAVE).status_code == 405
        assert client.get(DAY_OFF_SAVE).status_code == 405


def describe_tour_settings():
    def _post(**overrides: str) -> dict[str, Any]:
        data = {
            "accepting_bookings": "on",
            "closed_message": "Back soon.",
            "booking_horizon_days": "7",
            "min_notice_hours": "12",
            "cancel_cutoff_hours": "48",
            "default_tour_minutes": "90",
            "default_seats": "8",
        }
        data.update(overrides)
        return data

    def it_saves_and_lands_back_on_the_page(admin: Member):
        response = signed_in(admin).post(SETTINGS, _post())
        assert response.status_code == 302
        assert response["Location"] == SETTINGS
        saved = FacilityTourSettings.load()
        assert (
            saved.booking_horizon_days,
            saved.min_notice_hours,
            saved.cancel_cutoff_hours,
            saved.default_tour_minutes,
            saved.default_seats,
        ) == (7, 12, 48, 90, 8)

    def it_shows_a_field_error_and_saves_nothing_out_of_range(admin: Member):
        response = signed_in(admin).post(SETTINGS, _post(booking_horizon_days="400"))
        assert response.status_code == 200
        assert "Use 1 to 365 days." in response.content.decode()
        assert FacilityTourSettings.load().booking_horizon_days == 30

    def it_opens_the_closed_message_when_bookings_are_off(admin: Member):
        client = signed_in(admin)
        content = client.get(SETTINGS).content.decode()
        assert 'x-show="!accepting" x-cloak data-tour-closed-message' in content
        client.post(SETTINGS, _post(accepting_bookings=""))
        settings_row = FacilityTourSettings.load()
        assert settings_row.accepting_bookings is False
        assert 'x-show="!accepting" data-tour-closed-message' in client.get(SETTINGS).content.decode()

    def it_puts_a_tour_settings_card_on_admin_tools(admin: Member, guide: Member):
        assert (
            f'href="{SETTINGS}" data-tool-tour-settings'
            in signed_in(admin).get(reverse("hub_admin_tools")).content.decode()
        )
        response = signed_in(guide).get(reverse("hub_admin_tools"))
        assert "data-tool-tour-settings" not in response.content.decode()
