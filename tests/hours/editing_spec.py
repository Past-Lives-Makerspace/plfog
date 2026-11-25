"""Editing My Hours (#1300, part 4b): Every Week and Available On save themselves; + Add Dates; who may edit."""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hours.edit import gives_tours, held_owners
from hours.forms import AddDatesForm, Context, HoursSetForm, hours_formset
from hours.models import Hours
from membership.models import Member, OrientationAvailability
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationAvailabilityFactory,
    OrientationTypeFactory,
)
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourDayOffFactory, TourHoursFactory
from tours.models import FacilityTourSettings, TourHours

pytestmark = pytest.mark.django_db

SAVE = "/hours/save/"
DATES = "/hours/dates/"


def _ahead(weekday: int, weeks: int = 1) -> date:
    today = timezone.localdate()
    return today + timedelta(days=(weekday - today.weekday()) % 7 + 7 * weeks)


def _orientor(**kwargs: Any) -> tuple[Member, Any]:
    """A Tour Guide who leads a guild with one orientation, orientations switched on."""
    person = make_guide(**kwargs)
    guild = GuildFactory(name="Print Guild", guild_lead=person)
    GuildOrientationSettingsFactory(guild=guild)
    OrientationTypeFactory(guild=guild, name="Intro to Print")
    return person, guild


def _context(person: Member) -> Context:
    return Context(person, gives_tours(person), held_owners(person), FacilityTourSettings.load())


def _row(form: HoursSetForm, index: int, prefix: str, **changes: Any) -> dict[str, Any]:
    """One row's POST as the page renders it (``changes`` by short field name, None to leave a field out)."""
    data: dict[str, Any] = {}
    for name in form.fields:
        value = form[name].value()
        if isinstance(value, bool):
            if value:
                data[name] = "on"
        elif value is not None:
            data[name] = value.isoformat() if isinstance(value, date) else str(value)
    for name, value in changes.items():
        data.pop(name, None)
        if value is not None:
            data[name] = value
    return {f"{prefix}-{index}-{name}": value for name, value in data.items()}


def _post(person: Member, rows: list[dict[str, Any]], *, prefix: str = "weekly", initial: int = 0) -> dict[str, Any]:
    data: dict[str, Any] = {
        "member": str(person.pk),
        "list": prefix,
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for row in rows:
        data.update(row)
    return data


def _new(person: Member, index: int = 0, prefix: str = "weekly", **changes: Any) -> dict[str, Any]:
    form = hours_formset(_context(person), dated=prefix == "dated").empty_form
    return _row(form, index, prefix, **changes)


def _saved(person: Member, index: int = 0, prefix: str = "weekly", **changes: Any) -> dict[str, Any]:
    form = hours_formset(_context(person), dated=prefix == "dated").forms[index]
    return _row(form, index, prefix, **changes)


def _autosave(client: Client, data: dict[str, Any]) -> Any:
    return client.post(SAVE, data, HTTP_X_AUTOSAVE="1")


def describe_who_may_edit():
    def it_refuses_someone_with_no_hours_page(plain: Member) -> None:
        assert signed_in(plain).get(reverse("hours:my_hours")).status_code == 403
        assert signed_in(plain).post(SAVE, _post(plain, [])).status_code == 403
        assert signed_in(plain).post(DATES, {"member": plain.pk}).status_code == 403

    def it_refuses_a_lead_editing_a_staffers_hours():
        lead, guild = _orientor()
        staffer = make_guide()
        response = signed_in(lead).post(
            SAVE, _post(staffer, [_new(staffer, weekday="1", start_time="10:00", end_time="12:00")])
        )
        assert response.status_code == 403
        assert signed_in(lead).post(DATES, {"member": staffer.pk}).status_code == 403
        assert not Hours.objects.exists()

    def it_lets_an_admin_edit_anyones_hours(admin: Member) -> None:
        guide = make_guide()
        data = _post(guide, [_new(guide, weekday="1", start_time="10:00", end_time="12:00")])
        assert _autosave(signed_in(admin), data).status_code == 200
        assert Hours.objects.get().member == guide

    def it_404s_an_admins_pick_that_is_not_a_member_number(admin: Member) -> None:
        assert signed_in(admin).post(SAVE, {"member": "x"}).status_code == 404


def describe_open_to():
    def it_offers_only_the_kinds_the_person_holds():
        person = MemberFactory()
        guild = GuildFactory(name="Wood Guild", guild_lead=person)
        OrientationTypeFactory(guild=guild, name="Bandsaw")
        other = GuildFactory(name="Glass Guild")
        OrientationTypeFactory(guild=other, name="Glass")
        page = signed_in(person).get(reverse("hours:my_hours")).content.decode()
        assert f'data-hours-kind="guild-{guild.pk}"' in page
        assert f'data-hours-kind="guild-{other.pk}"' not in page
        assert 'data-hours-kind="tours"' not in page

    def it_offers_tours_to_a_guide_and_ticks_everything_on_a_new_set():
        person, guild = _orientor()
        form = hours_formset(_context(person), dated=False).empty_form
        assert [block.key for block in form.blocks] == ["tours", f"guild-{guild.pk}"]
        assert form["tours-on"].value() is True
        assert form[f"guild-{guild.pk}-on"].value() is True
        page = signed_in(person).get(reverse("hours:my_hours")).content.decode()
        assert 'data-hours-kind="tours"' in page

    def it_shows_a_saved_row_of_a_kind_no_longer_held_so_it_can_be_ticked_off():
        person = make_guide()
        rule = OrientationAvailabilityFactory(orienter=person, start_time=time(16), end_time=time(18), slot_minutes=75)
        retired = rule.orientation_type
        retired.is_active = False
        retired.save()
        form = hours_formset(_context(person), dated=False).forms[0]
        assert [block.key for block in form.blocks] == ["tours", f"rule-{rule.pk}"]
        assert form[f"rule-{rule.pk}-on"].value() is True
        assert (str(retired.pk), retired.name) in form.fields[f"rule-{rule.pk}-orientation_type"].choices
        assert ("75", "75 minutes") in form.fields[f"rule-{rule.pk}-slot_minutes"].choices
        assert form["tours-on"].value() is False

    def it_keeps_a_saved_time_off_the_half_hour():
        person = make_guide()
        TourHoursFactory(guide=person, start_time=time(16, 15), end_time=time(18))
        form = hours_formset(_context(person), dated=False).forms[0]
        assert form["start_time"].value() == "16:15"


def describe_every_week():
    def it_saves_a_new_set_once_day_from_and_until_are_picked():
        person, guild = _orientor()
        client = signed_in(person)
        response = _autosave(client, _post(person, [_new(person, weekday="1", start_time="10:00", end_time="12:00")]))
        hours = Hours.objects.get()
        assert json.loads(response.content) == {"saved": True, "rows": {"weekly": [hours.pk]}}
        assert hours.tour_hours.count() == 1 and hours.orientation_rules.get().guild == guild

    def it_skips_a_new_row_posted_as_it_rendered():
        person, _ = _orientor()
        response = _autosave(signed_in(person), _post(person, [_new(person)]))
        assert json.loads(response.content)["rows"] == {"weekly": [None]}
        assert not Hours.objects.exists()

    def it_changes_a_saved_set_and_ticks_a_kind_off():
        person, guild = _orientor()
        client = signed_in(person)
        _autosave(client, _post(person, [_new(person, weekday="1", start_time="10:00", end_time="12:00")]))
        rule = OrientationAvailability.objects.get()
        row = _saved(person, start_time="09:00", **{"tours-on": None, f"rule-{rule.pk}-seats": "2"})
        response = _autosave(client, _post(person, [row], initial=1))
        assert response.status_code == 200
        hours = Hours.objects.get()
        assert hours.start_time == time(9) and not TourHours.objects.exists()
        assert hours.orientation_rules.get().seats == 2

    def it_repeats_every_other_week_from_a_start_day():
        person, _ = _orientor()
        start = _ahead(1)
        row = _new(
            person,
            weekday="1",
            start_time="10:00",
            end_time="12:00",
            cadence="fortnightly",
            anchor_date=start.isoformat(),
        )
        assert _autosave(signed_in(person), _post(person, [row])).status_code == 200
        assert TourHours.objects.get().cadence == "fortnightly"

    def it_deletes_a_set():
        person, _ = _orientor()
        client = signed_in(person)
        _autosave(client, _post(person, [_new(person, weekday="1", start_time="10:00", end_time="12:00")]))
        response = _autosave(client, _post(person, [_saved(person, DELETE="on")], initial=1))
        assert json.loads(response.content)["rows"] == {"weekly": [None]}
        assert not Hours.objects.exists() and not OrientationAvailability.objects.exists()

    def it_drops_a_new_row_flagged_for_deletion():
        person, _ = _orientor()
        row = _new(person, weekday="1", start_time="10:00", end_time="12:00", DELETE="on")
        assert json.loads(_autosave(signed_in(person), _post(person, [row])).content)["rows"] == {"weekly": [None]}
        assert not Hours.objects.exists()

    def it_answers_a_refusal_on_the_field():
        person, guild = _orientor()
        row = _new(person, weekday="1", start_time="12:00", end_time="10:00")
        response = _autosave(signed_in(person), _post(person, [row]))
        assert response.status_code == 422
        assert json.loads(response.content)["errors"]["weekly-0-end_time"] == ["Until must be after From."]

    def it_puts_a_kinds_problem_in_its_block():
        person, guild = _orientor()
        row = _new(
            person, weekday="1", start_time="10:00", end_time="12:00", **{f"guild-{guild.pk}-orientation_type": ""}
        )
        errors = json.loads(_autosave(signed_in(person), _post(person, [row])).content)["errors"]
        assert errors[f"weekly-0-guild-{guild.pk}-orientation_type"] == ["Pick an orientation."]
        tours = _new(person, weekday="1", start_time="10:00", end_time="10:30", **{f"guild-{guild.pk}-on": None})
        errors = json.loads(_autosave(signed_in(person), _post(person, [tours])).content)["errors"]
        assert errors["weekly-0-tours-tour_minutes"] == ["These hours are shorter than one tour."]
        nothing = _new(
            person,
            weekday="1",
            start_time="10:00",
            end_time="12:00",
            **{f"guild-{guild.pk}-on": None, "tours-on": None},
        )
        errors = json.loads(_autosave(signed_in(person), _post(person, [nothing])).content)["errors"]
        assert errors["weekly-0-__all__"] == ["Tick at least one thing these hours are open to, or delete them."]

    def it_skips_and_names_a_set_deleted_elsewhere():
        person, _ = _orientor()
        client = signed_in(person)
        _autosave(client, _post(person, [_new(person, weekday="1", start_time="10:00", end_time="12:00")]))
        row = _saved(person)
        Hours.objects.get().remove()
        response = _autosave(client, _post(person, [row], initial=1))
        body = json.loads(response.content)
        assert body["gone"] == {"weekly": [0]} and body["rows"] == {"weekly": [None]}
        assert "deleted elsewhere" in response["HX-Trigger"]

    def it_saves_and_refuses_without_the_script():
        person, _ = _orientor()
        client = signed_in(person)
        response = client.post(SAVE, _post(person, [_new(person, weekday="1", start_time="10:00", end_time="12:00")]))
        assert response.status_code == 302 and response["Location"] == reverse("hours:my_hours")
        refused = client.post(SAVE, _post(person, [_new(person, weekday="2", start_time="12:00", end_time="10:00")]))
        assert refused.status_code == 200
        assert b"data-hours-refused" in refused.content


def describe_available_on():
    def it_edits_a_dated_set_in_place():
        person, _ = _orientor()
        day = _ahead(5)
        TourHoursFactory(guide=person, date=day, weekday=day.weekday())
        row = _saved(person, prefix="dated", end_time="19:00")
        response = _autosave(signed_in(person), _post(person, [row], prefix="dated", initial=1))
        assert response.status_code == 200
        assert Hours.objects.get().end_time == time(19)

    def it_refuses_a_dated_set_without_the_script():
        person, _ = _orientor()
        day = _ahead(5)
        TourHoursFactory(guide=person, date=day, weekday=day.weekday())
        row = _saved(person, prefix="dated", end_time="10:00")
        response = signed_in(person).post(SAVE, _post(person, [row], prefix="dated", initial=1))
        assert response.status_code == 200 and b"data-hours-refused" in response.content


def describe_add_dates():
    def _data(person: Member, days: list[date], **changes: Any) -> dict[str, Any]:
        form = AddDatesForm(context=_context(person))
        data = {key.removeprefix("add_dates-"): value for key, value in _row(form, 0, "x").items()}
        data = {key.removeprefix("x-0-"): value for key, value in data.items()}
        data.update({"start_time": "10:00", "end_time": "12:00"})
        for name, value in changes.items():
            data.pop(name, None)
            if value is not None:
                data[name] = value
        posted = {f"add_dates-{key}": value for key, value in data.items()}
        return {"member": str(person.pk), "dates": [day.isoformat() for day in days], **posted}

    def it_adds_the_same_hours_on_every_date_picked():
        person, guild = _orientor()
        days = [_ahead(5), _ahead(5, weeks=2)]
        response = signed_in(person).post(DATES, _data(person, days))
        assert response.status_code == 302
        assert sorted(Hours.objects.values_list("date", flat=True)) == days
        assert OrientationAvailability.objects.filter(guild=guild, date__in=days).count() == 2

    def it_adds_nothing_when_any_date_is_refused():
        person, _ = _orientor()
        past, ahead = timezone.localdate() - timedelta(days=1), _ahead(5)
        far = timezone.localdate() + timedelta(days=400)
        TourHoursFactory(guide=person, date=ahead, weekday=ahead.weekday(), start_time=time(11), end_time=time(13))
        response = signed_in(person).post(DATES, _data(person, [past, ahead, far], **{"tours-on": "on"}))
        page = response.content.decode()
        assert response.status_code == 200 and "data-hours-dates-refused" in page
        assert "it has passed" in page and "more than a year ahead" in page and "overlap your tours" in page
        assert Hours.objects.count() == 1

    def it_needs_a_date():
        person, _ = _orientor()
        response = signed_in(person).post(DATES, {**_data(person, []), "dates": ["nope"]})
        assert "Pick at least one date." in response.content.decode()

    def it_draws_days_off_and_dated_hours_on_the_calendar():
        person, _ = _orientor()
        day = _ahead(5)
        TourHoursFactory(guide=person, date=day, weekday=day.weekday())
        TourDayOffFactory(
            guide=person, starts_on=timezone.localdate() - timedelta(days=1), ends_on=timezone.localdate()
        )
        page = signed_in(person).get(reverse("hours:my_hours")).content.decode()
        config = json.loads(page.split('id="hours-dates-config" type="application/json">')[1].split("</script>")[0])
        assert config["dated"] == [day.isoformat()]
        assert config["off"] == [timezone.localdate().isoformat()]


def describe_queries():
    def _render_rows(person: Member) -> None:
        context = _context(person)
        for dated in (False, True):
            formset = hours_formset(context, dated=dated)
            for form in formset:
                str(form)
                list(form.block_rows)

    def it_reads_the_same_number_of_queries_for_one_set_or_three(django_assert_num_queries):
        person, guild = _orientor()
        OrientationAvailabilityFactory(guild=guild, orienter=person, start_time=time(10), end_time=time(12))
        TourHoursFactory(guide=person, start_time=time(10), end_time=time(12))
        _render_rows(person)  # warm the per process caches
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as one:
            _render_rows(person)
        for weekday in (2, 3):
            OrientationAvailabilityFactory(
                guild=guild, orienter=person, weekday=weekday, start_time=time(10), end_time=time(12)
            )
            TourHoursFactory(guide=person, weekday=weekday, start_time=time(10), end_time=time(12))
        assert Hours.objects.filter(member=person).count() == 3
        with django_assert_num_queries(len(one.captured_queries)):
            _render_rows(person)
