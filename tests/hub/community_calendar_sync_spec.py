"""BDD specs for the Google-push wiring on the calendar read/write paths:

- echo de-dup (a FOG-pushed event's iCal re-import is hidden from the calendar + export),
- published-only visibility of the CalendarEvent echo,
- the create/edit/delete views push (best-effort — a Google failure never rolls back FOG),
- delete removes the Google event BEFORE deleting the FOG row.

All Google calls are mocked.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.integrations.google_calendar import GoogleCalendarClient, GoogleCalendarError
from core.models import SiteConfiguration
from hub.views import _get_calendar_context
from membership.models import CalendarEvent, CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

CALENDAR_ID = "cal@group.calendar.google.com"


def _calendar_event(uid: str, title: str, *, days: int = 3) -> CalendarEvent:
    start = timezone.now() + timedelta(days=days)
    return CalendarEvent.objects.create(
        guild=None, uid=uid, title=title, start_dt=start, end_dt=start + timedelta(hours=2), fetched_at=timezone.now()
    )


def _enable_config() -> None:
    config = SiteConfiguration.load()
    config.google_calendar_sync_enabled = True
    config.member_google_calendar_id = CALENDAR_ID  # default event target is MEMBER
    config.save(update_fields=["google_calendar_sync_enabled", "member_google_calendar_id"])


def _lead(client: Client, username: str, **guild_kwargs) -> tuple[User, object]:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    guild = GuildFactory(guild_lead=user.member, **guild_kwargs)
    client.login(username=username, password="pass")
    return user, guild


def _admin(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _payload(**overrides: str) -> dict:
    data = {
        "title": "Forge Night",
        "starts_at": "2026-07-11T18:00",
        "ends_at": "2026-07-11T20:00",
        "location": "Main Studio",
        "description": "Come forge.",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


def _failing_client() -> MagicMock:
    client = MagicMock(spec=GoogleCalendarClient)
    client.enabled = True
    client.insert_event = MagicMock(side_effect=GoogleCalendarError("boom"))
    client.update_event = MagicMock(side_effect=GoogleCalendarError("boom"))
    client.delete_event = MagicMock(side_effect=GoogleCalendarError("boom"))
    return client


def describe_echo_dedup_in_the_calendar_context():
    def it_excludes_the_ical_echo_of_a_pushed_event():
        CommunityEventFactory(community=True, pushed=True, google_ical_uid="echo-uid@google.com")
        _calendar_event("echo-uid@google.com", "ECHO")
        _calendar_event("other-uid@google.com", "REAL")
        ctx = _get_calendar_context(RequestFactory().get("/"))
        titles = [e.title for e in ctx["month_events"]]
        assert "ECHO" not in titles
        assert "REAL" in titles

    def it_does_not_exclude_an_unpushed_published_event():
        # A published event with a blank google_ical_uid must not poison the exclusion set.
        CommunityEventFactory(community=True)  # blank google_ical_uid, PUBLISHED
        _calendar_event("live-uid@google.com", "LIVE")
        ctx = _get_calendar_context(RequestFactory().get("/"))
        titles = [e.title for e in ctx["month_events"]]
        assert "LIVE" in titles


def describe_echo_dedup_in_the_ics_export():
    def it_excludes_the_ical_echo_of_a_pushed_event(client: Client):
        MembershipPlanFactory()
        User.objects.create_user(username="ics", password="pass")
        client.login(username="ics", password="pass")
        CommunityEventFactory(community=True, pushed=True, google_ical_uid="echo-uid@google.com")
        _calendar_event("echo-uid@google.com", "ECHO")
        _calendar_event("other-uid@google.com", "REAL")
        body = client.get(reverse("hub_calendar_export_ics")).content.decode()
        assert "SUMMARY:ECHO" not in body
        assert "SUMMARY:REAL" in body


def describe_create_pushes():
    def it_keeps_the_fog_event_when_the_push_fails_on_create(client: Client):
        user, guild = _lead(client, "pf1")
        _enable_config()
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=_failing_client()),
            patch.object(CommunityEvent, "announce"),
        ):
            resp = client.post(reverse("hub_guild_event_add", args=[guild.pk]), data=_payload())
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(guild=guild)
        assert event.sync_state == CommunityEvent.SyncState.FAILED

    def it_pushes_a_new_admin_event(client: Client):
        _admin(client, "ad1")
        _enable_config()  # sets the Member calendar id; a new admin event defaults to the MEMBER target
        inserted = MagicMock(return_value={"id": "adm1", "iCalUID": "adm1@google.com"})
        fake = MagicMock(spec=GoogleCalendarClient)
        fake.enabled = True
        fake.insert_event = inserted
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=fake),
            patch.object(CommunityEvent, "announce"),
        ):
            client.post(reverse("hub_event_add"), data=_payload(title="Site Party", event_type="community", guild=""))
        inserted.assert_called_once()
        assert CommunityEvent.objects.get(title="Site Party").sync_state == CommunityEvent.SyncState.SYNCED


def describe_edit_repushes():
    def it_repushes_a_published_event_on_edit(client: Client):
        user, guild = _lead(client, "ed1")
        event = CommunityEventFactory(guild=guild, google_event_id="ge1", sync_state=CommunityEvent.SyncState.SYNCED)
        _enable_config()
        update = MagicMock(return_value={"id": "ge1", "iCalUID": "ge1@google.com"})
        fake = MagicMock(spec=GoogleCalendarClient)
        fake.enabled = True
        fake.update_event = update
        fake.insert_event = MagicMock()
        with patch.object(GoogleCalendarClient, "from_settings", return_value=fake):
            resp = client.post(
                reverse("hub_guild_event_edit", args=[guild.pk, event.pk]), data=_payload(title="Renamed")
            )
        assert resp.status_code == 302
        update.assert_called_once()
        fake.insert_event.assert_not_called()

    def it_keeps_the_fog_edit_when_the_push_fails(client: Client):
        user, guild = _lead(client, "ed2")
        event = CommunityEventFactory(guild=guild, google_event_id="ge2", sync_state=CommunityEvent.SyncState.SYNCED)
        _enable_config()
        with patch.object(GoogleCalendarClient, "from_settings", return_value=_failing_client()):
            client.post(reverse("hub_guild_event_edit", args=[guild.pk, event.pk]), data=_payload(title="Renamed2"))
        event.refresh_from_db()
        assert event.title == "Renamed2"  # FOG edit persisted
        assert event.sync_state == CommunityEvent.SyncState.FAILED


def describe_delete_removes_from_google_first():
    def it_calls_remove_from_google_before_delete(client: Client):
        user, guild = _lead(client, "dl1")
        event = CommunityEventFactory(guild=guild, pushed=True)
        _enable_config()
        seen_pk: list[int | None] = []

        def _record(self) -> None:
            seen_pk.append(self.pk)  # non-None here proves the row still existed

        with patch.object(CommunityEvent, "remove_from_google", _record):
            resp = client.post(reverse("hub_guild_event_delete", args=[guild.pk, event.pk]))
        assert resp.status_code == 302
        assert seen_pk == [event.pk]  # remove ran while the row still existed
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()  # ...then it was deleted

    def it_deletes_even_when_the_google_delete_fails(client: Client):
        _admin(client, "dl2")
        event = CommunityEventFactory(community=True, pushed=True)
        _enable_config()
        with patch.object(GoogleCalendarClient, "from_settings", return_value=_failing_client()):
            resp = client.post(reverse("hub_event_delete", args=[event.pk]))
        assert resp.status_code == 302
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()
