"""BDD specs for the Screen D config forms — the three Site-Settings fields
(member-event policy + general Google Calendar ID + the sync toggle), all on the
one Calendar tab, and the admin gate on the settings page.

The one-tab layout is a correctness guarantee: because every field lives in a single
``<form>``, a Calendar-tab save carries ``member_event_policy`` too and can't blank it.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _settings_payload(**overrides: str) -> dict:
    """A complete, valid Site-Settings POST (all tabs share one form in the DOM)."""
    data = {
        "registration_mode": SiteConfiguration.RegistrationMode.INVITE_ONLY,
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "submitted_tab": "calendar",
        "feeds-TOTAL_FORMS": "0",
        "feeds-INITIAL_FORMS": "0",
        "feeds-MIN_NUM_FORMS": "0",
        "feeds-MAX_NUM_FORMS": "1000",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def describe_site_settings_calendar_fields():
    def it_renders_the_three_fields_on_the_calendar_tab(client):
        _user("adm", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm", password="pass")
        resp = client.get(reverse("hub_admin_site_settings") + "?tab=calendar")
        assert resp.status_code == 200
        body = resp.content
        assert b"Member events &amp; Google sync" in body
        assert b'name="member_event_policy"' in body
        assert b'name="general_google_calendar_id"' in body
        assert b'name="google_calendar_sync_enabled"' in body

    def it_saves_all_three_fields_from_a_calendar_tab_save(client):
        _user("adm2", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm2", password="pass")
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_payload(
                member_event_policy=SiteConfiguration.MemberEventPolicy.OPEN,
                general_google_calendar_id="general@group.calendar.google.com",
                google_calendar_sync_enabled="on",
            ),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.member_event_policy == SiteConfiguration.MemberEventPolicy.OPEN
        assert config.general_google_calendar_id == "general@group.calendar.google.com"
        assert config.google_calendar_sync_enabled is True

    def it_does_not_blank_member_event_policy_on_a_calendar_tab_save(client):
        """One-tab guarantee: a Calendar-tab save carries member_event_policy and
        can't reset it to the default."""
        _user("adm3", fog_role=Member.FogRole.ADMIN)
        config = SiteConfiguration.load()
        config.member_event_policy = SiteConfiguration.MemberEventPolicy.OPEN
        config.save(update_fields=["member_event_policy"])
        client.login(username="adm3", password="pass")

        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_payload(
                member_event_policy=SiteConfiguration.MemberEventPolicy.OPEN,
                general_google_calendar_id="c@group.calendar.google.com",
            ),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.member_event_policy == SiteConfiguration.MemberEventPolicy.OPEN

    def it_forbids_a_non_admin(client):
        _user("pm")
        client.login(username="pm", password="pass")
        assert client.get(reverse("hub_admin_site_settings")).status_code == 403
