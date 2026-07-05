"""BDD specs for the guild meeting-notes views + templates (read tab, management,
add/edit editor with the attachment formset, delete)."""

from __future__ import annotations


import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from membership.models import GuildMeetingNote, GuildMeetingNoteAttachment, GuildStaffMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildMeetingNoteAttachmentFactory,
    GuildMeetingNoteFactory,
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


def _att_management(total: int = 0, initial: int = 0) -> dict:
    return {
        "att-TOTAL_FORMS": str(total),
        "att-INITIAL_FORMS": str(initial),
        "att-MIN_NUM_FORMS": "0",
        "att-MAX_NUM_FORMS": "1000",
    }


@pytest.mark.django_db
def describe_gating():
    def it_403s_a_non_staff_member_on_the_management_list(client: Client):
        _user_with_role("m1")
        guild = GuildFactory()
        client.login(username="m1", password="pass")
        resp = client.get(reverse("hub_guild_meeting_notes", args=[guild.pk]))
        assert resp.status_code == 403

    def it_403s_a_non_staff_member_on_add(client: Client):
        _user_with_role("m2")
        guild = GuildFactory()
        client.login(username="m2", password="pass")
        resp = client.get(reverse("hub_guild_meeting_note_add", args=[guild.pk]))
        assert resp.status_code == 403

    def it_403s_a_non_staff_member_on_delete(client: Client):
        _user_with_role("m3")
        guild = GuildFactory()
        note = GuildMeetingNoteFactory(guild=guild)
        client.login(username="m3", password="pass")
        resp = client.post(reverse("hub_guild_meeting_note_delete", args=[guild.pk, note.pk]))
        assert resp.status_code == 403

    def it_redirects_the_guild_lead_to_the_meeting_notes_tab(client: Client):
        # The list is now an in-page tab on the guild editor; the old list URL redirects there.
        user = _user_with_role("lead1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead1", password="pass")
        resp = client.get(reverse("hub_guild_meeting_notes", args=[guild.pk]))
        assert resp.status_code == 302
        assert resp["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=meeting_notes"

    def it_redirects_a_staff_member_to_the_meeting_notes_tab(client: Client):
        user = _user_with_role("staff1")
        guild = GuildFactory()
        GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
        client.login(username="staff1", password="pass")
        resp = client.get(reverse("hub_guild_meeting_notes", args=[guild.pk]))
        assert resp.status_code == 302
        assert resp["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=meeting_notes"


@pytest.mark.django_db
def describe_read_tab():
    def it_hides_the_tab_for_non_staff_with_zero_notes(client: Client):
        _user_with_role("r1")
        guild = GuildFactory()
        client.login(username="r1", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        # Target the tab button's unique Alpine directive, not the bare phrase
        # "Meeting Notes" — that phrase also appears in the release-notes/changelog
        # copy rendered elsewhere on the hub page, which would false-positive here.
        assert b"section = 'notes'" not in resp.content

    def it_shows_the_tab_for_staff_with_zero_notes(client: Client):
        user = _user_with_role("r2")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="r2", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"section = 'notes'" in resp.content
        assert b"Post your first agenda" in resp.content

    def it_shows_the_tab_for_everyone_when_a_note_exists(client: Client):
        _user_with_role("r3")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild, title="June recap")
        client.login(username="r3", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"section = 'notes'" in resp.content
        assert b"June recap" in resp.content

    def it_renders_a_link_attachment_with_rel_and_target(client: Client):
        _user_with_role("r4")
        guild = GuildFactory()
        note = GuildMeetingNoteFactory(guild=guild)
        GuildMeetingNoteAttachmentFactory(note=note, label="Agenda", url="https://docs.example.com/x")
        client.login(username="r4", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b'rel="noopener nofollow noreferrer"' in resp.content
        assert b"Agenda" in resp.content

    def it_renders_a_file_attachment_as_a_download_link(client: Client):
        _user_with_role("r5")
        guild = GuildFactory()
        note = GuildMeetingNoteFactory(guild=guild)
        GuildMeetingNoteAttachmentFactory(note=note, file_doc=True, label="Slides")
        client.login(username="r5", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"download" in resp.content
        assert b"Slides" in resp.content


@pytest.mark.django_db
def describe_management_list_template():
    def it_shows_the_empty_state_and_add_button_on_the_tab(client: Client):
        user = _user_with_role("e1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="e1", password="pass")
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert b"No meeting notes yet" in resp.content
        assert b"+ Add meeting notes" in resp.content


@pytest.mark.django_db
def describe_add_and_edit():
    def it_creates_a_note_with_a_link_attachment(client: Client):
        user = _user_with_role("a1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="a1", password="pass")
        payload = {
            "meeting_date": "2026-06-01",
            "title": "June meeting",
            "body": "**recap**",
            **_att_management(total=1),
            "att-0-label": "Agenda",
            "att-0-url": "https://docs.example.com/agenda",
            "att-0-file": "",
            "att-0-sort_order": "0",
        }
        resp = client.post(reverse("hub_guild_meeting_note_add", args=[guild.pk]), data=payload)
        assert resp.status_code == 302
        note = GuildMeetingNote.objects.get(guild=guild)
        assert note.title == "June meeting"
        assert note.created_by == user
        assert note.attachments.count() == 1

    def it_rejects_an_attachment_with_both_file_and_url(client: Client):
        user = _user_with_role("a2")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="a2", password="pass")
        payload = {
            "meeting_date": "2026-06-01",
            "title": "Bad",
            "body": "",
            **_att_management(total=1),
            "att-0-label": "Both",
            "att-0-url": "https://docs.example.com/x",
            "att-0-file": SimpleUploadedFile("a.pdf", b"%PDF-1.4", "application/pdf"),
            "att-0-sort_order": "0",
        }
        resp = client.post(reverse("hub_guild_meeting_note_add", args=[guild.pk]), data=payload)
        assert resp.status_code == 200
        assert b"exactly one of" in resp.content
        assert GuildMeetingNote.objects.count() == 0

    def it_rejects_an_attachment_with_neither_file_nor_url(client: Client):
        user = _user_with_role("a3")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="a3", password="pass")
        payload = {
            "meeting_date": "2026-06-01",
            "title": "Empty row",
            "body": "",
            **_att_management(total=1),
            "att-0-label": "Nothing",
            "att-0-url": "",
            "att-0-file": "",
            "att-0-sort_order": "0",
        }
        resp = client.post(reverse("hub_guild_meeting_note_add", args=[guild.pk]), data=payload)
        assert resp.status_code == 200
        assert b"exactly one of" in resp.content
        assert GuildMeetingNote.objects.count() == 0

    def it_rejects_a_wrong_extension_file(client: Client):
        user = _user_with_role("a4")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="a4", password="pass")
        payload = {
            "meeting_date": "2026-06-01",
            "title": "Bad type",
            "body": "",
            **_att_management(total=1),
            "att-0-label": "Exe",
            "att-0-url": "",
            "att-0-file": SimpleUploadedFile("a.exe", b"MZ", "application/octet-stream"),
            "att-0-sort_order": "0",
        }
        resp = client.post(reverse("hub_guild_meeting_note_add", args=[guild.pk]), data=payload)
        assert resp.status_code == 200
        assert b"Unsupported file type" in resp.content
        assert GuildMeetingNote.objects.count() == 0

    def it_persists_two_attachment_rows(client: Client):
        user = _user_with_role("a5")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="a5", password="pass")
        payload = {
            "meeting_date": "2026-06-01",
            "title": "Two rows",
            "body": "",
            **_att_management(total=2),
            "att-0-label": "One",
            "att-0-url": "https://docs.example.com/1",
            "att-0-file": "",
            "att-0-sort_order": "0",
            "att-1-label": "Two",
            "att-1-url": "https://docs.example.com/2",
            "att-1-file": "",
            "att-1-sort_order": "1",
        }
        resp = client.post(reverse("hub_guild_meeting_note_add", args=[guild.pk]), data=payload)
        assert resp.status_code == 302
        note = GuildMeetingNote.objects.get(guild=guild)
        assert note.attachments.count() == 2

    def it_deletes_one_row_via_the_delete_flag_on_edit(client: Client):
        user = _user_with_role("a6")
        guild = GuildFactory(guild_lead=user.member)
        note = GuildMeetingNoteFactory(guild=guild, title="Edit me")
        keep = GuildMeetingNoteAttachmentFactory(note=note, url="https://docs.example.com/keep")
        drop = GuildMeetingNoteAttachmentFactory(note=note, url="https://docs.example.com/drop")
        client.login(username="a6", password="pass")
        payload = {
            "meeting_date": "2026-06-01",
            "title": "Edited",
            "body": "",
            **_att_management(total=2, initial=2),
            "att-0-id": str(keep.pk),
            "att-0-label": keep.label,
            "att-0-url": keep.url,
            "att-0-file": "",
            "att-0-sort_order": "0",
            "att-1-id": str(drop.pk),
            "att-1-label": drop.label,
            "att-1-url": drop.url,
            "att-1-file": "",
            "att-1-sort_order": "1",
            "att-1-DELETE": "on",
        }
        resp = client.post(reverse("hub_guild_meeting_note_edit", args=[guild.pk, note.pk]), data=payload)
        assert resp.status_code == 302
        note.refresh_from_db()
        assert note.title == "Edited"
        remaining = list(note.attachments.all())
        assert remaining == [keep]


@pytest.mark.django_db
def describe_delete():
    def it_deletes_the_note_and_cascades_attachments(client: Client):
        user = _user_with_role("d1")
        guild = GuildFactory(guild_lead=user.member)
        note = GuildMeetingNoteFactory(guild=guild)
        GuildMeetingNoteAttachmentFactory(note=note)
        client.login(username="d1", password="pass")
        resp = client.post(reverse("hub_guild_meeting_note_delete", args=[guild.pk, note.pk]))
        assert resp.status_code == 302
        assert GuildMeetingNote.objects.count() == 0
        assert GuildMeetingNoteAttachment.objects.count() == 0

    def it_rejects_a_get_request(client: Client):
        user = _user_with_role("d2")
        guild = GuildFactory(guild_lead=user.member)
        note = GuildMeetingNoteFactory(guild=guild)
        client.login(username="d2", password="pass")
        resp = client.get(reverse("hub_guild_meeting_note_delete", args=[guild.pk, note.pk]))
        assert resp.status_code == 405
        assert GuildMeetingNote.objects.count() == 1
