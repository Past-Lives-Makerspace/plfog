"""BDD specs for the guild orientation config editor (settings, rules, one-off slots)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import GuildOrientationSettings, Member, OrientationAvailability, OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationSlotFactory,
)

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


def _hours_payload(**overrides: str) -> dict[str, str]:
    data = {
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
    def it_renders_the_editor_inside_the_orientations_tab(client: Client):
        # The editor is now an in-page tab on the guild edit page, not a standalone page.
        _user_with_role("ed_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_admin", password="pass")
        response = client.get(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")
        assert response.status_code == 200
        assert b"Recurring hours" in response.content
        assert b"Save orientation settings" in response.content
        # Recurring hours now save through their own form with their own button.
        assert b"Save Hours" in response.content
        assert reverse("hub_guild_orientation_hours_save", args=[guild.pk]).encode() in response.content
        assert b"Who runs orientations" in response.content

    def it_redirects_a_get_to_the_orientations_tab(client: Client):
        _user_with_role("ed_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_get", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"

    def it_creates_settings_when_the_tab_is_opened(client: Client):
        _user_with_role("ed_create", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_create", password="pass")
        assert GuildOrientationSettings.objects.filter(guild=guild).count() == 0
        client.get(reverse("hub_guild_edit", args=[guild.pk]))
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
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.is_enabled is True
        assert settings_obj.default_location == "Front desk"

    def it_saves_the_custom_request_toggle(client: Client):
        _user_with_role("ed_custom", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ed_custom", password="pass")
        # Unchecked in the POST → False (a posted checkbox is only present when on).
        client.post(reverse("hub_guild_orientation_edit", args=[guild.pk]), _settings_payload(is_enabled="on"))
        assert GuildOrientationSettings.objects.get(guild=guild).allow_custom_requests is False
        client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(is_enabled="on", allow_custom_requests="on"),
        )
        assert GuildOrientationSettings.objects.get(guild=guild).allow_custom_requests is True

    def it_keeps_existing_hours_when_only_settings_are_saved(client: Client):
        # The settings form no longer carries the hours formset — saving settings must
        # leave a guild's recurring hours untouched.
        _user_with_role("ed_keep", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        rule = OrientationAvailabilityFactory(guild=guild)
        client.login(username="ed_keep", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(is_enabled="on", default_location="Front desk"),
        )
        assert response.status_code == 302
        assert OrientationAvailability.objects.filter(pk=rule.pk).exists()

    def it_no_longer_touches_the_email_fields(client: Client):
        # The follow-up emails moved to the Announcements/Emails tab; the orientation
        # settings save must leave the email fields untouched even if they're posted.
        _user_with_role("ed_no_email", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        settings_obj = GuildOrientationSettingsFactory(
            guild=guild, thankyou_email_subject="Keep me", thankyou_email_enabled=True
        )
        client.login(username="ed_no_email", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(is_enabled="on", thankyou_email_subject="Overwritten?", thankyou_email_enabled=""),
        )
        assert response.status_code == 302
        settings_obj.refresh_from_db()
        assert settings_obj.thankyou_email_subject == "Keep me"
        assert settings_obj.thankyou_email_enabled is True


def describe_guild_orientation_hours_save():
    """Recurring hours save through their own form/view, separate from the settings form."""

    def it_saves_a_recurring_rule_and_redirects_to_the_tab(client: Client):
        _user_with_role("hrs_add", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="hrs_add", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            _hours_payload(
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-0-weekday": "1",
                    "rules-0-start_time": "18:00",
                    "rules-0-end_time": "19:00",
                    "rules-0-seats": "5",
                    "rules-0-is_active": "on",
                }
            ),
            follow=True,
        )
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"
        rule = OrientationAvailability.objects.get(guild=guild)
        assert rule.weekday == 1
        assert rule.seats == 5
        assert "Recurring hours saved." in [str(m) for m in response.context["messages"]]

    def it_edits_an_existing_rule(client: Client):
        _user_with_role("hrs_edit", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        rule = OrientationAvailabilityFactory(guild=guild, weekday=0, seats=2)
        client.login(username="hrs_edit", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            _hours_payload(
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-INITIAL_FORMS": "1",
                    "rules-0-id": str(rule.pk),
                    "rules-0-weekday": "3",
                    "rules-0-start_time": "10:00",
                    "rules-0-end_time": "12:00",
                    "rules-0-seats": "6",
                    "rules-0-is_active": "on",
                }
            ),
        )
        assert response.status_code == 302
        rule.refresh_from_db()
        assert rule.weekday == 3
        assert rule.seats == 6

    def it_deletes_a_flagged_rule_via_the_delete_field(client: Client):
        _user_with_role("hrs_del", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        rule = OrientationAvailabilityFactory(guild=guild)
        client.login(username="hrs_del", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            _hours_payload(
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-INITIAL_FORMS": "1",
                    "rules-0-id": str(rule.pk),
                    "rules-0-weekday": str(rule.weekday),
                    "rules-0-start_time": "09:00",
                    "rules-0-end_time": "10:00",
                    "rules-0-seats": str(rule.seats),
                    "rules-0-is_active": "on",
                    "rules-0-DELETE": "on",
                }
            ),
        )
        assert response.status_code == 302
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()

    def it_generates_bookable_slots_when_a_rule_is_saved(client: Client):
        # Saving recurring hours materializes slots immediately — no waiting for the cron.
        # (Slots only generate for a guild that is accepting orientations.)
        _user_with_role("hrs_gen", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        client.login(username="hrs_gen", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            _hours_payload(
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-0-weekday": "1",
                    "rules-0-start_time": "18:00",
                    "rules-0-end_time": "19:00",
                    "rules-0-seats": "5",
                    "rules-0-is_active": "on",
                }
            ),
        )
        assert response.status_code == 302
        assert OrientationSlot.objects.filter(guild=guild, source=OrientationSlot.Source.GENERATED).exists()

    def it_keeps_existing_settings_when_only_hours_are_saved(client: Client):
        # Saving hours through its own form must not disturb the guild's orientation settings.
        _user_with_role("hrs_keep", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        settings_obj = GuildOrientationSettingsFactory(guild=guild, is_enabled=True, default_location="Front desk")
        client.login(username="hrs_keep", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            _hours_payload(
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-0-weekday": "2",
                    "rules-0-start_time": "10:00",
                    "rules-0-end_time": "11:00",
                    "rules-0-seats": "3",
                    "rules-0-is_active": "on",
                }
            ),
        )
        assert response.status_code == 302
        settings_obj.refresh_from_db()
        assert settings_obj.is_enabled is True
        assert settings_obj.default_location == "Front desk"

    def it_rejects_a_rule_whose_end_is_before_its_start(client: Client):
        # The bad time range re-renders the page with the field error and saves nothing.
        _user_with_role("hrs_bad", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="hrs_bad", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            _hours_payload(
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-0-weekday": "1",
                    "rules-0-start_time": "19:00",
                    "rules-0-end_time": "18:00",
                    "rules-0-seats": "4",
                    "rules-0-is_active": "on",
                }
            ),
        )
        assert response.status_code == 200
        assert OrientationAvailability.objects.filter(guild=guild).count() == 0
        assert response.context["rule_formset"].errors

    def it_lets_the_guild_lead_save_hours(client: Client):
        user = _user_with_role("hrs_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="hrs_lead", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            _hours_payload(
                **{
                    "rules-TOTAL_FORMS": "1",
                    "rules-0-weekday": "1",
                    "rules-0-start_time": "18:00",
                    "rules-0-end_time": "19:00",
                    "rules-0-seats": "5",
                    "rules-0-is_active": "on",
                }
            ),
        )
        assert response.status_code == 302
        assert OrientationAvailability.objects.filter(guild=guild).count() == 1

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("hrs_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="hrs_reg", password="pass")
        response = client.post(reverse("hub_guild_orientation_hours_save", args=[guild.pk]), _hours_payload())
        assert response.status_code == 403
        assert OrientationAvailability.objects.filter(guild=guild).count() == 0

    def it_rejects_get_requests(client: Client):
        _user_with_role("hrs_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="hrs_get", password="pass")
        response = client.get(reverse("hub_guild_orientation_hours_save", args=[guild.pk]))
        assert response.status_code == 405


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
