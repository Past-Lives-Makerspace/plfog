"""BDD specs for the guild wide Orientation info as rich text (#502 prelude).

The Orientations tab of guild settings edits ``GuildOrientationSettings.info`` in the Quill
editor, the form stores it sanitized (``core.html_sanitize.clean_rich_body``), and the guild
page's Orientations tab renders the whole body through ``rich_body``: editor HTML as is, a
plain text body saved before the editor as paragraphs. The autosave path (#575) carries the
same field through the same clean.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import GuildOrientationSettings, Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}
EDITOR_HTML = (
    '<p>Read the <a href="https://example.com/safety">safety sheet</a> first.</p>'
    "<script>alert(1)</script><ul><li>Closed toe shoes</li></ul>"
)


def _member_user(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    member.sync_user_permissions()
    return user


def _lead_login(client: Client, username: str, guild):
    user = _member_user(username)
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    client.login(username=username, password="pass")
    return user


def _settings_payload(**overrides: str) -> dict[str, str]:
    data = {"is_enabled": "on", "allow_custom_requests": "on", "info": "", "closed_message": ""}
    data.update(overrides)
    return data


def _orientation_section(content: str) -> str:
    """Just the guild page's orientation panel: the changelog renders into every hub page."""
    return content.split('id="guild-orientation"')[1].split("</section>")[0]


def _info_block(content: str) -> str:
    return _orientation_section(content).split('class="hub-text-muted pl-orientation-info"')[1].split("</div>")[0]


def describe_the_orientations_tab_editor():
    def it_renders_the_quill_mount_for_info(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        _lead_login(client, "ri_mount", guild)
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        assert 'data-rte-for="id_info"' in content
        assert '<textarea name="info" id="id_info" class="pl-rte-source"' in content
        assert "Orientation info" in content

    def it_seeds_the_editor_with_a_plain_text_body_as_paragraphs(client: Client):
        # A body saved before the editor existed opens as the paragraphs the page renders,
        # not one run on line (RichBodyEditorWidget.format_value).
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, info="Bring shoes.\n\nNo sandals.")
        _lead_login(client, "ri_seed", guild)
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        textarea = content.split('<textarea name="info"')[1].split("</textarea>")[0]
        assert "&lt;p&gt;Bring shoes.&lt;/p&gt;&lt;p&gt;No sandals.&lt;/p&gt;" in textarea


def describe_saving_editor_html():
    def it_stores_the_sanitized_html_and_the_guild_tab_renders_the_link(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        OrientationTypeFactory(guild=guild)
        _lead_login(client, "ri_save", guild)
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]), _settings_payload(info=EDITOR_HTML)
        )
        assert response.status_code == 302
        stored = GuildOrientationSettings.objects.get(guild=guild).info
        assert "<script>" not in stored
        assert "alert(1)" not in stored  # the sanitizer drops a script with its code
        assert 'href="https://example.com/safety"' in stored
        assert 'rel="noopener nofollow noreferrer"' in stored
        assert 'target="_blank"' in stored
        assert "<ul><li>Closed toe shoes</li></ul>" in stored
        page = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        block = _info_block(page)
        assert '<a href="https://example.com/safety" rel="noopener nofollow noreferrer" target="_blank">' in block
        assert "<script>" not in block
        assert "<li>Closed toe shoes</li>" in block

    def it_stores_an_empty_editor_as_blank(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, info="Old words")
        _lead_login(client, "ri_empty", guild)
        client.post(reverse("hub_guild_orientation_edit", args=[guild.pk]), _settings_payload(info="<p><br></p>"))
        assert GuildOrientationSettings.objects.get(guild=guild).info == ""


def describe_the_guild_page_tab():
    def it_renders_a_plain_text_body_as_paragraphs_in_full(client: Client):
        # No truncation: this is the lead's instructions to the member, not a teaser.
        guild = GuildFactory()
        words = " ".join(f"word{i}" for i in range(60))
        GuildOrientationSettingsFactory(guild=guild, info=f"Bring shoes.\n\n{words}")
        OrientationTypeFactory(guild=guild)
        _member_user("ri_plain")
        client.login(username="ri_plain", password="pass")
        block = _info_block(client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode())
        assert "<p>Bring shoes.</p>" in block
        assert "word59" in block
        assert "…" not in block

    def it_renders_nothing_for_a_blank_body(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, info="")
        OrientationTypeFactory(guild=guild)
        _member_user("ri_blank")
        client.login(username="ri_blank", password="pass")
        section = _orientation_section(client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode())
        assert "pl-orientation-info" not in section

    def it_renders_the_info_page_through_the_same_filter(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, info='<p>See the <a href="https://example.com/x">guide</a>.</p>')
        _member_user("ri_page")
        client.login(username="ri_page", password="pass")
        content = client.get(reverse("hub_orientation_info", args=[guild.pk])).content.decode()
        assert '<a href="https://example.com/x" rel="noopener nofollow noreferrer" target="_blank">guide</a>' in content


def describe_the_autosave_path():
    def it_answers_200_and_stores_the_sanitized_value(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        _lead_login(client, "ri_auto", guild)
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]), _settings_payload(info=EDITOR_HTML), **AUTOSAVE
        )
        assert response.status_code == 200
        assert response.json()["saved"] is True
        stored = GuildOrientationSettings.objects.get(guild=guild).info
        assert "<script>" not in stored
        assert 'href="https://example.com/safety"' in stored
        assert 'rel="noopener nofollow noreferrer"' in stored
