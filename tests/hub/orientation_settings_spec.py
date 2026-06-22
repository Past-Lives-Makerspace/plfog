"""BDD specs for the guild orientation config editor (settings, rules, one-off slots)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import GuildOrientationSettings, OrientationAvailability, OrientationSlot
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory, OrientationSlotFactory

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _settings_payload(**overrides: str) -> dict[str, str]:
    data = {
        "default_seats": "4",
        "default_duration_minutes": "60",
        "rules-TOTAL_FORMS": "0",
        "rules-INITIAL_FORMS": "0",
        "rules-MIN_NUM_FORMS": "0",
        "rules-MAX_NUM_FORMS": "1000",
    }
    data.update(overrides)
    return data


def _future(hours: int) -> str:
    return (timezone.localtime() + timedelta(days=2, hours=hours)).strftime("%Y-%m-%dT%H:%M")


def describe_guild_orientation_edit():
    def it_renders_the_editor_for_an_editor(client: Client):
        _user_with_role("ed_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_admin", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.status_code == 200
        assert b"Recurring hours" in response.content

    def it_creates_settings_on_first_view(client: Client):
        _user_with_role("ed_create", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_create", password="pass")
        assert GuildOrientationSettings.objects.filter(guild=guild).count() == 0
        client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert GuildOrientationSettings.objects.filter(guild=guild).count() == 1

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("ed_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="ed_reg", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.status_code == 403

    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_lets_the_guild_lead_save_settings(client: Client):
        user = _user_with_role("ed_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="ed_lead", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(is_enabled="on", default_location="Front desk"),
        )
        assert response.status_code == 302
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.is_enabled is True
        assert settings_obj.default_location == "Front desk"

    def it_saves_a_recurring_rule(client: Client):
        _user_with_role("ed_rule", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_rule", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(
                is_enabled="on",
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-0-weekday": "1",
                    "rules-0-start_time": "18:00",
                    "rules-0-end_time": "19:00",
                    "rules-0-seats": "5",
                    "rules-0-location": "Studio B",
                    "rules-0-is_active": "on",
                },
            ),
        )
        assert response.status_code == 302
        rule = OrientationAvailability.objects.get(guild=guild)
        assert rule.weekday == 1
        assert rule.seats == 5

    def it_generates_bookable_slots_when_a_rule_is_saved(client: Client):
        # Saving recurring hours materializes slots immediately — no waiting for the cron.
        _user_with_role("ed_gen", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_gen", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(
                is_enabled="on",
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-0-weekday": "1",
                    "rules-0-start_time": "18:00",
                    "rules-0-end_time": "19:00",
                    "rules-0-seats": "5",
                    "rules-0-is_active": "on",
                },
            ),
        )
        assert response.status_code == 302
        assert OrientationSlot.objects.filter(guild=guild, source=OrientationSlot.Source.GENERATED).exists()

    def it_rejects_a_thankyou_email_with_no_subject(client: Client):
        _user_with_role("ed_email", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_email", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(thankyou_email_enabled="on", thankyou_email_body="Thanks!"),
        )
        assert response.status_code == 200
        assert "thankyou_email_subject" in response.context["form"].errors
        assert GuildOrientationSettings.objects.get(guild=guild).thankyou_email_enabled is False

    def it_rejects_a_welcome_email_with_no_body(client: Client):
        _user_with_role("ed_join", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_join", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(join_email_enabled="on", join_email_subject="Welcome!"),
        )
        assert response.status_code == 200
        assert "join_email_body" in response.context["form"].errors

    def it_rejects_a_rule_whose_end_is_before_its_start(client: Client):
        _user_with_role("ed_badrule", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_badrule", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(
                is_enabled="on",
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-0-weekday": "1",
                    "rules-0-start_time": "19:00",
                    "rules-0-end_time": "18:00",
                    "rules-0-seats": "4",
                    "rules-0-location": "",
                    "rules-0-is_active": "on",
                },
            ),
        )
        assert response.status_code == 200
        assert OrientationAvailability.objects.filter(guild=guild).count() == 0


def describe_guild_orientation_slot_add():
    def it_adds_a_one_off_slot(client: Client):
        _user_with_role("slot_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="slot_admin", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            {"starts_at": _future(0), "ends_at": _future(1), "seats": "3", "location": "Lobby"},
        )
        assert response.status_code == 302
        slot = OrientationSlot.objects.get(guild=guild)
        assert slot.source == OrientationSlot.Source.MANUAL
        assert slot.seats == 3

    def it_rejects_a_slot_in_the_past(client: Client):
        _user_with_role("slot_past", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="slot_past", password="pass")
        past = (timezone.localtime() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            {"starts_at": past, "ends_at": _future(1), "seats": "3", "location": ""},
        )
        assert response.status_code == 302
        assert OrientationSlot.objects.filter(guild=guild).count() == 0

    def it_rejects_when_the_end_is_before_the_start(client: Client):
        _user_with_role("slot_order", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="slot_order", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            {"starts_at": _future(2), "ends_at": _future(1), "seats": "3", "location": ""},
        )
        assert response.status_code == 302
        assert OrientationSlot.objects.filter(guild=guild).count() == 0

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("slot_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="slot_reg", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            {"starts_at": _future(0), "ends_at": _future(1), "seats": "3", "location": ""},
        )
        assert response.status_code == 403
        assert OrientationSlot.objects.filter(guild=guild).count() == 0

    def it_rejects_get_requests(client: Client):
        _user_with_role("slot_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="slot_get", password="pass")
        response = client.get(reverse("hub_guild_orientation_slot_add", args=[guild.pk]))
        assert response.status_code == 405


def describe_guild_orientation_slot_cancel():
    def it_cancels_a_slot(client: Client):
        _user_with_role("can_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        slot = OrientationSlotFactory(guild=guild)
        client.login(username="can_admin", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_cancel", args=[guild.pk, slot.pk]), {"reason": "weather"}
        )
        assert response.status_code == 302
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.cancelled_reason == "weather"

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("can_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        slot = OrientationSlotFactory(guild=guild)
        client.login(username="can_reg", password="pass")
        response = client.post(reverse("hub_guild_orientation_slot_cancel", args=[guild.pk, slot.pk]))
        assert response.status_code == 403
        slot.refresh_from_db()
        assert slot.is_cancelled is False

    def it_rejects_get_requests(client: Client):
        _user_with_role("can_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        slot = OrientationSlotFactory(guild=guild)
        client.login(username="can_get", password="pass")
        response = client.get(reverse("hub_guild_orientation_slot_cancel", args=[guild.pk, slot.pk]))
        assert response.status_code == 405
