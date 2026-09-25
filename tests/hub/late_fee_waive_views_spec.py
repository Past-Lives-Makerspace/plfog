"""BDD specs for waiving a late cancellation fee from the hub (#456, part 3).

The waive POST by each allowed role and the 403 naming the governing owner (with the toast
for an htmx caller), the required reason, a paid fee, ``next`` safety and login; the
respond page's fee card in each state with the Waive control only while unpaid; and the
equipment manage tab's fee list, its empty state, its gate on the site switch, and its one
query batch.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth.models import User
from django.db import connection
from django.http import HttpResponse
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from billing import late_fees
from billing.models import LateCancellationFee
from core.models import SiteActivity, SiteConfiguration
from membership.models import Equipment, EquipmentReservation, Guild, Member, OrientationBooking
from tests.billing.factories import LateCancellationFeeFactory
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.preferred_name = username.replace("_", " ").title()
    member.save(update_fields=["fog_role", "status", "preferred_name"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _site(*, enabled: bool = True) -> None:
    config = SiteConfiguration.load()
    config.late_cancel_fees_enabled = enabled
    config.save()


def _messages(response: HttpResponse) -> list[str]:
    return [str(m) for m in django_messages.get_messages(response.wsgi_request)]


def _guild_fee(*, guild: Guild | None = None, member: Member | None = None, **overrides: Any) -> LateCancellationFee:
    settings_obj = (
        GuildOrientationSettingsFactory(guild=guild) if guild is not None else GuildOrientationSettingsFactory()
    )
    slot = OrientationSlotFactory(guild=settings_obj.guild)
    booking = OrientationBookingFactory(
        slot=slot, member=member or MemberFactory(), status=OrientationBooking.Status.CANCELLED
    )
    return LateCancellationFeeFactory(orientation_booking=booking, **overrides)


def _equipment_orientation_fee(equipment: Equipment, **overrides: Any) -> LateCancellationFee:
    orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
    starts = timezone.now() - timedelta(days=2)
    slot = OrientationSlotFactory(
        equipment_owned=True, orientation_type=orientation_type, starts_at=starts, ends_at=starts + timedelta(hours=1)
    )
    booking = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CANCELLED)
    return LateCancellationFeeFactory(orientation_booking=booking, **overrides)


def _reservation_fee(equipment: Equipment, **overrides: Any) -> LateCancellationFee:
    reservation = EquipmentReservationFactory(equipment=equipment, status=EquipmentReservation.Status.CANCELLED)
    return LateCancellationFeeFactory(for_reservation=True, reservation=reservation, **overrides)


def _waive(client: Client, fee: LateCancellationFee, *, reason: str = "Family emergency", **extra: Any) -> HttpResponse:
    data = {f"waive-{fee.pk}-reason": reason, "next": reverse("hub_home")}
    data.update(extra)
    return client.post(reverse("hub_late_fee_waive", args=[fee.pk]), data)


def describe_hub_late_fee_waive():
    def it_lets_an_admin_waive_a_guild_fee_and_returns_to_next_with_a_message(client: Client):
        user = _login(client, "lfw_admin", fog_role=Member.FogRole.ADMIN)
        fee = _guild_fee()
        response = _waive(client, fee, next=reverse("hub_orientation_respond", args=[fee.orientation_booking_id]))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_orientation_respond", args=[fee.orientation_booking_id])
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.WAIVED
        assert fee.waived_by == user
        assert fee.waived_reason == "Family emergency"
        assert any("can book again" in m for m in _messages(response))
        assert late_fees.unpaid_fee_for(fee.member) is None
        assert SiteActivity.objects.get(kind=SiteActivity.Kind.LATE_FEE_WAIVED).actor == user

    def it_lets_the_governing_guilds_staff_waive(client: Client):
        user = _login(client, "lfw_staff")
        fee = _guild_fee()
        GuildStaffMembershipFactory(guild=fee.orientation_booking.guild, member=user.member)
        assert _waive(client, fee).status_code == 302
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.WAIVED

    def it_lets_the_governing_guilds_lead_waive(client: Client):
        user = _login(client, "lfw_lead")
        fee = _guild_fee()
        guild = fee.orientation_booking.guild
        guild.guild_lead = user.member
        guild.save(update_fields=["guild_lead"])
        assert _waive(client, fee).status_code == 302
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.WAIVED

    def it_lets_the_governing_equipments_manager_waive_a_reservation_fee(client: Client):
        user = _login(client, "lfw_manager")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        fee = _reservation_fee(equipment)
        assert _waive(client, fee).status_code == 302
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.WAIVED

    def it_lets_the_governing_equipments_manager_waive_its_orientation_fee(client: Client):
        user = _login(client, "lfw_manager_o")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        fee = _equipment_orientation_fee(equipment)
        assert _waive(client, fee).status_code == 302
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.WAIVED

    def it_403s_a_lead_of_another_guild_naming_the_governing_guild(client: Client):
        user = _login(client, "lfw_other_lead")
        GuildFactory(name="Art Framing", guild_lead=user.member)
        fee = _guild_fee(guild=GuildFactory(name="Ceramics"))
        response = _waive(client, fee)
        assert response.status_code == 403
        assert response.content.decode() == "Only Ceramics staff or an admin can waive this fee."
        assert not response.has_header("HX-Trigger")
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.UNPAID

    def it_403s_a_manager_of_another_equipment_naming_the_governing_equipment(client: Client):
        user = _login(client, "lfw_other_mgr")
        EquipmentStaffMembershipFactory(member=user.member)
        fee = _reservation_fee(EquipmentFactory(name="Laser Cutter"))
        response = _waive(client, fee)
        assert response.status_code == 403
        assert response.content.decode() == "Only the Laser Cutter managers or an admin can waive this fee."

    def it_403s_a_plain_member(client: Client):
        _login(client, "lfw_plain")
        assert _waive(client, _guild_fee()).status_code == 403

    def it_toasts_the_owner_sentence_for_an_htmx_caller(client: Client):
        _login(client, "lfw_htmx")
        fee = _guild_fee(guild=GuildFactory(name="Ceramics"))
        response = client.post(
            reverse("hub_late_fee_waive", args=[fee.pk]),
            {f"waive-{fee.pk}-reason": "x"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 403
        toast = json.loads(response["HX-Trigger"])["showToast"]
        assert toast["type"] == "error"
        assert toast["message"] == "Only Ceramics staff or an admin can waive this fee."

    def it_requires_a_reason(client: Client):
        _login(client, "lfw_noreason", fog_role=Member.FogRole.ADMIN)
        fee = _guild_fee()
        response = _waive(client, fee, reason="   ")
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_home")
        assert any("reason is required" in m for m in _messages(response))
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.UNPAID

    def it_refuses_a_paid_fee_with_the_services_sentence(client: Client):
        _login(client, "lfw_paid", fog_role=Member.FogRole.ADMIN)
        fee = _guild_fee(status=LateCancellationFee.Status.PAID, stripe_payment_id="pi_paid")
        response = _waive(client, fee)
        assert response.status_code == 302
        assert any("This fee is already paid." in m for m in _messages(response))
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID

    def it_falls_back_to_the_owner_page_for_an_offsite_or_missing_next(client: Client):
        _login(client, "lfw_next", fog_role=Member.FogRole.ADMIN)
        offsite = _guild_fee()
        response = _waive(client, offsite, next="https://evil.example/steal")
        assert response["Location"] == offsite.owner_page_path()
        missing = _guild_fee()
        response = _waive(client, missing, next="")
        assert response["Location"] == missing.owner_page_path()

    def it_404s_an_unknown_fee(client: Client):
        _login(client, "lfw_404", fog_role=Member.FogRole.ADMIN)
        assert client.post(reverse("hub_late_fee_waive", args=[999999]), {"next": "/"}).status_code == 404

    def it_rejects_get(client: Client):
        _login(client, "lfw_get", fog_role=Member.FogRole.ADMIN)
        assert client.get(reverse("hub_late_fee_waive", args=[_guild_fee().pk])).status_code == 405

    def it_requires_login(client: Client):
        response = _waive(client, _guild_fee())
        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]


def describe_respond_page_fee_card():
    def _page(client: Client, fee: LateCancellationFee) -> str:
        response = client.get(reverse("hub_orientation_respond", args=[fee.orientation_booking_id]))
        assert response.status_code == 200
        return response.content.decode()

    def it_shows_an_unpaid_fee_with_the_waive_control_to_the_guilds_staff(client: Client):
        user = _login(client, "rpc_staff")
        fee = _guild_fee()
        GuildStaffMembershipFactory(guild=fee.orientation_booking.guild, member=user.member)
        content = _page(client, fee)
        assert f'data-late-fee-card="{fee.pk}"' in content
        assert 'data-late-fee-state="unpaid"' in content
        assert f"'waive-fee-{fee.pk}'" in content
        assert f'data-late-fee-waive="{fee.pk}"' in content
        assert f'action="{reverse("hub_late_fee_waive", args=[fee.pk])}"' in content
        assert f'value="{reverse("hub_orientation_respond", args=[fee.orientation_booking_id])}"' in content
        assert f'name="waive-{fee.pk}-reason"' in content
        assert 'hx-boost="false"' in content[content.index(f'data-late-fee-waive="{fee.pk}"') - 200 :]
        assert fee.item_label in content

    def it_shows_a_paid_fee_without_the_waive_control(client: Client):
        _login(client, "rpc_paid", fog_role=Member.FogRole.ADMIN)
        fee = _guild_fee(status=LateCancellationFee.Status.PAID, paid_at=timezone.now())
        content = _page(client, fee)
        assert 'data-late-fee-state="paid"' in content
        assert "data-late-fee-waive" not in content
        assert reverse("hub_late_fee_waive", args=[fee.pk]) not in content

    def it_shows_who_waived_a_fee_and_when(client: Client):
        user = _login(client, "rpc_waived", fog_role=Member.FogRole.ADMIN)
        fee = _guild_fee(
            status=LateCancellationFee.Status.WAIVED,
            waived_by=user,
            waived_at=timezone.make_aware(timezone.datetime(2026, 9, 12, 10, 0)),
        )
        content = _page(client, fee)
        assert 'data-late-fee-state="waived"' in content
        assert "Waived by Rpc Waived on Sep 12" in content
        assert "data-late-fee-waive" not in content

    def it_shows_a_refunded_fee(client: Client):
        _login(client, "rpc_refunded", fog_role=Member.FogRole.ADMIN)
        fee = _guild_fee(status=LateCancellationFee.Status.REFUNDED, paid_at=timezone.now())
        assert 'data-late-fee-state="refunded"' in _page(client, fee)

    def it_renders_no_card_for_a_booking_without_a_fee(client: Client):
        _login(client, "rpc_none", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        assert "data-late-fee-card" not in content


def describe_equipment_manage_fee_list():
    def _manage(client: Client, equipment: Equipment) -> str:
        response = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations")
        assert response.status_code == 200
        return response.content.decode()

    def _manager(client: Client, username: str) -> Equipment:
        user = _login(client, username)
        equipment = EquipmentFactory(name="CNC Router")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        return equipment

    def it_lists_the_equipments_fees_newest_first_with_waive_only_while_unpaid(client: Client):
        equipment = _manager(client, "eml_rows")
        paid = _reservation_fee(equipment, status=LateCancellationFee.Status.PAID, paid_at=timezone.now())
        unpaid_reservation = _reservation_fee(equipment)
        unpaid_orientation = _equipment_orientation_fee(equipment)
        _reservation_fee(EquipmentFactory(name="Other"))  # another equipment's fee stays out
        content = _manage(client, equipment)
        assert f'data-late-fee-list="{equipment.pk}"' in content
        rows = [int(chunk.split('"', 1)[0]) for chunk in content.split('data-late-fee-row="')[1:]]
        assert rows == [unpaid_orientation.pk, unpaid_reservation.pk, paid.pk]
        assert content.count("data-late-fee-waive=") == 2
        assert f'data-late-fee-waive="{unpaid_reservation.pk}"' in content
        assert f'data-late-fee-waive="{unpaid_orientation.pk}"' in content
        assert f'data-late-fee-waive="{paid.pk}"' not in content
        assert f'name="waive-{unpaid_reservation.pk}-reason"' in content
        next_value = f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations"
        assert f'value="{next_value}"' in content
        assert unpaid_reservation.member.display_name in content
        assert unpaid_orientation.item_label in content

    def it_shows_the_empty_state_while_the_site_switch_is_on(client: Client):
        _site()
        equipment = _manager(client, "eml_empty")
        content = _manage(client, equipment)
        assert f'data-late-fee-list="{equipment.pk}"' in content
        assert "No late fees here." in content

    def it_hides_the_card_while_the_switch_is_off_and_no_fee_exists(client: Client):
        _site(enabled=False)
        equipment = _manager(client, "eml_off")
        assert "data-late-fee-list" not in _manage(client, equipment)

    def it_keeps_the_card_while_the_switch_is_off_but_fees_exist(client: Client):
        _site(enabled=False)
        equipment = _manager(client, "eml_off_fees")
        fee = _reservation_fee(equipment, status=LateCancellationFee.Status.WAIVED)
        content = _manage(client, equipment)
        assert f'data-late-fee-row="{fee.pk}"' in content
        assert 'data-late-fee-state="waived"' in content

    def it_reads_the_fees_in_one_batch(client: Client):
        equipment = _manager(client, "eml_batch")
        url = f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations"
        _reservation_fee(equipment)
        _equipment_orientation_fee(equipment)
        assert client.get(url).status_code == 200  # warm up: singleton rows and caches settle on the first render
        with CaptureQueriesContext(connection) as two_fees:
            assert client.get(url).status_code == 200
        for _ in range(3):
            _reservation_fee(equipment)
            _reservation_fee(equipment, status=LateCancellationFee.Status.WAIVED)
        with CaptureQueriesContext(connection) as eight_fees:
            assert client.get(url).status_code == 200
        assert len(eight_fees) == len(two_fees)
