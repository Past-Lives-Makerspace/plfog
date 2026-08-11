"""BDD specs for the meeting workspace views (Meetings spec §6.2/§6.3 — phase 2).

The workspace GET rendering matrix (draft/locked × editor/read-only, incl. the
strictly-static attendance), the create endpoint (§6.2 prefill contract +
double-create guard + forbidden-scope 403), and the add/delete endpoints with
their template-state assertions. Home/guild-tab/lifecycle surfaces are phases 3/4.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import GuildStaffMembership, Meeting, MeetingActionItem, MeetingAttachment, Member
from tests.membership.factories import (
    GuildFactory,
    MeetingActionItemFactory,
    MeetingAgendaItemFactory,
    MeetingAttendeeFactory,
    MeetingFactory,
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
