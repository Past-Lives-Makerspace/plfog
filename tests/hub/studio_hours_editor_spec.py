"""BDD specs for the Studio Hours editor: the translating StudioHoursForm (weekday+time ⇄ a
WEEKLY STUDIO_HOURS CommunityEvent), and the guild_studio_hours_save view (editor gating,
save + redirect, delete-flips-DELETE, remove-from-Google-before-delete).

All Google calls degrade to a disabled client in the test env; the delete-propagation test
records remove_from_google instead. Datetime math is Portland local time."""

from __future__ import annotations

from datetime import time

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import StudioHoursForm
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _aware(y: int, m: int, d: int, hour: int, minute: int = 0):
    return timezone.make_aware(timezone.datetime(y, m, d, hour, minute))


def _lead(client: Client, username: str, **guild_kwargs):
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    guild = GuildFactory(guild_lead=user.member, **guild_kwargs)
    client.login(username=username, password="pass")
    return user, guild


def _plain_member(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user


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


def describe_StudioHoursForm():
    def it_translates_weekday_and_times_into_a_weekly_studio_hours_row(db):
        guild = GuildFactory(name="Ceramics")
        form = StudioHoursForm(
            data={
                "weekday": "1",
                "start_time": "18:00",
                "end_time": "21:00",
                "location": "Studio B",
                "note": "Drop in",
            },
            guild=guild,
        )
        assert form.is_valid(), form.errors
        event = form.save()
        assert event.event_type == CommunityEvent.EventType.STUDIO_HOURS
        assert event.recurrence == CommunityEvent.Recurrence.WEEKLY
        assert event.google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.guild == guild
        assert event.title == "Ceramics Studio Hours"
        assert event.location == "Studio B"
        assert event.description == "Drop in"
        local_start = timezone.localtime(event.starts_at)
        local_end = timezone.localtime(event.ends_at)
        assert local_start.weekday() == 1  # Tuesday
        assert local_start.time() == time(18, 0)
        assert local_end.time() == time(21, 0)

    def it_anchors_to_an_upcoming_occurrence_of_the_weekday(db):
        guild = GuildFactory()
        form = StudioHoursForm(
            data={"weekday": "3", "start_time": "10:00", "end_time": "12:00"}, guild=guild
        )  # Thursday
        assert form.is_valid(), form.errors
        event = form.save()
        local_start = timezone.localtime(event.starts_at)
        assert local_start.weekday() == 3
        assert event.starts_at >= timezone.now()  # never in the past

    def it_rejects_an_end_time_at_or_before_the_start(db):
        guild = GuildFactory()
        form = StudioHoursForm(data={"weekday": "1", "start_time": "18:00", "end_time": "18:00"}, guild=guild)
        assert not form.is_valid()
        assert "End time must be after start time." in form.errors["end_time"]

    def it_requires_weekday_and_both_times(db):
        guild = GuildFactory()
        form = StudioHoursForm(data={}, guild=guild)
        assert not form.is_valid()
        for field in ("weekday", "start_time", "end_time"):
            assert field in form.errors

    def it_round_trips_an_existing_row_back_to_initial_on_edit(db):
        guild = GuildFactory()
        event = CommunityEventFactory(
            studio_hours=True,
            guild=guild,
            starts_at=_aware(2026, 7, 7, 18),  # Tuesday
            ends_at=_aware(2026, 7, 7, 21),
            location="Studio B",
            description="Bring clay.",
        )
        form = StudioHoursForm(instance=event, guild=guild)
        assert form.fields["weekday"].initial == "1"
        assert form.fields["start_time"].initial == time(18, 0)
        assert form.fields["end_time"].initial == time(21, 0)
        assert form.fields["location"].initial == "Studio B"
        assert form.fields["note"].initial == "Bring clay."


def describe_meetings_tab_render():
    def it_renders_the_studio_hours_editor_for_the_lead(client: Client):
        _user, guild = _lead(client, "lead_render")
        CommunityEventFactory(
            studio_hours=True, guild=guild, starts_at=_aware(2026, 7, 7, 18), ends_at=_aware(2026, 7, 7, 21)
        )
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "+ Add studio hours" in content
        assert "studio-hours-empty-template" in content
        assert f'action="{reverse("hub_guild_studio_hours_save", args=[guild.pk])}"' in content


def describe_guild_studio_hours_save_view():
    def it_forbids_a_non_editor(client: Client):
        _plain_member(client, "outsider")
        guild = GuildFactory()
        resp = client.post(
            reverse("hub_guild_studio_hours_save", args=[guild.pk]),
            data=_sh_payload([{"weekday": "1", "start_time": "18:00", "end_time": "21:00", "id": ""}]),
        )
        assert resp.status_code == 403
        assert not guild.events.studio_hours().exists()

    def it_lets_the_lead_save_a_new_block_and_redirects_to_the_meetings_tab(client: Client):
        _user, guild = _lead(client, "lead_sh")
        resp = client.post(
            reverse("hub_guild_studio_hours_save", args=[guild.pk]),
            data=_sh_payload(
                [
                    {
                        "weekday": "1",
                        "start_time": "18:00",
                        "end_time": "21:00",
                        "location": "Studio B",
                        "note": "",
                        "id": "",
                    }
                ]
            ),
        )
        assert resp.status_code == 302
        assert "tab=meetings" in resp.headers["Location"]
        row = guild.events.studio_hours().get()
        assert row.recurrence == CommunityEvent.Recurrence.WEEKLY
        assert timezone.localtime(row.starts_at).weekday() == 1

    def it_deletes_a_row_and_calls_remove_from_google_first(client: Client):
        _user, guild = _lead(client, "lead_del")
        event = CommunityEventFactory(studio_hours=True, guild=guild, pushed=True)
        seen: list[int | None] = []

        def _record(self) -> None:
            seen.append(self.pk)  # non-None proves the row still existed when remove ran

        from unittest.mock import patch

        with patch.object(CommunityEvent, "remove_from_google", _record):
            resp = client.post(
                reverse("hub_guild_studio_hours_save", args=[guild.pk]),
                data=_sh_payload(
                    [
                        {
                            "weekday": "1",
                            "start_time": "18:00",
                            "end_time": "21:00",
                            "location": "",
                            "note": "",
                            "id": str(event.pk),
                            "DELETE": "on",
                        }
                    ],
                    initial=1,
                ),
            )
        assert resp.status_code == 302
        assert seen == [event.pk]
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()
