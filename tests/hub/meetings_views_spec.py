"""BDD specs for the meeting workspace views (Meetings spec §6.2/§6.3 — phases 2+3).

The workspace GET rendering matrix (draft/locked × editor/read-only, incl. the
strictly-static attendance), the create endpoint (§6.2 prefill contract +
double-create guard + forbidden-scope 403), the add/delete endpoints, and the
phase-3 lifecycle (approve / unlock / delete), carryover, and proposal flows
(propose / decide / withdraw) with their template-state assertions. The home and
guild-tab surfaces are phase 4.
"""

from __future__ import annotations

from datetime import time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import EventDelivery
from membership.models import (
    CommunityEvent,
    GuildStaffMembership,
    Meeting,
    MeetingActionItem,
    MeetingAttachment,
    MeetingItemProposal,
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
    MemberFactory,
    MembershipPlanFactory,
    UserFactory,
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


@pytest.mark.django_db
def describe_workspace_get():
    def describe_draft_for_an_editor():
        def it_renders_the_editable_workspace(client: Client):
            guild = GuildFactory(name="Woodshop")
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert resp.status_code == 200
            content = resp.content.decode()
            assert "Woodshop — Monthly Meeting" in content
            assert "pl-meeting-savestate" in content  # the autosave pill
            assert "Special meeting" in content  # the toggle
            assert "Time TBD" in content  # the half-hour select's blank choice
            assert '<input type="time"' not in content  # rule 19
            assert "+ Add item" in content
            assert "pl-meeting-attendee-picker" in content
            assert "+ Attach file or link" in content
            assert "Print / Save as PDF" in content
            assert "data-autosave" in content

        def it_renders_the_agenda_items_with_open_action_badges(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            item = MeetingAgendaItemFactory(meeting=meeting, name="Budget review")
            MeetingActionItemFactory(item=item)
            MeetingActionItemFactory(item=item, status=MeetingActionItem.Status.DONE)
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            content = resp.content.decode()
            assert "Budget review" in content
            assert "1★" in content  # only OPEN actions count

        def it_credits_the_proposer_on_an_item(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposer = UserFactory(username="steven")
            MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild), proposed_by=proposer)
            meeting = Meeting.objects.get()
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert "Proposed by" in resp.content.decode()

        def it_shows_the_editor_empty_state_for_a_bare_agenda(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert "No agenda topics yet." in resp.content.decode()

        def it_keeps_the_query_count_flat_as_the_agenda_grows(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            small = MeetingFactory(guild=guild)
            item = MeetingAgendaItemFactory(meeting=small, proposed_by=UserFactory())
            MeetingActionItemFactory(item=item)
            MeetingAttendeeFactory(meeting=small)
            big = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=30))
            for _ in range(5):
                big_item = MeetingAgendaItemFactory(meeting=big, proposed_by=UserFactory())
                MeetingActionItemFactory(item=big_item)
                MeetingActionItemFactory(item=big_item)
                MeetingAttendeeFactory(meeting=big)
            client.get(reverse("hub_meeting", args=[small.pk]))  # warm caches
            with CaptureQueriesContext(connection) as small_ctx:
                client.get(reverse("hub_meeting", args=[small.pk]))
            with CaptureQueriesContext(connection) as big_ctx:
                client.get(reverse("hub_meeting", args=[big.pk]))
            assert len(small_ctx) == len(big_ctx)

    def describe_draft_for_a_plain_member():
        def it_renders_read_only_with_no_autosave_surface(client: Client):
            guild = GuildFactory()
            _member_client(client)
            meeting = MeetingFactory(guild=guild, special_notes="<p>Read me first</p>")
            MeetingAgendaItemFactory(meeting=meeting, name="Budget")
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert resp.status_code == 200
            content = resp.content.decode()
            assert "Budget" in content
            assert "Read me first" in content
            assert "pl-meeting-savestate" not in content
            assert "data-autosave" not in content
            assert "+ Add item" not in content
            assert "+ Attach file or link" not in content

        def it_renders_the_read_only_agenda_empty_state(client: Client):
            _member_client(client)
            meeting = MeetingFactory()
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert "The agenda hasn't been written yet." in resp.content.decode()

        def it_renders_attendance_as_a_strictly_static_list(client: Client):
            guild = GuildFactory()
            _member_client(client)
            meeting = MeetingFactory(guild=guild)
            present = MeetingAttendeeFactory(meeting=meeting, member__full_legal_name="Here Person")
            MeetingAttendeeFactory(meeting=meeting, guest=True, present=False)
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            content = resp.content.decode()
            assert "Here Person" in content
            assert "pl-meeting-attendee--static" in content
            assert "pl-meeting-attendee--absent" in content  # strikethrough absentee
            assert "pl-meeting-attendee-picker" not in content  # no add row
            assert "pl-meeting-check" not in content  # no checkboxes
            assert "pl-meeting-row-delete" not in content  # no × buttons
            assert present.display_name in content

        def it_hides_the_empty_notes_sections(client: Client):
            _member_client(client)
            meeting = MeetingFactory(special_notes="", other_notes="")
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            content = resp.content.decode()
            assert "Notes for this meeting" not in content
            assert "Other discussion" not in content

    def describe_locked_mode():
        def it_renders_read_only_even_for_an_editor(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, approved=True, other_notes="<p>Off agenda</p>")
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            assert resp.status_code == 200
            content = resp.content.decode()
            assert "Off agenda" in content
            assert "pl-meeting-savestate" not in content
            assert "data-autosave" not in content
            assert "+ Add item" not in content
            assert "pl-meeting-attendee-picker" not in content
            assert "Print / Save as PDF" in content  # both modes

        def it_renders_static_facts(client: Client):
            guild = GuildFactory()
            _member_client(client)
            meeting = MeetingFactory(
                guild=guild, approved=True, video_call_url="https://meet.example.com/z", scheduled_time=None
            )
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            content = resp.content.decode()
            assert "Time TBD" in content
            assert "Join meeting" in content
            assert "https://meet.example.com/z" in content

    def it_renders_a_council_meeting_workspace(client: Client):
        user = _user_with_role("councilstaff")
        guild = GuildFactory()
        GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
        client.login(username="councilstaff", password="pass")
        meeting = MeetingFactory(guild=None)
        resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
        content = resp.content.decode()
        assert "Council — Monthly Meeting" in content
        assert "pl-meeting-savestate" in content  # any-guild staff edit the council scope

    def it_offers_all_guild_leadership_in_the_council_roster_picker(client: Client):
        user = _user_with_role("councilstaff2")
        guild = GuildFactory()
        GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
        other_guild = GuildFactory()
        lead = MemberFactory(full_legal_name="Lena Lead")
        other_guild.guild_lead = lead
        other_guild.save(update_fields=["guild_lead"])
        outsider = MemberFactory(full_legal_name="Otis Outsider")
        outsider.guild_memberships.create(guild=other_guild)
        client.login(username="councilstaff2", password="pass")
        meeting = MeetingFactory(guild=None)
        resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
        content = resp.content.decode()
        assert "Lena Lead" in content
        assert "Otis Outsider" not in content  # plain members are not council roster

    def it_excludes_already_added_members_from_the_guild_roster_picker(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        added = MemberFactory(full_legal_name="Alma Added")
        added.guild_memberships.create(guild=guild)
        available = MemberFactory(full_legal_name="Vera Available")
        available.guild_memberships.create(guild=guild)
        meeting = MeetingFactory(guild=guild)
        MeetingAttendeeFactory(meeting=meeting, member=added)
        resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
        content = resp.content.decode()
        picker_html = content.split('id="pl-meeting-attendee-picker"')[1].split("</select>")[0]
        assert "Vera Available" in picker_html
        assert "Alma Added" not in picker_html

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

    def it_prefills_date_and_time_from_the_start_the_agenda_contract(client: Client):
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


@pytest.mark.django_db
def describe_item_add():
    def it_appends_an_empty_item_that_arrives_auto_expanded(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_item_add", args=[meeting.pk]))
        assert resp.status_code == 200
        item = meeting.items.get()
        assert item.name == ""  # create-empty-then-fill
        content = resp.content.decode()
        assert f'id="item-{item.pk}"' in content
        assert f"openAgendaItem({item.pk}, true)" in content  # auto-expand + focus
        assert "meeting-saved" in resp["HX-Trigger"]

    def it_lands_new_items_at_the_bottom(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        first = MeetingAgendaItemFactory(meeting=meeting, name="First")
        client.post(reverse("hub_meeting_item_add", args=[meeting.pk]))
        new_item = meeting.items.exclude(pk=first.pk).get()
        assert new_item.sort_order > first.sort_order

    def it_403s_a_non_editor(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        assert client.post(reverse("hub_meeting_item_add", args=[meeting.pk])).status_code == 403


@pytest.mark.django_db
def describe_item_delete():
    def it_deletes_the_item_and_returns_an_empty_row_swap(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        item = MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild))
        resp = client.post(reverse("hub_meeting_item_delete", args=[item.pk]))
        assert resp.status_code == 200
        assert resp.content == b""
        assert "showToast" in resp["HX-Trigger"]
        assert not Meeting.objects.get().items.exists()

    def it_cascades_to_its_actions(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        item = MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild))
        MeetingActionItemFactory(item=item)
        client.post(reverse("hub_meeting_item_delete", args=[item.pk]))
        assert not MeetingActionItem.objects.exists()


@pytest.mark.django_db
def describe_action_add():
    def it_appends_an_empty_action_row_that_focuses_its_name(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        item = MeetingAgendaItemFactory(meeting=MeetingFactory(guild=guild))
        resp = client.post(reverse("hub_meeting_action_add", args=[item.pk]))
        assert resp.status_code == 200
        action = item.actions.get()
        assert action.name == ""
        content = resp.content.decode()
        assert f'id="action-{action.pk}"' in content
        assert ".focus()" in content  # the focus_new x-init
        assert "meeting-saved" in resp["HX-Trigger"]

    def it_403s_a_non_editor(client: Client):
        _member_client(client)
        item = MeetingAgendaItemFactory()
        assert client.post(reverse("hub_meeting_action_add", args=[item.pk])).status_code == 403


@pytest.mark.django_db
def describe_action_delete():
    def it_deletes_the_action(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        action = MeetingActionItemFactory(item__meeting=MeetingFactory(guild=guild))
        resp = client.post(reverse("hub_meeting_action_delete", args=[action.pk]))
        assert resp.status_code == 200
        assert not MeetingActionItem.objects.exists()


@pytest.mark.django_db
def describe_attendee_delete():
    def it_removes_the_row_with_a_named_toast(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        attendee = MeetingAttendeeFactory(meeting=MeetingFactory(guild=guild), guest=True, guest_name="Sam Visitor")
        resp = client.post(reverse("hub_meeting_attendee_delete", args=[attendee.pk]))
        assert resp.status_code == 200
        assert "Sam Visitor" in resp["HX-Trigger"]
        assert not Meeting.objects.get().attendees.exists()

    def it_403s_when_the_meeting_is_locked(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        attendee = MeetingAttendeeFactory(meeting=MeetingFactory(guild=guild, approved=True))
        assert client.post(reverse("hub_meeting_attendee_delete", args=[attendee.pk])).status_code == 403


@pytest.mark.django_db
def describe_attachment_add():
    def it_adds_a_link_attachment_and_redirects_back_to_the_workspace(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(
            reverse("hub_meeting_attachment_add", args=[meeting.pk]),
            {"label": "Agenda doc", "url": "https://docs.example.com/agenda"},
        )
        assert resp.status_code == 302
        assert resp["Location"] == reverse("hub_meeting", args=[meeting.pk])
        attachment = meeting.attachments.get()
        assert attachment.is_link
        assert attachment.display_name == "Agenda doc"

    def it_adds_a_file_attachment(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        upload = SimpleUploadedFile("budget.pdf", b"%PDF-1.4 test", "application/pdf")
        resp = client.post(reverse("hub_meeting_attachment_add", args=[meeting.pk]), {"file": upload})
        assert resp.status_code == 302
        assert meeting.attachments.get().is_file

    def it_re_renders_the_workspace_with_the_xor_error_and_the_modal_open(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_attachment_add", args=[meeting.pk]), {"label": "Nothing"})
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "Pick a file or paste a link, not both." in content
        assert "{ open: true }" in content  # the modal re-opens showing the error
        assert not meeting.attachments.exists()

    def it_rejects_both_file_and_link(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        upload = SimpleUploadedFile("budget.pdf", b"%PDF-1.4 test", "application/pdf")
        resp = client.post(
            reverse("hub_meeting_attachment_add", args=[meeting.pk]),
            {"file": upload, "url": "https://docs.example.com/x"},
        )
        assert resp.status_code == 200
        assert not meeting.attachments.exists()

    def it_403s_a_non_editor(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        resp = client.post(
            reverse("hub_meeting_attachment_add", args=[meeting.pk]), {"url": "https://docs.example.com/x"}
        )
        assert resp.status_code == 403


@pytest.mark.django_db
def describe_attachment_delete():
    def it_deletes_the_row(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        attachment = MeetingAttachment.objects.create(meeting=meeting, url="https://docs.example.com/x")
        resp = client.post(reverse("hub_meeting_attachment_delete", args=[attachment.pk]))
        assert resp.status_code == 200
        assert not meeting.attachments.exists()

    def it_403s_when_the_meeting_is_locked(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, approved=True)
        attachment = MeetingAttachment.objects.create(meeting=meeting, url="https://docs.example.com/x")
        assert client.post(reverse("hub_meeting_attachment_delete", args=[attachment.pk])).status_code == 403


@pytest.mark.django_db
def describe_workspace_rendering_of_attachments():
    def it_lists_attachments_with_per_row_delete_confirms_for_editors(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        attachment = MeetingAttachment.objects.create(meeting=meeting, label="Slides", url="https://docs.example.com/s")
        resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
        content = resp.content.decode()
        assert "Slides" in content
        assert f"del-attachment-{attachment.pk}" in content

    def it_shows_links_only_in_read_only_mode(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        attachment = MeetingAttachment.objects.create(meeting=meeting, label="Slides", url="https://docs.example.com/s")
        resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
        content = resp.content.decode()
        assert "Slides" in content
        assert f"del-attachment-{attachment.pk}" not in content

    def it_hides_the_whole_block_from_read_only_viewers_when_empty(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
        assert "Attachments" not in resp.content.decode()


# --- Phase 3: lifecycle (approve / unlock / delete) ---------------------------


@pytest.mark.django_db
def describe_approve():
    def it_locks_stamps_and_redirects_with_a_message(client: Client):
        guild = GuildFactory()
        user = _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_approve", args=[meeting.pk]))
        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("hub_meeting", args=[meeting.pk])
        assert "Minutes approved and locked." in _messages(resp)
        meeting.refresh_from_db()
        assert meeting.status == Meeting.Status.APPROVED
        assert meeting.approved_by == user
        assert meeting.approved_at is not None

    def it_auto_declines_pending_proposals_and_notifies_each_proposer(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        proposal = MeetingItemProposalFactory(meeting=meeting, proposed_by=UserFactory())
        client.post(reverse("hub_meeting_approve", args=[meeting.pk]))
        proposal.refresh_from_db()
        assert proposal.state == MeetingItemProposal.State.DECLINED
        assert proposal.review_note == "The meeting was closed before this was reviewed."
        assert EventDelivery.objects.filter(event_key="meeting.item_decided", channel="in_app").count() == 1

    def it_422s_an_undated_meeting_with_the_toast(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, scheduled_date=None)
        resp = client.post(reverse("hub_meeting_approve", args=[meeting.pk]))
        assert resp.status_code == 422
        assert "Set the meeting date before approving." in resp["HX-Trigger"]
        meeting.refresh_from_db()
        assert meeting.status == Meeting.Status.DRAFT

    def it_403s_a_non_editor(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        assert client.post(reverse("hub_meeting_approve", args=[meeting.pk])).status_code == 403

    def it_403s_when_already_locked(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, approved=True)
        assert client.post(reverse("hub_meeting_approve", args=[meeting.pk])).status_code == 403

    def it_405s_a_get(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        assert client.get(reverse("hub_meeting_approve", args=[meeting.pk])).status_code == 405


@pytest.mark.django_db
def describe_unlock():
    def it_reopens_the_workspace_for_an_admin_keeping_the_stamps(client: Client):
        _admin_client(client)
        meeting = MeetingFactory(approved=True)
        stamped_by = meeting.approved_by
        resp = client.post(reverse("hub_meeting_unlock", args=[meeting.pk]))
        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("hub_meeting", args=[meeting.pk])
        assert "Minutes unlocked — the workspace is editable again." in _messages(resp)
        meeting.refresh_from_db()
        assert meeting.status == Meeting.Status.DRAFT
        assert meeting.approved_by == stamped_by  # history kept until re-approve

    def it_403s_the_guilds_own_lead(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, approved=True)
        resp = client.post(reverse("hub_meeting_unlock", args=[meeting.pk]))
        assert resp.status_code == 403
        meeting.refresh_from_db()
        assert meeting.status == Meeting.Status.APPROVED

    def it_403s_a_plain_member(client: Client):
        _member_client(client)
        meeting = MeetingFactory(approved=True)
        assert client.post(reverse("hub_meeting_unlock", args=[meeting.pk])).status_code == 403

    def it_422s_a_meeting_that_is_not_locked(client: Client):
        _admin_client(client)
        meeting = MeetingFactory()
        resp = client.post(reverse("hub_meeting_unlock", args=[meeting.pk]))
        assert resp.status_code == 422
        assert "Only approved minutes can be unlocked." in resp["HX-Trigger"]


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
        with patch.object(CommunityEvent, "schedule_or_go_live"):
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
        meeting.link_event(event, event.starts_at.date(), by=user)
        with patch.object(CommunityEvent, "remove_from_google") as google:
            resp = client.post(reverse("hub_meeting_delete", args=[meeting.pk]))
        assert resp.status_code == 204
        google.assert_not_called()
        assert CommunityEvent.objects.filter(pk=event.pk).exists()
        assert not Meeting.objects.exists()

    def it_403s_locked_minutes(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=guild, approved=True)
        assert client.post(reverse("hub_meeting_delete", args=[meeting.pk])).status_code == 403
        assert Meeting.objects.filter(pk=meeting.pk).exists()

    def it_403s_a_non_editor(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        assert client.post(reverse("hub_meeting_delete", args=[meeting.pk])).status_code == 403
        assert Meeting.objects.filter(pk=meeting.pk).exists()


# --- Phase 3: the carryover endpoint (§5.5) -----------------------------------


def _carryover_setup(client: Client, *, source_approved: bool = False):
    """A source meeting with one open action, and a later draft target meeting."""
    guild = GuildFactory()
    user = _lead_client(client, guild)
    source = MeetingFactory(
        guild=guild, scheduled_date=timezone.localdate() + timedelta(days=1), approved=source_approved
    )
    action = MeetingActionItemFactory(item__meeting=source)
    target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
    return user, guild, source, action, target


@pytest.mark.django_db
def describe_carryover():
    def it_completes_the_action_stamping_closed_in(client: Client):
        _, _, _, action, target = _carryover_setup(client)
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "complete", "meeting": str(target.pk)},
        )
        assert resp.status_code == 200
        assert "Marked done." in resp["HX-Trigger"]
        action.refresh_from_db()
        assert action.status == MeetingActionItem.Status.DONE
        assert action.closed_in == target

    def it_dismisses_the_action(client: Client):
        _, _, _, action, target = _carryover_setup(client)
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "dismiss", "meeting": str(target.pk)},
        )
        assert resp.status_code == 200
        assert "Dismissed." in resp["HX-Trigger"]
        action.refresh_from_db()
        assert action.status == MeetingActionItem.Status.DISMISSED
        assert action.closed_in == target

    def it_removes_the_panel_once_the_last_row_closes(client: Client):
        _, _, _, action, target = _carryover_setup(client)
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "complete", "meeting": str(target.pk)},
        )
        assert "pl-meeting-carryover" not in resp.content.decode()

    def it_re_renders_the_remaining_rows(client: Client):
        _, _, source, action, target = _carryover_setup(client)
        other = MeetingActionItemFactory(item=action.item, name="Still open")
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "complete", "meeting": str(target.pk)},
        )
        content = resp.content.decode()
        assert f'id="carryover-{other.pk}"' in content
        assert "Still open" in content
        assert f"From {source.display_title}" in content

    def it_allows_closing_an_action_from_a_locked_source_meeting(client: Client):
        _, _, _, action, target = _carryover_setup(client, source_approved=True)
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "complete", "meeting": str(target.pk)},
        )
        assert resp.status_code == 200
        action.refresh_from_db()
        assert action.status == MeetingActionItem.Status.DONE

    def it_403s_when_the_panel_meeting_is_locked(client: Client):
        _, guild, _, action, _ = _carryover_setup(client)
        locked_target = MeetingFactory(
            guild=guild, scheduled_date=timezone.localdate() + timedelta(days=14), approved=True
        )
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "complete", "meeting": str(locked_target.pk)},
        )
        assert resp.status_code == 403

    def it_403s_a_non_editor(client: Client):
        source = MeetingFactory(scheduled_date=timezone.localdate() + timedelta(days=1))
        action = MeetingActionItemFactory(item__meeting=source)
        target = MeetingFactory(guild=source.guild, scheduled_date=timezone.localdate() + timedelta(days=7))
        _member_client(client)
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "complete", "meeting": str(target.pk)},
        )
        assert resp.status_code == 403

    def it_404s_an_action_outside_the_panel_meetings_scope(client: Client):
        _, _, _, _, target = _carryover_setup(client)
        foreign = MeetingActionItemFactory(
            item__meeting=MeetingFactory(scheduled_date=timezone.localdate() + timedelta(days=1))
        )
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[foreign.pk]),
            {"op": "complete", "meeting": str(target.pk)},
        )
        assert resp.status_code == 404

    def it_404s_when_the_panel_meeting_is_undated(client: Client):
        _, guild, _, action, _ = _carryover_setup(client)
        undated = MeetingFactory(guild=guild, scheduled_date=None)
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "complete", "meeting": str(undated.pk)},
        )
        assert resp.status_code == 404

    def it_400s_an_unknown_op(client: Client):
        _, _, _, action, target = _carryover_setup(client)
        resp = client.post(
            reverse("hub_meeting_action_carryover", args=[action.pk]),
            {"op": "snooze", "meeting": str(target.pk)},
        )
        assert resp.status_code == 400

    def it_400s_a_garbage_meeting_param(client: Client):
        _, _, _, action, _ = _carryover_setup(client)
        resp = client.post(reverse("hub_meeting_action_carryover", args=[action.pk]), {"op": "complete"})
        assert resp.status_code == 400


# --- Phase 3: proposals (propose / decide / withdraw) -------------------------


@pytest.mark.django_db
def describe_propose():
    def it_creates_a_pending_proposal_with_the_scope_toast(client: Client):
        guild = GuildFactory(name="Woodshop")
        user = _guild_member_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(
            reverse("hub_meeting_propose", args=[meeting.pk]),
            {"title": "New sander", "why": "Ours died."},
        )
        assert resp.status_code == 204
        # json.dumps escapes the em dash in the header, so assert the ASCII tail.
        assert "Woodshop leadership will review it." in resp["HX-Trigger"]
        proposal = meeting.proposals.get()
        assert proposal.title == "New sander"
        assert proposal.why == "Ours died."
        assert proposal.proposed_by == user
        assert proposal.state == MeetingItemProposal.State.PENDING

    def it_pings_the_reviewers_through_the_spine(client: Client):
        guild = GuildFactory()
        lead_user = _user_with_role("leadnotify")
        lead_user.email = "leadnotify@example.com"  # recipients need a usable email
        lead_user.last_login = timezone.now()
        lead_user.save(update_fields=["email", "last_login"])
        guild.guild_lead = lead_user.member
        guild.save(update_fields=["guild_lead"])
        _guild_member_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        client.post(reverse("hub_meeting_propose", args=[meeting.pk]), {"title": "Dust collection", "why": ""})
        assert EventDelivery.objects.filter(event_key="meeting.item_proposed", channel="in_app").exists()

    def it_422s_a_blank_topic(client: Client):
        guild = GuildFactory()
        _guild_member_client(client, guild)
        meeting = MeetingFactory(guild=guild)
        resp = client.post(reverse("hub_meeting_propose", args=[meeting.pk]), {"title": "", "why": "x"})
        assert resp.status_code == 422
        assert not meeting.proposals.exists()

    def it_403s_a_non_member_of_the_guild(client: Client):
        _member_client(client)
        meeting = MeetingFactory()
        resp = client.post(reverse("hub_meeting_propose", args=[meeting.pk]), {"title": "Nope"})
        assert resp.status_code == 403

    def it_403s_a_locked_meeting(client: Client):
        guild = GuildFactory()
        _guild_member_client(client, guild)
        meeting = MeetingFactory(guild=guild, approved=True)
        resp = client.post(reverse("hub_meeting_propose", args=[meeting.pk]), {"title": "Too late"})
        assert resp.status_code == 403
        assert not meeting.proposals.exists()

    def it_403s_a_past_meeting(client: Client):
        guild = GuildFactory()
        _guild_member_client(client, guild)
        meeting = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=1))
        assert client.post(reverse("hub_meeting_propose", args=[meeting.pk]), {"title": "Too late"}).status_code == 403

    def it_403s_a_plain_member_for_the_council_scope(client: Client):
        _member_client(client)
        meeting = MeetingFactory(guild=None)
        assert (
            client.post(reverse("hub_meeting_propose", args=[meeting.pk]), {"title": "Council thing"}).status_code
            == 403
        )

    def it_lets_a_guild_lead_propose_to_the_council(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        meeting = MeetingFactory(guild=None)
        resp = client.post(reverse("hub_meeting_propose", args=[meeting.pk]), {"title": "Budget summit"})
        assert resp.status_code == 204
        assert "Council leadership will review it." in resp["HX-Trigger"]


@pytest.mark.django_db
def describe_proposal_decide():
    def describe_loading_the_modal():
        def it_loads_the_approve_form_prefilled_for_edit_then_approve(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposal = MeetingItemProposalFactory(
                meeting=MeetingFactory(guild=guild), title="Raw idea", why="Rough rationale"
            )
            resp = client.get(reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"mode": "approve"})
            assert resp.status_code == 200
            content = resp.content.decode()
            assert 'value="Raw idea"' in content
            assert "Rough rationale" in content
            assert "Add to agenda" in content
            assert 'name="decision" value="approve"' in content

        def it_loads_the_decline_form_with_the_optional_note(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild))
            resp = client.get(reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"mode": "decline"})
            content = resp.content.decode()
            assert "Note to the proposer (optional)" in content
            assert 'name="decision" value="decline"' in content

        def it_400s_an_unknown_mode(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild))
            assert (
                client.get(reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"mode": "maybe"}).status_code
                == 400
            )

        def it_403s_a_plain_member_even_the_proposer(client: Client):
            guild = GuildFactory()
            user = _guild_member_client(client, guild)
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild), proposed_by=user)
            resp = client.get(reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"mode": "approve"})
            assert resp.status_code == 403

    def describe_approving():
        def it_adds_the_item_credited_and_oob_appends_it(client: Client):
            guild = GuildFactory()
            reviewer = _lead_client(client, guild)
            proposer = UserFactory()
            proposal = MeetingItemProposalFactory(
                meeting=MeetingFactory(guild=guild), title="New sander", why="Ours died.", proposed_by=proposer
            )
            resp = client.post(
                reverse("hub_meeting_proposal_decide", args=[proposal.pk]),
                {"decision": "approve", "title": "New sander", "why": "Ours died."},
            )
            assert resp.status_code == 200
            assert "Added to the agenda." in resp["HX-Trigger"]
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.APPROVED
            assert proposal.reviewed_by == reviewer
            item = proposal.created_item
            assert item is not None
            assert item.name == "New sander"
            assert item.description == "Ours died."
            assert item.proposed_by == proposer
            content = resp.content.decode()
            assert 'hx-swap-oob="beforeend:#pl-meeting-item-list"' in content
            assert f'id="item-{item.pk}"' in content

        def it_applies_edit_then_approve_overrides(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposer = UserFactory()
            proposal = MeetingItemProposalFactory(
                meeting=MeetingFactory(guild=guild), title="Raw idea", why="Rough", proposed_by=proposer
            )
            client.post(
                reverse("hub_meeting_proposal_decide", args=[proposal.pk]),
                {"decision": "approve", "title": "Polished topic", "why": "Sharpened rationale"},
            )
            proposal.refresh_from_db()
            item = proposal.created_item
            assert item is not None
            assert item.name == "Polished topic"
            assert item.description == "Sharpened rationale"
            assert item.proposed_by == proposer  # credit survives the edit

        def it_422s_approve_without_a_topic(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild))
            resp = client.post(
                reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"decision": "approve", "title": ""}
            )
            assert resp.status_code == 422
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.PENDING

    def describe_declining():
        def it_declines_with_a_note_and_names_the_proposer_in_the_toast(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild))
            resp = client.post(
                reverse("hub_meeting_proposal_decide", args=[proposal.pk]),
                {"decision": "decline", "note": "Covered last month."},
            )
            assert resp.status_code == 200
            assert "was notified." in resp["HX-Trigger"]  # "Declined — {proposer} was notified."
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.DECLINED
            assert proposal.review_note == "Covered last month."

        def it_falls_back_to_the_username_when_the_proposer_has_no_member(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposer = UserFactory(username="ghostuser")
            proposer.member.delete()
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild), proposed_by=proposer)
            resp = client.post(reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"decision": "decline"})
            assert resp.status_code == 200
            assert "ghostuser" in resp["HX-Trigger"]

        def it_allows_a_blank_note(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild))
            resp = client.post(reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"decision": "decline"})
            assert resp.status_code == 200
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.DECLINED
            assert proposal.review_note == ""

    def it_422s_a_double_decision(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild))
        client.post(reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"decision": "decline"})
        resp = client.post(
            reverse("hub_meeting_proposal_decide", args=[proposal.pk]),
            {"decision": "approve", "title": "Again"},
        )
        assert resp.status_code == 422
        assert "This proposal was already decided." in resp["HX-Trigger"]

    def it_400s_an_unknown_decision(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild))
        assert (
            client.post(reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"decision": "maybe"}).status_code
            == 400
        )

    def it_403s_when_the_meeting_is_locked(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild, approved=True))
        resp = client.post(
            reverse("hub_meeting_proposal_decide", args=[proposal.pk]), {"decision": "approve", "title": "X"}
        )
        assert resp.status_code == 403


@pytest.mark.django_db
def describe_proposal_withdraw():
    def it_lets_the_proposer_withdraw_with_a_toast(client: Client):
        guild = GuildFactory()
        user = _guild_member_client(client, guild)
        proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild), proposed_by=user)
        resp = client.post(reverse("hub_meeting_proposal_withdraw", args=[proposal.pk]))
        assert resp.status_code == 200
        assert resp.content == b""
        assert "Withdrawn." in resp["HX-Trigger"]
        proposal.refresh_from_db()
        assert proposal.state == MeetingItemProposal.State.WITHDRAWN

    def it_403s_an_admin(client: Client):
        _admin_client(client)
        proposal = MeetingItemProposalFactory(proposed_by=UserFactory())
        resp = client.post(reverse("hub_meeting_proposal_withdraw", args=[proposal.pk]))
        assert resp.status_code == 403
        proposal.refresh_from_db()
        assert proposal.state == MeetingItemProposal.State.PENDING

    def it_403s_the_meetings_editor(client: Client):
        guild = GuildFactory()
        _lead_client(client, guild)
        proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild), proposed_by=UserFactory())
        assert client.post(reverse("hub_meeting_proposal_withdraw", args=[proposal.pk])).status_code == 403

    def it_403s_another_plain_member(client: Client):
        _member_client(client)
        proposal = MeetingItemProposalFactory(proposed_by=UserFactory())
        assert client.post(reverse("hub_meeting_proposal_withdraw", args=[proposal.pk])).status_code == 403

    def it_422s_an_already_decided_proposal(client: Client):
        guild = GuildFactory()
        user = _guild_member_client(client, guild)
        proposal = MeetingItemProposalFactory(meeting=MeetingFactory(guild=guild), proposed_by=user)
        proposal.decline(reviewer=UserFactory())
        resp = client.post(reverse("hub_meeting_proposal_withdraw", args=[proposal.pk]))
        assert resp.status_code == 422


# --- Phase 3: workspace template states (banner / footer / carryover / strips) --


@pytest.mark.django_db
def describe_workspace_lifecycle_rendering():
    def describe_the_approved_banner():
        def it_names_the_approver_and_date(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, approved=True)
            resp = client.get(reverse("hub_meeting", args=[meeting.pk]))
            content = resp.content.decode()
            assert "Minutes approved by" in content
            assert meeting.approved_at.strftime("%B") in content

        def it_offers_unlock_to_admins_only(client: Client):
            meeting = MeetingFactory(approved=True)
            _admin_client(client)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "unlock-meeting" in content
            assert "Unlock minutes" in content

        def it_hides_unlock_from_the_guilds_lead(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, approved=True)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Minutes approved by" in content
            assert "unlock-meeting" not in content

        def it_is_absent_on_a_draft(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            assert "Minutes approved by" not in client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()

    def describe_the_footer():
        def it_shows_approve_and_delete_with_the_guild_audience_line(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Approve minutes" in content
            assert "Delete meeting" in content
            assert "Guild members will be notified." in content
            assert "Any pending proposals are declined." in content
            assert "Deletes this meeting, its agenda, attendance, and action items." in content

        def it_shows_the_council_audience_line(client: Client):
            _admin_client(client)
            meeting = MeetingFactory(guild=None)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "All guild leads will be notified." in content

        def it_names_the_calendar_cascade_for_an_owned_event(client: Client):
            guild = GuildFactory()
            user = _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "schedule_or_go_live"):
                meeting.create_calendar_event(by=user)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Its calendar event is also removed from the calendar, Google, and Discord." in content

        def it_names_the_guild_stay_line_for_a_merely_linked_event(client: Client):
            guild = GuildFactory()
            user = _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            event = CommunityEventFactory(guild=guild)
            meeting.link_event(event, event.starts_at.date(), by=user)
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
            assert "Approve minutes" not in content
            assert "Delete meeting" not in content

        def it_is_absent_in_locked_mode(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild, approved=True)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Approve minutes" not in content
            assert "Delete meeting" not in content

    def describe_the_carryover_panel():
        def it_shows_open_actions_grouped_by_source_meeting(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            source = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=1))
            MeetingActionItemFactory(item__meeting=source, name="Order sandpaper")
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
            content = client.get(reverse("hub_meeting", args=[target.pk])).content.decode()
            assert "Carried over — still open from earlier meetings" in content
            assert f"From {source.display_title}" in content
            assert "Order sandpaper" in content

        def it_is_absent_when_nothing_carries_over(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            assert "Carried over" not in client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()

        def it_is_absent_for_read_only_members(client: Client):
            guild = GuildFactory()
            source = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=1))
            MeetingActionItemFactory(item__meeting=source)
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
            _member_client(client)
            assert "Carried over" not in client.get(reverse("hub_meeting", args=[target.pk])).content.decode()

        def it_is_absent_in_locked_mode(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            source = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=1))
            MeetingActionItemFactory(item__meeting=source)
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7), approved=True)
            assert "Carried over" not in client.get(reverse("hub_meeting", args=[target.pk])).content.decode()

    def describe_the_pending_proposals_strip():
        def it_shows_pending_rows_with_decide_buttons_to_editors(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            proposal = MeetingItemProposalFactory(meeting=meeting, title="New sander", why="Ours died.")
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Proposed agenda items (1)" in content
            assert "New sander" in content
            assert "Ours died." in content
            assert f"/meetings/proposals/{proposal.pk}/decide/?mode=approve" in content
            assert f"/meetings/proposals/{proposal.pk}/decide/?mode=decline" in content

        def it_is_absent_for_read_only_members(client: Client):
            guild = GuildFactory()
            meeting = MeetingFactory(guild=guild)
            MeetingItemProposalFactory(meeting=meeting)
            _member_client(client)
            assert "Proposed agenda items" not in client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()

        def it_omits_decided_proposals(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            declined = MeetingItemProposalFactory(meeting=meeting, title="Old idea")
            declined.decline(reviewer=UserFactory())
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Proposed agenda items" not in content

    def describe_your_proposals():
        def it_shows_a_pending_proposal_with_a_withdraw_confirm(client: Client):
            guild = GuildFactory()
            user = _guild_member_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            proposal = MeetingItemProposalFactory(meeting=meeting, proposed_by=user, title="New sander")
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Your proposals" in content
            assert "Waiting for review" in content
            assert f"withdraw-prop-{proposal.pk}" in content

        def it_links_an_approved_proposal_to_its_item(client: Client):
            guild = GuildFactory()
            user = _guild_member_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            proposal = MeetingItemProposalFactory(meeting=meeting, proposed_by=user)
            item = proposal.approve(reviewer=UserFactory())
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Added to the agenda ✓" in content
            assert f'href="#item-{item.pk}"' in content

        def it_shows_a_declined_proposal_with_the_reviewer_note(client: Client):
            guild = GuildFactory()
            user = _guild_member_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            proposal = MeetingItemProposalFactory(meeting=meeting, proposed_by=user)
            proposal.decline(reviewer=UserFactory(), note="Covered last month.")
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Declined" in content
            assert "Covered last month." in content

        def it_hides_withdrawn_proposals(client: Client):
            guild = GuildFactory()
            user = _guild_member_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            proposal = MeetingItemProposalFactory(meeting=meeting, proposed_by=user, title="Changed my mind")
            proposal.withdraw(by=user)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Your proposals" not in content
            assert "Changed my mind" not in content

        def it_renders_read_only_in_locked_mode(client: Client):
            guild = GuildFactory()
            user = _guild_member_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            proposal = MeetingItemProposalFactory(meeting=meeting, proposed_by=user)
            proposal.decline(reviewer=UserFactory(), note="")
            meeting.approve(by=UserFactory())
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Your proposals" in content
            assert "withdraw-prop-" not in content

    def describe_the_propose_button():
        def it_shows_for_a_guild_member_with_the_modal(client: Client):
            guild = GuildFactory()
            _guild_member_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            content = client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            assert "Propose an agenda item" in content
            assert "propose-item" in content
            assert "Helps leadership slot it into the meeting." in content

        def it_is_absent_for_editors(client: Client):
            guild = GuildFactory()
            _lead_client(client, guild)
            meeting = MeetingFactory(guild=guild)
            assert (
                "Propose an agenda item" not in client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            )

        def it_is_absent_on_a_locked_upcoming_meeting(client: Client):
            guild = GuildFactory()
            _guild_member_client(client, guild)
            meeting = MeetingFactory(guild=guild, approved=True)  # dated a week out, already approved
            assert (
                "Propose an agenda item" not in client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            )

        def it_is_absent_on_a_past_meeting(client: Client):
            guild = GuildFactory()
            _guild_member_client(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=1))
            assert (
                "Propose an agenda item" not in client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            )

        def it_is_absent_for_non_members_of_the_guild(client: Client):
            _member_client(client)
            meeting = MeetingFactory()
            assert (
                "Propose an agenda item" not in client.get(reverse("hub_meeting", args=[meeting.pk])).content.decode()
            )
