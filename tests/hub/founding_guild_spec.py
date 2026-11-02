"""BDD specs for Founding Guild (#1086) and Admin Tools > Guilds.

A per guild admin switch puts a gold Founding Guild badge on the guild page, its directory card
and its sidebar row, and changes nothing about voting. Admins flip it, and Visible to members,
from the guild editor's Basic tab and from one Guilds page; both post the same form to the same
endpoint. Anchors are markup attributes (``data-founding-badge``), never copy a changelog entry
could also contain (STANDARDS.md §8).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from hub.forms import GuildVisibilityForm, VotePreferenceForm
from membership.models import Guild, Member
from tests.features import turn_on
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}
GUILDS_HOST = "guilds.pastlives.space"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.space", "members.pastlives.space", "book.pastlives.space", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.space"],
    GUILDS_BASE_URL="https://guilds.pastlives.space",
    MEMBER_BASE_URL="https://members.pastlives.space",
    BOOK_BASE_URL="https://book.pastlives.space",
)


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _admin(client: Client, username: str = "fg_admin") -> Member:
    return _login(client, username, fog_role=Member.FogRole.ADMIN)


def _preview_as_member(client: Client) -> None:
    session = client.session
    session["view_as_role"] = "member"
    session.save()


def _save(client: Client, guild: Guild, data: dict[str, str], **extra: str):
    return client.post(reverse("hub_guild_visibility_save", args=[guild.pk]), data=data, **extra)


def describe_the_founding_flag():
    def it_is_off_for_a_new_guild():
        assert GuildFactory().is_founding is False


def describe_saving_the_two_switches():
    def it_lets_an_admin_turn_founding_on_and_keeps_the_guild_visible(client: Client):
        _admin(client)
        guild = GuildFactory()
        resp = _save(client, guild, {"is_active": "on", "is_founding": "on"}, **AUTOSAVE)
        assert resp.status_code == 200
        assert resp.json()["saved"] is True
        guild.refresh_from_db()
        assert (guild.is_active, guild.is_founding) == (True, True)

    def it_turns_founding_off_again_when_the_switch_is_absent(client: Client):
        _admin(client)
        guild = GuildFactory(is_founding=True)
        _save(client, guild, {"is_active": "on"}, **AUTOSAVE)
        guild.refresh_from_db()
        assert (guild.is_active, guild.is_founding) == (True, False)

    def it_lets_a_guild_be_hidden_and_founding_at_once(client: Client):
        _admin(client)
        guild = GuildFactory()
        _save(client, guild, {"is_founding": "on"}, **AUTOSAVE)
        guild.refresh_from_db()
        assert (guild.is_active, guild.is_founding) == (False, True)

    def it_changes_only_the_guild_it_was_posted_for(client: Client):
        _admin(client)
        guild, other = GuildFactory(), GuildFactory()
        _save(client, guild, {"is_active": "on", "is_founding": "on"}, **AUTOSAVE)
        other.refresh_from_db()
        assert other.is_founding is False

    def it_forbids_a_guild_lead(client: Client):
        lead = _login(client, "fg_lead")
        guild = GuildFactory(guild_lead=lead)
        assert _save(client, guild, {"is_active": "on", "is_founding": "on"}).status_code == 403
        guild.refresh_from_db()
        assert guild.is_founding is False

    def it_forbids_guild_staff(client: Client):
        staff = _login(client, "fg_staff")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staff)
        assert _save(client, guild, {"is_active": "on", "is_founding": "on"}).status_code == 403

    def it_forbids_an_admin_previewing_as_a_member(client: Client):
        _admin(client)
        _preview_as_member(client)
        guild = GuildFactory()
        assert _save(client, guild, {"is_active": "on", "is_founding": "on"}).status_code == 403
        guild.refresh_from_db()
        assert guild.is_founding is False


def describe_the_row_form():
    def it_names_each_switch_for_its_guild_and_joins_the_rows_form():
        guild = GuildFactory(name="Glass Guild")
        form = GuildVisibilityForm.for_row(guild)
        html = str(form["is_founding"])
        assert 'name="is_founding"' in html
        assert f'id="id_guild{guild.pk}_is_founding"' in html
        assert f'form="guild-row-{guild.pk}"' in html
        assert 'aria-label="Glass Guild: Founding Guild"' in html
        assert 'aria-label="Glass Guild: visible to members"' in str(form["is_active"])


def describe_admin_guilds_page():
    def it_lists_every_guild_by_name_with_both_switches(client: Client):
        _admin(client)
        hidden = GuildFactory(name="Bronze Guild", is_active=False)
        founding = GuildFactory(name="Amber Guild", is_founding=True)
        resp = client.get(reverse("hub_admin_guilds"))
        assert resp.status_code == 200
        names = [guild.name for guild, _form in resp.context["guild_rows"]]
        assert names == sorted(names)
        assert {"Amber Guild", "Bronze Guild"} <= set(names)
        html = resp.content.decode()
        assert f'id="guild-row-{hidden.pk}"' in html
        assert f'action="{reverse("hub_guild_visibility_save", args=[founding.pk])}"' in html
        assert f'id="id_guild{founding.pk}_is_founding"' in html
        assert f'href="{reverse("hub_guild_edit", args=[founding.pk])}?tab=basic"' in html
        assert "data-founding-badge" in html
        assert "data-guild-hidden-tag" in html

    def it_leaves_out_a_deleted_guild(client: Client):
        _admin(client)
        gone = GuildFactory(name="Gone Guild", deleted_at=timezone.now())
        resp = client.get(reverse("hub_admin_guilds"))
        assert gone not in [guild for guild, _form in resp.context["guild_rows"]]

    def it_shows_an_empty_line_with_no_guilds(client: Client):
        _admin(client)
        Guild.objects.all().delete()
        assert b"data-guilds-empty" in client.get(reverse("hub_admin_guilds")).content

    def it_forbids_a_guild_lead(client: Client):
        lead = _login(client, "fg_page_lead")
        GuildFactory(guild_lead=lead)
        assert client.get(reverse("hub_admin_guilds")).status_code == 403

    def it_forbids_guild_staff(client: Client):
        staff = _login(client, "fg_page_staff")
        GuildStaffMembershipFactory(guild=GuildFactory(), member=staff)
        assert client.get(reverse("hub_admin_guilds")).status_code == 403

    def it_forbids_an_admin_previewing_as_a_member(client: Client):
        _admin(client)
        _preview_as_member(client)
        assert client.get(reverse("hub_admin_guilds")).status_code == 403

    def it_sends_a_signed_out_visitor_to_sign_in(client: Client):
        resp = client.get(reverse("hub_admin_guilds"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]


def describe_the_admin_tools_card():
    def it_shows_to_an_admin(client: Client):
        _admin(client)
        assert b"data-tool-guilds" in client.get(reverse("hub_admin_tools")).content

    def it_is_not_on_guild_tools(client: Client):
        lead = _login(client, "fg_tools_lead")
        GuildFactory(guild_lead=lead)
        resp = client.get(reverse("hub_admin_tools"))
        assert resp.status_code == 200
        assert b"data-tool-guilds" not in resp.content

    def it_is_not_on_guild_tools_for_staff(client: Client):
        staff = _login(client, "fg_tools_staff")
        GuildStaffMembershipFactory(guild=GuildFactory(), member=staff)
        resp = client.get(reverse("hub_admin_tools"))
        assert resp.status_code == 200
        assert b"data-tool-guilds" not in resp.content

    def it_is_hidden_from_an_admin_previewing_as_a_member(client: Client):
        _admin(client)
        _preview_as_member(client)
        assert b"data-tool-guilds" not in client.get(reverse("hub_admin_tools"), follow=True).content


def describe_the_basic_tab_switch():
    def it_shows_to_an_admin(client: Client):
        _admin(client)
        guild = GuildFactory()
        html = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        assert 'name="is_founding"' in html

    def it_is_hidden_from_a_guild_lead(client: Client):
        lead = _login(client, "fg_tab_lead")
        guild = GuildFactory(guild_lead=lead)
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 200
        assert b'name="is_founding"' not in resp.content

    def it_is_hidden_from_guild_staff(client: Client):
        staff = _login(client, "fg_tab_staff")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staff)
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 200
        assert b'name="is_founding"' not in resp.content
        assert b'name="is_active"' not in resp.content

    def it_is_hidden_from_an_admin_previewing_as_a_member(client: Client):
        _admin(client)
        _preview_as_member(client)
        guild = GuildFactory()
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]), follow=True)
        assert b'name="is_founding"' not in resp.content


def describe_the_badge():
    def it_shows_on_the_guild_page_with_the_explainer(client: Client):
        _login(client, "fg_viewer")
        guild = GuildFactory(is_founding=True)
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-founding-badge" in html
        assert "data-founding-note" in html
        assert "Join in and help shape it." in html

    def it_is_absent_from_a_guild_that_is_not_founding(client: Client):
        _login(client, "fg_viewer_off")
        guild = GuildFactory()
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-founding-badge" not in html
        assert "data-founding-note" not in html

    def it_shows_on_the_guilds_host(client: Client):
        guild = GuildFactory(is_founding=True)
        with override_settings(**GUILDS_SETTINGS):
            resp = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST)
        assert resp.status_code == 200
        assert b"data-founding-note" in resp.content

    def it_shows_on_the_directory_card(client: Client):
        _login(client, "fg_dir")
        GuildFactory(name="Founding Dir Guild", is_founding=True)
        GuildFactory(name="Plain Dir Guild")
        html = client.get(reverse("hub_guild_directory")).content.decode()
        assert html.count("data-founding-badge") == 1

    def it_is_absent_from_the_directory_without_a_founding_guild(client: Client):
        _login(client, "fg_dir_off")
        GuildFactory(name="Plain Dir Guild")
        assert b"data-founding-badge" not in client.get(reverse("hub_guild_directory")).content


def describe_the_sidebar_tag():
    def it_tags_a_founding_guild_and_not_the_others(client: Client):
        turn_on("guilds")
        _login(client, "fg_side")
        GuildFactory(name="Founding Side Guild", is_founding=True)
        GuildFactory(name="Plain Side Guild")
        html = client.get(reverse("hub_home")).content.decode()
        assert html.count("data-sidebar-founding") == 1

    def it_sits_beside_hidden_for_a_hidden_founding_guild(client: Client):
        turn_on("guilds")
        _admin(client, "fg_side_admin")
        GuildFactory(name="Quiet Founding Guild", is_active=False, is_founding=True)
        html = client.get(reverse("hub_home")).content.decode()
        row = html[html.index("Quiet Founding Guild") :]
        row = row[: row.index("</a>")]
        assert ">Hidden</span>" in row
        assert "data-sidebar-founding" in row


def describe_voting():
    def it_keeps_a_founding_guild_on_the_ballot():
        founding = GuildFactory(is_founding=True)
        assert founding in VotePreferenceForm().fields["guild_1st"].queryset
