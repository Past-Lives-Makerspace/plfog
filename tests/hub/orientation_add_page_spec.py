"""BDD specs for the Add an Orientation page (#680).

``/orientations/add/`` is one form: a Guild select first, offering exactly the viewer's
``guilds_for_new_orientation``, then every orientation type field. Save creates a guild owned
type and lands on that guild's Orientations page. A guild outside the viewer's list is a form
error, never a created row. Assertions anchor on field names, URLs and rows, not on copy.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from membership.models import Guild, Member, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    LocationFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
    tiny_png_bytes,
)

pytestmark = pytest.mark.django_db

ADD_PAGE = "/orientations/add/"
OPTION = re.compile(r'<option value="(\d+)"( selected)?')


def _login(client: Client, username: str, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _guild_select(content: bytes) -> str:
    html = content.decode()
    start = html.index('name="guild"')
    return html[start : html.index("</select>", start)]


def _choices(content: bytes) -> list[int]:
    """The Guild select's guild pks, in order."""
    return [int(pk) for pk, _ in OPTION.findall(_guild_select(content))]


def _selected(content: bytes) -> list[int]:
    return [int(pk) for pk, selected in OPTION.findall(_guild_select(content)) if selected]


def _post(guild: Guild, **fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "guild": str(guild.pk),
        "name": "Lathe Basics",
        "duration_minutes": "90",
        "default_seats": "3",
        "sort_order": "2",
        "default_location": "",
        "description": "",
        "is_active": "on",
    }
    data.update(fields)
    return data


def _orientations_page(guild: Guild) -> str:
    return reverse("hub_guild_orientations", args=[guild.pk])


def describe_the_gate():
    def it_refuses_a_plain_member_with_no_editable_guild(client: Client):
        _login(client, "oa_plain")
        GuildFactory(name="Not Mine")
        assert client.get(ADD_PAGE).status_code == 403
        assert client.post(ADD_PAGE, _post(Guild.objects.get())).status_code == 403
        assert not OrientationType.objects.exists()

    def it_refuses_an_admin_previewing_as_a_member(client: Client):
        _login(client, "oa_preview", Member.FogRole.ADMIN)
        GuildFactory(name="Preview Guild")
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        assert client.get(ADD_PAGE).status_code == 403

    def it_sends_a_logged_out_visitor_to_log_in(client: Client):
        response = client.get(ADD_PAGE)
        assert response.status_code == 302
        assert "login" in response["Location"]


def describe_the_guild_select():
    def it_comes_first_and_lists_every_active_guild_by_name_for_an_admin(client: Client):
        _login(client, "oa_admin", Member.FogRole.ADMIN)
        beta = GuildFactory(name="Beta Guild")
        alpha = GuildFactory(name="Alpha Guild")
        GuildFactory(name="Dormant Guild", is_active=False)
        gone = GuildFactory(name="Gone Guild")
        gone.soft_delete()
        content = client.get(ADD_PAGE).content
        assert _choices(content) == [alpha.pk, beta.pk]
        assert _selected(content) == []
        html = content.decode()
        assert html.index('name="guild"') < html.index('name="name"')

    def it_lists_only_the_guilds_a_lead_or_staffer_runs(client: Client):
        member = _login(client, "oa_lead")
        zinc = GuildFactory(name="Zinc Guild", guild_lead=member)
        amber = GuildFactory(name="Amber Guild")
        GuildStaffMembershipFactory(guild=amber, member=member)
        GuildFactory(name="Stranger Guild")
        assert _choices(client.get(ADD_PAGE).content) == [amber.pk, zinc.pk]

    def it_preselects_the_only_guild(client: Client):
        member = _login(client, "oa_single")
        guild = GuildFactory(name="Only Guild")
        GuildStaffMembershipFactory(guild=guild, member=member)
        content = client.get(ADD_PAGE).content
        assert _choices(content) == [guild.pk]
        assert _selected(content) == [guild.pk]

    def it_preselects_the_guild_in_the_query_when_it_is_listed(client: Client):
        _login(client, "oa_query", Member.FogRole.ADMIN)
        GuildFactory(name="First Guild")
        wanted = GuildFactory(name="Second Guild")
        assert _selected(client.get(f"{ADD_PAGE}?guild={wanted.pk}").content) == [wanted.pk]

    @pytest.mark.parametrize("asked", ["stranger", "nonsense", "missing"])
    def it_ignores_a_query_guild_that_is_not_listed(client: Client, asked: str):
        member = _login(client, "oa_query_other")
        GuildFactory(name="Mine A", guild_lead=member)
        GuildFactory(name="Mine B", guild_lead=member)
        stranger = GuildFactory(name="Stranger Guild")
        value = {"stranger": str(stranger.pk), "nonsense": "abc", "missing": "99999"}[asked]
        content = client.get(f"{ADD_PAGE}?guild={value}").content
        assert stranger.pk not in _choices(content)
        assert _selected(content) == []

    def it_renders_save_last_and_cancel_back_to_orientations(client: Client):
        member = _login(client, "oa_layout")
        GuildFactory(name="Layout Guild", guild_lead=member)
        EquipmentFactory(name="Bandsaw")
        html = client.get(ADD_PAGE).content.decode()
        form = html[
            html.index("data-orientation-add-form") : html.index("</form>", html.index("data-orientation-add-form"))
        ]
        assert form.rstrip().endswith('<button type="submit" class="pl-btn pl-btn--primary">Save</button>\n    </div>')
        assert f'href="{reverse("hub_orientations")}"' in form
        for field in ("price", "is_donation", "donation_minimum", "donation_suggested", "photo", "uses_equipment"):
            assert f'name="{field}"' in form


def describe_saving():
    def it_creates_a_fixed_price_type_with_every_field_and_lands_on_the_guild_page(client: Client):
        member = _login(client, "oa_save")
        guild = GuildFactory(name="Wood Guild", guild_lead=member)
        lathe = EquipmentFactory(name="Lathe")
        area = LocationFactory(name="Wood Shop Floor")
        photo = SimpleUploadedFile("card.png", tiny_png_bytes(), "image/png")
        response = client.post(
            ADD_PAGE,
            _post(
                guild,
                price="25.50",
                default_location="By the lathe",
                area=str(area.pk),
                uses_equipment=[str(lathe.pk)],
                description="Learn the lathe.",
                photo=photo,
            ),
        )
        assert response.status_code == 302
        assert response["Location"] == _orientations_page(guild)
        created = OrientationType.objects.get()
        assert created.guild == guild
        assert created.equipment is None
        assert (created.name, created.duration_minutes, created.default_seats, created.sort_order) == (
            "Lathe Basics",
            90,
            3,
            2,
        )
        assert (created.price_cents, created.is_donation) == (2550, False)
        assert (created.default_location, created.area, created.description) == (
            "By the lathe",
            area,
            "Learn the lathe.",
        )
        assert list(created.uses_equipment.all()) == [lathe]
        assert created.photo.name.startswith("orientations/photos/")
        assert created.is_active
        landed = client.get(response["Location"]).content.decode()
        assert 'value="Lathe Basics"' in landed
        assert mail.outbox == []

    def it_creates_a_free_inactive_type_when_price_is_blank_and_active_unticked(client: Client):
        member = _login(client, "oa_free")
        guild = GuildFactory(name="Free Guild", guild_lead=member)
        data = _post(guild, price="")
        del data["is_active"]
        client.post(ADD_PAGE, data)
        created = OrientationType.objects.get()
        assert (created.price_cents, created.is_donation, created.is_active) == (0, False, False)

    def it_creates_a_donation_type_with_its_minimum_and_suggestion(client: Client):
        member = _login(client, "oa_donation")
        guild = GuildFactory(name="Donation Guild", guild_lead=member)
        client.post(ADD_PAGE, _post(guild, is_donation="on", price="", donation_minimum="5", donation_suggested="20"))
        created = OrientationType.objects.get()
        assert (created.is_donation, created.donation_minimum_cents, created.donation_suggested_cents) == (
            True,
            500,
            2000,
        )

    def it_lets_an_admin_add_to_any_active_guild(client: Client):
        _login(client, "oa_admin_save", Member.FogRole.ADMIN)
        guild = GuildFactory(name="Admin Target Guild")
        response = client.post(ADD_PAGE, _post(guild))
        assert response["Location"] == _orientations_page(guild)
        assert OrientationType.objects.get().guild == guild


def describe_refusals():
    def it_refuses_a_guild_the_viewer_cannot_edit_and_creates_nothing(client: Client):
        member = _login(client, "oa_cross")
        GuildFactory(name="My Guild", guild_lead=member)
        stranger = GuildFactory(name="Stranger Guild")
        response = client.post(ADD_PAGE, _post(stranger))
        assert response.status_code == 200
        assert "guild" in response.context["form"].errors
        assert not OrientationType.objects.exists()

    def it_refuses_a_hidden_guild_for_an_admin(client: Client):
        _login(client, "oa_hidden", Member.FogRole.ADMIN)
        GuildFactory(name="Shown Guild")
        hidden = GuildFactory(name="Hidden Guild", is_active=False)
        response = client.post(ADD_PAGE, _post(hidden))
        assert "guild" in response.context["form"].errors
        assert not OrientationType.objects.exists()

    def it_refuses_a_name_the_guild_already_uses(client: Client):
        member = _login(client, "oa_dupe")
        guild = GuildFactory(name="Dupe Guild", guild_lead=member)
        OrientationTypeFactory(guild=guild, name="Lathe Basics")
        response = client.post(ADD_PAGE, _post(guild))
        assert "name" in response.context["form"].errors
        assert OrientationType.objects.count() == 1


def describe_an_invalid_save():
    def it_re_renders_with_errors_and_keeps_what_was_typed(client: Client):
        member = _login(client, "oa_invalid")
        guild = GuildFactory(name="Invalid Guild", guild_lead=member)
        GuildFactory(name="Other Guild", guild_lead=member)
        response = client.post(
            ADD_PAGE, _post(guild, name="", description="Keep me", default_location="Back room", price="900")
        )
        assert response.status_code == 200
        errors = response.context["form"].errors
        assert set(errors) == {"name", "price"}
        html = response.content.decode()
        assert "Keep me" in html
        assert 'value="Back room"' in html
        assert 'value="900"' in html
        assert _selected(response.content) == [guild.pk]
        assert not OrientationType.objects.exists()


def describe_the_guild_orientations_page_header():
    def it_links_its_add_button_to_the_add_page_with_the_guild_chosen(client: Client):
        member = _login(client, "oa_header")
        guild = GuildFactory(name="Header Guild", guild_lead=member)
        html = client.get(_orientations_page(guild)).content.decode()
        assert f'href="{ADD_PAGE}?guild={guild.pk}"' in html
        button = html[
            html.rindex("<", 0, html.index("data-add-orientation-type")) : html.index("data-add-orientation-type")
        ]
        assert button.startswith(f'<a href="{ADD_PAGE}?guild={guild.pk}"')

    def it_keeps_the_in_card_add_row_button(client: Client):
        member = _login(client, "oa_incard")
        guild = GuildFactory(name="In Card Guild", guild_lead=member)
        html = client.get(_orientations_page(guild)).content.decode()
        types_form = html[html.index('id="otypes-form"') : html.index("</form>", html.index('id="otypes-form"'))]
        assert "data-formset-add" in types_form
