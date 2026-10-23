"""Specs for the guild page's Meeting Notes tab (#891): who sees it, what it lists, and its query cost."""

from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.templatetags.tab_picker import read_tabs
from membership.models import Guild, Member
from tests.features import coming_soon, hide, turn_on
from tests.membership.factories import (
    GuildFactory,
    GuildMeetingNoteAttachmentFactory,
    GuildMeetingNoteFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

TAB_BUTTON = 'data-guild-tab="meeting-notes">Meeting Notes</button>'
ROW_OPEN = '<nav role="tablist" class="pl-tabs">'


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _guild_page(client: Client, guild: Guild) -> str:
    response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
    assert response.status_code == 200
    return response.content.decode()


def _tab_row(html: str) -> str:
    start = html.index(ROW_OPEN)
    return html[start : html.index("</nav>", start) + len("</nav>")]


def describe_the_tab_button():
    @pytest.mark.parametrize("set_meetings", [hide, coming_soon, turn_on])
    def it_shows_for_a_member_whatever_the_meetings_switch_says(client, set_meetings):
        set_meetings("meetings")
        _login(client, f"notes_switch_{set_meetings.__name__}")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        html = _guild_page(client, guild)
        assert TAB_BUTTON in html
        assert "data-guild-meeting-notes" in html

    def it_is_absent_for_a_member_when_the_guild_has_no_notes(client):
        _login(client, "notes_none_member")
        html = _guild_page(client, GuildFactory())
        assert TAB_BUTTON not in html
        assert "data-guild-meeting-notes" not in html
        # A stray ?tab mapping on a guild with no pane would blank every pane.
        assert "t === 'meeting_notes'" not in html

    def it_is_absent_for_a_signed_out_visitor_on_the_members_host(client):
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        assert TAB_BUTTON not in _guild_page(client, guild)

    def it_is_absent_on_the_public_guilds_surface(client, settings):
        host = settings.GUILDS_HOSTS[0]
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, host]
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild, title="Surface secret")
        response = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=host)
        assert response.status_code == 200
        html = response.content.decode()
        assert TAB_BUTTON not in html
        assert "Surface secret" not in html

    def it_sits_in_the_guild_tab_row_so_the_phone_picker_lists_it(client):
        _login(client, "notes_picker")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        html = _guild_page(client, guild)
        row = _tab_row(html)
        tabs, _buttons = read_tabs(row)
        assert "Meeting Notes" in [tab.label for tab in tabs]
        assert 'class="pl-tab-picker"' in html[html.index(row) + len(row) :]

    def it_maps_the_tab_deep_link_when_the_tab_exists(client):
        _login(client, "notes_deeplink")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        assert "if (t === 'meeting_notes') section = 'meeting_notes';" in _guild_page(client, guild)


def describe_the_empty_state():
    def it_shows_the_tab_to_the_guild_lead_with_a_link_to_add_notes(client):
        user = _login(client, "notes_empty_lead")
        guild = GuildFactory(guild_lead=user.member)
        html = _guild_page(client, guild)
        assert TAB_BUTTON in html
        assert "data-meeting-notes-empty" in html
        assert f'href="{reverse("hub_guild_meeting_note_add", args=[guild.pk])}"' in html
        assert "+ Add meeting notes" in html

    def it_shows_the_tab_to_an_admin_with_a_link_to_add_notes(client):
        _login(client, "notes_empty_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        html = _guild_page(client, guild)
        assert TAB_BUTTON in html
        assert f'href="{reverse("hub_guild_meeting_note_add", args=[guild.pk])}"' in html


def describe_the_notes():
    def it_lists_notes_newest_meeting_first_with_date_title_and_rendered_markdown(client):
        _login(client, "notes_order")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild, meeting_date=date(2026, 3, 4), title="Spring kickoff zq")
        GuildMeetingNoteFactory(
            guild=guild, meeting_date=date(2026, 9, 10), title="Fall planning zq", body="We need **more clay** zq."
        )
        html = _guild_page(client, guild)
        assert html.index("Fall planning zq") < html.index("Spring kickoff zq")
        assert "September 10, 2026" in html
        assert "March 4, 2026" in html
        assert "<strong>more clay</strong>" in html

    def it_leaves_another_guilds_notes_out(client):
        _login(client, "notes_scope")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild, title="Ours zq")
        GuildMeetingNoteFactory(guild=GuildFactory(), title="Theirs zq")
        html = _guild_page(client, guild)
        assert "Ours zq" in html
        assert "Theirs zq" not in html

    def it_opens_a_link_attachment_by_its_name_in_a_new_tab(client):
        _login(client, "notes_link")
        note = GuildMeetingNoteFactory()
        GuildMeetingNoteAttachmentFactory(note=note, label="Agenda doc zq", url="https://docs.example.com/zq")
        html = _guild_page(client, note.guild)
        assert (
            '<a href="https://docs.example.com/zq" target="_blank" rel="noopener noreferrer" '
            'class="pl-guild-notes__attachment" data-meeting-note-link>Agenda doc zq</a>'
        ) in html

    def it_opens_a_file_attachment_by_its_file_name_outside_the_boost(client):
        _login(client, "notes_file")
        note = GuildMeetingNoteFactory()
        attachment = GuildMeetingNoteAttachmentFactory(note=note, file_doc=True, label="")
        html = _guild_page(client, note.guild)
        assert (
            f'<a href="{attachment.file.url}" target="_blank" rel="noopener" hx-boost="false" '
            f'class="pl-guild-notes__attachment" data-meeting-note-file>{attachment.display_name}</a>'
        ) in html
        assert attachment.display_name.endswith(".pdf")


def describe_the_edit_notes_link():
    def it_opens_the_editors_meeting_notes_tab_for_the_guild_lead(client):
        user = _login(client, "notes_edit_lead")
        guild = GuildFactory(guild_lead=user.member)
        GuildMeetingNoteFactory(guild=guild)
        html = _guild_page(client, guild)
        assert f'href="{reverse("hub_guild_edit", args=[guild.pk])}?tab=meeting_notes"' in html
        assert "data-meeting-notes-edit>Edit notes</a>" in html

    def it_shows_for_an_admin(client):
        _login(client, "notes_edit_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        assert "data-meeting-notes-edit>Edit notes</a>" in _guild_page(client, guild)

    def it_is_absent_for_a_member(client):
        _login(client, "notes_edit_member")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        assert "data-meeting-notes-edit" not in _guild_page(client, guild)


def describe_the_query_cost():
    def it_loads_every_note_and_attachment_in_a_fixed_number_of_queries(client):
        _login(client, "notes_queries")
        guild = GuildFactory()
        GuildMeetingNoteAttachmentFactory(note=GuildMeetingNoteFactory(guild=guild))
        _guild_page(client, guild)  # warm one-time caches (site config, switches)
        with CaptureQueriesContext(connection) as one:
            _guild_page(client, guild)
        for _ in range(3):
            note = GuildMeetingNoteFactory(guild=guild)
            GuildMeetingNoteAttachmentFactory(note=note)
            GuildMeetingNoteAttachmentFactory(note=note)
        with CaptureQueriesContext(connection) as many:
            _guild_page(client, guild)
        assert len(many.captured_queries) == len(one.captured_queries)
