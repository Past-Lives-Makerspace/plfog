"""BDD specs for the meeting views, scheduling only since #1113.

The meeting page GET (editor / read only / past, none of the retired agenda parts), the
create endpoint (Schedule it prefill + double-create guard + forbidden-scope 403), Delete,
the calendar card endpoints, the Meetings home and the guild page's Meetings tab, plus the
retired routes being gone.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import (
    CommunityEvent,
    Guild,
    GuildStaffMembership,
    Meeting,
    MeetingAttachment,
    Member,
)
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildMembershipFactory,
    MeetingActionItemFactory,
    MeetingAgendaItemFactory,
    MeetingAttendeeFactory,
    MeetingFactory,
    MeetingItemProposalFactory,
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
    user = _user_with_role(f"lead-{guild.pk}")
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    client.login(username=user.username, password="pass")
    return user


def _member_client(client: Client) -> User:
    user = _user_with_role("plainmember")
    client.login(username=user.username, password="pass")
    return user


def _guild_member_client(client: Client, guild) -> User:
    """An active plain member OF the guild (proposal rights, no edit rights)."""
    user = _user_with_role(f"guildmember-{guild.pk}")
    GuildMembershipFactory(guild=guild, member=user.member)
    client.login(username=user.username, password="pass")
    return user


def _admin_client(client: Client) -> User:
    user = _user_with_role("adminuser", fog_role=Member.FogRole.ADMIN)
    client.login(username=user.username, password="pass")
    return user


def _messages(resp) -> list[str]:
    return [str(m) for m in get_messages(resp.wsgi_request)]


_RETIRED_PARTS = (
    '<h2 class="hub-detail-label">Agenda</h2>',
    "Attendance</h2>",
    '<h2 class="hub-detail-label">Attachments</h2>',
    "Publish agenda",
    "Approve minutes",
    "Unlock minutes",
    "Propose an agenda item",
    "Notes for this meeting",
    "Other discussion",
    "+ Attach file or link",
    "pl-meeting-add-item-form",
    "pl-meeting-attendee-picker",
)


def _with_history(meeting: Meeting) -> Meeting:
    """Old workspace rows the page must no longer show: an agenda topic with minutes and an
    action, an attendee, an attachment, a proposal and both notes boxes (#1113)."""
    item = MeetingAgendaItemFactory(meeting=meeting, name="Old budget topic", minutes="<p>Old minutes text</p>")
    MeetingActionItemFactory(item=item, name="Old action item")
    MeetingAttendeeFactory(meeting=meeting, guest=True, guest_name="Old Guest")
    MeetingAttachment.objects.create(meeting=meeting, url="https://old.example/doc", label="Old slides")
    MeetingItemProposalFactory(meeting=meeting, title="Old proposal")
    meeting.special_notes = "<p>Old special notes</p>"
    meeting.other_notes = "<p>Old other notes</p>"
    meeting.save(update_fields=["special_notes", "other_notes"])
    return meeting


def _meeting_page(content: str) -> str:
    """The meeting page's own markup, without the hub chrome (the changelog modal on every page
    quotes old release notes about agendas and minutes)."""
    return content.split('<div class="pl-meeting-workspace"', 1)[1].split("<script>", 1)[0]


_HISTORY_TEXT = (
    "Old budget topic",
    "Old minutes text",
    "Old action item",
    "Old Guest",
    "Old slides",
    "Old proposal",
    "Old special notes",
    "Old other notes",
)


@pytest.mark.django_db
def describe_workspace_get():
    def describe_for_an_editor():
        def it_shows_scope_date_times_video_link_and_the_calendar_card(client: Client):
            guild = GuildFactory(name="Woodshop")
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert resp.status_code == 200
            content = resp.content.decode()
            assert "Woodshop — Monthly Meeting" in content
            assert "pl-meeting-savestate" in content  # the autosave pill
            for field in ("scheduled_date", "scheduled_time", "scheduled_end_time", "video_call_url"):
                assert f'"field": "{field}"' in content
            assert "Time TBD" in content  # the half-hour select's blank choice
            assert '<input type="time"' not in content  # rule 19
            assert "Add to Calendar" in content
            assert "Delete meeting" in content

        def it_shows_none_of_the_retired_parts_or_their_old_rows(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = _with_history(MeetingFactory(guild=guild))
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            page = _meeting_page(content)
            for text in _RETIRED_PARTS + _HISTORY_TEXT:
                assert text not in page, text
            assert "rich_editor" not in content
            assert "ql-toolbar" not in page

        def it_renders_in_a_fixed_number_of_queries(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            plain = MeetingFactory(guild=guild)
            busy = _with_history(MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=30)))
            client.get(reverse("hub_meeting", args=[plain.pk]))  # warm caches
            with CaptureQueriesContext(connection) as plain_ctx:
                client.get(reverse("hub_meeting", args=[plain.pk]))
            with CaptureQueriesContext(connection) as busy_ctx:
                client.get(reverse("hub_meeting", args=[busy.pk]))
            assert len(plain_ctx) == len(busy_ctx)

    def describe_for_a_plain_member():
        def it_renders_the_same_facts_read_only(client: Client):
            guild = GuildFactory()
            _member_client(client)
            meeting = MeetingFactory(guild=guild, video_call_url="https://meet.example.com/z", scheduled_time=None)
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert resp.status_code == 200
            content = resp.content.decode()
            assert "Time TBD" in content
            assert "Join meeting" in content
            assert "https://meet.example.com/z" in content
            assert "pl-meeting-savestate" not in content
            assert "data-autosave" not in content
            assert "Delete meeting" not in content
            assert "Add to Calendar" not in content

    def describe_an_old_meeting():
        def it_opens_read_only_for_a_plain_member_on_the_same_page_shape(client: Client):
            guild = GuildFactory()
            _member_client(client)
            meeting = MeetingFactory(
                guild=guild,
                scheduled_date=timezone.localdate() - timedelta(days=7),
                video_call_url="https://meet.example.com/old",
            )
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert resp.status_code == 200
            content = resp.content.decode()
            assert "pl-meeting-facts" in content  # the same facts strip
            assert "https://meet.example.com/old" in content
            assert "pl-meeting-savestate" not in content
            assert "data-autosave" not in content
            assert "Delete meeting" not in content

        def it_stays_editable_for_its_editor_so_a_wrong_date_can_be_fixed(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=7))
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert '"field": "scheduled_date"' in content
            assert "Delete meeting" in content

        def it_opens_approved_minutes_with_no_banner_or_old_rows(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = _with_history(
                MeetingFactory(guild=guild, approved=True, scheduled_date=timezone.localdate() - timedelta(days=30))
            )
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert resp.status_code == 200
            page = _meeting_page(resp.content.decode())
            assert "Minutes approved" not in page
            for text in _RETIRED_PARTS + _HISTORY_TEXT:
                assert text not in page, text

    def it_renders_a_council_meeting_page(client: Client):
        user = _user_with_role("councilstaff")
        guild = GuildFactory()
        GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
        client.login(username="councilstaff", password="pass")
        meeting = MeetingFactory(guild=None)
        resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
        content = resp.content.decode()
        assert "Council — Monthly Meeting" in content
        assert "pl-meeting-savestate" in content  # any-guild staff edit the council scope

    def it_redirects_anonymous_users_to_login(client: Client):
        meeting = MeetingFactory()
        resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
        assert resp.status_code == 302
        assert "login" in resp["Location"]

    def it_404s_a_missing_meeting(client: Client):
        _member_client(client)
        resp = client.get(reverse("hub_meeting", args=[999999]))
        assert resp.status_code == 404


@pytest.mark.django_db
def describe_create():
    def it_creates_an_empty_draft_and_redirects_into_the_workspace(client: Client):
        guild = GuildFactory()
        user = _lead_client(client, guild)
        resp = client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "monthly"})
        assert resp.status_code == 302
        meeting = Meeting.objects.get()
        assert resp["Location"] == reverse("hub_meeting", args=[meeting.pk])
        assert meeting.guild == guild
        assert meeting.status == Meeting.Status.DRAFT
        assert meeting.is_special is False
        assert meeting.scheduled_date is None
        assert meeting.created_by == user
        assert not meeting.items.exists()  # create-empty-then-fill

    def it_creates_a_special_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        resp = client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "special"})
        assert resp.status_code == 302
        assert Meeting.objects.get().is_special is True

    def it_prefills_date_and_time_from_the_schedule_it_contract(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        occurrence = timezone.localdate() + timedelta(days=10)
        resp = client.post(
            reverse("hub_meeting_create"),
            {"scope": str(guild.pk), "kind": "monthly", "date": occurrence.isoformat(), "time": "18:30"},
        )
        assert resp.status_code == 302
        meeting = Meeting.objects.get()
        assert meeting.scheduled_date == occurrence
        assert meeting.scheduled_time is not None and meeting.scheduled_time.strftime("%H:%M") == "18:30"

    def describe_the_double_create_guard():
        def it_redirects_into_an_existing_upcoming_meeting_for_the_same_scope_and_date(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            occurrence = timezone.localdate() + timedelta(days=10)
            existing = MeetingFactory(guild=guild, scheduled_date=occurrence)
            resp = client.post(
                reverse("hub_meeting_create"),
                {"scope": str(guild.pk), "kind": "monthly", "date": occurrence.isoformat()},
            )
            assert resp.status_code == 302
            assert resp["Location"] == reverse("hub_meeting", args=[existing.pk])
            assert Meeting.objects.count() == 1  # no twin

        def it_is_idempotent_on_a_double_post(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            occurrence = timezone.localdate() + timedelta(days=10)
            payload = {"scope": str(guild.pk), "kind": "monthly", "date": occurrence.isoformat()}
            first = client.post(reverse("hub_meeting_create"), payload)
            second = client.post(reverse("hub_meeting_create"), payload)
            assert first["Location"] == second["Location"]
            assert Meeting.objects.count() == 1

        def it_does_not_guard_the_dateless_modal_path(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "monthly"})
            client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "monthly"})
            assert Meeting.objects.count() == 2  # nothing to collide on, by design

        def it_ignores_a_different_scope_on_the_same_date(client: Client):
            guild = GuildFactory()
            other = GuildFactory()
            _lead_client(client, guild)
            occurrence = timezone.localdate() + timedelta(days=10)
            MeetingFactory(guild=other, scheduled_date=occurrence)
            resp = client.post(
                reverse("hub_meeting_create"),
                {"scope": str(guild.pk), "kind": "monthly", "date": occurrence.isoformat()},
            )
            assert resp.status_code == 302
            assert Meeting.objects.count() == 2

    def describe_scope_permissions():
        def it_403s_a_plain_member(client: Client):
            guild = GuildFactory()
            _member_client(client)
            resp = client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "monthly"})
            assert resp.status_code == 403
            assert not Meeting.objects.exists()

        def it_403s_a_lead_creating_for_a_guild_they_do_not_edit(client: Client):
            guild = GuildFactory()
            other = GuildFactory()
            _lead_client(client, guild)
            resp = client.post(reverse("hub_meeting_create"), {"scope": str(other.pk), "kind": "monthly"})
            assert resp.status_code == 403

        def it_403s_a_plain_member_for_the_council_scope(client: Client):
            _member_client(client)
            resp = client.post(reverse("hub_meeting_create"), {"scope": "council", "kind": "monthly"})
            assert resp.status_code == 403

        def it_lets_any_guild_staffer_create_a_council_meeting(client: Client):
            user = _user_with_role("councilmaker")
            guild = GuildFactory()
            GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.CO_LEAD)
            client.login(username="councilmaker", password="pass")
            resp = client.post(reverse("hub_meeting_create"), {"scope": "council", "kind": "monthly"})
            assert resp.status_code == 302
            assert Meeting.objects.get().guild is None

        def it_lets_an_admin_create_for_any_guild(client: Client):
            guild = GuildFactory()
            user = _user_with_role("adminuser", fog_role=Member.FogRole.ADMIN)
            client.login(username=user.username, password="pass")
            resp = client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "monthly"})
            assert resp.status_code == 302

    def it_400s_an_invalid_kind(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        resp = client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "weekly"})
        assert resp.status_code == 400

    def it_400s_a_garbage_prefill_date(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        resp = client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "monthly", "date": "nope"})
        assert resp.status_code == 400

    def it_400s_a_garbage_prefill_time(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        resp = client.post(reverse("hub_meeting_create"), {"scope": str(guild.pk), "kind": "monthly", "time": "late"})
        assert resp.status_code == 400

    def it_405s_a_get(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        assert client.get(reverse("hub_meeting_create")).status_code == 405


# --- Phase 3: lifecycle (approve / unlock / delete) ---------------------------


@pytest.mark.django_db
def describe_delete():
    def it_deletes_a_draft_and_redirects_to_the_meetings_home(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_delete", args=[meeting.pk]))
        assert resp.status_code == 204
        assert resp["HX-Redirect"] == "/meetings/"
        assert "Meeting deleted." in _messages(resp)
        assert not Meeting.objects.exists()

    def it_unwinds_an_owned_calendar_event_through_the_rails(client: Client):
        guild = GuildFactory()
        user = _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
        with patch.object(CommunityEvent, "push_live"):
            meeting.create_calendar_event(by=user)
        meeting.refresh_from_db()
        event_pk = meeting.event_id
        with (
            patch.object(CommunityEvent, "remove_from_google") as google,
            patch.object(CommunityEvent, "remove_from_discord") as discord,
        ):
            resp = client.post(reverse("hub_meeting_delete", args=[meeting.pk]))
        assert resp.status_code == 204
        google.assert_called_once()
        discord.assert_called_once()
        assert not CommunityEvent.objects.filter(pk=event_pk).exists()
        assert not Meeting.objects.exists()

    def it_leaves_a_merely_linked_event_completely_alone(client: Client):
        guild = GuildFactory()
        user = _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        event = CommunityEventFactory(guild=guild)
        meeting.link_event(event, timezone.localdate(event.starts_at), by=user)
        with patch.object(CommunityEvent, "remove_from_google") as google:
            resp = client.post(reverse("hub_meeting_delete", args=[meeting.pk]))
        assert resp.status_code == 204
        google.assert_not_called()
        assert CommunityEvent.objects.filter(pk=event.pk).exists()
        assert not Meeting.objects.exists()

    def it_lets_an_editor_delete_a_past_dated_meeting(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=1))
        assert client.post(reverse("hub_meeting_delete", args=[meeting.pk])).status_code == 204
        assert not Meeting.objects.filter(pk=meeting.pk).exists()

    def it_403s_a_non_editor(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        assert client.post(reverse("hub_meeting_delete", args=[meeting.pk])).status_code == 403
        assert Meeting.objects.filter(pk=meeting.pk).exists()


# --- The meeting page footer (Delete) -------------------------------------------


@pytest.mark.django_db
def describe_workspace_footer():
    def describe_the_delete_button():
        def it_shows_delete_alone_with_the_plain_confirm_line(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Delete meeting" in content
            assert "Deletes this meeting." in content
            assert "Approve minutes" not in content

        def it_names_the_calendar_cascade_for_an_owned_event(client: Client):
            guild = GuildFactory()
            user = _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                meeting.create_calendar_event(by=user)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Its calendar event is also removed from the calendar, Google, and Discord." in content

        def it_names_the_guild_stay_line_for_a_merely_linked_event(client: Client):
            guild = GuildFactory()
            user = _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            event = CommunityEventFactory(guild=guild)
            meeting.link_event(event, timezone.localdate(event.starts_at), by=user)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "cancel it separately from the guild's Events tab" in content

        def it_names_the_council_stay_line_for_a_merely_linked_event(client: Client):
            _admin_client(client)
            meeting = MeetingFactory(guild=None)
            event = CommunityEventFactory(lead_meeting=True)
            meeting.event = event
            meeting.save(update_fields=["event"])
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "an admin can cancel it from the events editor" in content

        def it_is_absent_for_read_only_viewers(client: Client):
            _member_client(client)
            meeting = MeetingFactory()
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Delete meeting" not in content


# --- Phase 4: surfaces + rails (spec §6.1, §6.2, §6.3 calendar, §6.4) ----------


def _cadence_guild(name: str = "Cadenced") -> Guild:
    """A guild with a monthly cadence + time, so ``next_meeting_occurrence`` resolves."""
    return GuildFactory(
        name=name,
        meeting_cadence=Guild.MeetingCadence.MONTHLY,
        meeting_weekday=2,
        meeting_week_of_month=2,
        meeting_time=time(18, 30),
    )


@pytest.mark.django_db
def describe_sidebar():
    def it_marks_the_meetings_link_active_on_the_home(client: Client):
        _member_client(client)
        content = client.get(reverse("hub_meetings")).content.decode()
        assert 'href="/meetings/" class="hub-sidebar__link active"' in content

    def it_marks_the_meetings_link_active_on_a_workspace_page(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
        assert 'href="/meetings/" class="hub-sidebar__link active"' in content

    def it_is_inactive_elsewhere_and_present_in_the_admin_sidebar(client: Client):
        _admin_client(client)
        content = client.get(reverse("hub_member_directory")).content.decode()
        assert 'href="/meetings/" class="hub-sidebar__link "' in content
        assert "active" not in content.split('href="/meetings/"')[1].split("</a>")[0].split(">")[0]


@pytest.mark.django_db
def describe_meetings_home():
    def describe_zone_1_upcoming():
        def it_lists_upcoming_meetings_soonest_first_with_no_status(client: Client):
            _member_client(client)
            later = MeetingFactory(
                guild=GuildFactory(name="Zebra"),
                scheduled_date=timezone.localdate() + timedelta(days=10),
                approved=True,
            )
            sooner = MeetingFactory(
                guild=GuildFactory(name="Alpha"), scheduled_date=timezone.localdate() + timedelta(days=3)
            )
            content = client.get(reverse("hub_meetings")).content.decode()
            upcoming_zone = content.split("Upcoming")[1].split("Meetings by guild")[0]
            assert upcoming_zone.index(sooner.display_title) < upcoming_zone.index(later.display_title)
            for status in ("Draft", "Published", "Approved"):
                assert f">{status}</span>" not in upcoming_zone

        def it_shows_a_join_button_when_the_meeting_has_a_video_link(client: Client):
            _member_client(client)
            MeetingFactory(video_call_url="https://meet.example/home-zone")
            content = client.get(reverse("hub_meetings")).content.decode()
            assert 'href="https://meet.example/home-zone"' in content
            assert "Join meeting" in content

        def it_shows_no_topic_or_proposal_counts_and_no_propose_button(client: Client):
            guild = GuildFactory()
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0), scheduled_end_time=time(19, 30))
            MeetingAgendaItemFactory(meeting=meeting)
            MeetingItemProposalFactory(meeting=meeting)
            _lead_client(client, guild)
            upcoming_zone = client.get(reverse("hub_meetings")).content.decode().split("Upcoming")[1]
            upcoming_zone = upcoming_zone.split("Meetings by guild")[0]
            assert "6:00 PM &ndash; 7:30 PM" in upcoming_zone
            assert "topic" not in upcoming_zone
            assert "proposal" not in upcoming_zone
            assert "Propose an agenda item" not in upcoming_zone

        def it_shows_the_empty_state(client: Client):
            _member_client(client)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "No meetings scheduled. Guild leadership can create one." in content

        def it_cards_an_upcoming_council_meeting_for_everyone(client: Client):
            _member_client(client)
            MeetingFactory(guild=None)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "Council — Monthly Meeting" in content.split("Upcoming")[1].split("Meetings by guild")[0]

        def it_repeats_the_new_meeting_button_in_the_empty_state_for_editors(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "No meetings scheduled." in content
            assert content.count("+ New meeting") == 2  # header + empty state

    def describe_the_needs_attention_strip():
        def it_shows_undated_meetings_to_the_scopes_editor(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            MeetingFactory(guild=guild, scheduled_date=None)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "Needs attention" in content
            assert "No date set" in content

        def it_never_lists_a_past_meeting_as_awaiting_approval(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=5))
            MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=9), published=True)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "Awaiting approval" not in content
            assert "Needs attention" not in content

        def it_scopes_rows_to_the_editors_guilds(client: Client):
            guild = GuildFactory(name="Mine")
            other = GuildFactory(name="NotMine")
            _lead_client(client, guild)
            MeetingFactory(guild=other, scheduled_date=None)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "Needs attention" not in content

        def it_includes_council_rows_for_council_editors(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)  # any-guild lead edits the council scope
            MeetingFactory(guild=None, scheduled_date=None)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "Needs attention" in content
            assert "Council — Monthly Meeting" in content

        def it_never_renders_for_a_plain_member(client: Client):
            _member_client(client)
            MeetingFactory(scheduled_date=None)
            assert "Needs attention" not in client.get(reverse("hub_meetings")).content.decode()

        def it_is_hidden_when_everything_is_clean(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            MeetingFactory(guild=guild)  # future-dated draft — nothing to nudge
            assert "Needs attention" not in client.get(reverse("hub_meetings")).content.decode()

    def describe_the_schedule_it_strip():
        def it_shows_a_start_button_for_a_leads_cadenced_scope_with_nothing_upcoming(client: Client):
            guild = _cadence_guild()
            _lead_client(client, guild)
            content = client.get(reverse("hub_meetings")).content.decode()
            strip = content.split("Needs attention")[1].split("Meetings by guild")[0]
            assert "pl-meeting-attention__row--start" in strip
            assert "Schedule it" in strip
            assert "Start the agenda" not in content

        def it_hides_the_start_button_from_a_non_editor(client: Client):
            _cadence_guild()
            _member_client(client)
            assert "pl-meeting-attention__row--start" not in client.get(reverse("hub_meetings")).content.decode()

        def it_omits_a_scope_that_already_has_a_draft(client: Client):
            guild = _cadence_guild()
            _lead_client(client, guild)
            MeetingFactory(guild=guild)  # a future-dated draft exists → next_meeting not None
            assert "pl-meeting-attention__row--start" not in client.get(reverse("hub_meetings")).content.decode()

        def it_omits_an_uncadenced_editable_scope(client: Client):
            guild = GuildFactory()  # no cadence configured
            _lead_client(client, guild)
            assert "pl-meeting-attention__row--start" not in client.get(reverse("hub_meetings")).content.decode()

        def it_reuses_hub_meeting_create_and_does_not_twin_an_existing_meeting(client: Client):
            guild = _cadence_guild()
            _lead_client(client, guild)
            occurrence = guild.next_meeting_occurrence()
            post_data = {
                "scope": str(guild.pk),
                "kind": "monthly",
                "date": occurrence.when.strftime("%Y-%m-%d"),
                "time": occurrence.when.strftime("%H:%M"),
            }
            first = client.post(reverse("hub_meeting_create"), post_data)
            assert first.status_code == 302
            created = Meeting.objects.get(guild=guild)
            assert first["Location"] == reverse("hub_meeting", args=[created.pk])
            second = client.post(reverse("hub_meeting_create"), post_data)
            assert Meeting.objects.filter(guild=guild).count() == 1  # redirected into the existing one, no twin
            assert second["Location"] == reverse("hub_meeting", args=[created.pk])

    def describe_zone_2_coordinator_table():
        def it_puts_the_council_row_first(client: Client):
            guild = GuildFactory(name="Atrium")
            MeetingFactory(guild=guild)
            _member_client(client)
            content = client.get(reverse("hub_meetings")).content.decode()
            table = content.split('class="pl-meeting-dash"')[1].split("</table>")[0]
            assert table.index("Council") < table.index("Atrium")

        def it_shows_the_latest_past_meeting_with_no_status_mark(client: Client):
            guild = GuildFactory()
            older = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=40), approved=True)
            latest = MeetingFactory(
                guild=guild, scheduled_date=timezone.localdate() - timedelta(days=10), approved=True
            )
            _member_client(client)
            content = client.get(reverse("hub_meetings")).content.decode()
            table = content.split('class="pl-meeting-dash"')[1].split("</table>")[0]
            assert reverse("hub_meeting", args=[latest.pk]) in table
            assert reverse("hub_meeting", args=[older.pk]) not in table
            assert "pl-meeting-tick" not in table
            assert "Published" not in table

        def it_keeps_a_future_meeting_out_of_most_recent(client: Client):
            guild = GuildFactory()
            future = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=5), approved=True)
            _member_client(client)
            content = client.get(reverse("hub_meetings")).content.decode()
            table = content.split('class="pl-meeting-dash"')[1].split("</table>")[0]
            row = table.split("</tr>")[2]  # thead row, council row, then this guild's row
            most_recent_cell = row.split('data-label="Most recent"')[1].split("</td>")[0]
            next_cell = row.split('data-label="Next scheduled"')[1].split("</td>")[0]
            assert reverse("hub_meeting", args=[future.pk]) not in most_recent_cell
            assert reverse("hub_meeting", args=[future.pk]) in next_cell

        def it_renders_the_cadence_fallback_as_plain_text_for_members(client: Client):
            _cadence_guild()
            _member_client(client)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "(from schedule)" in content
            assert "Schedule it" not in content

        def it_gives_that_guilds_editors_a_schedule_it_button_with_the_prefill(client: Client):
            guild = _cadence_guild()
            _lead_client(client, guild)
            occurrence = guild.next_meeting_occurrence()
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "Schedule it" in content
            assert f'name="date" value="{occurrence.when:%Y-%m-%d}"' in content
            assert 'name="time" value="18:30"' in content

        def it_hides_schedule_it_from_editors_of_other_guilds(client: Client):
            _cadence_guild()
            other = GuildFactory(name="Elsewhere")
            _lead_client(client, other)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "(from schedule)" in content
            assert "Schedule it" not in content

        def it_shows_studio_hours_and_still_suggests_the_meeting(client: Client):
            # The row agrees with the guild page card (#953); Schedule it follows the meeting.
            # The clock sits at 10 AM: on a meeting day after 4:30 PM, today's passed 6:30 PM
            # meeting would sort ahead of studio hours two hours from now.
            ten_am = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
            with patch("django.utils.timezone.now", return_value=ten_am):
                _assert_studio_hours_row_suggests_the_meeting(client)

        def _assert_studio_hours_row_suggests_the_meeting(client: Client) -> None:
            guild = _cadence_guild("Hoursmiths")
            start = timezone.now() + timedelta(hours=2)
            CommunityEventFactory(
                studio_hours=True,
                guild=guild,
                title="Open Hours",
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=start,
                ends_at=start + timedelta(hours=3),
            )
            _lead_client(client, guild)
            meeting = guild.next_meeting_occurrence(meetings_only=True)
            content = client.get(reverse("hub_meetings")).content.decode()
            table = content.split('class="pl-meeting-dash"')[1].split("</table>")[0]
            assert "Open Hours" in table
            assert "Schedule it" in table
            assert f'name="date" value="{meeting.when:%Y-%m-%d}"' in table
            strip = content.split("Needs attention")[1].split("Meetings by guild")[0]
            assert f'name="date" value="{meeting.when:%Y-%m-%d}"' in strip

        def it_shows_studio_hours_alone_with_nothing_to_schedule(client: Client):
            guild = GuildFactory(name="Kilnwrights")
            start = timezone.now() + timedelta(days=2)
            CommunityEventFactory(
                studio_hours=True,
                guild=guild,
                title="Open Kiln Hours",
                starts_at=start,
                ends_at=start + timedelta(hours=3),
            )
            _lead_client(client, guild)
            content = client.get(reverse("hub_meetings")).content.decode()
            table = content.split('class="pl-meeting-dash"')[1].split("</table>")[0]
            assert "Open Kiln Hours" in table
            assert "Schedule it" not in content
            assert "pl-meeting-attention__row--start" not in content

        def it_shows_a_google_event_that_names_the_guild(client: Client):
            from membership.models import CalendarEvent

            GuildFactory(name="Potluckers Guild")
            start = timezone.now() + timedelta(days=4)
            CalendarEvent.objects.create(
                uid="potluck@google.com",
                source=CalendarEvent.Source.GENERAL,
                title="Monthly Pot Luck sponsored by Potluckers Guild",
                start_dt=start,
                end_dt=start + timedelta(hours=2),
                fetched_at=timezone.now(),
            )
            _member_client(client)
            table = client.get(reverse("hub_meetings")).content.decode().split('class="pl-meeting-dash"')[1]
            assert "Monthly Pot Luck sponsored by Potluckers Guild" in table.split("</table>")[0]

        def it_reads_every_guild_row_in_the_same_number_of_queries(client: Client):
            _member_client(client)
            GuildFactory(name="Batch A")
            client.get(reverse("hub_meetings"))  # warm caches
            with CaptureQueriesContext(connection) as small_ctx:
                client.get(reverse("hub_meetings"))
            for n in range(5):
                GuildFactory(name=f"Batch More {n}")
            with CaptureQueriesContext(connection) as big_ctx:
                client.get(reverse("hub_meetings"))
            assert len(big_ctx.captured_queries) == len(small_ctx.captured_queries)

        def it_shows_a_dash_when_there_is_neither_meeting_nor_cadence(client: Client):
            GuildFactory(name="Quiet")
            _member_client(client)
            content = client.get(reverse("hub_meetings")).content.decode()
            table = content.split('class="pl-meeting-dash"')[1].split("</table>")[0]
            assert "—" in table

    def describe_zone_3_archive():
        def it_lists_past_meetings_and_undated_drafts_with_no_date_last(client: Client):
            _member_client(client)
            dated = MeetingFactory(
                guild=GuildFactory(name="Dated"), scheduled_date=timezone.localdate() - timedelta(days=3)
            )
            undated = MeetingFactory(guild=GuildFactory(name="Undated"), scheduled_date=None)
            content = client.get(reverse("hub_meetings")).content.decode()
            archive = content.split(">Archive<")[1]
            assert "No date" in archive
            assert archive.index(dated.display_title) < archive.index(undated.display_title)

        def it_lists_by_date_and_guild_with_no_status_column(client: Client):
            _member_client(client)
            MeetingFactory(
                guild=GuildFactory(name="Pubbed"),
                scheduled_date=timezone.localdate() - timedelta(days=3),
                published=True,
            )
            MeetingFactory(
                guild=GuildFactory(name="Approvd"),
                scheduled_date=timezone.localdate() - timedelta(days=4),
                approved=True,
            )
            content = client.get(reverse("hub_meetings")).content.decode()
            archive = content.split(">Archive<")[1]
            assert "<th>Meeting</th><th>Date</th><th>Guild</th></tr>" in archive
            assert "<th>Status</th>" not in archive
            for status in ("Draft", "Published", "Approved"):
                assert f">{status}</span>" not in archive
                assert f"✓ {status}" not in archive
            assert '<span class="hub-badge">Pubbed</span>' in archive

        def it_keeps_undated_drafts_in_every_year_filter(client: Client):
            _member_client(client)
            MeetingFactory(guild=GuildFactory(name="Dated"), scheduled_date=timezone.localdate() - timedelta(days=3))
            undated = MeetingFactory(guild=GuildFactory(name="Undated"), scheduled_date=None)
            content = client.get(reverse("hub_meetings"), {"year": "1999"}).content.decode()
            archive = content.split(">Archive<")[1]
            assert undated.display_title in archive
            assert "Dated — Monthly Meeting" not in archive

        def it_filters_by_guild_and_by_council(client: Client):
            _member_client(client)
            mine = MeetingFactory(
                guild=GuildFactory(name="Filtered"), scheduled_date=timezone.localdate() - timedelta(days=3)
            )
            council = MeetingFactory(guild=None, scheduled_date=timezone.localdate() - timedelta(days=4))
            by_guild = client.get(reverse("hub_meetings"), {"guild": str(mine.guild.pk)}).content.decode()
            assert mine.display_title in by_guild.split(">Archive<")[1]
            assert "Council — Monthly Meeting" not in by_guild.split(">Archive<")[1]
            by_council = client.get(reverse("hub_meetings"), {"guild": "council"}).content.decode()
            assert council.display_title in by_council.split(">Archive<")[1]
            assert "Filtered — Monthly Meeting" not in by_council.split(">Archive<")[1]

        def it_paginates_at_25_per_page_preserving_filters(client: Client):
            _member_client(client)
            guild = GuildFactory(name="Paged")
            for offset in range(26):
                MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=offset + 1))
            content = client.get(reverse("hub_meetings"), {"guild": str(guild.pk)}).content.decode()
            assert "Page 1 of 2" in content
            assert f"?page=2&guild={guild.pk}" in content

        def it_shows_the_two_empty_states(client: Client):
            _member_client(client)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "Nothing here yet. Past meetings show up here." in content
            filtered = client.get(reverse("hub_meetings"), {"guild": "council"}).content.decode()
            assert "No meetings match those filters." in filtered
            assert "Clear filters" in filtered

    def describe_the_create_modal():
        def _scope_select(content: str) -> str:
            return content.split('name="scope"')[1].split("</select>")[0]

        def it_offers_a_lead_their_guild_plus_council(client: Client):
            guild = GuildFactory(name="Leadable")
            GuildFactory(name="Foreign")
            _lead_client(client, guild)
            content = client.get(reverse("hub_meetings")).content.decode()
            select = _scope_select(content)
            assert "Leadable" in select
            assert "Council" in select
            assert "Foreign" not in select

        def it_offers_an_admin_every_guild_plus_council(client: Client):
            GuildFactory(name="Alpha")
            GuildFactory(name="Beta")
            _admin_client(client)
            select = _scope_select(client.get(reverse("hub_meetings")).content.decode())
            assert "Alpha" in select
            assert "Beta" in select
            assert "Council" in select

        def it_renders_no_modal_for_a_plain_member(client: Client):
            _member_client(client)
            content = client.get(reverse("hub_meetings")).content.decode()
            assert "+ New meeting" not in content
            assert 'name="scope"' not in content

        def it_preselects_the_scope_from_the_guild_query_param(client: Client):
            guild = GuildFactory(name="Presel")
            _lead_client(client, guild)
            content = client.get(reverse("hub_meetings"), {"guild": str(guild.pk)}).content.decode()
            select = _scope_select(content)
            assert f'value="{guild.pk}" selected' in select


@pytest.mark.django_db
def describe_guild_meetings_tab():
    def it_renames_the_tab_and_maps_the_legacy_notes_deep_link(client: Client):
        guild = GuildFactory()
        _member_client(client)
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "section === 'meetings'" in content
        assert "section === 'notes'" not in content
        assert "t === 'notes'" in content  # the ?tab=notes → meetings mapping in the Alpine init

    def describe_the_next_meeting_card():
        def it_shows_the_soonest_upcoming_meeting_with_join_and_no_topic_count(client: Client):
            guild = GuildFactory()
            _guild_member_client(client, guild)
            MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=20))
            soonest = MeetingFactory(
                guild=guild,
                scheduled_date=timezone.localdate() + timedelta(days=5),
                scheduled_time=time(18, 0),
                video_call_url="https://meet.example/guild-tab",
            )
            MeetingAgendaItemFactory(meeting=soonest)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            card = content.split('<h3 class="hub-detail-label">Next meeting</h3>')[1].split("Past meetings")[0]
            assert reverse("hub_meeting", args=[soonest.pk]) in card
            assert "6:00 PM" in card
            assert "topic" not in card
            assert 'href="https://meet.example/guild-tab"' in card
            assert "Propose an agenda item" not in content

        def it_shows_no_propose_button_or_locked_hint_to_the_lead(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            MeetingFactory(guild=guild, approved=True)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            assert "Propose an agenda item" not in content
            assert "The agenda is locked" not in content
            assert 'id="propose-item-title"' not in content

        def it_falls_back_to_the_cadence_with_schedule_it_for_editors(client: Client):
            guild = _cadence_guild("CadeLead")
            _lead_client(client, guild)
            occurrence = guild.next_meeting_occurrence()
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            assert "Next per schedule:" in content
            assert "Schedule it" in content
            assert "Start the agenda" not in content
            assert f'name="date" value="{occurrence.when:%Y-%m-%d}"' in content
            assert 'name="time" value="18:30"' in content

        def it_suggests_the_meeting_though_studio_hours_come_sooner(client: Client):
            # Schedule it follows the soonest meeting, not the soonest of anything (#953).
            guild = _cadence_guild("LoomLead")
            start = timezone.now() + timedelta(hours=2)
            CommunityEventFactory(
                studio_hours=True,
                guild=guild,
                title="Open Loom Hours",
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=start,
                ends_at=start + timedelta(hours=3),
            )
            _lead_client(client, guild)
            occurrence = guild.next_meeting_occurrence(meetings_only=True)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            card = content.split('<h3 class="hub-detail-label">Next meeting</h3>')[1].split("Past meetings")[0]
            assert "Next per schedule:" in card
            assert "Open Loom Hours" not in card
            assert "Schedule it" in card
            assert f'name="date" value="{occurrence.when:%Y-%m-%d}"' in card

        def it_names_a_guild_meeting_event_by_its_title(client: Client):
            guild = GuildFactory()
            start = timezone.now() + timedelta(days=2)
            CommunityEventFactory(
                guild_meeting=True,
                guild=guild,
                title="Monthly Gathering",
                starts_at=start,
                ends_at=start + timedelta(hours=1),
            )
            _guild_member_client(client, guild)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            card = content.split('<h3 class="hub-detail-label">Next meeting</h3>')[1].split("Past meetings")[0]
            assert "Monthly Gathering: " in card

        def it_says_no_meeting_when_the_guild_has_only_studio_hours(client: Client):
            guild = GuildFactory()
            start = timezone.now() + timedelta(days=2)
            CommunityEventFactory(
                studio_hours=True,
                guild=guild,
                title="Open Loom Hours",
                starts_at=start,
                ends_at=start + timedelta(hours=3),
            )
            _lead_client(client, guild)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            card = content.split('<h3 class="hub-detail-label">Next meeting</h3>')[1].split("Past meetings")[0]
            assert "No meeting scheduled yet." in card
            assert "Schedule it" not in card

        def it_shows_members_the_schedule_with_no_button(client: Client):
            guild = _cadence_guild("CadeMember")
            _guild_member_client(client, guild)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            assert "Next per schedule:" in content
            assert "Proposals open" not in content
            assert "Schedule it" not in content

        def it_shows_the_double_empty_state(client: Client):
            guild = GuildFactory()
            _guild_member_client(client, guild)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            assert "No meeting scheduled yet." in content

    def describe_past_meetings():
        def it_lists_the_last_five_past_meetings_newest_first_with_no_status(client: Client):
            guild = GuildFactory(name="Pastry")
            today = timezone.localdate()
            past = [MeetingFactory(guild=guild, scheduled_date=today - timedelta(days=d)) for d in range(1, 7)]
            MeetingFactory(guild=guild, scheduled_date=today - timedelta(days=8), approved=True)
            MeetingFactory(guild=guild, scheduled_date=None)
            upcoming = MeetingFactory(guild=guild, scheduled_date=today + timedelta(days=3))
            _guild_member_client(client, guild)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            card = content.split('<h3 class="hub-detail-label">Past meetings</h3>')[1].split("See All in the Archive")[
                0
            ]
            links = [reverse("hub_meeting", args=[m.pk]) for m in past[:5]]
            assert [card.index(link) for link in links] == sorted(card.index(link) for link in links)
            assert reverse("hub_meeting", args=[past[5].pk]) not in card
            assert reverse("hub_meeting", args=[upcoming.pk]) not in card
            assert "pl-meeting-badge" not in card
            assert "minutes" not in card.lower()

        def it_shows_the_empty_state(client: Client):
            guild = GuildFactory()
            _guild_member_client(client, guild)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            assert "No past meetings yet." in content
            assert "No approved minutes yet." not in content

    def describe_editor_extras():
        def it_gives_editors_the_new_meeting_entry_and_no_pending_chip(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            MeetingItemProposalFactory(meeting=meeting)
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            assert "+ New meeting" in content
            assert f"/meetings/?guild={guild.pk}" in content
            assert "proposal pending" not in content

        def it_hides_the_editor_extras_from_members(client: Client):
            guild = GuildFactory()
            _guild_member_client(client, guild)
            MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild))
            content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            assert "+ New meeting" not in content
            assert "proposal pending" not in content


@pytest.mark.django_db
def describe_calendar_rails():
    def describe_the_create_endpoint():
        def it_creates_an_owned_event_and_pushes_it_live(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live") as go_live:
                resp = client.post(reverse("hub_meeting_event", args=[meeting.pk]))
            assert resp.status_code == 204
            assert resp["HX-Redirect"] == reverse("hub_meeting", args=[meeting.pk])
            go_live.assert_called_once()
            meeting.refresh_from_db()
            assert meeting.event is not None
            assert meeting.owns_event is True
            assert meeting.event_occurrence == meeting.scheduled_date
            assert "On the calendar. Its announcement is waiting in your Announcements drafts." in _messages(resp)

        def it_422s_without_a_date_and_time(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=None)
            resp = client.post(reverse("hub_meeting_event", args=[meeting.pk]))
            assert resp.status_code == 422
            assert "HX-Trigger" in resp.headers  # the error toast
            meeting.refresh_from_db()
            assert meeting.event is None

        def it_422s_when_already_linked(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(
                guild=guild,
                scheduled_time=time(18, 0),
                event=event,
                event_occurrence=timezone.localtime(event.starts_at).date(),
            )
            assert client.post(reverse("hub_meeting_event", args=[meeting.pk])).status_code == 422

        def it_403s_a_plain_member(client: Client):
            guild = GuildFactory()
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            _member_client(client)
            assert client.post(reverse("hub_meeting_event", args=[meeting.pk])).status_code == 403
            assert client.post(reverse("hub_meeting_event_unlink", args=[meeting.pk])).status_code == 403

    def describe_the_link_existing_path():
        def it_links_a_scope_event_without_owning_it(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(guild=guild)
            resp = client.post(reverse("hub_meeting_event", args=[meeting.pk]), {"event": str(event.pk)})
            assert resp.status_code == 204
            meeting.refresh_from_db()
            assert meeting.event == event
            assert meeting.owns_event is False
            assert meeting.event_occurrence == timezone.localtime(event.starts_at).date()

        def it_links_a_recurring_event_at_the_posted_occurrence(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild, recurrence=CommunityEvent.Recurrence.MONTHLY)
            meeting = MeetingFactory(guild=guild)
            horizon = timezone.localdate() + timedelta(days=90)
            occurrences = [
                timezone.localtime(when).date() for when in event.occurrences_in(timezone.localdate(), horizon)
            ]
            chosen = occurrences[1]
            resp = client.post(
                reverse("hub_meeting_event", args=[meeting.pk]),
                {"event": str(event.pk), "occurrence": chosen.isoformat()},
            )
            assert resp.status_code == 204
            meeting.refresh_from_db()
            assert meeting.event_occurrence == chosen

        def it_422s_an_occurrence_that_is_not_one_of_the_events(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(guild=guild)
            bad = timezone.localtime(event.starts_at).date() + timedelta(days=1)
            resp = client.post(
                reverse("hub_meeting_event", args=[meeting.pk]),
                {"event": str(event.pk), "occurrence": bad.isoformat()},
            )
            assert resp.status_code == 422
            meeting.refresh_from_db()
            assert meeting.event is None

        def it_404s_an_event_outside_the_meetings_scope(client: Client):
            guild = GuildFactory()
            other = GuildFactory()
            _lead_client(client, guild)
            foreign = CommunityEventFactory(guild=other)
            meeting = MeetingFactory(guild=guild)
            resp = client.post(reverse("hub_meeting_event", args=[meeting.pk]), {"event": str(foreign.pk)})
            assert resp.status_code == 404

        def it_400s_a_garbage_event_pk(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            assert client.post(reverse("hub_meeting_event", args=[meeting.pk]), {"event": "nope"}).status_code == 400

    def describe_the_unlink_endpoint():
        def it_clears_the_link_and_leaves_the_event_alone(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(
                guild=guild, event=event, event_occurrence=timezone.localtime(event.starts_at).date(), owns_event=True
            )
            resp = client.post(reverse("hub_meeting_event_unlink", args=[meeting.pk]))
            assert resp.status_code == 204
            meeting.refresh_from_db()
            assert meeting.event is None
            assert meeting.event_occurrence is None
            assert meeting.owns_event is False
            assert CommunityEvent.objects.filter(pk=event.pk).exists()  # never deleted

        def it_422s_when_nothing_is_linked(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            assert client.post(reverse("hub_meeting_event_unlink", args=[meeting.pk])).status_code == 422

        def it_403s_non_editors(client: Client):
            guild = GuildFactory()
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(
                guild=guild, event=event, event_occurrence=timezone.localtime(event.starts_at).date()
            )
            _member_client(client)
            assert client.post(reverse("hub_meeting_event_unlink", args=[meeting.pk])).status_code == 403

    def describe_the_workspace_calendar_block():
        def it_offers_add_to_calendar_on_an_unlinked_draft(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "'add-to-calendar'" in content  # the modal trigger (the bare phrase appears in changelog copy)
            assert "Create calendar event" in content
            assert "Set a date and time first." not in content

        def it_disables_create_with_the_hint_until_date_and_time_are_set(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=None)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Set a date and time first." in content

        def it_lists_the_scopes_upcoming_events_with_occurrence_selects_for_recurring(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            CommunityEventFactory(guild=guild, title="One-off Social")
            # Still "upcoming" (hasn't ended) but its start date is behind us: no
            # linkable occurrence, so it must not clutter the select. Use a full day
            # back, not a sub-24h offset: a 20-hour offset lands on *today* whenever the
            # suite runs after 20:00 local, which spuriously gives the event a today
            # occurrence and reddens this assertion (a nightly wall-clock flake).
            CommunityEventFactory(
                guild=guild,
                title="Started Yesterday",
                starts_at=timezone.now() - timedelta(days=1),
                ends_at=timezone.now() + timedelta(hours=4),
            )
            CommunityEventFactory(guild=guild, title="Standing Sync", recurrence=CommunityEvent.Recurrence.MONTHLY)
            CommunityEventFactory(guild=GuildFactory(name="Foreign"), title="Foreign Party")
            meeting = MeetingFactory(guild=guild)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "One-off Social" in content
            assert "Standing Sync" in content
            assert "Foreign Party" not in content
            assert "Started Yesterday" not in content
            assert "Which occurrence?" in content
            assert "Link this event" in content

        def it_shows_the_linked_line_with_unlink_and_the_guild_hint(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild, title="Woodshop Monthly")
            occurrence = timezone.localtime(event.starts_at).date()
            meeting = MeetingFactory(
                guild=guild,
                scheduled_date=occurrence,
                scheduled_time=timezone.localtime(event.starts_at).time(),
                event=event,
                event_occurrence=occurrence,
            )
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "On the calendar ✓" in content
            assert "Woodshop Monthly" in content
            assert reverse("hub_event_detail", args=[event.pk]) in content
            assert "Unlink" in content
            assert "edit or cancel it from the guild's Events tab." in content
            assert "'add-to-calendar'" not in content

        def it_uses_the_council_copy_for_a_council_meeting(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)  # any-guild lead edits council meetings
            event = CommunityEventFactory(lead_meeting=True)
            occurrence = timezone.localtime(event.starts_at).date()
            meeting = MeetingFactory(guild=None, event=event, event_occurrence=occurrence)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "an admin can edit or cancel it from the events editor." in content

        def it_shows_no_mismatch_warning_for_an_owned_synced_link(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting_date = timezone.localdate() + timedelta(days=7)
            starts = timezone.make_aware(datetime.combine(meeting_date, time(18, 0)))
            event = CommunityEventFactory(guild=guild, starts_at=starts)
            meeting = MeetingFactory(
                guild=guild,
                scheduled_date=meeting_date,
                scheduled_time=time(18, 0),
                event=event,
                event_occurrence=meeting_date,
                owns_event=True,
            )
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "unlink, or edit the event to match." not in content

        def it_warns_when_a_merely_linked_event_no_longer_matches(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting_date = timezone.localdate() + timedelta(days=7)
            starts = timezone.make_aware(datetime.combine(meeting_date, time(18, 0)))
            event = CommunityEventFactory(guild=guild, starts_at=starts)
            meeting = MeetingFactory(
                guild=guild,
                scheduled_date=meeting_date,
                scheduled_time=time(19, 0),  # drifted from the event's 6 PM
                event=event,
                event_occurrence=meeting_date,
                owns_event=False,
            )
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Calendar event shows" in content
            assert "unlink, or edit the event to match." in content

        def it_warns_on_a_cleared_date_under_an_owned_link(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            event = CommunityEventFactory(guild=guild)
            meeting = MeetingFactory(
                guild=guild,
                scheduled_date=None,
                event=event,
                event_occurrence=timezone.localtime(event.starts_at).date(),
                owns_event=True,
            )
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "unlink, or edit the event to match." in content

        def it_shows_read_only_viewers_the_line_without_unlink(client: Client):
            guild = GuildFactory()
            _member_client(client)
            event = CommunityEventFactory(guild=guild, title="Readable Link")
            occurrence = timezone.localtime(event.starts_at).date()
            meeting = MeetingFactory(
                guild=guild,
                scheduled_date=occurrence,
                scheduled_time=timezone.localtime(event.starts_at).time(),
                event=event,
                event_occurrence=occurrence,
            )
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "On the calendar ✓" in content
            assert "Unlink" not in content
            assert "'add-to-calendar'" not in content


@pytest.mark.django_db
def describe_retired_routes():
    def it_resolves_none_of_the_removed_meeting_url_names():
        from django.urls import NoReverseMatch

        retired = [
            "hub_meeting_publish",
            "hub_meeting_unpublish",
            "hub_meeting_approve",
            "hub_meeting_unlock",
            "hub_meeting_item_add",
            "hub_meeting_item_save",
            "hub_meeting_item_move",
            "hub_meeting_item_delete",
            "hub_meeting_item_upvote",
            "hub_meeting_action_add",
            "hub_meeting_action_save",
            "hub_meeting_action_delete",
            "hub_meeting_action_carryover",
            "hub_meeting_attachment_add",
            "hub_meeting_attachment_delete",
            "hub_meeting_attendee_add",
            "hub_meeting_attendee_save",
            "hub_meeting_attendee_delete",
            "hub_meeting_propose",
            "hub_meeting_proposal_decide",
            "hub_meeting_proposal_withdraw",
        ]
        for name in retired:
            with pytest.raises(NoReverseMatch):
                reverse(name, args=[1])

    def it_404s_the_old_paths(client: Client):
        _admin_client(client)
        meeting = MeetingFactory()
        for path in (
            f"/meetings/{meeting.pk}/approve/",
            f"/meetings/{meeting.pk}/publish/",
            f"/meetings/{meeting.pk}/items/add/",
            f"/meetings/{meeting.pk}/propose/",
            f"/meetings/{meeting.pk}/attendees/add/",
            f"/meetings/{meeting.pk}/attachments/add/",
        ):
            assert client.post(path).status_code == 404, path
