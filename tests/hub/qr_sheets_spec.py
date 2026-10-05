"""BDD specs for the equipment and orientation QR sheets (#631).

An equipment sheet at ``/equipment/<slug>/flyer/`` and an orientation sheet at
``/orientations/types/<pk>/flyer/``, each with SVG and PNG QR downloads, open only to the
people who run that tool or orientation; everyone else gets 403 on all three. A retired
tool or a turned off type is refused with the reason. The QRs encode URLs that survive a
rename (the equipment page by its set once slug, the orientation by a pk permalink), and a
logged out scan goes through login and lands on the target. The Print QR Sheet links show
only where runners work. Names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse

from membership.models import AdminCapability, Equipment, Member, OrientationType
from membership.qr import qr_png_bytes, qr_svg
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

BASE = "https://members.pastlives.test"


@pytest.fixture(autouse=True)
def _member_base_url():
    with override_settings(MEMBER_BASE_URL=BASE):
        yield


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = f"Qrsheet {username}"
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["fog_role", "full_legal_name", "status"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _equipment_urls(equipment: Equipment) -> list[str]:
    return [
        reverse("hub_equipment_flyer", args=[equipment.slug]),
        reverse("hub_equipment_qr", args=[equipment.slug, "svg"]),
        reverse("hub_equipment_qr", args=[equipment.slug, "png"]),
    ]


def _type_urls(orientation_type: OrientationType) -> list[str]:
    return [
        reverse("hub_orientation_type_flyer", args=[orientation_type.pk]),
        reverse("hub_orientation_type_qr", args=[orientation_type.pk, "svg"]),
        reverse("hub_orientation_type_qr", args=[orientation_type.pk, "png"]),
    ]


def _statuses(client: Client, urls: list[str]) -> list[int]:
    return [client.get(url).status_code for url in urls]


# ---------------------------------------------------------------------------
# Who may open the equipment sheet
# ---------------------------------------------------------------------------

#: Each runner role: given the client and the tool's guild, sign in someone who runs the tool.
EQUIPMENT_RUNNERS: dict[str, Callable[..., Member]] = {
    "admin": lambda client, equipment: _login(client, "eq_admin", fog_role=Member.FogRole.ADMIN),
    "equipment_capability": lambda client, equipment: _capability_holder(client, "eq_cap"),
    "guild_lead": lambda client, equipment: _guild_lead(client, "eq_lead", equipment),
    "guild_staff": lambda client, equipment: _guild_staffer(client, "eq_gstaff", equipment),
    "equipment_staff": lambda client, equipment: _equipment_staffer(client, "eq_staff", equipment),
}


def _capability_holder(client: Client, username: str) -> Member:
    member = _login(client, username)
    member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
    return member


def _guild_lead(client: Client, username: str, equipment: Equipment) -> Member:
    member = _login(client, username)
    assert equipment.guild is not None
    equipment.guild.guild_lead = member
    equipment.guild.save(update_fields=["guild_lead"])
    return member


def _guild_staffer(client: Client, username: str, equipment: Equipment) -> Member:
    member = _login(client, username)
    GuildStaffMembershipFactory(guild=equipment.guild, member=member)
    return member


def _equipment_staffer(client: Client, username: str, equipment: Equipment) -> Member:
    member = _login(client, username)
    EquipmentStaffMembershipFactory(equipment=equipment, member=member)
    return member


def _tool(**kwargs: object) -> Equipment:
    return EquipmentFactory(name="Qrsheet Bandsaw", guild=GuildFactory(name="Qrsheet Woodshop"), **kwargs)


def describe_equipment_sheet_access():
    @pytest.mark.parametrize("role", sorted(EQUIPMENT_RUNNERS))
    def it_opens_the_sheet_and_both_downloads_to_each_runner(client: Client, role: str):
        equipment = _tool()
        member = EQUIPMENT_RUNNERS[role](client, equipment)
        assert equipment.is_run_by(member) or role in ("admin", "equipment_capability")
        assert _statuses(client, _equipment_urls(equipment)) == [200, 200, 200]

    def it_forbids_a_plain_member(client: Client):
        equipment = _tool()
        _login(client, "eq_plain")
        assert _statuses(client, _equipment_urls(equipment)) == [403, 403, 403]

    def it_forbids_a_guild_officer_who_runs_nothing_here(client: Client):
        # Officers get no blanket equipment grant (can_manage_equipment's site tier is admin only).
        equipment = _tool()
        _login(client, "eq_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        assert _statuses(client, _equipment_urls(equipment)) == [403, 403, 403]

    def it_forbids_the_lead_of_another_guild(client: Client):
        equipment = _tool()
        GuildFactory(name="Qrsheet Elsewhere", guild_lead=_login(client, "eq_other_lead"))
        assert _statuses(client, _equipment_urls(equipment)) == [403, 403, 403]

    def it_forbids_the_staff_of_another_tool(client: Client):
        equipment = _tool()
        EquipmentStaffMembershipFactory(equipment=EquipmentFactory(name="Qrsheet Lathe"), member=_login(client, "eq_x"))
        assert _statuses(client, _equipment_urls(equipment)) == [403, 403, 403]

    def it_sends_a_logged_out_visitor_to_login(client: Client):
        equipment = _tool()
        for url in _equipment_urls(equipment):
            response = client.get(url)
            assert response.status_code == 302
            assert response["Location"].startswith("/accounts/login/")

    def it_404s_an_unknown_tool_and_an_unknown_format(client: Client):
        equipment = _tool()
        _login(client, "eq_fmt_admin", fog_role=Member.FogRole.ADMIN)
        assert client.get(reverse("hub_equipment_flyer", args=["qrsheet-nothing"])).status_code == 404
        assert client.get(reverse("hub_equipment_qr", args=[equipment.slug, "pdf"])).status_code == 404


def describe_a_retired_tool():
    def it_refuses_a_runner_and_says_why(client: Client):
        equipment = _tool(is_active=False)
        _equipment_staffer(client, "eq_retired", equipment)
        for url in _equipment_urls(equipment):
            response = client.get(url)
            assert response.status_code == 403
            assert response.content.decode() == equipment.qr_sheet_refusal
        assert "retired" in equipment.qr_sheet_refusal

    def it_gives_a_non_runner_the_plain_refusal(client: Client):
        equipment = _tool(is_active=False)
        _login(client, "eq_retired_plain")
        response = client.get(_equipment_urls(equipment)[0])
        assert response.status_code == 403
        assert "retired" not in response.content.decode()


# ---------------------------------------------------------------------------
# What the equipment sheet carries
# ---------------------------------------------------------------------------


def describe_equipment_sheet_content():
    def it_encodes_the_equipment_page_and_names_it_where_it_is(client: Client):
        equipment = _tool(location_note="Back corner of the qrsheet shop", description="Cuts curves in qrsheet stock.")
        _equipment_staffer(client, "eq_content", equipment)
        target = f"{BASE}/equipment/{equipment.slug}/"
        assert equipment.qr_url == target
        body = client.get(_equipment_urls(equipment)[0]).content.decode()
        assert 'data-qr-sheet="equipment"' in body
        assert "Qrsheet Bandsaw" in body
        assert "Back corner of the qrsheet shop" in body
        assert "Cuts curves in qrsheet stock." in body
        assert qr_svg(target) in body
        assert 'data-qr-target="orientation"' not in body
        assert "hub-sidebar" not in body  # standalone print page, no member chrome

    def it_serves_the_same_qr_as_svg_and_png(client: Client):
        equipment = _tool()
        _equipment_staffer(client, "eq_dl", equipment)
        target = f"{BASE}/equipment/{equipment.slug}/"
        _, svg_url, png_url = _equipment_urls(equipment)
        svg = client.get(svg_url)
        assert svg["Content-Type"] == "image/svg+xml"
        assert svg["Content-Disposition"] == f'attachment; filename="{equipment.slug}-qr.svg"'
        assert svg.content.decode() == qr_svg(target)
        png = client.get(png_url)
        assert png["Content-Type"] == "image/png"
        assert png["Content-Disposition"] == f'attachment; filename="{equipment.slug}-qr.png"'
        assert png.content == qr_png_bytes(target)

    def it_keeps_its_qr_through_a_rename(client: Client):
        equipment = _tool()
        before = equipment.qr_url
        equipment.name = "Qrsheet Renamed Saw"
        equipment.save()
        equipment.refresh_from_db()
        assert equipment.qr_url == before
        assert client.get(before.removeprefix(BASE)).status_code == 302  # still routes (to login here)

    def it_adds_a_second_qr_to_book_the_required_orientation(client: Client):
        safety = GuildFactory(name="Qrsheet Safety")
        GuildOrientationSettingsFactory(guild=safety)
        orientation_type = OrientationTypeFactory(guild=safety, name="Qrsheet Saw Basics")
        equipment = _tool(required_orientation=orientation_type)
        _equipment_staffer(client, "eq_second", equipment)
        body = client.get(_equipment_urls(equipment)[0]).content.decode()
        assert 'data-qr-target="orientation"' in body
        assert "New here? Book the orientation first." in body
        assert "Qrsheet Saw Basics" in body
        assert qr_svg(f"{BASE}/orientations/types/{orientation_type.pk}/") in body

    def it_leaves_the_second_qr_off_when_the_owning_guild_has_orientations_switched_off(client: Client):
        switched_off = GuildFactory(name="Qrsheet Switched Off")
        GuildOrientationSettingsFactory(guild=switched_off, is_enabled=False)
        orientation_type = OrientationTypeFactory(guild=switched_off, name="Qrsheet Unbookable Basics")
        equipment = _tool(required_orientation=orientation_type)
        _equipment_staffer(client, "eq_second_switched_off", equipment)
        body = client.get(_equipment_urls(equipment)[0]).content.decode()
        assert 'data-qr-target="orientation"' not in body
        assert "Qrsheet Unbookable Basics" not in body
        assert equipment.qr_sheet_orientation is None

    def it_leaves_the_second_qr_off_while_that_orientation_is_turned_off(client: Client):
        orientation_type = OrientationTypeFactory(guild=GuildFactory(name="Qrsheet Paused"), is_active=False)
        equipment = _tool(required_orientation=orientation_type)
        _equipment_staffer(client, "eq_second_off", equipment)
        body = client.get(_equipment_urls(equipment)[0]).content.decode()
        assert 'data-qr-target="orientation"' not in body
        assert equipment.qr_sheet_orientation is None


# ---------------------------------------------------------------------------
# Who may open the orientation sheet
# ---------------------------------------------------------------------------


def _guild_type(**kwargs: object) -> OrientationType:
    guild = GuildFactory(name="Qrsheet Jewelry")
    GuildOrientationSettingsFactory(guild=guild)
    return OrientationTypeFactory(guild=guild, name="Qrsheet Torch Basics", **kwargs)


def _tool_type(*, equipment_active: bool = True) -> OrientationType:
    equipment = _tool(is_active=equipment_active)
    return OrientationTypeFactory(guild=None, equipment=equipment, name="Qrsheet Bandsaw Checkout")


def describe_orientation_sheet_access_for_a_guild_type():
    def it_opens_to_the_guild_lead(client: Client):
        orientation_type = _guild_type()
        assert orientation_type.guild is not None
        orientation_type.guild.guild_lead = _login(client, "ot_lead")
        orientation_type.guild.save(update_fields=["guild_lead"])
        assert _statuses(client, _type_urls(orientation_type)) == [200, 200, 200]

    def it_opens_to_guild_staff(client: Client):
        orientation_type = _guild_type()
        GuildStaffMembershipFactory(guild=orientation_type.guild, member=_login(client, "ot_staff"))
        assert _statuses(client, _type_urls(orientation_type)) == [200, 200, 200]

    @pytest.mark.parametrize("fog_role", [Member.FogRole.ADMIN, Member.FogRole.GUILD_OFFICER])
    def it_opens_to_admins_and_officers(client: Client, fog_role: str):
        orientation_type = _guild_type()
        _login(client, f"ot_{fog_role}", fog_role=fog_role)
        assert _statuses(client, _type_urls(orientation_type)) == [200, 200, 200]

    def it_forbids_a_plain_member(client: Client):
        orientation_type = _guild_type()
        _login(client, "ot_plain")
        assert _statuses(client, _type_urls(orientation_type)) == [403, 403, 403]

    def it_forbids_the_lead_of_another_guild(client: Client):
        orientation_type = _guild_type()
        GuildFactory(name="Qrsheet Other Guild", guild_lead=_login(client, "ot_other"))
        assert _statuses(client, _type_urls(orientation_type)) == [403, 403, 403]

    def it_forbids_an_equipment_administrator(client: Client):
        # The EQUIPMENT capability reaches equipment owned types only.
        orientation_type = _guild_type()
        _capability_holder(client, "ot_cap")
        assert _statuses(client, _type_urls(orientation_type)) == [403, 403, 403]

    def it_sends_a_logged_out_visitor_to_login(client: Client):
        orientation_type = _guild_type()
        for url in _type_urls(orientation_type):
            response = client.get(url)
            assert response.status_code == 302
            assert response["Location"].startswith("/accounts/login/")


def describe_orientation_sheet_access_for_a_tool_type():
    @pytest.mark.parametrize("role", sorted(EQUIPMENT_RUNNERS))
    def it_opens_to_each_runner_of_the_tool(client: Client, role: str):
        orientation_type = _tool_type()
        assert orientation_type.equipment is not None
        EQUIPMENT_RUNNERS[role](client, orientation_type.equipment)
        assert _statuses(client, _type_urls(orientation_type)) == [200, 200, 200]

    def it_forbids_a_plain_member(client: Client):
        orientation_type = _tool_type()
        _login(client, "tt_plain")
        assert _statuses(client, _type_urls(orientation_type)) == [403, 403, 403]

    def it_forbids_a_guild_officer(client: Client):
        orientation_type = _tool_type()
        _login(client, "tt_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        assert _statuses(client, _type_urls(orientation_type)) == [403, 403, 403]

    def it_forbids_the_staff_of_another_tool(client: Client):
        orientation_type = _tool_type()
        EquipmentStaffMembershipFactory(equipment=EquipmentFactory(name="Qrsheet Drill"), member=_login(client, "tt_x"))
        assert _statuses(client, _type_urls(orientation_type)) == [403, 403, 403]

    def it_404s_an_unknown_type_and_an_unknown_format(client: Client):
        orientation_type = _tool_type()
        _login(client, "tt_fmt", fog_role=Member.FogRole.ADMIN)
        assert client.get(reverse("hub_orientation_type_flyer", args=[orientation_type.pk + 999])).status_code == 404
        assert client.get(reverse("hub_orientation_type_qr", args=[orientation_type.pk, "gif"])).status_code == 404


def describe_an_orientation_that_cannot_print():
    def it_refuses_a_turned_off_type_and_says_why(client: Client):
        orientation_type = _guild_type(is_active=False)
        _login(client, "off_admin", fog_role=Member.FogRole.ADMIN)
        for url in _type_urls(orientation_type):
            response = client.get(url)
            assert response.status_code == 403
            assert response.content.decode() == orientation_type.qr_sheet_refusal
        assert "turned off" in orientation_type.qr_sheet_refusal

    def it_refuses_a_type_on_retired_equipment_and_says_why(client: Client):
        orientation_type = _tool_type(equipment_active=False)
        _login(client, "off_tool_admin", fog_role=Member.FogRole.ADMIN)
        response = client.get(_type_urls(orientation_type)[0])
        assert response.status_code == 403
        assert response.content.decode() == orientation_type.qr_sheet_refusal
        assert "retired" in orientation_type.qr_sheet_refusal

    def it_refuses_a_type_whose_guild_has_orientations_switched_off_and_says_how_to_fix_it(client: Client):
        orientation_type = _guild_type()
        assert orientation_type.guild is not None
        orientation_type.guild.orientation_settings.is_enabled = False
        orientation_type.guild.orientation_settings.save()
        orientation_type.guild.guild_lead = _login(client, "off_guild_lead")
        orientation_type.guild.save(update_fields=["guild_lead"])
        refusal = orientation_type.qr_sheet_refusal
        assert "Orientations are switched off for Qrsheet Jewelry" in refusal
        assert "Offer orientation booking" in refusal
        assert "Orientations tab" in refusal
        for url in _type_urls(orientation_type):
            response = client.get(url)
            assert response.status_code == 403
            assert response.content.decode() == refusal

    def it_refuses_a_guild_type_with_no_orientation_settings_at_all():
        orientation_type = OrientationTypeFactory(guild=GuildFactory(name="Qrsheet Unset Guild"), name="Qrsheet Unset")
        assert "switched off for Qrsheet Unset Guild" in orientation_type.qr_sheet_refusal

    def it_still_prints_for_a_hidden_guild_with_orientations_on(client: Client):
        orientation_type = _guild_type()
        assert orientation_type.guild is not None
        orientation_type.guild.is_active = False
        orientation_type.guild.save(update_fields=["is_active"])
        _login(client, "hidden_guild_admin", fog_role=Member.FogRole.ADMIN)
        assert orientation_type.qr_sheet_refusal == ""
        assert _statuses(client, _type_urls(orientation_type)) == [200, 200, 200]

    def it_has_nothing_to_refuse_for_an_active_type():
        assert _guild_type().qr_sheet_refusal == ""
        assert _tool_type().qr_sheet_refusal == ""


# ---------------------------------------------------------------------------
# What the orientation sheet carries, and where its QR goes
# ---------------------------------------------------------------------------


def describe_orientation_sheet_content():
    def it_names_the_orientation_its_owner_and_its_price_when_paid(client: Client):
        orientation_type = _guild_type(price_cents=2500, default_location="Qrsheet bench three")
        _login(client, "content_admin", fog_role=Member.FogRole.ADMIN)
        body = client.get(_type_urls(orientation_type)[0]).content.decode()
        assert 'data-qr-sheet="orientation"' in body
        assert "Qrsheet Torch Basics" in body
        assert "Run by Qrsheet Jewelry" in body
        assert "data-qr-sheet-price" in body
        assert "$25" in body
        assert "Qrsheet bench three" in body
        assert qr_svg(f"{BASE}/orientations/types/{orientation_type.pk}/") in body

    def it_leaves_the_price_off_a_free_orientation(client: Client):
        orientation_type = _guild_type()
        _login(client, "free_admin", fog_role=Member.FogRole.ADMIN)
        assert "data-qr-sheet-price" not in client.get(_type_urls(orientation_type)[0]).content.decode()

    def it_serves_the_same_qr_as_svg_and_png(client: Client):
        orientation_type = _guild_type()
        _login(client, "dl_admin", fog_role=Member.FogRole.ADMIN)
        target = f"{BASE}/orientations/types/{orientation_type.pk}/"
        _, svg_url, png_url = _type_urls(orientation_type)
        svg = client.get(svg_url)
        assert svg["Content-Disposition"] == f'attachment; filename="orientation-{orientation_type.pk}-qr.svg"'
        assert svg.content.decode() == qr_svg(target)
        png = client.get(png_url)
        assert png["Content-Type"] == "image/png"
        assert png.content == qr_png_bytes(target)

    def it_keeps_its_qr_through_a_rename_of_the_type_and_its_guild():
        orientation_type = _guild_type()
        before = orientation_type.qr_url
        orientation_type.name = "Qrsheet Renamed Torch"
        orientation_type.save()
        assert orientation_type.guild is not None
        orientation_type.guild.name = "Qrsheet Renamed Guild"
        orientation_type.guild.save()
        orientation_type.refresh_from_db()
        assert orientation_type.qr_url == before == f"{BASE}/orientations/types/{orientation_type.pk}/"


def describe_the_orientation_permalink():
    def it_sends_a_member_to_the_card_on_the_orientations_page_when_listed(client: Client):
        orientation_type = _guild_type()
        _login(client, "perma_member")
        response = client.get(reverse("hub_orientation_type_permalink", args=[orientation_type.pk]))
        assert response.status_code == 302
        assert response["Location"] == f"/orientations/#orientation-type-{orientation_type.pk}"

    def it_sends_a_member_to_the_owner_page_when_the_page_does_not_list_it(client: Client):
        orientation_type = _guild_type()
        assert orientation_type.guild is not None
        orientation_type.guild.orientation_settings.is_enabled = False
        orientation_type.guild.orientation_settings.save()
        _login(client, "perma_unlisted")
        response = client.get(reverse("hub_orientation_type_permalink", args=[orientation_type.pk]))
        assert response.status_code == 302
        assert response["Location"] == orientation_type.orientation_anchor_path()

    def it_sends_a_tool_type_to_its_card_too(client: Client):
        orientation_type = _tool_type()
        _login(client, "perma_tool")
        response = client.get(reverse("hub_orientation_type_permalink", args=[orientation_type.pk]))
        assert response["Location"] == f"/orientations/#orientation-type-{orientation_type.pk}"

    def it_404s_an_unknown_type(client: Client):
        _login(client, "perma_404")
        assert client.get(reverse("hub_orientation_type_permalink", args=[987654])).status_code == 404


# ---------------------------------------------------------------------------
# A logged out scan: login first, then the target
# ---------------------------------------------------------------------------


def _scan_and_log_in(client: Client, path: str) -> str:
    """Scan ``path`` logged out, log in by emailed code, and return where login lands."""
    MembershipPlanFactory()
    user = User.objects.create_user(username="scanner", email="qrsheet-scanner@example.com")
    EmailAddress.objects.filter(user=user).delete()
    EmailAddress.objects.create(user=user, email="qrsheet-scanner@example.com", verified=True, primary=True)

    scan = client.get(path)
    assert scan.status_code == 302
    assert scan["Location"] == f"/accounts/login/?next={path}"

    mail.outbox.clear()
    requested = client.post("/accounts/login/code/", {"email": "qrsheet-scanner@example.com", "next": path})
    assert requested.status_code == 302
    code = mail.outbox[-1].body.split("Past Lives Makerspace is:")[1].split()[0]
    done = client.post("/accounts/login/code/confirm/", {"code": code, "next": path})
    assert done.status_code == 302
    return done["Location"]


def describe_a_logged_out_scan():
    def it_logs_in_then_lands_on_the_equipment_page(client: Client):
        equipment = _tool()
        path = equipment.qr_url.removeprefix(BASE)
        assert _scan_and_log_in(client, path) == path
        assert client.get(path).status_code == 200

    def it_logs_in_then_lands_on_the_orientation_booking(client: Client):
        orientation_type = _guild_type()
        path = orientation_type.qr_url.removeprefix(BASE)
        assert _scan_and_log_in(client, path) == path
        response = client.get(path)
        assert response["Location"] == f"/orientations/#orientation-type-{orientation_type.pk}"


# ---------------------------------------------------------------------------
# Where the Print QR Sheet links sit, and who sees them
# ---------------------------------------------------------------------------


def describe_the_print_links():
    def it_shows_on_the_equipment_page_to_a_runner_only(client: Client):
        equipment = _tool()
        flyer = reverse("hub_equipment_flyer", args=[equipment.slug])
        detail = reverse("hub_equipment_detail", args=[equipment.slug])
        _equipment_staffer(client, "link_staff", equipment)
        body = client.get(detail).content.decode()
        assert "data-qr-sheet-link" in body
        assert f'href="{flyer}"' in body
        client.logout()
        _login(client, "link_plain")
        body = client.get(detail).content.decode()
        assert "data-qr-sheet-link" not in body
        assert flyer not in body

    def it_hides_on_a_retired_tool(client: Client):
        equipment = _tool(is_active=False)
        _login(client, "link_retired", fog_role=Member.FogRole.ADMIN)
        for name in ("hub_equipment_detail", "hub_equipment_manage"):
            assert "data-qr-sheet-link" not in client.get(reverse(name, args=[equipment.slug])).content.decode()

    def it_shows_on_the_manage_page_with_one_link_per_active_type(client: Client):
        equipment = _tool()
        active = OrientationTypeFactory(guild=None, equipment=equipment, name="Qrsheet Active Checkout")
        off = OrientationTypeFactory(guild=None, equipment=equipment, name="Qrsheet Off Checkout", is_active=False)
        _equipment_staffer(client, "link_manage", equipment)
        body = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
        assert f'href="{reverse("hub_equipment_flyer", args=[equipment.slug])}"' in body
        assert f'href="{reverse("hub_orientation_type_flyer", args=[active.pk])}"' in body
        assert reverse("hub_orientation_type_flyer", args=[off.pk]) not in body

    def it_shows_per_active_type_on_the_guild_orientations_tab(client: Client):
        active = _guild_type()
        assert active.guild is not None
        off = OrientationTypeFactory(guild=active.guild, name="Qrsheet Retired Torch", is_active=False)
        active.guild.guild_lead = _login(client, "link_guild_lead")
        active.guild.save(update_fields=["guild_lead"])
        body = client.get(f"{reverse('hub_guild_edit', args=[active.guild.pk])}?tab=orientations").content.decode()
        assert body.count("data-qr-sheet-type-link") == 1
        assert f'href="{reverse("hub_orientation_type_flyer", args=[active.pk])}"' in body
        assert reverse("hub_orientation_type_flyer", args=[off.pk]) not in body


def describe_the_bookings_tab_menu():
    def _tab(client: Client) -> str:
        response = client.get("/orientations/", {"view": "bookings"})
        assert response.status_code == 200
        return response.content.decode()

    def it_lists_the_printable_types_a_runner_runs(client: Client):
        lead = _login(client, "menu_lead")
        mine = _guild_type()
        assert mine.guild is not None
        mine.guild.guild_lead = lead
        mine.guild.save(update_fields=["guild_lead"])
        off = OrientationTypeFactory(guild=mine.guild, name="Qrsheet Off Torch", is_active=False)
        other = OrientationTypeFactory(guild=GuildFactory(name="Qrsheet Not Mine"), name="Qrsheet Other Type")
        body = _tab(client)
        assert "data-bookings-qr-sheet " in body
        assert "data-bookings-qr-sheet-menu" in body
        assert f'href="{reverse("hub_orientation_type_flyer", args=[mine.pk])}"' in body
        assert reverse("hub_orientation_type_flyer", args=[off.pk]) not in body
        assert reverse("hub_orientation_type_flyer", args=[other.pk]) not in body

    def it_lists_a_tool_type_for_its_staff_but_not_one_on_retired_gear(client: Client):
        staffer = _login(client, "menu_tool")
        live = _tool_type()
        assert live.equipment is not None
        EquipmentStaffMembershipFactory(equipment=live.equipment, member=staffer)
        retired = EquipmentFactory(name="Qrsheet Retired Gear", is_active=False)
        gone = OrientationTypeFactory(guild=None, equipment=retired, name="Qrsheet Gone Checkout")
        EquipmentStaffMembershipFactory(equipment=retired, member=staffer)
        body = _tab(client)
        assert reverse("hub_orientation_type_flyer", args=[live.pk]) in body
        assert reverse("hub_orientation_type_flyer", args=[gone.pk]) not in body

    def it_leaves_out_a_guild_with_orientations_switched_off_but_keeps_a_hidden_one(client: Client):
        lead = _login(client, "menu_switched_lead")
        switched_off = GuildFactory(name="Qrsheet Menu Off", guild_lead=lead)
        GuildOrientationSettingsFactory(guild=switched_off, is_enabled=False)
        off_type = OrientationTypeFactory(guild=switched_off, name="Qrsheet Menu Off Type")
        hidden = GuildFactory(name="Qrsheet Menu Hidden", guild_lead=lead, is_active=False)
        GuildOrientationSettingsFactory(guild=hidden)
        hidden_type = OrientationTypeFactory(guild=hidden, name="Qrsheet Menu Hidden Type")
        body = _tab(client)
        assert reverse("hub_orientation_type_flyer", args=[off_type.pk]) not in body
        assert f'href="{reverse("hub_orientation_type_flyer", args=[hidden_type.pk])}"' in body

    def it_never_shows_for_a_plain_member(client: Client):
        _guild_type()
        _login(client, "menu_plain")
        body = _tab(client)
        assert "data-bookings-qr-sheet" not in body
