"""BDD specs for #1300 part 5b: the old hours editors are gone, and every old URL lands on My Hours.

The Tours page's My Hours tab and what it posted to, the Edit Hours modal for a person, and the
Days Off modal each answer with My Hours (``?member=`` when they named someone other than the
viewer, ``#days-off`` for a day off), writing nothing. The shared rows (a guild's Guild Hours, a
tool's Edit Shared Hours) keep their editors.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from hours.links import my_hours_url
from membership.models import Member, OrientationAvailability, OrientationDayOff
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    MemberFactory,
    OrientationAvailabilityFactory,
    OrientationTypeFactory,
)
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourDayOffFactory, TourHoursFactory
from tours.models import TourDayOff, TourGuideSettings, TourHours

pytestmark = pytest.mark.django_db

MY_HOURS = reverse("hours:my_hours")


def describe_the_tours_pages_old_tab():
    def it_lands_on_my_hours_carrying_an_admins_pick_and_a_calendar_gap(admin: Member):
        client = signed_in(admin)
        assert client.get(reverse("tours:hours"))["Location"] == MY_HOURS
        assert client.get(reverse("tours:hours"), {"guide": "7"})["Location"] == f"{MY_HOURS}?member=7"
        response = client.get(reverse("tours:hours"), {"guide": "7", "add": "2030-01-02"})
        assert response["Location"] == f"{MY_HOURS}?member=7&add=2030-01-02"

    def it_sends_someone_with_no_my_hours_to_give_tours(plain: Member):
        assert signed_in(plain).get(reverse("tours:hours"))["Location"] == reverse("tours:give")

    def it_sends_a_signed_out_visitor_to_sign_in():
        assert "login" in Client().get(reverse("tours:hours"))["Location"]

    @pytest.mark.parametrize(
        ("name", "args"),
        [
            ("tours:hours_save", []),
            ("tours:dates_save", []),
            ("tours:dates_delete", [1]),
            ("tours:day_off_save", []),
            ("tours:day_off_delete", [1]),
        ],
    )
    def it_answers_every_old_save_and_delete_with_my_hours_writing_nothing(name: str, args: list[int]):
        guide = make_guide()
        row = TourHoursFactory(guide=guide)
        TourDayOffFactory(guide=guide)
        client = signed_in(guide)
        assert client.post(reverse(name, args=args), {"hours-TOTAL_FORMS": "0"})["Location"] == MY_HOURS
        assert client.post(reverse(name, args=args), {"guide": "12"})["Location"] == f"{MY_HOURS}?member=12"
        assert client.get(reverse(name, args=args))["Location"] == MY_HOURS
        assert list(TourHours.objects.all()) == [row] and TourDayOff.objects.count() == 1


def describe_booking_notice_without_the_script():
    def it_saves_and_lands_on_my_hours(admin: Member):
        guide = make_guide()
        response = signed_in(admin).post(reverse("tours:notice_save"), {"guide": guide.pk, "min_notice_minutes": "60"})
        assert response["Location"] == f"{MY_HOURS}?member={guide.pk}"
        assert TourGuideSettings.objects.get(guide=guide).min_notice_minutes == 60

    def it_names_a_refusal_and_lands_on_my_hours():
        guide = make_guide()
        response = signed_in(guide).post(reverse("tours:notice_save"), {"min_notice_minutes": "7"})
        assert response["Location"] == MY_HOURS
        assert not TourGuideSettings.objects.filter(guide=guide, min_notice_minutes=7).exists()


def describe_the_old_edit_hours_modal():
    def it_opens_my_hours_for_the_person_it_named():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        client = signed_in(lead)
        url = reverse("hub_guild_orientation_hours_form", args=[guild.pk])
        assert client.get(url, {"orienter": lead.pk})["Location"] == MY_HOURS
        assert client.get(url, {"orienter": 99})["Location"] == f"{MY_HOURS}?member=99"
        assert client.get(url)["Location"] == MY_HOURS
        htmx = client.get(url, {"orienter": 99}, HTTP_HX_REQUEST="true")
        assert htmx.status_code == 204 and htmx["HX-Redirect"] == f"{MY_HOURS}?member=99"
        assert client.get(reverse("hub_guild_orientation_hours_form", args=[guild.pk + 1000])).status_code == 404

    def it_saves_nothing_for_a_person_and_sends_them_to_my_hours():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=lead, seats=4)
        response = signed_in(lead).post(
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            {"orienter_scope": str(lead.pk), "formset_prefix": "modal_rules", "modal_rules-0-seats": "9"},
            HTTP_HX_REQUEST="true",
        )
        assert response["HX-Redirect"] == MY_HOURS
        rule.refresh_from_db()
        assert rule.seats == 4

    def it_does_the_same_for_a_tool(admin: Member):
        tool = EquipmentFactory()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=tool)
        person = MemberFactory()
        rule = OrientationAvailabilityFactory(equipment_owned=True, orientation_type=orientation_type, orienter=person)
        client = signed_in(admin)
        form = reverse("hub_equipment_orientation_hours_form", args=[tool.slug])
        assert client.get(form, {"orienter": person.pk})["Location"] == f"{MY_HOURS}?member={person.pk}"
        response = client.post(
            reverse("hub_equipment_orientation_hours_save", args=[tool.slug]), {"orienter_scope": str(person.pk)}
        )
        assert response["Location"] == f"{MY_HOURS}?member={person.pk}"
        assert OrientationAvailability.objects.get(pk=rule.pk).seats == rule.seats
        # The shared rows keep their modal.
        assert b"Editing Shared Hours" in client.get(form).content


def describe_the_old_days_off_modal():
    @pytest.mark.parametrize(
        "name",
        [
            "hub_guild_orientation_days_off",
            "hub_guild_orientation_day_off_preview",
            "hub_guild_orientation_day_off_save",
            "hub_equipment_orientation_days_off",
            "hub_equipment_orientation_day_off_preview",
            "hub_equipment_orientation_day_off_save",
        ],
    )
    def it_opens_my_hours_days_off_for_the_person_it_named(name: str):
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        tool = EquipmentFactory(guild=guild)
        arg = guild.pk if "guild" in name else tool.slug
        client = signed_in(lead)
        assert client.get(reverse(name, args=[arg]), {"orienter": lead.pk})["Location"] == f"{MY_HOURS}#days-off"
        assert client.post(reverse(name, args=[arg]), {"orienter": "5"})["Location"] == f"{MY_HOURS}?member=5#days-off"

    def it_deletes_nothing_from_an_old_delete():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        tool = EquipmentFactory(guild=guild)
        day_off = OrientationDayOff.objects.create(orienter=lead, date="2030-01-02")
        client = signed_in(lead)
        for url in (
            reverse("hub_guild_orientation_day_off_delete", args=[guild.pk, day_off.pk]),
            reverse("hub_equipment_orientation_day_off_delete", args=[tool.slug, day_off.pk]),
        ):
            assert client.post(url)["Location"] == f"{MY_HOURS}#days-off"
        assert OrientationDayOff.objects.filter(pk=day_off.pk).exists()


def describe_my_hours_url():
    def it_is_the_bare_page_for_the_viewer_nobody_or_garbage(rf):
        person = MemberFactory()
        request = rf.get("/")
        request.user = type("Signed", (), {"member": person})()
        assert my_hours_url(request, str(person.pk)) == MY_HOURS
        assert my_hours_url(request, "") == MY_HOURS
        assert my_hours_url(request, "x1") == MY_HOURS
        assert my_hours_url(request, "42") == f"{MY_HOURS}?member=42"
