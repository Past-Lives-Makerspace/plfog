"""BDD specs for #1300 part 5: every place hours were set opens My Hours, and My Hours keeps what those places had.

Each door is a link to ``/hours/`` (``?member=`` when an admin opens someone else's): the Tours
page's My Hours tab, a Calendar gap's Add My Hours (``?add=``, which opens + Add Dates on that day),
a guild's or tool's Orientation Schedule, Manage Bookings' Your Hours, the "Post your orientation
hours" nudge, Get Set Up, the permission guide and the staff welcome email. What the old editors
had that My Hours lacked comes across: the admin's picker names each person's Google Calendar, a
paused orientation shows (unticked) and comes back with its settings, and the "Off ..." line on an
Orientation Schedule reads the one Days Off list.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from hours.days_off import days_off_by_member
from hours.edit import gives_tours, held_owners
from hours.forms import PAUSED_HINT, Context, hours_formset
from hours.models import DayOff
from membership.models import AdminCapability, Member, OrientationAvailability
from membership.staff_onboarding import tool_links
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationTypeFactory,
)
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourCalendarConnectionFactory, TourDayOffFactory
from tours.models import FacilityTourSettings, TourCalendarConnection
from tours.setup import TOUR_GUIDE_SETUP

pytestmark = pytest.mark.django_db

URL = reverse("hours:my_hours")


def _orientor() -> tuple[Member, Any]:
    person = make_guide()
    guild = GuildFactory(name="Print Guild", guild_lead=person)
    GuildOrientationSettingsFactory(guild=guild)
    OrientationTypeFactory(guild=guild, name="Intro to Print")
    return person, guild


def _context(person: Member) -> Context:
    return Context(person, gives_tours(person), held_owners(person), FacilityTourSettings.load())


def describe_add_from_a_calendar_gap():
    def it_opens_add_dates_with_the_day_picked():
        guide = make_guide()
        day = timezone.localdate() + timedelta(days=3)
        response = signed_in(guide).get(URL, {"add": day.isoformat()})
        assert response.context["add_dates_open"] is True
        assert response.context["add_dates_calendar"]["picked"] == [day.isoformat()]
        assert 'id="available-on"' in response.content.decode()

    @pytest.mark.parametrize("raw", ["nope", "2001-01-01", "far"])
    def it_ignores_a_day_add_dates_could_not_pick(raw: str):
        guide = make_guide()
        if raw == "far":
            raw = (timezone.localdate() + timedelta(days=400)).isoformat()
        response = signed_in(guide).get(URL, {"add": raw})
        assert response.context["add_dates_open"] is False
        assert response.context["add_dates_calendar"]["picked"] == []


def describe_the_admins_picker():
    def it_names_each_persons_google_calendar_once_google_is_set_up(admin: Member, google_on: None):
        connected = make_guide(full_legal_name="Cora Connected")
        TourCalendarConnectionFactory(guide=connected)
        broken = make_guide(full_legal_name="Bram Broken")
        TourCalendarConnectionFactory(guide=broken, status=TourCalendarConnection.Status.BROKEN)
        make_guide(full_legal_name="Nell None")
        page = signed_in(admin).get(URL).content.decode()
        assert "Cora Connected · Calendar connected</option>" in page
        assert "Bram Broken · Calendar needs reconnecting</option>" in page
        assert "Nell None · No calendar</option>" in page

    def it_leaves_the_names_plain_while_google_is_not_set_up(admin: Member, settings: Any):
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        make_guide(full_legal_name="Cora Connected")
        page = signed_in(admin).get(URL).content.decode()
        assert ">Cora Connected</option>" in page
        assert "data-hours-calendar" not in page


def describe_a_paused_orientation():
    def _paused(person: Member, guild: Any) -> OrientationAvailability:
        rule = OrientationAvailability(
            guild=guild,
            orienter=person,
            orientation_type=guild.orientation_types.get(),
            weekday=1,
            start_time=time(16),
            end_time=time(18),
            seats=3,
            is_active=False,
        )
        rule.save()
        return rule

    def it_shows_unticked_with_its_settings_while_the_person_gives_that_kind():
        person, guild = _orientor()
        rule = _paused(person, guild)
        form = hours_formset(_context(person), dated=False).forms[0]
        block = next(block for block in form.blocks if block.rule == rule)
        assert block.hint == PAUSED_HINT
        assert form[f"rule-{rule.pk}-on"].value() is False
        assert form[f"rule-{rule.pk}-seats"].value() == 3
        # The paused row stands for its guild: no second block for it.
        assert [b.key for b in form.blocks] == ["tours", f"rule-{rule.pk}"]

    def it_stays_hidden_once_the_person_no_longer_gives_that_kind():
        person = make_guide()
        other = GuildFactory(name="Left Guild")
        OrientationTypeFactory(guild=other)
        rule = _paused(person, other)
        form = hours_formset(_context(person), dated=False).forms[0]
        assert all(block.rule != rule for block in form.blocks)

    def it_comes_back_with_its_settings_when_ticked_on():
        person, guild = _orientor()
        rule = _paused(person, guild)
        form = hours_formset(_context(person), dated=False).forms[0]
        data: dict[str, Any] = {
            "member": str(person.pk),
            "list": "weekly",
            "weekly-TOTAL_FORMS": "1",
            "weekly-INITIAL_FORMS": "1",
            "weekly-MIN_NUM_FORMS": "0",
            "weekly-MAX_NUM_FORMS": "1000",
        }
        for name in form.fields:
            value = form[name].value()
            if isinstance(value, bool):
                if value:
                    data[f"weekly-0-{name}"] = "on"
            elif value is not None:
                data[f"weekly-0-{name}"] = value.isoformat() if isinstance(value, date) else str(value)
        data[f"weekly-0-rule-{rule.pk}-on"] = "on"
        response = signed_in(person).post(reverse("hours:save"), data, HTTP_X_AUTOSAVE="1")
        assert response.status_code == 200
        rule.refresh_from_db()
        assert rule.is_active is True and rule.seats == 3


def describe_the_off_line():
    def it_reads_every_kinds_days_off_soonest_first_and_none_over():
        person = MemberFactory()
        today = timezone.localdate()
        later = TourDayOffFactory(guide=person, starts_on=today + timedelta(days=9), ends_on=today + timedelta(days=11))
        TourDayOffFactory(guide=person, starts_on=today - timedelta(days=5), ends_on=today - timedelta(days=4))
        soon = TourDayOffFactory(guide=person, starts_on=today + timedelta(days=2), ends_on=today + timedelta(days=2))
        found = days_off_by_member([person])[str(person.pk)]
        assert [(d.starts_on, d.ends_on) for d in found] == [
            (soon.starts_on, soon.ends_on),
            (later.starts_on, later.ends_on),
        ]
        assert all(isinstance(day_off, DayOff) for day_off in found)
        assert days_off_by_member([MemberFactory()]) == {}

    def it_draws_a_run_of_days_off_on_the_orientation_schedule():
        person, guild = _orientor()
        today = timezone.localdate()
        first, last = today + timedelta(days=9), today + timedelta(days=11)
        TourDayOffFactory(guide=person, starts_on=first, ends_on=last)
        page = signed_in(person).get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        label = f"{first:%a, %b} {first.day} to {last:%a, %b} {last.day}"
        assert f"data-days-off-line>Off {label}.</p>" in page


def describe_the_doors():
    def it_sends_the_tours_tab_get_set_up_and_the_permission_guide_to_my_hours():
        guide = make_guide()
        page = signed_in(guide).get(reverse("tours:upcoming")).content.decode()
        assert (
            f'<a href="{URL}" class="vote-tab" data-tours-tab="hours" data-guidance-target="tours.my-hours">My Hours</a>'
            in page
        )
        assert {step.action_url for step in TOUR_GUIDE_SETUP.steps if step.action_url_name.startswith("hours:")} == {
            URL
        }
        assert "tours:hours" not in {step.action_url_name for step in TOUR_GUIDE_SETUP.steps}
        guide_page = AdminCapability.GUIDES[AdminCapability.Capability.TOUR_GUIDE]
        assert ("My Hours", "hours:my_hours") in [(page.label, page.url_name) for page in guide_page.pages]

    def it_lists_my_hours_in_the_staff_welcome_email():
        guild = GuildFactory()
        assert ("My Hours", URL) in [(name, path) for name, _purpose, path in tool_links(guild)]

    def it_lets_an_admin_open_a_staffers_hours_from_the_orientation_schedule(admin: Member):
        guild = GuildFactory(guild_lead=MemberFactory())
        page = signed_in(admin).get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        assert f'href="{URL}?member={guild.guild_lead.pk}" class="hub-btn hub-btn--sm" data-edit-hours-link' in page
        assert f'href="{URL}?member={guild.guild_lead.pk}#days-off"' in page


def describe_the_get_set_up_card():
    def it_sits_atop_a_tour_guides_own_my_hours(admin: Member):
        guide = make_guide()
        assert 'data-permission-setup="tour_guide"' in signed_in(guide).get(URL).content.decode()
        # An admin opening someone else's hours reads theirs, with no card of the admin's own.
        response = signed_in(admin).get(URL, {"member": guide.pk})
        assert response.context["setup"] is None

    def it_toasts_the_badge_on_the_load_that_finishes_setup(monkeypatch: pytest.MonkeyPatch):
        guide = make_guide()

        class Finished:
            just_finished = True
            hidden = True

            class spec:  # noqa: N801 - the state's spec, as SetupState names it
                celebration = "Your Tour Guide badge is on your Member Directory card."

        monkeypatch.setattr("hours.views.SetupState.load", lambda member, capability: Finished())
        response = signed_in(guide).get(URL)
        assert [str(m) for m in response.context["messages"]] == [Finished.spec.celebration]

    def it_shows_no_card_to_an_orientor_without_the_grant():
        person = MemberFactory()
        GuildFactory(guild_lead=person)
        assert signed_in(person).get(URL).context["setup"] is None
