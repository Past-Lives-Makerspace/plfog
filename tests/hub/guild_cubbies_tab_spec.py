"""BDD specs for where guild cubbies show (#977): the guild page's Cubbies tab, Guild Settings > Cubbies,
and the Cubbies cards on Guild Tools and Admin Tools.

The tab shows only while the guild's switch is on, on the members surface, to a signed in member;
the switch off hides it from everyone while staff keep building in the manager. Assertions anchor
on markup and factory strings (STANDARDS.md, section 8).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Cubby, Member, ShelfUnit
from tests.membership.factories import CubbyFactory, GuildFactory, MembershipPlanFactory, ShelfUnitFactory

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["fog_role", "status"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _page(client: Client, guild) -> str:
    response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
    assert response.status_code == 200
    return response.content.decode()


def _shelf(guild):
    unit = ShelfUnitFactory(guild=guild, name="Glaze Wall", rows=2, columns=3)
    CubbyFactory(
        unit=unit, label="A1", size="12 x 12 x 16 in", note="By the sink", floor_cents=2500, suggested_cents=3000
    )
    CubbyFactory(unit=unit, label="A2", column=2, out_of_service=True, note="Shelf is being repaired")
    return unit


def describe_the_guild_page_tab():
    def it_hides_the_tab_while_the_switch_is_off_even_for_staff(client: Client):
        lead = _login(client, "tab_off_lead")
        guild = GuildFactory(guild_lead=lead)
        _shelf(guild)
        content = _page(client, guild)
        assert 'data-guild-tab="cubbies"' not in content
        assert "css/cubbies.css" not in content

    def it_shows_the_tab_after_reservations_with_its_deep_link(client: Client):
        _login(client, "tab_on_member")
        guild = GuildFactory(show_cubbies_tab=True)
        _shelf(guild)
        content = _page(client, guild)
        assert '@click="section = \'cubbies\'" data-guild-tab="cubbies">Cubbies</button>' in content
        assert "if (t === 'cubbies') section = 'cubbies';" in content
        assert "css/cubbies.css" in content
        nav = content.split('class="pl-tabs"', 1)[1].split("</nav>", 1)[0]
        assert nav.index("Overview") < nav.index(">Cubbies<")

    def it_draws_the_shelves_and_the_list_with_states_in_words(client: Client):
        _login(client, "tab_draw_member")
        guild = GuildFactory(show_cubbies_tab=True)
        _shelf(guild)
        content = _page(client, guild)
        assert "data-cubby-wall" in content and "data-cubby-list" in content
        assert 'aria-label="A1, open, from $25 a month. Details."' in content
        assert 'aria-label="A2, out of service"' in content
        assert "<strong>Top row, far left</strong>12 x 12 x 16 in" in content
        assert "<strong>Top row, second from the left</strong>Shelf is being repaired" in content
        assert 'data-where="Glaze Wall, top row, far left"' in content
        assert (
            '<span class="pl-cubby-summary__big">1</span><span class="pl-cubby-summary__of">of 2 open</span>' in content
        )
        assert "window.matchMedia('(max-width: 639.98px)').matches ? 'list' : 'grid'" in content
        assert 'id="cubby-detail-title">About This Cubby' in content
        assert "pl-modal pl-modal--md pl-cubby-sheet" in content

    def it_says_no_cubbies_yet_and_offers_staff_the_manager(client: Client):
        lead = _login(client, "tab_empty_lead")
        guild = GuildFactory(guild_lead=lead, show_cubbies_tab=True)
        ShelfUnitFactory(guild=guild)  # a unit with no cubby is not shown
        content = _page(client, guild)
        assert "data-cubbies-empty" in content
        assert reverse("hub_guild_cubbies", args=[guild.pk]) in content

    def it_offers_a_member_no_manager_link(client: Client):
        _login(client, "tab_empty_member")
        guild = GuildFactory(show_cubbies_tab=True)
        content = _page(client, guild)
        assert "data-cubbies-empty" in content
        assert reverse("hub_guild_cubbies", args=[guild.pk]) not in content

    def it_leaves_archived_shelves_and_cubbies_off(client: Client):
        _login(client, "tab_archived_member")
        guild = GuildFactory(show_cubbies_tab=True)
        CubbyFactory(unit=ShelfUnitFactory(guild=guild, name="Gone Rack", archived_at=timezone.now()), label="G1")
        CubbyFactory(unit=ShelfUnitFactory(guild=guild), label="X7", archived_at=timezone.now())
        content = _page(client, guild)
        assert "Gone Rack" not in content and 'data-cubby="X7"' not in content

    def it_shows_nothing_on_the_guilds_surface_even_signed_in(client: Client, settings):
        _login(client, "tab_guilds_surface")
        settings.ALLOWED_HOSTS = ["guilds.pastlives.space", "testserver"]
        settings.GUILDS_HOSTS = ["guilds.pastlives.space"]
        guild = GuildFactory(show_cubbies_tab=True)
        _shelf(guild)
        response = client.get(f"/guilds/{guild.slug}/", HTTP_HOST="guilds.pastlives.space")
        assert response.status_code == 200
        assert response.wsgi_request.surface == "guilds"
        assert response.context["cubby_tab"] is None


def _cubby_queries(client: Client, guild) -> list[str]:
    """Every query the guild page ran that reads a shelf unit or a cubby."""
    tables = (f'"{ShelfUnit._meta.db_table}"', f'"{Cubby._meta.db_table}"')
    with CaptureQueriesContext(connection) as queries:
        _page(client, guild)
    return [q["sql"] for q in queries.captured_queries if any(table in q["sql"] for table in tables)]


def describe_the_guild_page_queries():
    def it_runs_no_cubby_query_while_the_switch_is_off(client: Client):
        _login(client, "queries_off")
        guild = GuildFactory()
        _shelf(guild)
        assert _cubby_queries(client, guild) == []

    def it_runs_the_same_queries_for_one_cubby_or_many(client: Client):
        _login(client, "queries_on")
        one = GuildFactory(show_cubbies_tab=True)
        CubbyFactory(unit=ShelfUnitFactory(guild=one), label="O1")
        many = GuildFactory(show_cubbies_tab=True)
        for unit_index in range(3):
            unit = ShelfUnitFactory(guild=many, rows=2, columns=3)
            for row in (1, 2):
                for column in (1, 2, 3):
                    CubbyFactory(unit=unit, label=f"M{unit_index}{row}{column}", row=row, column=column)
        _page(client, one)  # warm per-request caches that are not the tab's
        one_queries = _cubby_queries(client, one)
        many_queries = _cubby_queries(client, many)
        assert len(one_queries) == len(many_queries) == 2


def describe_guild_settings():
    def it_adds_a_self_saving_cubbies_switch_after_reservations(client: Client):
        lead = _login(client, "settings_lead")
        guild = GuildFactory(guild_lead=lead)
        content = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        after = content.split(">Reservations</button>", 1)[1].lstrip()
        assert after.startswith("<button") and "data-cubbies-tab>Cubbies</button>" in after.split("\n", 1)[0]
        section = content.split("data-cubbies-settings", 1)[1].split("</form>", 1)[0]
        assert reverse("hub_guild_cubbies_settings_save", args=[guild.pk]) in section
        assert "data-autosave" in section
        assert 'name="show_cubbies_tab"' in section
        assert 'type="submit"' not in section
        assert "Build Shelves and Set Prices →" in section


def describe_tools_cards():
    def it_puts_a_cubbies_card_with_counts_in_guild_tools(client: Client):
        lead = _login(client, "tools_lead")
        guild = GuildFactory(guild_lead=lead)
        unit = ShelfUnitFactory(guild=guild)
        CubbyFactory(unit=unit, label="A1")
        CubbyFactory(unit=unit, label="A2", column=2, out_of_service=True)
        content = client.get(reverse("hub_admin_tools")).content.decode()
        card = content.split("data-tool-guild-cubbies", 1)[1].split("</a>", 1)[0]
        assert reverse("hub_guild_cubbies", args=[guild.pk]) in content.split("data-tool-guild-cubbies", 1)[0][-200:]
        assert "1 open" in card and "1 out of service" in card
        assert "data-tool-cubbies>" not in content

    def it_leaves_the_counts_off_with_no_cubbies(client: Client):
        lead = _login(client, "tools_lead_empty")
        GuildFactory(guild_lead=lead)
        content = client.get(reverse("hub_admin_tools")).content.decode()
        card = content.split("data-tool-guild-cubbies", 1)[1].split("</a>", 1)[0]
        assert "data-cubby-toolstats" not in card

    def it_gives_admins_a_manage_card_to_every_guild(client: Client):
        _login(client, "tools_admin", fog_role=Member.FogRole.ADMIN)
        content = client.get(reverse("hub_admin_tools")).content.decode()
        manage = content.split('data-tools-section="manage"', 1)[1].split("</section>", 1)[0]
        assert f'href="{reverse("hub_cubbies_home")}" data-tool-cubbies' in manage
