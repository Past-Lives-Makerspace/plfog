"""BDD specs for the meeting page autosave endpoint (Meetings spec §5.2, scheduling only since #1113).

Whitelisted-field saves, unlisted field → 400 (the retired notes fields included), invalid
value → 422 + toast header, the coupled is_special/special_title rule, sync_event
call-sites, the non-editor 403 / deleted 404 contract, and the meeting-saved trigger.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import (
    CommunityEvent,
    Meeting,
    Member,
)
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    MeetingFactory,
    MembershipPlanFactory,
)


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _lead_client(client: Client, guild) -> User:
    """Log in as the guild's lead and return the user."""
    user = _user_with_role(f"lead-{guild.pk}")
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    client.login(username=user.username, password="pass")
    return user


def _member_client(client: Client) -> User:
    user = _user_with_role("plainmember")
    client.login(username=user.username, password="pass")
    return user


def _save(client: Client, url_name: str, pk: int, field: str, value: str):
    return client.post(reverse(url_name, args=[pk]), {"field": field, "value": value})


@pytest.mark.django_db
def describe_meeting_save():
    def it_saves_a_whitelisted_field(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = _save(client, "hub_meeting_save", meeting.pk, "video_call_url", "https://meet.example.com/abc")
        assert resp.status_code == 204
        meeting.refresh_from_db()
        assert meeting.video_call_url == "https://meet.example.com/abc"

    def it_returns_the_meeting_saved_trigger_on_204(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = _save(client, "hub_meeting_save", meeting.pk, "scheduled_date", "2026-09-01")
        assert resp.status_code == 204
        assert "meeting-saved" in resp["HX-Trigger"]

    def it_rejects_an_unlisted_field_with_400(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = _save(client, "hub_meeting_save", meeting.pk, "status", "approved")
        assert resp.status_code == 400

    def it_rejects_a_missing_field_param_with_400(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_save", args=[meeting.pk]), {"value": "x"})
        assert resp.status_code == 400

    def it_rejects_a_bad_url_with_422_and_toast(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = _save(client, "hub_meeting_save", meeting.pk, "video_call_url", "not a link")
        assert resp.status_code == 422
        assert "showToast" in resp["HX-Trigger"]
        meeting.refresh_from_db()
        assert meeting.video_call_url == ""

    def it_accepts_a_cleared_url(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, video_call_url="https://meet.example.com/x")
        resp = _save(client, "hub_meeting_save", meeting.pk, "video_call_url", "")
        assert resp.status_code == 204
        meeting.refresh_from_db()
        assert meeting.video_call_url == ""

    def it_rejects_an_off_grid_time_with_422(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = _save(client, "hub_meeting_save", meeting.pk, "scheduled_time", "18:07")
        assert resp.status_code == 422
        assert "showToast" in resp["HX-Trigger"]

    def it_saves_a_half_hour_time_and_a_blank_time(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        assert _save(client, "hub_meeting_save", meeting.pk, "scheduled_time", "18:30").status_code == 204
        meeting.refresh_from_db()
        assert meeting.scheduled_time is not None and meeting.scheduled_time.strftime("%H:%M") == "18:30"
        assert _save(client, "hub_meeting_save", meeting.pk, "scheduled_time", "").status_code == 204
        meeting.refresh_from_db()
        assert meeting.scheduled_time is None

    def it_saves_a_half_hour_end_time(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
        assert _save(client, "hub_meeting_save", meeting.pk, "scheduled_end_time", "19:30").status_code == 204
        meeting.refresh_from_db()
        assert meeting.scheduled_end_time is not None and meeting.scheduled_end_time.strftime("%H:%M") == "19:30"

    def it_422s_an_end_time_at_or_before_the_start(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
        resp = _save(client, "hub_meeting_save", meeting.pk, "scheduled_end_time", "18:00")
        assert resp.status_code == 422
        meeting.refresh_from_db()
        assert meeting.scheduled_end_time is None  # the invalid end was not saved

    def it_422s_a_start_time_moved_past_the_end(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0), scheduled_end_time=time(19, 0))
        resp = _save(client, "hub_meeting_save", meeting.pk, "scheduled_time", "20:00")
        assert resp.status_code == 422

    def it_rejects_a_bad_date_with_422(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = _save(client, "hub_meeting_save", meeting.pk, "scheduled_date", "not-a-date")
        assert resp.status_code == 422

    def it_clears_the_date_on_blank(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        assert _save(client, "hub_meeting_save", meeting.pk, "scheduled_date", "").status_code == 204
        meeting.refresh_from_db()
        assert meeting.scheduled_date is None

    def it_rejects_an_overlong_special_title_with_422(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, is_special=True)
        resp = _save(client, "hub_meeting_save", meeting.pk, "special_title", "x" * 121)
        assert resp.status_code == 422

    def describe_the_coupled_special_fields():
        def it_clears_special_title_when_is_special_turns_off_in_the_same_save(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, is_special=True, special_title="Emergency")
            resp = _save(client, "hub_meeting_save", meeting.pk, "is_special", "false")
            assert resp.status_code == 204
            meeting.refresh_from_db()
            assert meeting.is_special is False
            assert meeting.special_title == ""  # no IntegrityError path exists (§5.2)

        def it_keeps_special_title_when_is_special_turns_on(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            assert _save(client, "hub_meeting_save", meeting.pk, "is_special", "true").status_code == 204
            assert _save(client, "hub_meeting_save", meeting.pk, "special_title", "Planning").status_code == 204
            meeting.refresh_from_db()
            assert meeting.is_special is True
            assert meeting.special_title == "Planning"

        def it_rejects_naming_a_monthly_meeting_with_422(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, is_special=False)
            resp = _save(client, "hub_meeting_save", meeting.pk, "special_title", "Sneaky")
            assert resp.status_code == 422

        def it_rejects_a_garbage_boolean_with_422(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            resp = _save(client, "hub_meeting_save", meeting.pk, "is_special", "maybe")
            assert resp.status_code == 422

    def describe_sync_event():
        def it_syncs_an_owned_event_on_a_date_save(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(guild=guild, event=event, owns_event=True, event_occurrence=None)
            new_date = timezone.localdate() + timedelta(days=14)
            with (
                patch.object(CommunityEvent, "push_to_google") as google,
                patch.object(CommunityEvent, "push_to_discord") as discord,
            ):
                resp = _save(client, "hub_meeting_save", meeting.pk, "scheduled_date", new_date.isoformat())
            assert resp.status_code == 204
            event.refresh_from_db()
            assert timezone.localdate(event.starts_at) == new_date
            assert google.called
            assert discord.called

        def it_propagates_a_changed_video_url_to_the_owned_event_location(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(guild=guild, event=event, owns_event=True)
            with patch.object(CommunityEvent, "push_to_google"), patch.object(CommunityEvent, "push_to_discord"):
                resp = _save(client, "hub_meeting_save", meeting.pk, "video_call_url", "https://meet.example.com/y")
            assert resp.status_code == 204
            event.refresh_from_db()
            assert event.location == "https://meet.example.com/y"

        def it_leaves_a_merely_linked_event_untouched(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            original_starts = event.starts_at
            meeting = MeetingFactory(guild=guild, event=event, owns_event=False)
            with patch.object(CommunityEvent, "push_to_google") as google:
                resp = _save(
                    client,
                    "hub_meeting_save",
                    meeting.pk,
                    "scheduled_date",
                    (timezone.localdate() + timedelta(days=21)).isoformat(),
                )
            assert resp.status_code == 204
            event.refresh_from_db()
            assert event.starts_at == original_starts
            assert not google.called

    def it_rejects_the_retired_notes_fields_with_400(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        for field in ("special_notes", "other_notes"):
            resp = _save(client, "hub_meeting_save", meeting.pk, field, "<p>hello</p>")
            assert resp.status_code == 400
        meeting.refresh_from_db()
        assert meeting.special_notes == ""
        assert meeting.other_notes == ""

    def it_still_saves_a_past_meeting_so_a_year_can_be_typed_through(client: Client):
        # The date input saves while a year is typed (0002, 0020, 0202 on the way to 2026),
        # so the field save never refuses a meeting for being past.
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, scheduled_date=date(2, 1, 1))
        resp = _save(client, "hub_meeting_save", meeting.pk, "scheduled_date", "2026-12-01")
        assert resp.status_code == 204
        meeting.refresh_from_db()
        assert meeting.scheduled_date == date(2026, 12, 1)

    def it_403s_a_non_editor(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        resp = _save(client, "hub_meeting_save", meeting.pk, "video_call_url", "https://meet.example/x")
        assert resp.status_code == 403

    def it_404s_a_deleted_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        resp = _save(client, "hub_meeting_save", 999999, "video_call_url", "https://meet.example/x")
        assert resp.status_code == 404


@pytest.mark.django_db
def describe_create_prefill_edge_cases():
    def it_uses_localdate_not_utc_for_the_upcoming_window(client: Client):
        # tz gotcha (§9): a meeting dated "today" in Portland must count as upcoming.
        guild = GuildFactory()
        _lead_client(client, guild)
        today = timezone.localdate()
        existing = MeetingFactory(guild=guild, scheduled_date=today)
        resp = client.post(
            reverse("hub_meeting_create"),
            {"scope": str(guild.pk), "kind": "monthly", "date": today.isoformat()},
        )
        assert resp.status_code == 302
        assert resp["Location"] == reverse("hub_meeting", args=[existing.pk])
        assert Meeting.objects.count() == 1

    def it_accepts_an_off_grid_legacy_cadence_time(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        prefill = date.fromisoformat((timezone.localdate() + timedelta(days=3)).isoformat())
        resp = client.post(
            reverse("hub_meeting_create"),
            {"scope": str(guild.pk), "kind": "monthly", "date": prefill.isoformat(), "time": "18:15"},
        )
        assert resp.status_code == 302
        meeting = Meeting.objects.get()
        assert meeting.scheduled_time is not None and meeting.scheduled_time.strftime("%H:%M") == "18:15"
