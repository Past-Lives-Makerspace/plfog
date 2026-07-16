"""BDD specs for the one-way publish-out of Studio Hours to Google (Part C). All Google calls
are mocked — never a live call. Proves the row targets the PUBLIC calendar, carries a
FREQ=WEEKLY RRULE, and that an off/failed push never blocks the FOG save."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.integrations.google_calendar import GoogleCalendarClient, GoogleCalendarError
from core.models import SiteConfiguration
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

PUBLIC_CALENDAR_ID = "public@group.calendar.google.com"
MEMBER_CALENDAR_ID = "member@group.calendar.google.com"


def _lead(client: Client, username: str):
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    guild = GuildFactory(guild_lead=user.member)
    client.login(username=username, password="pass")
    return user, guild


def _enable_config() -> None:
    config = SiteConfiguration.load()
    config.google_calendar_sync_enabled = True
    config.public_google_calendar_id = PUBLIC_CALENDAR_ID
    config.member_google_calendar_id = MEMBER_CALENDAR_ID
    config.save(
        update_fields=[
            "google_calendar_sync_enabled",
            "public_google_calendar_id",
            "member_google_calendar_id",
        ]
    )


def _failing_client() -> MagicMock:
    fake = MagicMock(spec=GoogleCalendarClient)
    fake.enabled = True
    fake.insert_event = MagicMock(side_effect=GoogleCalendarError("boom"))
    fake.update_event = MagicMock(side_effect=GoogleCalendarError("boom"))
    fake.delete_event = MagicMock(side_effect=GoogleCalendarError("boom"))
    return fake


def _sh_row(**overrides: str) -> dict[str, str]:
    row = {"weekday": "1", "start_time": "18:00", "end_time": "21:00", "location": "Studio B", "note": "", "id": ""}
    row.update(overrides)
    return row


def _sh_payload(rows: list[dict[str, str]], *, initial: int = 0) -> dict[str, str]:
    data = {
        "studio_hours-TOTAL_FORMS": str(len(rows)),
        "studio_hours-INITIAL_FORMS": str(initial),
        "studio_hours-MIN_NUM_FORMS": "0",
        "studio_hours-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        for key, value in row.items():
            data[f"studio_hours-{i}-{key}"] = value
    return data


def describe_studio_hours_publish_out():
    def it_pushes_to_the_public_calendar_with_a_weekly_rrule(client: Client):
        _user, guild = _lead(client, "sync_lead")
        _enable_config()
        inserted = MagicMock(return_value={"id": "sh1", "iCalUID": "sh1@google.com"})
        fake = MagicMock(spec=GoogleCalendarClient)
        fake.enabled = True
        fake.insert_event = inserted
        with patch.object(GoogleCalendarClient, "from_settings", return_value=fake):
            resp = client.post(reverse("hub_guild_studio_hours_save", args=[guild.pk]), data=_sh_payload([_sh_row()]))
        assert resp.status_code == 302
        inserted.assert_called_once()
        target, body = inserted.call_args.args
        assert target == PUBLIC_CALENDAR_ID  # forced PUBLIC, not the member calendar
        assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=TU"]
        assert guild.events.studio_hours().get().sync_state == CommunityEvent.SyncState.SYNCED

    def it_records_pending_when_sync_is_off_but_still_saves(client: Client):
        _user, guild = _lead(client, "off_lead")
        # No _enable_config() — Google sync is off; the client is disabled in the test env.
        resp = client.post(reverse("hub_guild_studio_hours_save", args=[guild.pk]), data=_sh_payload([_sh_row()]))
        assert resp.status_code == 302
        row = guild.events.studio_hours().get()
        assert row.sync_state == CommunityEvent.SyncState.PENDING

    def it_records_failed_on_an_api_error_but_still_saves(client: Client):
        _user, guild = _lead(client, "fail_lead")
        _enable_config()
        with patch.object(GoogleCalendarClient, "from_settings", return_value=_failing_client()):
            resp = client.post(reverse("hub_guild_studio_hours_save", args=[guild.pk]), data=_sh_payload([_sh_row()]))
        assert resp.status_code == 302
        row = guild.events.studio_hours().get()
        assert row.sync_state == CommunityEvent.SyncState.FAILED  # push failed
        assert row.pk is not None  # ...but the FOG row persisted

    def it_removes_a_deleted_row_from_google_before_deleting_it(client: Client):
        _user, guild = _lead(client, "del_lead")
        _enable_config()
        event = CommunityEventFactory(studio_hours=True, guild=guild, pushed=True)
        deleted = MagicMock()
        fake = MagicMock(spec=GoogleCalendarClient)
        fake.enabled = True
        fake.delete_event = deleted
        with patch.object(GoogleCalendarClient, "from_settings", return_value=fake):
            resp = client.post(
                reverse("hub_guild_studio_hours_save", args=[guild.pk]),
                data=_sh_payload([_sh_row(id=str(event.pk), DELETE="on")], initial=1),
            )
        assert resp.status_code == 302
        deleted.assert_called_once()
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()
