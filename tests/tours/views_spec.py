"""The Tours page (#947): its tabs, its admin Settings tab, and where it lives (the sidebar, #1278).

A guide's hours and days off are set on My Hours since #1300 (``tests/hours/``); the old tab redirects there."""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.tours.conftest import signed_in
from tours.models import FacilityTourSettings

pytestmark = pytest.mark.django_db

UPCOMING = reverse("tours:upcoming")
SETTINGS = reverse("tours:settings")
GIVE = reverse("tours:give")


def describe_access():
    def it_sends_a_signed_out_visitor_to_sign_in():
        response = Client().get(UPCOMING)
        assert response.status_code == 302
        assert "/login" in response["Location"] or "/accounts/" in response["Location"]

    def it_sends_a_member_without_the_permission_to_give_tours(plain: Member):
        client = signed_in(plain)
        assert client.get(UPCOMING).url == GIVE
        assert client.get(SETTINGS).url == GIVE

    def it_opens_the_page_to_a_tour_guide_and_an_admin(guide: Member, admin: Member):
        assert signed_in(guide).get(UPCOMING).status_code == 200
        assert signed_in(admin).get(UPCOMING).status_code == 200

    def it_keeps_tour_settings_to_admins(guide: Member, admin: Member):
        assert signed_in(guide).get(SETTINGS).status_code == 403
        assert signed_in(admin).get(SETTINGS).status_code == 200

    def it_refuses_an_account_with_no_member_record():
        user = User.objects.create_superuser("rootless", "rootless@example.com", "pass")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)
        assert client.get(UPCOMING).status_code == 404


def describe_where_it_lives():
    """#1278: Tours is a sidebar entry for every active member, after Reservations; the Tools page no longer opens it."""

    def it_gives_guides_admins_and_members_the_sidebar_entry(guide: Member, admin: Member, plain: Member):
        upcoming = reverse("tours:upcoming")
        for member in (guide, admin, plain):
            content = signed_in(member).get(reverse("hub_member_directory")).content.decode()
            assert f'<a href="{upcoming}" class="hub-sidebar__link " data-nav="tours"' in content

    def it_lights_the_tours_entry_not_the_tools_entry_with_no_way_back(guide: Member, admin: Member):
        for member in (guide, admin):
            content = signed_in(member).get(UPCOMING).content.decode()
            assert f'href="{reverse("tours:upcoming")}" class="hub-sidebar__link active" data-nav="tours"' in content
            assert not re.search(rf'href="{reverse("hub_admin_tools")}" class="hub-sidebar__link\s+active"', content)
            assert "data-tours-back" not in content

    def it_does_not_light_the_tours_entry_elsewhere(guide: Member):
        content = signed_in(guide).get(reverse("hub_member_directory")).content.decode()
        assert 'class="hub-sidebar__link active" data-nav="tours"' not in content

    def it_gives_admins_alone_the_settings_tab(guide: Member, admin: Member):
        assert 'data-tours-tab="settings"' not in signed_in(guide).get(UPCOMING).content.decode()
        content = signed_in(admin).get(UPCOMING).content.decode()
        assert f'<a href="{SETTINGS}" class="vote-tab" data-tours-tab="settings">Settings</a>' in content
        settings_page = signed_in(admin).get(SETTINGS).content.decode()
        assert 'aria-current="page" data-tours-tab="settings">Settings</a>' in settings_page
        # The My Hours tab opens My Hours, every kind's hours in one place (#1300).
        assert (
            f'<a href="{reverse("hours:my_hours")}" class="vote-tab" data-tours-tab="hours" data-guidance-target="tours.my-hours">My Hours</a>'
            in settings_page
        )

    def it_opens_the_page_once_the_permission_is_granted(plain: Member):
        client = signed_in(plain)
        assert client.get(UPCOMING).url == GIVE
        plain.set_admin_capability("tour_guide", True)
        assert client.get(UPCOMING).status_code == 200


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

    def it_has_no_card_on_admin_tools(admin: Member):
        content = signed_in(admin).get(reverse("hub_admin_tools")).content.decode()
        assert f'href="{SETTINGS}"' not in content
        assert "data-tool-tours" not in content


def describe_an_admin_previewing_as_a_member():
    def it_still_opens_the_page(admin: Member):
        client = signed_in(admin)
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        content = client.get(UPCOMING).content.decode()
        assert "Facility tours for people curious about Past Lives." in content
