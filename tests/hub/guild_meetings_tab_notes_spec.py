"""Specs for the meeting notes on the guild page's Meetings tab (#891).

The tab carries the guild's meeting notes whatever the Meetings switch says; the Meetings
feature parts (next meeting, proposals, minutes, + New meeting) still follow the switch.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import FeatureSwitch
from core.templatetags.tab_picker import read_tabs
from membership.models import Guild, Meeting, Member
from tests.features import coming_soon, hide, turn_on
from tests.membership.factories import (
    GuildFactory,
    GuildMeetingNoteAttachmentFactory,
    GuildMeetingNoteFactory,
    MeetingFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

TAB_BUTTON = "@click=\"section = 'meetings'\">Meetings</button>"
FEATURE_PARTS = 'class="pl-guild-section pl-guild-meetings"'
NOTES_SECTION = "data-guild-meeting-notes"
DEEP_LINK = "if (t === 'notes' || t === 'meetings') section = 'meetings';"
ROW_OPEN = '<nav role="tablist" class="pl-tabs">'
SWITCH_OFF: list[Callable[[str], FeatureSwitch]] = [hide, coming_soon]


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


def describe_the_meetings_tab():
    def it_has_no_separate_meeting_notes_tab(client):
        turn_on("meetings")
        _login(client, "notes_no_separate_tab")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        html = _guild_page(client, guild)
        labels = [tab.label for tab in read_tabs(_tab_row(html))[0]]
        assert labels.count("Meetings") == 1
        assert "Meeting Notes" not in labels
        assert "section === 'meeting_notes'" not in html

    def describe_when_the_switch_is_off():
        @pytest.mark.parametrize("set_switch", SWITCH_OFF)
        def it_shows_the_tab_with_only_the_notes_when_the_guild_has_notes(client, set_switch):
            set_switch("meetings")
            _login(client, f"notes_off_{set_switch.__name__}")
            guild = GuildFactory()
            GuildMeetingNoteFactory(guild=guild, title="Notes only zq")
            html = _guild_page(client, guild)
            assert TAB_BUTTON in html
            assert NOTES_SECTION in html
            assert "Notes only zq" in html
            assert FEATURE_PARTS not in html
            assert "+ New meeting" not in html

        @pytest.mark.parametrize("set_switch", SWITCH_OFF)
        def it_shows_no_tab_to_a_member_when_the_guild_has_no_notes(client, set_switch):
            set_switch("meetings")
            _login(client, f"notes_off_none_{set_switch.__name__}")
            html = _guild_page(client, GuildFactory())
            assert TAB_BUTTON not in html
            # A ?tab mapping with no pane would blank every pane.
            assert DEEP_LINK not in html

        def it_shows_the_tab_to_the_guild_lead_with_an_empty_state(client):
            hide("meetings")
            user = _login(client, "notes_off_lead")
            guild = GuildFactory(guild_lead=user.member)
            html = _guild_page(client, guild)
            assert TAB_BUTTON in html
            assert "data-meeting-notes-empty" in html
            assert f'href="{reverse("hub_guild_meeting_note_add", args=[guild.pk])}"' in html
            assert "+ Add meeting notes" in html
            assert FEATURE_PARTS not in html

        def it_shows_the_tab_to_an_admin_with_an_empty_state(client):
            hide("meetings")
            _login(client, "notes_off_admin", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory()
            html = _guild_page(client, guild)
            assert TAB_BUTTON in html
            assert f'href="{reverse("hub_guild_meeting_note_add", args=[guild.pk])}"' in html

        def it_runs_no_meetings_feature_queries(client):
            hide("meetings")
            _login(client, "notes_off_queries")
            guild = GuildFactory()
            GuildMeetingNoteFactory(guild=guild)
            with CaptureQueriesContext(connection) as queries:
                _guild_page(client, guild)
            assert not [q["sql"] for q in queries.captured_queries if f'"{Meeting._meta.db_table}"' in q["sql"]]

        def it_shows_no_upcoming_meeting_row_as_the_next_meeting(client):
            hide("meetings")
            _login(client, "notes_off_next")
            guild = GuildFactory()
            MeetingFactory(guild=guild, scheduled_date=date(2099, 3, 4))
            html = _guild_page(client, guild)
            assert "Mar 4" not in html
            assert "Wednesday, March 4" not in html

        def it_lists_the_tab_in_the_phone_picker(client):
            hide("meetings")
            _login(client, "notes_off_picker")
            guild = GuildFactory()
            GuildMeetingNoteFactory(guild=guild)
            html = _guild_page(client, guild)
            row = _tab_row(html)
            assert "Meetings" in [tab.label for tab in read_tabs(row)[0]]
            assert 'class="pl-tab-picker"' in html[html.index(row) + len(row) :]

    def describe_when_the_switch_is_on():
        def it_shows_the_feature_parts_and_the_notes_below_them(client):
            turn_on("meetings")
            _login(client, "notes_on_member")
            guild = GuildFactory()
            GuildMeetingNoteFactory(guild=guild, title="Below the parts zq")
            html = _guild_page(client, guild)
            assert TAB_BUTTON in html
            assert html.index(FEATURE_PARTS) < html.index(NOTES_SECTION) < html.index("Below the parts zq")

        def it_shows_a_member_no_notes_section_when_there_are_no_notes(client):
            turn_on("meetings")
            _login(client, "notes_on_none")
            html = _guild_page(client, GuildFactory())
            assert TAB_BUTTON in html
            assert FEATURE_PARTS in html
            assert NOTES_SECTION not in html

    def it_maps_the_meetings_and_legacy_notes_deep_links_when_the_tab_exists(client):
        hide("meetings")
        _login(client, "notes_deeplink")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        assert DEEP_LINK in _guild_page(client, guild)

    def it_shows_no_tab_to_a_signed_out_visitor_while_the_switch_is_off(client):
        hide("meetings")
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild)
        assert TAB_BUTTON not in _guild_page(client, guild)

    def it_shows_no_notes_on_the_public_guilds_surface(client, settings):
        turn_on("meetings")
        host = settings.GUILDS_HOSTS[0]
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, host]
        guild = GuildFactory()
        GuildMeetingNoteFactory(guild=guild, title="Surface secret zq")
        response = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=host)
        assert response.status_code == 200
        html = response.content.decode()
        assert TAB_BUTTON not in html
        assert "Surface secret zq" not in html


def describe_the_notes():
    @pytest.fixture(autouse=True)
    def _switch_hidden(db):
        hide("meetings")

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


def describe_the_edit_notes_link():
    @pytest.fixture(autouse=True)
    def _switch_hidden(db):
        hide("meetings")

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
