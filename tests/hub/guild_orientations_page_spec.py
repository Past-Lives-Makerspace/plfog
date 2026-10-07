"""BDD specs for the guild's Orientations page (#672).

Orientation setup moved off the Orientations tab of Guild Settings onto its own page,
``/guilds/<pk>/orientations/``, titled "<Guild> Orientations" with "+ Add an orientation
type" first. These specs pin the page and its permission tiers, the redirect from the old
tab URL, where every orientation save lands, and every link into the page.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership import help_content
from membership.models import Guild, GuildStaffMembership, Member, OrientationSlot
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _user(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = f"{username} Person"
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _page(guild: Guild) -> str:
    return reverse("hub_guild_orientations", args=[guild.pk])


def _edit_hours_targets(content: str, guild: Guild) -> list[str]:
    """The orienter pks whose Edit Hours button the page renders."""
    form_url = re.escape(reverse("hub_guild_orientation_hours_form", args=[guild.pk]))
    return re.findall(rf'hx-get="{form_url}\?orienter=(\d+)"', content)


def describe_the_page():
    def it_is_titled_for_the_guild_and_puts_add_an_orientation_type_first(client: Client):
        _user(client, "gop_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Tech Guild")
        response = client.get(_page(guild))
        assert response.status_code == 200
        content = response.content.decode()
        assert "<title>Tech Guild Orientations" in content
        assert '<h1 class="hub-page-title pl-guild-settings__title">Tech Guild Orientations</h1>' in content
        page = content.split("data-guild-orientations", 1)[1]
        first_button = page[page.index("<button") : page.index("</button>")]
        assert 'form="otypes-form"' in first_button
        assert "data-formset-add" in first_button
        assert first_button.endswith(">+ Add an orientation type")
        assert f'<a href="{reverse("hub_guild_edit", args=[guild.pk])}"' in page
        assert "Back to Tech Guild Settings" in page

    def it_carries_the_autosave_root_and_every_orientation_card(client: Client):
        _user(client, "gop_cards", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        content = client.get(_page(guild)).content.decode()
        assert re.search(r'x-data="plGuildAutosave\([^"]*\)" data-guild-autosave', content)
        assert 'id="otypes-form"' in content
        for action in (
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            reverse("hub_guild_orientation_types_save", args=[guild.pk]),
            reverse("hub_guild_emails_save", args=[guild.pk]),
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
        ):
            assert f'action="{action}"' in content, action
        assert "leave-while-saving" in content
        # No tab state: every card renders, none hidden behind an x-show on section.
        assert "section ===" not in content

    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.get(_page(guild))
        assert response.status_code == 302
        assert response["Location"].endswith(f"?next={_page(guild)}")


def describe_permission_tiers():
    def it_shows_a_lead_every_orienters_edit_hours_row(client: Client):
        user = _user(client, "gop_lead")
        guild = GuildFactory(guild_lead=user.member)
        orienter = MemberFactory()
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        response = client.get(_page(guild))
        assert response.status_code == 200
        assert response.context["can_edit_others_hours"] is True
        assert sorted(_edit_hours_targets(response.content.decode(), guild)) == sorted(
            [str(user.member.pk), str(orienter.pk)]
        )

    def it_shows_an_admin_every_orienters_edit_hours_row(client: Client):
        _user(client, "gop_admin_rows", fog_role=Member.FogRole.ADMIN)
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        orienter = MemberFactory()
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        response = client.get(_page(guild))
        assert response.status_code == 200
        assert sorted(_edit_hours_targets(response.content.decode(), guild)) == sorted([str(lead.pk), str(orienter.pk)])

    def it_shows_an_orienter_on_staff_only_their_own_row(client: Client):
        user = _user(client, "gop_orienter")
        guild = GuildFactory(guild_lead=MemberFactory())
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        GuildStaffMembershipFactory(guild=guild, member=MemberFactory(), role=GuildStaffMembership.Role.ORIENTER)
        response = client.get(_page(guild))
        assert response.status_code == 200
        assert response.context["show_my_hours_card"] is True
        assert response.context["can_edit_others_hours"] is False
        assert response.context["slot_form_locked"] is True
        assert _edit_hours_targets(response.content.decode(), guild) == [str(user.member.pk)]

    def it_refuses_a_member_who_cannot_edit_the_guild_as_guild_settings_does(client: Client):
        _user(client, "gop_member")
        guild = GuildFactory()
        page = client.get(_page(guild))
        settings_page = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert page.status_code == settings_page.status_code == 403
        assert page.content == settings_page.content


def describe_the_old_tab_url():
    def it_redirects_tab_orientations_to_the_page(client: Client):
        _user(client, "gop_old", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]), {"tab": "orientations"})
        assert response.status_code == 302
        assert response["Location"] == _page(guild)

    def it_leaves_every_other_tab_on_guild_settings(client: Client):
        _user(client, "gop_other", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]), {"tab": "staff"})
        assert response.status_code == 200
        assert "hub/guild_edit.html" in [t.name for t in response.templates]

    def it_refuses_before_redirecting_a_member_who_cannot_edit(client: Client):
        _user(client, "gop_old_member")
        guild = GuildFactory()
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]), {"tab": "orientations"})
        assert response.status_code == 403

    def it_turns_the_settings_tab_into_a_link_to_the_page(client: Client):
        _user(client, "gop_tablink", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        content = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        assert f'<a href="{_page(guild)}" class="vote-tab" data-help-key="guild.run-orientations"' in content


def describe_where_saves_land():
    def it_returns_a_one_off_slot_add_to_the_page(client: Client):
        user = _user(client, "gop_slot_add")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(reverse("hub_guild_orientation_slot_add", args=[guild.pk]), {})
        assert response.status_code == 302
        assert response["Location"] == _page(guild)

    def it_returns_a_slot_cancel_to_the_page(client: Client):
        user = _user(client, "gop_slot_cancel")
        guild = GuildFactory(guild_lead=user.member)
        slot = OrientationSlotFactory(guild=guild, orientation_type=OrientationTypeFactory(guild=guild))
        response = client.post(reverse("hub_guild_orientation_slot_cancel", args=[guild.pk, slot.pk]))
        assert response.status_code == 302
        assert response["Location"] == _page(guild)
        assert OrientationSlot.objects.get(pk=slot.pk).is_cancelled is True

    def it_returns_a_bulk_cancel_to_the_page(client: Client):
        user = _user(client, "gop_bulk")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(reverse("hub_guild_orientation_times_bulk_cancel", args=[guild.pk]), {})
        assert response.status_code == 302
        assert response["Location"] == _page(guild)

    def it_returns_a_window_cancel_to_the_page(client: Client):
        user = _user(client, "gop_window")
        guild = GuildFactory(guild_lead=user.member)
        window = OrientationAvailabilityBlockFactory(guild=guild, orienter=user.member)
        response = client.post(reverse("hub_orientation_block_cancel", args=[window.pk]))
        assert response.status_code == 302
        assert response["Location"] == _page(guild)

    def it_sends_a_get_of_the_settings_and_email_endpoints_to_the_page(client: Client):
        user = _user(client, "gop_gets")
        guild = GuildFactory(guild_lead=user.member)
        for name in ("hub_guild_orientation_edit", "hub_guild_emails_save"):
            response = client.get(reverse(name, args=[guild.pk]))
            assert response.status_code == 302, name
            assert response["Location"] == _page(guild), name

    def it_re_renders_an_invalid_types_save_on_the_page(client: Client):
        user = _user(client, "gop_types_bad")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(
            reverse("hub_guild_orientation_types_save", args=[guild.pk]),
            {
                "otypes-TOTAL_FORMS": "1",
                "otypes-INITIAL_FORMS": "0",
                "otypes-MIN_NUM_FORMS": "0",
                "otypes-MAX_NUM_FORMS": "1000",
                "otypes-0-name": "Laser Basics",
                "otypes-0-duration_minutes": "",
            },
        )
        assert response.status_code == 200
        assert "hub/guild_orientations.html" in [t.name for t in response.templates]
        assert response.context["orientation_type_formset"].errors

    def it_keeps_an_invalid_welcome_email_on_guild_settings(client: Client):
        user = _user(client, "gop_welcome_bad")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"form_id": "welcome_email", "welcome_email_subject": "x" * 201},
        )
        assert response.status_code == 200
        assert "hub/guild_edit.html" in [t.name for t in response.templates]
        assert response.context["active_tab"] == "welcome_email"
        assert response.context["welcome_email_form"].errors


def describe_links_into_the_page():
    def it_links_the_equipment_forms_create_a_type_to_the_page(client: Client):
        _user(client, "gop_equip", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Print Guild")
        equipment = EquipmentFactory(guild=guild)
        content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
        assert f'<a href="{_page(guild)}">Create a new Print Guild orientation type</a>' in content

    def it_points_both_running_orientations_screenshots_at_the_page():
        article = next(a for a in help_content.ARTICLES if a["slug"] == "running-orientations")
        pages = [shot["page"] for shot in article["screenshots"] if shot["file"].startswith(("01-", "02-"))]
        assert pages == ["/guilds/1/orientations/", "/guilds/1/orientations/"]

    def it_leaves_no_help_screenshot_on_the_old_tab_url():
        pages = [shot["page"] for article in help_content.ARTICLES for shot in article.get("screenshots", ())]
        assert not [page for page in pages if page and re.search(r"/guilds/\d+/edit/\?tab=orientations", page)]
