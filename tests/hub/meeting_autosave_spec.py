"""BDD specs for the meeting workspace autosave endpoints (Meetings spec §5.2 / §9).

Per endpoint: whitelisted-field saves, unlisted field → 400, invalid value → 422 +
toast header, the coupled is_special/special_title rule, sync_event call-sites,
sanitizer application, the locked-403 / deleted-404 contract, the meeting-saved
trigger, the move endpoint, and the attendee add XOR + duplicate backstop.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import (
    CommunityEvent,
    GuildStaffMembership,
    Meeting,
    MeetingActionItem,
    Member,
)
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    MeetingActionItemFactory,
    MeetingAgendaItemFactory,
    MeetingAttendeeFactory,
    MeetingFactory,
    MemberFactory,
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

        def it_does_not_sync_on_a_notes_save(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(guild=guild, event=event, owns_event=True)
            with patch.object(CommunityEvent, "push_to_google") as google:
                resp = _save(client, "hub_meeting_save", meeting.pk, "special_notes", "<p>hello</p>")
            assert resp.status_code == 204
            assert not google.called

    def it_sanitizes_notes_html(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = _save(client, "hub_meeting_save", meeting.pk, "other_notes", "<p>ok</p><script>alert(1)</script>")
        assert resp.status_code == 204
        meeting.refresh_from_db()
        assert "<script>" not in meeting.other_notes
        assert "<p>ok</p>" in meeting.other_notes

    def it_403s_a_locked_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, approved=True)
        resp = _save(client, "hub_meeting_save", meeting.pk, "special_notes", "<p>x</p>")
        assert resp.status_code == 403

    def it_403s_a_non_editor(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        resp = _save(client, "hub_meeting_save", meeting.pk, "special_notes", "<p>x</p>")
        assert resp.status_code == 403

    def it_404s_a_deleted_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        resp = _save(client, "hub_meeting_save", 999999, "special_notes", "<p>x</p>")
        assert resp.status_code == 404


@pytest.mark.django_db
def describe_item_save():
    def it_saves_name_description_and_sanitized_minutes(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        item = MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_item_save", item.pk, "name", "  Budget  ").status_code == 204
        assert _save(client, "hub_meeting_item_save", item.pk, "description", "The Q3 numbers").status_code == 204
        resp = _save(client, "hub_meeting_item_save", item.pk, "minutes", "<p>done</p><script>x()</script>")
        assert resp.status_code == 204
        item.refresh_from_db()
        assert item.name == "Budget"  # stripped
        assert item.description == "The Q3 numbers"
        assert "<script>" not in item.minutes
        assert "<p>done</p>" in item.minutes

    def it_rejects_an_overlong_name_with_422(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        item = MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_item_save", item.pk, "name", "x" * 201).status_code == 422

    def it_rejects_an_unlisted_field_with_400(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        item = MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_item_save", item.pk, "sort_order", "1").status_code == 400

    def it_403s_when_the_owning_meeting_is_locked(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        item = MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild, approved=True))
        assert _save(client, "hub_meeting_item_save", item.pk, "name", "x").status_code == 403

    def it_404s_a_deleted_child_row(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        assert _save(client, "hub_meeting_item_save", 999999, "name", "x").status_code == 404


@pytest.mark.django_db
def describe_item_move():
    @pytest.fixture
    def agenda(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        items = [MeetingAgendaItemFactory(meeting=meeting, name=f"Item {i}") for i in range(3)]
        return meeting, items

    def _names(meeting):
        return [item.name for item in meeting.items.all()]

    def it_moves_an_item_up(client: Client, agenda):
        meeting, items = agenda
        resp = client.post(reverse("hub_meeting_item_move", args=[items[1].pk]), {"direction": "up"})
        assert resp.status_code == 200
        assert _names(meeting) == ["Item 1", "Item 0", "Item 2"]

    def it_moves_an_item_down(client: Client, agenda):
        meeting, items = agenda
        resp = client.post(reverse("hub_meeting_item_move", args=[items[1].pk]), {"direction": "down"})
        assert resp.status_code == 200
        assert _names(meeting) == ["Item 0", "Item 2", "Item 1"]

    def it_clamps_at_the_top(client: Client, agenda):
        meeting, items = agenda
        resp = client.post(reverse("hub_meeting_item_move", args=[items[0].pk]), {"direction": "up"})
        assert resp.status_code == 200
        assert _names(meeting) == ["Item 0", "Item 1", "Item 2"]

    def it_clamps_at_the_bottom(client: Client, agenda):
        meeting, items = agenda
        resp = client.post(reverse("hub_meeting_item_move", args=[items[2].pk]), {"direction": "down"})
        assert resp.status_code == 200
        assert _names(meeting) == ["Item 0", "Item 1", "Item 2"]

    def it_moves_despite_tied_sort_orders(client: Client, agenda):
        meeting, items = agenda
        meeting.items.update(sort_order=0)  # ties broken by pk; a naive swap would be invisible
        resp = client.post(reverse("hub_meeting_item_move", args=[items[2].pk]), {"direction": "up"})
        assert resp.status_code == 200
        assert _names(meeting) == ["Item 0", "Item 2", "Item 1"]

    def it_rejects_an_unknown_direction_with_400(client: Client, agenda):
        _, items = agenda
        resp = client.post(reverse("hub_meeting_item_move", args=[items[0].pk]), {"direction": "sideways"})
        assert resp.status_code == 400

    def it_re_renders_the_list_container(client: Client, agenda):
        _, items = agenda
        resp = client.post(reverse("hub_meeting_item_move", args=[items[1].pk]), {"direction": "up"})
        content = resp.content.decode()
        assert 'id="pl-meeting-item-list"' in content
        assert "meeting-saved" in resp["HX-Trigger"]

    def it_403s_on_a_locked_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        item = MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild, approved=True))
        resp = client.post(reverse("hub_meeting_item_move", args=[item.pk]), {"direction": "up"})
        assert resp.status_code == 403


@pytest.mark.django_db
def describe_action_save():
    def it_saves_the_name(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(item__meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_action_save", action.pk, "name", "Order sandpaper").status_code == 204
        action.refresh_from_db()
        assert action.name == "Order sandpaper"

    def it_saves_the_flag(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(item__meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_action_save", action.pk, "is_flagged", "true").status_code == 204
        action.refresh_from_db()
        assert action.is_flagged is True

    def it_completes_through_the_domain_transition(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(item__meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_action_save", action.pk, "status", "done").status_code == 204
        action.refresh_from_db()
        assert action.status == MeetingActionItem.Status.DONE
        assert action.closed_at is not None

    def it_reopens_a_done_action(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(
            item__meeting=MeetingFactory(guild=guild), status=MeetingActionItem.Status.DONE, closed_at=timezone.now()
        )
        assert _save(client, "hub_meeting_action_save", action.pk, "status", "open").status_code == 204
        action.refresh_from_db()
        assert action.status == MeetingActionItem.Status.OPEN
        assert action.closed_at is None

    def it_is_idempotent_on_a_repeated_status(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(item__meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_action_save", action.pk, "status", "open").status_code == 204
        action.refresh_from_db()
        assert action.status == MeetingActionItem.Status.OPEN

    def it_422s_reopening_a_dismissed_action(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(
            item__meeting=MeetingFactory(guild=guild), status=MeetingActionItem.Status.DISMISSED
        )
        assert _save(client, "hub_meeting_action_save", action.pk, "status", "open").status_code == 422

    def it_422s_an_unknown_status_value(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(item__meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_action_save", action.pk, "status", "dismissed").status_code == 422

    def it_400s_an_unlisted_field(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(item__meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_action_save", action.pk, "closed_at", "now").status_code == 400

    def it_403s_when_the_owning_meeting_is_locked(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(item__meeting=MeetingFactory(guild=guild, approved=True))
        assert _save(client, "hub_meeting_action_save", action.pk, "name", "x").status_code == 403

    def it_404s_a_deleted_action(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        assert _save(client, "hub_meeting_action_save", 999999, "name", "x").status_code == 404


@pytest.mark.django_db
def describe_attendee_save():
    def it_saves_present(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        attendee = MeetingAttendeeFactory(meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_attendee_save", attendee.pk, "present", "false").status_code == 204
        attendee.refresh_from_db()
        assert attendee.present is False

    def it_saves_a_guest_name_on_a_guest_row(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        attendee = MeetingAttendeeFactory(meeting=MeetingFactory(guild=guild), guest=True)
        assert _save(client, "hub_meeting_attendee_save", attendee.pk, "guest_name", "Sam Visitor").status_code == 204
        attendee.refresh_from_db()
        assert attendee.guest_name == "Sam Visitor"

    def it_422s_a_guest_name_on_a_roster_row(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        attendee = MeetingAttendeeFactory(meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_attendee_save", attendee.pk, "guest_name", "Nope").status_code == 422

    def it_422s_an_empty_guest_name(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        attendee = MeetingAttendeeFactory(meeting=MeetingFactory(guild=guild), guest=True)
        assert _save(client, "hub_meeting_attendee_save", attendee.pk, "guest_name", "  ").status_code == 422

    def it_400s_an_unlisted_field(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        attendee = MeetingAttendeeFactory(meeting=MeetingFactory(guild=guild))
        assert _save(client, "hub_meeting_attendee_save", attendee.pk, "member", "1").status_code == 400

    def it_403s_when_the_meeting_is_locked(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        attendee = MeetingAttendeeFactory(meeting=MeetingFactory(guild=guild, approved=True))
        assert _save(client, "hub_meeting_attendee_save", attendee.pk, "present", "false").status_code == 403

    def it_404s_a_deleted_attendee(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        assert _save(client, "hub_meeting_attendee_save", 999999, "present", "false").status_code == 404


@pytest.mark.django_db
def describe_attendee_add():
    def it_adds_a_roster_member_and_oob_swaps_the_picker(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        member = MemberFactory(full_legal_name="Robin Roster")
        member.guild_memberships.create(guild=guild)
        resp = client.post(reverse("hub_meeting_attendee_add", args=[meeting.pk]), {"member": str(member.pk)})
        assert resp.status_code == 200
        content = resp.content.decode()
        assert meeting.attendees.filter(member=member).exists()
        assert "Robin Roster" in content
        assert 'hx-swap-oob="true"' in content
        # The picker half of the response no longer offers the just-added member.
        picker_html = content.split('id="pl-meeting-attendee-picker"')[1]
        assert f'value="{member.pk}"' not in picker_html
        assert "meeting-saved" in resp["HX-Trigger"]

    def it_adds_a_guest(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_attendee_add", args=[meeting.pk]), {"guest_name": "Sam Visitor"})
        assert resp.status_code == 200
        assert meeting.attendees.filter(guest_name="Sam Visitor").exists()

    def it_422s_when_both_member_and_guest_are_posted(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        member = MemberFactory()
        resp = client.post(
            reverse("hub_meeting_attendee_add", args=[meeting.pk]),
            {"member": str(member.pk), "guest_name": "Also Sam"},
        )
        assert resp.status_code == 422

    def it_422s_when_neither_is_posted(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_attendee_add", args=[meeting.pk]), {})
        assert resp.status_code == 422

    def it_422s_a_non_numeric_member(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_attendee_add", args=[meeting.pk]), {"member": "abc"})
        assert resp.status_code == 422

    def it_422s_a_duplicate_member_with_a_toast(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        member = MemberFactory()
        MeetingAttendeeFactory(meeting=meeting, member=member)
        resp = client.post(reverse("hub_meeting_attendee_add", args=[meeting.pk]), {"member": str(member.pk)})
        assert resp.status_code == 422
        assert "Already on the list." in resp["HX-Trigger"]

    def it_403s_a_locked_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, approved=True)
        resp = client.post(reverse("hub_meeting_attendee_add", args=[meeting.pk]), {"guest_name": "Sam"})
        assert resp.status_code == 403

    def it_404s_a_deleted_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        resp = client.post(reverse("hub_meeting_attendee_add", args=[999999]), {"guest_name": "Sam"})
        assert resp.status_code == 404


@pytest.mark.django_db
def describe_the_locked_403_contract():
    def it_403s_every_mutating_route_on_a_locked_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, approved=True)
        item = MeetingAgendaItemFactory(meeting=meeting)
        action = MeetingActionItemFactory(item=item)
        attendee = MeetingAttendeeFactory(meeting=meeting)
        routes = [
            (reverse("hub_meeting_save", args=[meeting.pk]), {"field": "special_notes", "value": "x"}),
            (reverse("hub_meeting_item_add", args=[meeting.pk]), {}),
            (reverse("hub_meeting_item_save", args=[item.pk]), {"field": "name", "value": "x"}),
            (reverse("hub_meeting_item_move", args=[item.pk]), {"direction": "up"}),
            (reverse("hub_meeting_item_delete", args=[item.pk]), {}),
            (reverse("hub_meeting_action_add", args=[item.pk]), {}),
            (reverse("hub_meeting_action_save", args=[action.pk]), {"field": "name", "value": "x"}),
            (reverse("hub_meeting_action_delete", args=[action.pk]), {}),
            (reverse("hub_meeting_attendee_add", args=[meeting.pk]), {"guest_name": "Sam"}),
            (reverse("hub_meeting_attendee_save", args=[attendee.pk]), {"field": "present", "value": "false"}),
            (reverse("hub_meeting_attendee_delete", args=[attendee.pk]), {}),
            (reverse("hub_meeting_attachment_add", args=[meeting.pk]), {"url": "https://docs.example.com/x"}),
        ]
        for url, data in routes:
            assert client.post(url, data).status_code == 403, url

    def it_403s_a_council_meeting_for_a_plain_member(client: Client):
        _member_client(client)
        meeting = MeetingFactory(guild=None)
        resp = _save(client, "hub_meeting_save", meeting.pk, "special_notes", "<p>x</p>")
        assert resp.status_code == 403

    def it_allows_a_guild_staffer_to_edit_a_council_meeting(client: Client):
        user = _user_with_role("staffer")
        guild = GuildFactory()
        GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
        client.login(username="staffer", password="pass")
        meeting = MeetingFactory(guild=None)
        resp = _save(client, "hub_meeting_save", meeting.pk, "special_notes", "<p>council</p>")
        assert resp.status_code == 204
        meeting.refresh_from_db()
        assert "<p>council</p>" in meeting.special_notes


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
