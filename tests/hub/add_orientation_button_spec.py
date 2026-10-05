"""BDD specs for the Orientations page's "+ Add an Orientation" button (#637).

Admins, guild officers and anyone who leads or staffs an active guild see it in the page
header on every tab; one guild links straight to that guild's settings on the Orientations
tab, several open a menu. Assertions anchor on the ``data-add-orientation`` hooks and URLs,
never on copy a changelog entry could carry (STANDARDS.md, Testing Traps).
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.template.loader import render_to_string
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from hub.view_as import ViewAs
from membership.models import Guild, Member
from membership.permissions import can_edit_guild, guilds_for_new_orientation
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

PAGE = "/orientations/"
HOOK = b"data-add-orientation"
LINK = re.compile(r'href="([^"]+)"[^>]*data-add-orientation-link')


def _login(client: Client, username: str, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _preview_as_member(client: Client) -> None:
    session = client.session
    session["view_as_role"] = "member"
    session.save()


def _targets(content: bytes) -> list[str]:
    return LINK.findall(content.decode())


def _tab(guild: Guild) -> str:
    return f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"


def describe_who_sees_it():
    def it_shows_for_an_admin(client: Client):
        _login(client, "ao_admin", Member.FogRole.ADMIN)
        GuildFactory(name="Admin Seen Guild")
        assert HOOK in client.get(PAGE).content

    def it_shows_for_a_guild_officer(client: Client):
        _login(client, "ao_officer", Member.FogRole.GUILD_OFFICER)
        GuildFactory(name="Officer Seen Guild")
        assert HOOK in client.get(PAGE).content

    def it_shows_for_a_guild_lead(client: Client):
        member = _login(client, "ao_lead")
        guild = GuildFactory(name="Lead Seen Guild", guild_lead=member)
        assert _targets(client.get(PAGE).content) == [_tab(guild)]

    def it_shows_for_guild_staff(client: Client):
        member = _login(client, "ao_staff")
        guild = GuildFactory(name="Staff Seen Guild")
        GuildStaffMembershipFactory(guild=guild, member=member)
        assert _targets(client.get(PAGE).content) == [_tab(guild)]

    def it_hides_from_a_plain_member(client: Client):
        _login(client, "ao_plain")
        GuildFactory(name="Plain Unseen Guild")
        assert HOOK not in client.get(PAGE).content

    def it_hides_from_an_equipment_manager_who_is_not_guild_staff(client: Client):
        member = _login(client, "ao_equipment")
        EquipmentStaffMembershipFactory(
            member=member, equipment=EquipmentFactory(guild=GuildFactory(name="Tool Guild"))
        )
        assert HOOK not in client.get(PAGE).content

    def it_hides_from_an_admin_previewing_as_a_member(client: Client):
        _login(client, "ao_preview", Member.FogRole.ADMIN)
        GuildFactory(name="Preview Unseen Guild")
        _preview_as_member(client)
        assert HOOK not in client.get(PAGE).content

    def it_hides_from_an_admin_previewing_as_a_guest_even_one_who_leads_a_guild(client: Client):
        member = _login(client, "ao_guest", Member.FogRole.ADMIN)
        GuildFactory(name="Guest Led Guild", guild_lead=member)
        session = client.session
        session["view_as_role"] = "guest"
        session.save()
        assert HOOK not in client.get(PAGE).content

    def it_gives_a_previewing_admin_who_leads_a_guild_only_that_guild(client: Client):
        member = _login(client, "ao_preview_lead", Member.FogRole.ADMIN)
        led = GuildFactory(name="Preview Led Guild", guild_lead=member)
        GuildFactory(name="Preview Other Guild")
        _preview_as_member(client)
        assert _targets(client.get(PAGE).content) == [_tab(led)]

    def it_leaves_out_a_lead_whose_only_guild_is_inactive(client: Client):
        member = _login(client, "ao_inactive")
        GuildFactory(name="Dormant Guild", guild_lead=member, is_active=False)
        assert HOOK not in client.get(PAGE).content

    @pytest.mark.parametrize("view", ["", "?view=calendar", "?view=bookings"])
    def it_sits_in_the_header_on_every_tab(client: Client, view: str):
        member = _login(client, "ao_tab")
        guild = GuildFactory(name="Every Tab Guild", guild_lead=member)
        content = client.get(PAGE + view).content.decode()
        assert _tab(guild) in content
        header = content[content.index('class="hub-page-header"') : content.index("plListCalendar")]
        assert "data-add-orientation" in header


def describe_one_guild_or_several():
    def it_links_one_guild_straight_to_its_orientations_tab(client: Client):
        member = _login(client, "ao_single")
        guild = GuildFactory(name="Single Guild", guild_lead=member)
        content = client.get(PAGE).content.decode()
        assert (
            f'<a href="{_tab(guild)}" class="hub-btn hub-btn--sm hub-btn--primary" data-add-orientation-link>'
            in content
        )
        block = content[content.index("data-add-orientation>") :].split("</div>")[0]
        assert 'aria-haspopup="menu"' not in block

    def it_opens_a_menu_of_guilds_sorted_by_name_for_several(client: Client):
        member = _login(client, "ao_several")
        zinc = GuildFactory(name="Zinc Guild", guild_lead=member)
        amber = GuildFactory(name="Amber Guild")
        GuildStaffMembershipFactory(guild=amber, member=member)
        mid = GuildFactory(name="Mid Guild", guild_lead=member)
        content = client.get(PAGE).content.decode()
        block = content[content.index("data-add-orientation>") :]
        assert 'aria-haspopup="menu"' in block
        assert 'role="menuitem"' in block
        assert _targets(content.encode()) == [_tab(amber), _tab(mid), _tab(zinc)]

    def it_lists_every_active_guild_for_an_admin_and_no_inactive_or_deleted_one(client: Client):
        _login(client, "ao_admin_all", Member.FogRole.ADMIN)
        second = GuildFactory(name="Beta Admin Guild")  # made first, listed second: the order is by name
        first = GuildFactory(name="Alpha Admin Guild")
        dormant = GuildFactory(name="Dormant Admin Guild", is_active=False)
        gone = GuildFactory(name="Gone Admin Guild")
        gone.soft_delete()
        targets = _targets(client.get(PAGE).content)
        assert targets == [_tab(g) for g in Guild.objects.filter(is_active=True).order_by("name")]
        assert targets.index(_tab(first)) < targets.index(_tab(second))
        assert _tab(dormant) not in targets
        assert _tab(gone) not in targets


def describe_every_link_is_editable():
    @pytest.mark.parametrize(
        ("fog_role", "lead", "staff", "preview"),
        [
            (Member.FogRole.ADMIN, False, False, False),
            (Member.FogRole.GUILD_OFFICER, False, False, False),
            (Member.FogRole.MEMBER, True, False, False),
            (Member.FogRole.MEMBER, False, True, False),
            (Member.FogRole.MEMBER, True, True, False),
            (Member.FogRole.MEMBER, False, False, False),
            (Member.FogRole.ADMIN, True, False, True),
            (Member.FogRole.GUILD_OFFICER, False, True, True),
        ],
    )
    def it_lists_exactly_the_active_guilds_can_edit_guild_allows(
        client: Client, rf: Any, fog_role: str, lead: bool, staff: bool, preview: bool
    ):
        member = _login(client, "ao_parity", fog_role)
        led = GuildFactory(name="Parity Led Guild", guild_lead=member if lead else None)
        staffed = GuildFactory(name="Parity Staffed Guild")
        if staff:
            GuildStaffMembershipFactory(guild=staffed, member=member)
        GuildFactory(name="Parity Stranger Guild")
        GuildFactory(name="Parity Dormant Guild", guild_lead=member, is_active=False)
        EquipmentStaffMembershipFactory(
            member=member, equipment=EquipmentFactory(guild=GuildFactory(name="Parity Tool Guild"))
        )
        assert led.pk  # the lead row exists whichever way the case sets it
        if preview:
            _preview_as_member(client)

        request = rf.get(PAGE)
        request.user = member.user
        request.session = client.session
        request.view_as = ViewAs.for_request(request)
        listed = guilds_for_new_orientation(request)
        editable = [g for g in Guild.objects.filter(is_active=True).order_by("name") if can_edit_guild(request, g)]
        assert listed == editable

        # The page links exactly those, and each one opens for this viewer.
        targets = _targets(client.get(PAGE).content)
        assert targets == [_tab(g) for g in listed]
        for target in targets:
            assert client.get(target).status_code == 200


def describe_the_cost():
    def it_reads_the_guilds_in_one_query_however_many(client: Client):
        member = _login(client, "ao_cost")
        GuildFactory(name="Cost Guild 0", guild_lead=member)
        client.get(PAGE)  # warm the per process caches
        with CaptureQueriesContext(connection) as one:
            client.get(PAGE)
        for index in range(1, 5):
            GuildStaffMembershipFactory(guild=GuildFactory(name=f"Cost Guild {index}"), member=member)
        with CaptureQueriesContext(connection) as five:
            content = client.get(PAGE).content
        assert len(_targets(content)) == 5
        assert len(five.captured_queries) == len(one.captured_queries)


def describe_the_components():
    def it_renders_page_headers_action_include_in_place_of_the_link():
        html = render_to_string(
            "components/page_header.html",
            {
                "title": "Orientations",
                "action_include": "hub/partials/add_orientation_menu_items.html",
                "add_orientation_guilds": [],
            },
        )
        assert 'class="hub-page-header"' in html
        assert "<a href" not in html

    def it_gives_row_actions_a_labelled_trigger_when_asked():
        html = render_to_string(
            "components/row_actions.html",
            {
                "menu_include": "hub/partials/add_orientation_menu_items.html",
                "menu_label": "Pick one",
                "menu_trigger_text": "+ Open It",
                "menu_trigger_class": "hub-btn hub-btn--sm",
                "add_orientation_guilds": [],
            },
        )
        assert 'class="hub-btn hub-btn--sm" x-ref="trigger"' in html
        assert "+ Open It" in html
        assert "<svg" not in html

    def it_keeps_row_actions_kebab_by_default():
        html = render_to_string(
            "components/row_actions.html",
            {
                "menu_include": "hub/partials/add_orientation_menu_items.html",
                "menu_label": "Actions",
                "add_orientation_guilds": [],
            },
        )
        assert 'class="pl-row-menu__trigger" x-ref="trigger"' in html
        assert "<svg" in html
