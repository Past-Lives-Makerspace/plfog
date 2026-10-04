"""BDD specs for the guild settings Reservations tab (#502, part 4).

The tab holds the guild page's Reservations tab toggle, which saves itself like every other
guild settings section (#575, no Save button), and the guild's reservable items with a Manage
link where the viewer may manage the item. Assertions anchor on markup and factory names
(STANDARDS.md, section 8).
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Equipment, Member
from tests.hub._markup import assert_autosave_forms_have_no_submit
from tests.membership.factories import EquipmentFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _lead(client: Client, username: str):
    user = _user(username)
    guild = GuildFactory(guild_lead=user.member)
    client.login(username=username, password="pass")
    return user, guild


def _settings(client: Client, guild) -> str:
    response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
    assert response.status_code == 200
    return response.content.decode()


def _reservations_form(content: str, guild) -> str:
    action = re.escape(reverse("hub_guild_reservations_settings_save", args=[guild.pk]))
    match = re.search(rf'<form[^>]*action="{action}"[^>]*>.*?</form>', content, re.S)
    assert match is not None
    return match.group(0)


def describe_the_settings_tab():
    def it_puts_the_tab_directly_after_orientations(client: Client):
        _user_obj, guild = _lead(client, "rs_tab")
        content = _settings(client, guild)
        after_orientations = content.split(">Orientations</button>", 1)[1].lstrip()
        next_button = after_orientations[: after_orientations.index("</button>") + len("</button>")]
        assert "section === 'reservations'" in next_button
        assert next_button.endswith(">Reservations</button>")
        assert "x-show=\"section === 'reservations'\"" in content
        assert "css/guild-settings" in content

    def it_renders_the_toggle_in_a_self_saving_form_with_no_save_button(client: Client):
        _user_obj, guild = _lead(client, "rs_toggle")
        content = _settings(client, guild)
        form = _reservations_form(content, guild)
        assert "data-autosave" in form.split(">", 1)[0]
        assert 'name="show_reservations_tab"' in form
        assert 'type="submit"' not in form
        assert "Show a Reservations tab on the guild page" in form
        assert_autosave_forms_have_no_submit(content)

    def it_reflects_the_saved_toggle(client: Client):
        _user_obj, guild = _lead(client, "rs_checked")
        guild.show_reservations_tab = True
        guild.save(update_fields=["show_reservations_tab"])
        form = _reservations_form(_settings(client, guild), guild)
        assert re.search(r'<input[^>]*name="show_reservations_tab"[^>]*checked', form)

    def it_lists_the_guilds_items_active_first_with_manage_links_for_a_lead(client: Client):
        _user_obj, guild = _lead(client, "rs_rows")
        inactive = EquipmentFactory(name="Aardvark Retired Mill", guild=guild, is_active=False)
        saw = EquipmentFactory(name="Zebrawood Bandsaw", guild=guild)
        room = EquipmentFactory(name="Mahogany Finishing Room", guild=guild, kind=Equipment.Kind.ROOM)
        EquipmentFactory(name="Otherguild Kiln", guild=GuildFactory())
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        rows = response.context["reservable_items"]
        assert [row["equipment"] for row in rows] == [room, saw, inactive]
        assert all(row["can_manage"] for row in rows)
        content = response.content.decode()
        listing = content.split('<ul class="pl-guild-reservables">', 1)[1].split("</ul>", 1)[0]
        assert "Otherguild Kiln" not in listing
        for item in (room, saw, inactive):
            assert reverse("hub_equipment_manage", args=[item.slug]) in listing
        assert '<span class="hub-badge">Room</span>' in listing
        assert '<span class="hub-badge">Tool</span>' in listing
        assert listing.count('<span class="hub-pill hub-pill--ok">Active</span>') == 2
        assert listing.count('<span class="hub-pill hub-pill--neutral">Inactive</span>') == 1

    def it_leaves_the_manage_link_off_for_a_viewer_who_cannot_manage_the_item(client: Client):
        # A guild officer edits every guild but gets no blanket grant over equipment.
        _user(username="rs_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        client.login(username="rs_officer", password="pass")
        guild = GuildFactory()
        saw = EquipmentFactory(name="Zebrawood Bandsaw", guild=guild)
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.context["reservable_items"] == [{"equipment": saw, "can_manage": False}]
        assert reverse("hub_equipment_manage", args=[saw.slug]) not in response.content.decode()

    def it_shows_the_empty_state_without_the_add_link_for_a_lead(client: Client):
        _user_obj, guild = _lead(client, "rs_empty")
        content = _settings(client, guild)
        assert 'class="hub-text-muted pl-guild-reservables__empty"' in content
        assert '<ul class="pl-guild-reservables">' not in content
        assert reverse("hub_equipment_add") not in content

    def it_offers_the_add_link_to_someone_who_can_create_items(client: Client):
        _user(username="rs_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="rs_admin", password="pass")
        guild = GuildFactory()
        content = _settings(client, guild)
        empty = content.split('class="hub-text-muted pl-guild-reservables__empty"', 1)[1].split("</p>", 1)[0]
        assert f'<a href="{reverse("hub_equipment_add")}">Add one on the Reservations page.</a>' in empty


def describe_the_save():
    def it_forbids_a_plain_member(client: Client):
        _user(username="rs_plain")
        client.login(username="rs_plain", password="pass")
        guild = GuildFactory()
        url = reverse("hub_guild_reservations_settings_save", args=[guild.pk])
        response = client.post(url, {"show_reservations_tab": "on"}, **AUTOSAVE)
        assert response.status_code == 403
        guild.refresh_from_db()
        assert guild.show_reservations_tab is False

    def it_refuses_a_get(client: Client):
        _user_obj, guild = _lead(client, "rs_get")
        assert client.get(reverse("hub_guild_reservations_settings_save", args=[guild.pk])).status_code == 405

    def it_turns_the_tab_on_through_the_autosave_path(client: Client):
        _user_obj, guild = _lead(client, "rs_auto")
        url = reverse("hub_guild_reservations_settings_save", args=[guild.pk])
        response = client.post(url, {"show_reservations_tab": "on"}, **AUTOSAVE)
        assert response.status_code == 200
        assert response.json()["saved"] is True
        guild.refresh_from_db()
        assert guild.show_reservations_tab is True

    def it_turns_the_tab_off_through_the_plain_path_and_lands_on_the_tab(client: Client):
        _user_obj, guild = _lead(client, "rs_plainpath")
        guild.show_reservations_tab = True
        guild.save(update_fields=["show_reservations_tab"])
        url = reverse("hub_guild_reservations_settings_save", args=[guild.pk])
        response = client.post(url, {})
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=reservations"
        guild.refresh_from_db()
        assert guild.show_reservations_tab is False
