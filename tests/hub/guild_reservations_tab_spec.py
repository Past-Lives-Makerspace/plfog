"""BDD specs for the guild page's Reservations tab (#502, part 4).

The tab shows while the guild turns it on in settings and owns at least one active item; its
pane is the Reservations page's own card grid (``hub/partials/equipment_cards.html``) built by
the shared ``reservation_cards`` helper, filtered to the guild. Never on the guest guilds
surface, where the item pages and the Reservations page do not resolve. Assertions anchor on
markup and factory names (STANDARDS.md, section 8).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from membership.models import Equipment, Guild
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

TAB_BUTTON = ">Reservations</button>"
PANE = "data-guild-reservations"


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    client.login(username=username, password="pass")
    return user


def _page(client: Client, guild: Guild) -> str:
    return client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()


def describe_the_tab_gate():
    def it_is_absent_with_the_toggle_off_and_items_present(client: Client):
        _login(client, "rt_off")
        guild = GuildFactory()
        EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        content = _page(client, guild)
        assert TAB_BUTTON not in content
        assert PANE not in content
        assert "t === 'reservations'" not in content

    def it_is_absent_with_the_toggle_on_and_no_active_item(client: Client):
        _login(client, "rt_inactive")
        guild = GuildFactory(show_reservations_tab=True)
        EquipmentFactory(name="Quillwood Retired Saw", guild=guild, is_active=False)
        content = _page(client, guild)
        assert TAB_BUTTON not in content
        assert PANE not in content

    def it_is_present_with_the_toggle_on_and_an_active_item(client: Client):
        _login(client, "rt_on")
        guild = GuildFactory(show_reservations_tab=True)
        EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        content = _page(client, guild)
        assert TAB_BUTTON in content
        assert PANE in content
        assert f'href="{reverse("hub_equipment_index")}">All reservations</a>' in content

    def it_sits_directly_after_the_orientations_tab(client: Client):
        _login(client, "rt_order")
        guild = GuildFactory(show_reservations_tab=True)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationTypeFactory(guild=guild)
        EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        content = _page(client, guild)
        after_orientations = content.split(">Orientations</button>", 1)[1].lstrip()
        next_button = after_orientations[: after_orientations.index("</button>") + len("</button>")]
        assert next_button.startswith("<button")
        assert next_button.endswith(TAB_BUTTON)

    def it_honors_the_reservations_deep_link(client: Client):
        _login(client, "rt_deep")
        guild = GuildFactory(show_reservations_tab=True)
        EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        content = _page(client, guild)
        assert "if (t === 'reservations') section = 'reservations';" in content
        assert "x-show=\"section === 'reservations'\"" in content

    def it_is_absent_for_an_anonymous_visitor_on_the_members_host(client: Client):
        """No linked Member, no tab: every card would read "Membership inactive" behind a login."""
        guild = GuildFactory(show_reservations_tab=True)
        EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        content = response.content.decode()
        assert TAB_BUTTON not in content
        assert PANE not in content
        assert "t === 'reservations'" not in content
        assert response.context["reservation_cards"] == []

    def it_is_absent_on_the_guest_guilds_surface(client: Client):
        """The cards link to item pages the guilds host does not resolve, so the tab stays on the members host."""
        guild = GuildFactory(show_reservations_tab=True)
        EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        with override_settings(
            ALLOWED_HOSTS=["guilds.pastlives.space", "testserver"],
            GUILDS_HOSTS=["guilds.pastlives.space"],
        ):
            response = client.get(f"/guilds/{guild.slug}/", HTTP_HOST="guilds.pastlives.space")
        assert response.status_code == 200
        content = response.content.decode()
        assert TAB_BUTTON not in content
        assert PANE not in content
        assert response.context["reservation_cards"] == []


def describe_the_pane():
    def it_holds_the_guilds_active_items_only(client: Client):
        _login(client, "rt_items")
        guild = GuildFactory(show_reservations_tab=True)
        mine = EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        room = EquipmentFactory(name="Quillwood Finishing Room", guild=guild, kind=Equipment.Kind.ROOM)
        EquipmentFactory(name="Quillwood Retired Saw", guild=guild, is_active=False)
        EquipmentFactory(name="Otherguild Kiln", guild=GuildFactory())
        EquipmentFactory(name="Standalone Press")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert [card["equipment"] for card in response.context["reservation_cards"]] == [mine, room]
        content = response.content.decode()
        assert reverse("hub_equipment_detail", args=[mine.slug]) in content
        assert reverse("hub_equipment_detail", args=[room.slug]) in content
        assert "Quillwood Retired Saw" not in content
        assert "Otherguild Kiln" not in content
        assert "Standalone Press" not in content

    def it_locks_a_tool_the_member_is_not_trained_on(client: Client):
        _login(client, "rt_locked")
        guild = GuildFactory(show_reservations_tab=True)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        lathe_basics = OrientationTypeFactory(guild=guild, name="Quillwood Lathe Basics")
        EquipmentFactory(name="Quillwood Lathe", guild=guild, required_orientation=lathe_basics)
        content = _page(client, guild)
        pane = content.split(PANE, 1)[1]
        assert "pl-equip-card--locked" in pane
        assert 'class="pl-equip-card__cta">Book the orientation</a>' in pane

    def it_builds_its_cards_with_the_reservations_pages_helper(client: Client):
        from hub.equipment_views import _equipment_queryset, reservation_cards

        user = _login(client, "rt_same")
        guild = GuildFactory(show_reservations_tab=True)
        EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        EquipmentFactory(name="Quillwood Members Saw", guild=guild, requires_guild_membership=True)
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        expected = reservation_cards(user.member, _equipment_queryset().on_guild_page(guild))
        page_cards = response.context["reservation_cards"]
        assert [(c["equipment"], c["access_state"], c["availability"]) for c in page_cards] == [
            (c["equipment"], c["access_state"], c["availability"]) for c in expected
        ]
        assert {c["access_state"] for c in page_cards} == {Equipment.AccessState.OK, Equipment.AccessState.NEEDS_GUILD}

    def it_renders_any_number_of_items_in_the_same_queries(client: Client):
        _login(client, "rt_queries")
        guild = GuildFactory(show_reservations_tab=True)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        url = reverse("hub_guild_detail", args=[guild.slug])

        def gated(name: str) -> None:
            # The gating types belong to another guild, so only the Reservations pane grows here.
            EquipmentFactory(name=name, guild=guild, required_orientation=OrientationTypeFactory(name=f"{name} basics"))

        def count_queries() -> int:
            client.get(url)  # warm the session and per-request caches so both samples are steady state
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(url).status_code == 200
            return len(ctx.captured_queries)

        gated("Quillwood Lathe")
        with_one = count_queries()
        gated("Quillwood Mill")
        gated("Quillwood Jointer")
        EquipmentFactory(name="Quillwood Booth", guild=guild, kind=Equipment.Kind.ROOM)
        assert count_queries() == with_one

    def it_costs_no_query_while_the_toggle_is_off(client: Client):
        _login(client, "rt_free")
        guild = GuildFactory()
        EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        url = reverse("hub_guild_detail", args=[guild.slug])

        def count_queries() -> int:
            client.get(url)
            with CaptureQueriesContext(connection) as ctx:
                client.get(url)
            return len(ctx.captured_queries)

        off = count_queries()
        Guild.objects.filter(pk=guild.pk).update(show_reservations_tab=True)
        Equipment.objects.filter(guild=guild).update(is_active=False)
        # On with nothing active: the item read runs and comes back empty, and nothing else does.
        assert count_queries() == off + 1
