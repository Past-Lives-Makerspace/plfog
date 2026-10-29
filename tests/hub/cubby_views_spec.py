"""BDD specs for the staff Cubbies manager (#977): Layout, a cubby's page, Pricing and the tab switch.

Every manage URL is refused (403) to a plain member and to another guild's staff, including a
crafted POST naming a cubby or unit of a guild they do not run: the guild is read from the
record, never the URL. Admins reach every guild; a Guild Officer only a guild they lead or staff
(#1015). Assertions anchor on markup and factory strings (STANDARDS.md, section 8).
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from PIL import Image
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing.models import BillingSettings
from hub.cubby_forms import CubbyForm, CubbyPricingForm, MoneyInput, ShapeChips, _dollars
from membership.models import Cubby, Guild, GuildStaffMembership, Member, ShelfUnit
from tests.membership.factories import CubbyFactory, GuildFactory, MembershipPlanFactory, ShelfUnitFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "tan").save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.replace("_", " ").title()
    member.save(update_fields=["fog_role", "full_legal_name"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _lead(client: Client, username: str = "cubby_lead") -> tuple[Member, Guild]:
    member = _login(client, username)
    return member, GuildFactory(guild_lead=member)


def _unit_post(unit: ShelfUnit, **extra: str) -> dict[str, str]:
    prefix = f"unit-{unit.pk}"
    return {
        f"{prefix}-name": unit.name,
        f"{prefix}-rows": str(unit.rows),
        f"{prefix}-columns": str(unit.columns),
        **extra,
    }


def _cubby_post(**overrides: str) -> dict[str, str]:
    data = {"label": "A1", "row": "1", "column": "1", "shape": "one", "size": "12 x 12 x 16 in", "note": ""}
    data.update(overrides)
    return data


def _manage_urls(guild: Guild, unit: ShelfUnit, cubby: Cubby) -> list[tuple[str, str, dict[str, str]]]:
    return [
        ("get", reverse("hub_guild_cubbies", args=[guild.pk]), {}),
        ("get", reverse("hub_guild_cubbies_pricing", args=[guild.pk]), {}),
        ("post", reverse("hub_guild_cubbies_pricing", args=[guild.pk]), {"floor": "1", "suggested": "1"}),
        ("post", reverse("hub_guild_cubbies_settings_save", args=[guild.pk]), {"show_cubbies_tab": "on"}),
        (
            "post",
            reverse("hub_shelf_unit_add", args=[guild.pk]),
            {"new-unit-name": "X", "new-unit-rows": "1", "new-unit-columns": "1"},
        ),
        ("post", reverse("hub_shelf_unit_save", args=[unit.pk]), _unit_post(unit, step="rows:1")),
        ("post", reverse("hub_shelf_unit_archive", args=[unit.pk]), {}),
        ("post", reverse("hub_shelf_unit_restore", args=[unit.pk]), {}),
        ("get", reverse("hub_cubby_add", args=[unit.pk]), {}),
        ("post", reverse("hub_cubby_add", args=[unit.pk]), _cubby_post(label="Z1", column="2")),
        ("get", reverse("hub_cubby_edit", args=[cubby.pk]), {}),
        ("post", reverse("hub_cubby_edit", args=[cubby.pk]), _cubby_post(label="HACKED")),
        ("post", reverse("hub_cubby_archive", args=[cubby.pk]), {}),
        ("post", reverse("hub_cubby_restore", args=[cubby.pk]), {}),
        ("post", reverse("hub_cubby_photo_delete", args=[cubby.pk]), {}),
    ]


def describe_permissions():
    def _theirs() -> tuple[Guild, ShelfUnit, Cubby]:
        guild = GuildFactory(name="Their Guild")
        unit = ShelfUnitFactory(guild=guild, name="Their Shelf")
        return guild, unit, CubbyFactory(unit=unit, label="T1")

    def it_refuses_a_plain_member_on_every_manage_url(client: Client):
        _login(client, "plain_member")
        guild, unit, cubby = _theirs()
        for method, url, data in _manage_urls(guild, unit, cubby):
            assert getattr(client, method)(url, data).status_code == 403, url

    def it_refuses_another_guilds_lead_on_every_url_including_crafted_posts(client: Client):
        _lead(client, "other_lead")
        guild, unit, cubby = _theirs()
        for method, url, data in _manage_urls(guild, unit, cubby):
            assert getattr(client, method)(url, data).status_code == 403, url
        cubby.refresh_from_db()
        unit.refresh_from_db()
        guild.refresh_from_db()
        assert cubby.label == "T1" and not cubby.is_archived
        assert not unit.is_archived and not guild.show_cubbies_tab
        assert not ShelfUnit.objects.filter(name="X").exists()

    def it_refuses_another_guilds_staff(client: Client):
        member = _login(client, "other_staff")
        GuildStaffMembership.objects.create(guild=GuildFactory(), member=member, role="treasurer")
        _guild, _unit, cubby = _theirs()
        assert client.post(reverse("hub_cubby_archive", args=[cubby.pk])).status_code == 403

    def it_lets_an_admin_reach_every_guild(client: Client):
        _login(client, "top_admin", fog_role=Member.FogRole.ADMIN)
        guild, unit, cubby = _theirs()
        for method, url, data in _manage_urls(guild, unit, cubby):
            assert getattr(client, method)(url, data).status_code in (200, 302), url

    def it_refuses_a_guild_officer_who_neither_leads_nor_staffs_the_guild(client: Client):
        # #1015: the Guild Officer role alone opens no guild's cubbies, and with no guild of their
        # own there is no manager for Admin Tools' Cubbies card to open.
        _login(client, "roaming_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        guild, unit, cubby = _theirs()
        for method, url, data in _manage_urls(guild, unit, cubby):
            assert getattr(client, method)(url, data).status_code == 403, url
        assert client.get(reverse("hub_cubbies_home")).status_code == 403
        cubby.refresh_from_db()
        guild.refresh_from_db()
        assert cubby.label == "T1" and not cubby.is_archived and not guild.show_cubbies_tab

    def it_lets_a_guild_officer_in_as_the_guilds_lead_or_staff(client: Client):
        officer = _login(client, "own_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        guild, unit, cubby = _theirs()
        guild.guild_lead = officer
        guild.save(update_fields=["guild_lead"])
        for method, url, data in _manage_urls(guild, unit, cubby):
            assert getattr(client, method)(url, data).status_code in (200, 302), url
        assert client.get(reverse("hub_cubbies_home")).url == reverse("hub_guild_cubbies", args=[guild.pk])
        guild.guild_lead = None
        guild.save(update_fields=["guild_lead"])
        assert client.get(reverse("hub_guild_cubbies", args=[guild.pk])).status_code == 403
        GuildStaffMembership.objects.create(guild=guild, member=officer, role="secretary")
        for method, url, data in _manage_urls(guild, unit, cubby):
            assert getattr(client, method)(url, data).status_code in (200, 302), url

    def it_refuses_an_admin_previewing_as_a_guild_officer(client: Client):
        _login(client, "preview_admin", fog_role=Member.FogRole.ADMIN)
        session = client.session
        session["view_as_role"] = "guild_officer"
        session.save()
        guild, unit, cubby = _theirs()
        for method, url, data in _manage_urls(guild, unit, cubby):
            assert getattr(client, method)(url, data).status_code == 403, url
        assert client.get(reverse("hub_cubbies_home")).status_code == 403

    def it_lets_the_guilds_staff_in(client: Client):
        member = _login(client, "own_staff")
        guild, _unit, _cubby = _theirs()
        GuildStaffMembership.objects.create(guild=guild, member=member, role="co_lead")
        assert client.get(reverse("hub_guild_cubbies", args=[guild.pk])).status_code == 200


def describe_the_payee_through_the_member_permissions_tab():
    def it_clears_when_an_admin_removes_the_payee_from_the_staff_there(client: Client):
        _login(client, "perm_admin", fog_role=Member.FogRole.ADMIN)
        staff = User.objects.create_user(username="perm_staff").member
        guild = GuildFactory(cubby_payee=staff)
        row = GuildStaffMembership.objects.create(guild=guild, member=staff, role="treasurer")
        client.post(reverse("hub_admin_member_guild_staff_remove", args=[staff.pk, row.pk]))
        guild.refresh_from_db()
        assert guild.cubby_payee is None

    def it_clears_when_an_admin_removes_the_payee_as_lead_there(client: Client):
        _login(client, "perm_admin_two", fog_role=Member.FogRole.ADMIN)
        lead = User.objects.create_user(username="perm_lead").member
        guild = GuildFactory(guild_lead=lead, cubby_payee=lead)
        client.post(reverse("hub_admin_member_guild_lead_clear", args=[lead.pk, guild.pk]))
        guild.refresh_from_db()
        assert guild.guild_lead is None and guild.cubby_payee is None


def describe_cubbies_home():
    def it_refuses_someone_who_runs_no_guild(client: Client):
        _login(client, "runs_nothing")
        assert client.get(reverse("hub_cubbies_home")).status_code == 403

    def it_refuses_an_admin_previewing_as_a_guest(client: Client):
        # A guest preview has no member behind it, so there is no guild to run (#1015).
        _login(client, "guest_preview_admin", fog_role=Member.FogRole.ADMIN)
        session = client.session
        session["view_as_role"] = "guest"
        session.save()
        guild = GuildFactory(name="Guest Guild")
        assert client.get(reverse("hub_cubbies_home")).status_code == 403
        assert client.get(reverse("hub_guild_cubbies", args=[guild.pk])).status_code == 403

    def it_opens_the_first_guild_or_the_one_asked_for(client: Client):
        _login(client, "home_admin", fog_role=Member.FogRole.ADMIN)
        alpha, beta = GuildFactory(name="Alpha Guild"), GuildFactory(name="Beta Guild")
        assert client.get(reverse("hub_cubbies_home")).url == reverse("hub_guild_cubbies", args=[alpha.pk])
        asked = client.get(reverse("hub_cubbies_home"), {"guild": beta.pk})
        assert asked.url == reverse("hub_guild_cubbies", args=[beta.pk])
        crafted = client.get(reverse("hub_cubbies_home"), {"guild": "²"})
        assert crafted.url == reverse("hub_guild_cubbies", args=[alpha.pk])


def describe_layout():
    def it_shows_the_empty_state_and_the_switch_off(client: Client):
        _member, guild = _lead(client)
        content = client.get(reverse("hub_guild_cubbies", args=[guild.pk])).content.decode()
        assert "data-cubby-empty" in content
        assert "pl-cubby-live--off" in content
        assert 'aria-current="page">Layout</a>' in content
        assert "css/cubbies.css" in content
        assert "data-cubby-guild-picker" not in content
        tabs = content.split("data-cubby-tabs", 1)[1].split("</nav>", 1)[0]
        assert reverse("hub_guild_cubbies_rentals", args=[guild.pk]) in tabs
        assert 'pl-cubby-tabcount">0<' in tabs

    def it_draws_each_unit_with_cubbies_and_add_squares(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild, name="Glaze Wall", rows=1, columns=3)
        cubby = CubbyFactory(unit=unit, label="Q4", width=2)
        content = client.get(reverse("hub_guild_cubbies", args=[guild.pk])).content.decode()
        assert f'id="cubby-unit-{unit.pk}"' in content
        assert reverse("hub_cubby_edit", args=[cubby.pk]) in content
        assert 'class="pl-cubby-cell--wide"' in content
        assert 'data-cubby-slot="1-3"' in content
        assert 'data-cubby-slot="1-2"' not in content
        assert "pl-cubby-grid--cols-3" in content

    def it_folds_archived_units_and_cubbies_under_show_archived(client: Client):
        _member, guild = _lead(client)
        ShelfUnitFactory(guild=guild, name="Old Rack", archived_at=timezone.now())
        unit = ShelfUnitFactory(guild=guild)
        old = CubbyFactory(unit=unit, label="Q9", archived_at=timezone.now())
        content = client.get(reverse("hub_guild_cubbies", args=[guild.pk])).content.decode()
        assert "Show Archived (2)" in content
        assert reverse("hub_cubby_restore", args=[old.pk]) in content

    def it_gives_an_admin_the_guild_picker(client: Client):
        _login(client, "picker_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Alpha Guild")
        GuildFactory(name="Beta Guild")
        content = client.get(reverse("hub_guild_cubbies", args=[guild.pk])).content.decode()
        assert "data-cubby-guild-picker" in content
        assert "Beta Guild" in content


def describe_add_shelf_unit():
    def it_adds_a_unit_to_the_guild_in_the_url(client: Client):
        _member, guild = _lead(client)
        response = client.post(
            reverse("hub_shelf_unit_add", args=[guild.pk]),
            {"new-unit-name": "Glaze Wall", "new-unit-rows": "3", "new-unit-columns": "4"},
        )
        unit = ShelfUnit.objects.get(name="Glaze Wall")
        assert unit.guild == guild
        assert response.url.endswith(f"#cubby-unit-{unit.pk}")

    def it_reopens_the_modal_with_the_error_on_the_field(client: Client):
        _member, guild = _lead(client)
        response = client.post(
            reverse("hub_shelf_unit_add", args=[guild.pk]),
            {"new-unit-name": "", "new-unit-rows": "3", "new-unit-columns": "9"},
        )
        content = response.content.decode()
        assert response.status_code == 200
        assert "Give the shelf a name." in content
        assert "$dispatch('open-modal', 'cubby-add-unit')" in content
        assert not ShelfUnit.objects.exists()


def describe_unit_card():
    def it_steps_a_row_more_and_swaps_the_card_back_saved(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        response = client.post(reverse("hub_shelf_unit_save", args=[unit.pk]), _unit_post(unit, step="rows:1"), **HTMX)
        unit.refresh_from_db()
        assert unit.rows == 4
        content = response.content.decode()
        assert "data-cubby-saved" in content
        assert "<output data-cubby-rows>4</output>" in content

    def it_renames_without_a_step(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        data = _unit_post(unit, selected="7")
        data[f"unit-{unit.pk}-name"] = "Bisque Rack"
        content = client.post(reverse("hub_shelf_unit_save", args=[unit.pk]), data, **HTMX).content.decode()
        unit.refresh_from_db()
        assert unit.name == "Bisque Rack"
        assert 'name="selected" value="7"' in content

    def it_refuses_shrinking_past_a_cubby_and_names_it(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild, columns=4)
        CubbyFactory(unit=unit, label="K4", column=4)
        response = client.post(
            reverse("hub_shelf_unit_save", args=[unit.pk]), _unit_post(unit, step="columns:-1"), **HTMX
        )
        content = response.content.decode()
        assert "data-cubby-unit-error" in content
        assert "Cubby K4 reaches spot 4 across" in content
        assert "data-cubby-saved" not in content
        unit.refresh_from_db()
        assert unit.columns == 4

    def it_ignores_a_garbled_step_and_reads_a_bad_number_as_the_stored_size(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        for step in ("rows:5", "height:1", "rows"):
            client.post(reverse("hub_shelf_unit_save", args=[unit.pk]), _unit_post(unit, step=step), **HTMX)
        data = _unit_post(unit, step="rows:-1")
        data[f"unit-{unit.pk}-rows"] = "lots"
        client.post(reverse("hub_shelf_unit_save", args=[unit.pk]), data, **HTMX)
        unit.refresh_from_db()
        assert unit.rows == 2

    def it_lands_back_on_layout_without_htmx_and_flashes_a_refusal(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        CubbyFactory(unit=unit, label="K3", row=3)
        ok = client.post(reverse("hub_shelf_unit_save", args=[unit.pk]), _unit_post(unit, step="columns:1"))
        assert ok.url.endswith(f"#cubby-unit-{unit.pk}")
        refused = client.post(
            reverse("hub_shelf_unit_save", args=[unit.pk]), _unit_post(unit, step="rows:-1"), follow=True
        )
        assert "Cubby K3 sits in row 3" in refused.content.decode()

    def it_puts_a_blank_name_back_on_the_field(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild, name="Kept Name")
        data = _unit_post(unit)
        data[f"unit-{unit.pk}-name"] = ""
        content = client.post(reverse("hub_shelf_unit_save", args=[unit.pk]), data, **HTMX).content.decode()
        assert "Give the shelf a name." in content
        unit.refresh_from_db()
        assert unit.name == "Kept Name"

    def it_will_not_save_an_archived_unit(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild, archived_at=timezone.now())
        assert client.post(reverse("hub_shelf_unit_save", args=[unit.pk]), _unit_post(unit)).status_code == 404


def describe_archive_and_restore_units():
    def it_archives_once_and_restores_once(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        client.post(reverse("hub_shelf_unit_archive", args=[unit.pk]))
        unit.refresh_from_db()
        archived_at = unit.archived_at
        assert archived_at is not None
        client.post(reverse("hub_shelf_unit_archive", args=[unit.pk]))
        unit.refresh_from_db()
        assert unit.archived_at == archived_at
        client.post(reverse("hub_shelf_unit_restore", args=[unit.pk]))
        client.post(reverse("hub_shelf_unit_restore", args=[unit.pk]))
        unit.refresh_from_db()
        assert not unit.is_archived

    def it_refuses_a_restore_whose_label_is_taken(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild, archived_at=timezone.now())
        CubbyFactory(unit=unit, label="M1")
        CubbyFactory(unit=ShelfUnitFactory(guild=guild), label="M1")
        response = client.post(reverse("hub_shelf_unit_restore", args=[unit.pk]), follow=True)
        assert "already called M1" in response.content.decode()
        unit.refresh_from_db()
        assert unit.is_archived


def describe_cubby_page():
    def it_opens_on_the_square_tapped_beside_its_unit_only(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild, name="Glaze Wall")
        ShelfUnitFactory(guild=guild, name="Other Rack")
        response = client.get(reverse("hub_cubby_add", args=[unit.pk]), {"row": "2", "column": "x"})
        content = response.content.decode()
        assert "data-cubby-panel" in content
        assert "New Cubby on Glaze Wall" in content
        assert '<option value="2" selected>2</option>' in content
        assert "Other Rack" not in content.split("data-cubby-panel", 1)[0].split("pl-cubby-build", 1)[1]

    def it_places_a_cubby(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        response = client.post(reverse("hub_cubby_add", args=[unit.pk]), _cubby_post(label="A1", shape="big"))
        cubby = Cubby.objects.get(label="A1")
        assert (cubby.unit, cubby.width, cubby.height, cubby.uses_guild_price) == (unit, 2, 2, True)
        assert response.url.endswith(f"#cubby-unit-{unit.pk}")

    def it_puts_a_refused_spot_on_its_field(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild, columns=4)
        CubbyFactory(unit=unit, label="A1")
        response = client.post(
            reverse("hub_cubby_add", args=[unit.pk]), _cubby_post(label="a1", column="4", shape="wide")
        )
        content = response.content.decode()
        assert response.status_code == 200
        assert "is 4 across, so it does not fit there." in content
        assert "Another cubby in the guild is already called a1." in content
        assert Cubby.objects.count() == 1

    def it_refuses_a_spot_taken_between_the_check_and_the_save(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        taken = ValidationError({"column": "That spot overlaps cubby R2."})
        with patch("hub.cubby_views.cubbies.save_cubby", side_effect=taken):
            content = client.post(reverse("hub_cubby_add", args=[unit.pk]), _cubby_post()).content.decode()
        assert "That spot overlaps cubby R2." in content

    def it_shows_a_whole_form_refusal_when_the_shelf_was_archived_meanwhile(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        with patch("hub.cubby_views.cubbies.save_cubby", side_effect=ValidationError("Restore its shelf first.")):
            content = client.post(reverse("hub_cubby_add", args=[unit.pk]), _cubby_post()).content.decode()
        assert "Restore its shelf first." in content.split("data-cubby-panel", 1)[1]
        assert not Cubby.objects.exists()

    def it_ignores_the_hidden_price_boxes_while_set_its_own_price_is_off(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        response = client.post(reverse("hub_cubby_add", args=[unit.pk]), _cubby_post(floor="ten", suggested="0.001"))
        assert response.status_code == 302
        assert Cubby.objects.get().uses_guild_price

    def it_saves_its_own_price_and_refuses_a_bad_one(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        missing = client.post(reverse("hub_cubby_add", args=[unit.pk]), _cubby_post(set_own_price="on"))
        assert "Enter the minimum, or turn off Set Its Own Price." in missing.content.decode()
        assert "Enter the suggested amount, or turn off Set Its Own Price." in missing.content.decode()
        low = client.post(
            reverse("hub_cubby_add", args=[unit.pk]), _cubby_post(set_own_price="on", floor="20", suggested="10")
        )
        assert "at least the minimum, $20" in low.content.decode()
        bad = client.post(
            reverse("hub_cubby_add", args=[unit.pk]), _cubby_post(set_own_price="on", floor="ten", suggested="10")
        )
        assert "Enter dollars, like 15 or 15.50." in bad.content.decode()
        client.post(
            reverse("hub_cubby_add", args=[unit.pk]), _cubby_post(set_own_price="on", floor="25", suggested="30.50")
        )
        cubby = Cubby.objects.get()
        assert (cubby.floor_cents, cubby.suggested_cents) == (2500, 3050)

    def it_edits_a_cubby_and_goes_back_to_the_guild_price(client: Client):
        _member, guild = _lead(client)
        cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild), label="D1", floor_cents=2500, suggested_cents=3000)
        page = client.get(reverse("hub_cubby_edit", args=[cubby.pk])).content.decode()
        assert "Cubby D1" in page
        assert "pl-cubby--selected" in page
        assert 'value="25"' in page
        client.post(
            reverse("hub_cubby_edit", args=[cubby.pk]),
            _cubby_post(
                label="D9", out_of_service="on", photo=SimpleUploadedFile("c.png", PNG, content_type="image/png")
            ),
        )
        cubby.refresh_from_db()
        assert cubby.label == "D9" and cubby.out_of_service and cubby.uses_guild_price
        assert cubby.photo

    def it_will_not_open_an_archived_cubby(client: Client):
        _member, guild = _lead(client)
        cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild), archived_at=timezone.now())
        assert client.get(reverse("hub_cubby_edit", args=[cubby.pk])).status_code == 404


def describe_archive_restore_and_photo():
    def it_archives_once_and_restores_a_cubby(client: Client):
        _member, guild = _lead(client)
        cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild))
        client.post(reverse("hub_cubby_archive", args=[cubby.pk]))
        client.post(reverse("hub_cubby_archive", args=[cubby.pk]))
        cubby.refresh_from_db()
        assert cubby.is_archived
        client.post(reverse("hub_cubby_restore", args=[cubby.pk]))
        client.post(reverse("hub_cubby_restore", args=[cubby.pk]))
        cubby.refresh_from_db()
        assert not cubby.is_archived

    def it_refuses_a_restore_onto_a_filled_square(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild)
        cubby = CubbyFactory(unit=unit, label="A1", archived_at=timezone.now())
        CubbyFactory(unit=unit, label="A2")
        response = client.post(reverse("hub_cubby_restore", args=[cubby.pk]), follow=True)
        assert "That spot overlaps cubby A2." in response.content.decode()

    def it_refuses_restoring_a_cubby_onto_an_archived_shelf(client: Client):
        _member, guild = _lead(client)
        unit = ShelfUnitFactory(guild=guild, archived_at=timezone.now())
        cubby = CubbyFactory(unit=unit, label="A1", archived_at=timezone.now())
        response = client.post(reverse("hub_cubby_restore", args=[cubby.pk]), follow=True)
        assert "Restore its shelf first." in response.content.decode()
        cubby.refresh_from_db()
        assert cubby.is_archived

    def it_removes_a_photo(client: Client, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        _member, guild = _lead(client)
        cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild), photo=SimpleUploadedFile("p.png", PNG))
        response = client.post(reverse("hub_cubby_photo_delete", args=[cubby.pk]))
        cubby.refresh_from_db()
        assert not cubby.photo
        assert response.url == reverse("hub_cubby_edit", args=[cubby.pk])
        assert client.post(reverse("hub_cubby_photo_delete", args=[cubby.pk])).status_code == 302

    def it_will_not_touch_an_archived_cubbys_photo(client: Client):
        _member, guild = _lead(client)
        cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild), archived_at=timezone.now())
        assert client.post(reverse("hub_cubby_photo_delete", args=[cubby.pk])).status_code == 404


def describe_pricing():
    def it_shows_the_guild_price_and_who_may_be_paid(client: Client):
        lead, guild = _lead(client)
        treasurer = User.objects.create_user(username="maya_okafor").member
        treasurer.full_legal_name = "Maya Okafor"
        treasurer.save()
        GuildStaffMembership.objects.create(guild=guild, member=treasurer, role="treasurer")
        content = client.get(reverse("hub_guild_cubbies_pricing", args=[guild.pk])).content.decode()
        assert 'aria-current="page">Pricing</a>' in content
        assert 'value="15"' in content and 'value="20"' in content
        assert "No one yet" in content.split("Maya Okafor")[0]
        assert "Maya Okafor (Treasurer)" in content
        assert f"{lead.display_name} (Guild Lead)" in content

    def it_saves_the_price_and_payee(client: Client):
        lead, guild = _lead(client)
        response = client.post(
            reverse("hub_guild_cubbies_pricing", args=[guild.pk]),
            {"floor": "12.50", "suggested": "18", "cubby_payee": str(lead.pk)},
        )
        guild.refresh_from_db()
        assert (guild.cubby_floor_cents, guild.cubby_suggested_cents, guild.cubby_payee) == (1250, 1800, lead)
        assert response.url == reverse("hub_guild_cubbies_pricing", args=[guild.pk])

    def it_shows_the_payees_payout_state(client: Client):
        lead, guild = _lead(client)
        guild.cubby_payee = lead
        guild.save()
        settings_row = BillingSettings.load()
        settings_row.connect_enabled = True
        settings_row.save()
        content = client.get(reverse("hub_guild_cubbies_pricing", args=[guild.pk])).content.decode()
        assert 'data-payee-status="self_not_set_up"' in content

    def it_refuses_a_floor_under_a_dollar_and_a_payee_off_the_staff(client: Client):
        _lead_member, guild = _lead(client)
        stranger = User.objects.create_user(username="a_stranger").member
        response = client.post(
            reverse("hub_guild_cubbies_pricing", args=[guild.pk]),
            {"floor": "0.50", "suggested": "1", "cubby_payee": str(stranger.pk)},
        )
        content = response.content.decode()
        assert response.status_code == 200
        assert "at least $1 a month" in content
        assert "Pick the guild lead or someone on the guild&#x27;s staff." in content
        guild.refresh_from_db()
        assert guild.cubby_floor_cents == 1500 and guild.cubby_payee is None


def describe_tab_switch():
    def it_autosaves_from_guild_settings(client: Client):
        _member, guild = _lead(client)
        response = client.post(
            reverse("hub_guild_cubbies_settings_save", args=[guild.pk]), {"show_cubbies_tab": "on"}, **AUTOSAVE
        )
        assert json.loads(response.content)["saved"] is True
        guild.refresh_from_db()
        assert guild.show_cubbies_tab

    def it_swaps_the_managers_strip_back_with_a_toast(client: Client):
        _member, guild = _lead(client)
        response = client.post(
            reverse("hub_guild_cubbies_settings_save", args=[guild.pk]),
            {"strip": "1", "cubbies-show_cubbies_tab": "on"},
            **HTMX,
        )
        assert "Also in Guild Settings" in response.content.decode()
        assert "Cubbies tab on." in response["HX-Trigger"]
        off = client.post(reverse("hub_guild_cubbies_settings_save", args=[guild.pk]), {"strip": "1"}, **HTMX)
        assert "Cubbies tab off." in off["HX-Trigger"]
        assert "pl-cubby-live--off" in off.content.decode()

    def it_lands_where_a_plain_post_came_from(client: Client):
        _member, guild = _lead(client)
        strip = client.post(reverse("hub_guild_cubbies_settings_save", args=[guild.pk]), {"strip": "1"})
        assert strip.url == reverse("hub_guild_cubbies", args=[guild.pk])
        settings_page = client.post(reverse("hub_guild_cubbies_settings_save", args=[guild.pk]), {})
        assert settings_page.url.endswith("?tab=cubbies")


def describe_forms():
    def it_draws_a_dollar_box_with_its_sign():
        html = MoneyInput().render("floor", "15")
        assert 'class="pl-cubby-money"' in html and 'inputmode="decimal"' in html and 'value="15"' in html

    def it_draws_the_shape_chips_with_the_picked_one_checked():
        html = ShapeChips(choices=Cubby.Shape.choices).render("shape", "tall", {"id": "id_shape"})
        assert 'value="tall" id="id_shape_2" checked' in html
        assert html.count('class="on"') == 1 + 2 + 2 + 4

    def it_reads_no_cents_as_no_dollars():
        assert _dollars(None) is None

    def it_opens_the_price_boxes_as_posted_or_as_stored():
        unit = ShelfUnitFactory()
        assert CubbyForm(_cubby_post(set_own_price="on"), unit=unit).own_price_on
        assert not CubbyForm(_cubby_post(), unit=unit).own_price_on
        own = CubbyFactory(unit=unit, floor_cents=2500, suggested_cents=2500)
        assert CubbyForm(instance=own, unit=unit).own_price_on
        assert not CubbyForm(unit=unit).own_price_on

    def it_asks_for_a_missing_guild_price_and_saves_without_commit():
        guild = GuildFactory()
        missing = CubbyPricingForm({"floor": "", "suggested": "20"}, instance=guild)
        assert missing.errors["floor"] == ["Enter an amount."]
        form = CubbyPricingForm({"floor": "9", "suggested": "9"}, instance=guild)
        assert form.is_valid(), form.errors
        assert form.save(commit=False).cubby_floor_cents == 900
        guild.refresh_from_db()
        assert guild.cubby_floor_cents == 1500

    def it_saves_with_commit():
        unit = ShelfUnitFactory()
        form = CubbyForm(_cubby_post(label="S1"), unit=unit)
        assert form.is_valid(), form.errors
        assert form.save().pk is not None
