"""#749 part 2 on the hub: the Pricing card's Who gets paid select, its clearing on the Staff tab,
its status line, the Reservations page payouts nudge and the Settings, Payouts tab copy.

Names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing.models import BillingSettings, PayoutAccount
from hub.forms import EquipmentSettingsForm
from membership.models import Equipment, EquipmentReservation, EquipmentStaffMembership, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str, name: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member  # type: ignore[attr-defined]
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _priced(**kwargs) -> Equipment:
    return EquipmentFactory(
        name=kwargs.pop("name", "Quillmarsh Router"),
        pricing=kwargs.pop("pricing", Equipment.Pricing.HOURLY),
        hourly_rate_cents=2500,
        **kwargs,
    )


def _manager_of(equipment: Equipment, name: str = "Sami Wrenfield") -> Member:
    member = MemberFactory(full_legal_name=name, preferred_name="")
    EquipmentStaffMembershipFactory(equipment=equipment, member=member)
    return member


def _payouts(on: bool = True) -> None:
    settings_obj = BillingSettings.load()
    settings_obj.connect_enabled = on
    settings_obj.save()


def _connect(member: Member, status: str = PayoutAccount.Status.ACTIVE) -> None:
    PayoutAccount.objects.create(
        member=member,
        stripe_account_id=f"acct_{member.pk}",
        livemode=False,
        status=status,
        active_since=timezone.now() - timedelta(days=5),
    )


def _settings(**overrides: str) -> dict[str, str]:
    return {
        "hours-TOTAL_FORMS": "0",
        "hours-INITIAL_FORMS": "0",
        "hours-MIN_NUM_FORMS": "0",
        "hours-MAX_NUM_FORMS": "1000",
        "reservations_open_shown": "1",
        "reservations_open": "on",
        "closed_message": "",
        "min_duration_minutes": "30",
        "max_duration_minutes": "240",
        "max_advance_days": "30",
        "max_active_reservations_per_member": "2",
        "pricing": "hourly",
        "hourly_rate": "25.00",
        **overrides,
    }


def _save(client: Client, equipment: Equipment, **overrides: str):
    return client.post(reverse("hub_equipment_hours_save", args=[equipment.slug]), _settings(**overrides))


def _card(client: Client, equipment: Equipment) -> str:
    content = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=hours").content.decode()
    start = content.index("data-pricing-card")
    return content[start : content.index("</form>", start)]


def describe_the_who_gets_paid_select():
    def it_lists_only_this_items_managers_with_no_one_yet_first():
        equipment = _priced()
        _manager_of(equipment, "Zed Quillfeather")
        _manager_of(equipment, "Amber Quillfeather")
        _manager_of(_priced(name="Elsewhere Lathe"), "Nobody Quillfeather")
        field = EquipmentSettingsForm(instance=equipment)["payee"]
        assert [str(label) for _, label in field.field.choices] == [
            "No one yet",
            "Amber Quillfeather",
            "Zed Quillfeather",
        ]
        assert field.label == "Who gets paid"

    def it_explains_the_split_in_the_tooltip_with_and_without_a_guild():
        guilded = _priced(guild=GuildFactory(name="Printmaking Guild"))
        assert EquipmentSettingsForm(instance=guilded).payee_tooltip == (
            "The person you pick gets 70% of each payment. The rest goes to the Printmaking Guild and Past Lives."
        )
        settings_obj = BillingSettings.load()
        settings_obj.reservation_manager_percent = Decimal("72.50")
        settings_obj.save()
        assert EquipmentSettingsForm(instance=_priced(name="Lone Kiln")).payee_tooltip == (
            "The person you pick gets 72.5% of each payment. The rest goes to Past Lives."
        )

    def it_saves_a_pick(client: Client):
        equipment = _priced()
        member = _login(client, "payeepicker", "Sami Wrenfield")
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        response = _save(client, equipment, payee=str(member.pk))
        assert response.status_code == 302
        equipment.refresh_from_db()
        assert equipment.payee == member

    def it_refuses_someone_who_is_not_a_manager(client: Client):
        equipment = _priced()
        member = _login(client, "payeeguard", "Sami Wrenfield")
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        outsider = MemberFactory(full_legal_name="Outsider Quillfeather")
        response = _save(client, equipment, payee=str(outsider.pk))
        assert response.status_code == 200
        assert "Pick one of this equipment&#x27;s managers." in response.content.decode()
        equipment.refresh_from_db()
        assert equipment.payee is None

    def it_keeps_the_pick_while_free_and_when_a_page_drawn_without_it_saves(client: Client):
        equipment = _priced()
        member = _login(client, "payeekeeper", "Sami Wrenfield")
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        _save(client, equipment, pricing="free", payee=str(member.pk))
        equipment.refresh_from_db()
        assert (equipment.pricing, equipment.payee) == (Equipment.Pricing.FREE, member)
        _save(client, equipment, pricing="free")  # no payee key: an older page
        equipment.refresh_from_db()
        assert equipment.payee == member

    def it_clears_the_pick_when_no_one_yet_is_chosen(client: Client):
        member = _login(client, "payeeclear", "Sami Wrenfield")
        equipment = _priced(payee=member)
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        _save(client, equipment, payee="")
        equipment.refresh_from_db()
        assert equipment.payee is None

    def it_hides_under_free_and_warns_when_a_price_is_set_and_nobody_is_picked(client: Client):
        equipment = _priced()
        member = _login(client, "payeewarn", "Sami Wrenfield")
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        card = _card(client, equipment)
        block = card[card.index("data-pricing-payee") - 200 : card.index("data-pricing-payee")]
        assert "x-show=\"pricing !== 'free'\"" in block
        assert "x-cloak" not in block
        warn = card[card.index("data-payee-warn") - 120 : card.index("data-payee-warn")]
        assert "x-cloak" not in warn
        assert (
            "No one is picked, so the whole payment stays with Past Lives. Pick a manager to pay them their share."
            in card
        )
        # The Who gets paid tooltip comes after every Limits tooltip, so the e2e nth(2) locator stays on Limits.
        assert "The person you pick gets 70% of each payment." in card

    def it_cloaks_the_warning_once_someone_is_picked_and_the_block_under_free(client: Client):
        member = _login(client, "payeecloak", "Sami Wrenfield")
        equipment = _priced(payee=member, pricing=Equipment.Pricing.FREE)
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        card = _card(client, equipment)
        assert "x-cloak data-pricing-payee" in card
        assert "x-cloak data-payee-warn" in card
        assert f'value="{member.pk}" selected' in card


def describe_removing_the_picked_manager():
    def it_clears_the_pick_on_the_staff_tab(client: Client):
        equipment = _priced()
        _login_member = _login(client, "staffremover", "Remover Quillfeather")
        EquipmentStaffMembershipFactory(equipment=equipment, member=_login_member)
        picked = _manager_of(equipment)
        Equipment.objects.filter(pk=equipment.pk).update(payee=picked)
        staff = EquipmentStaffMembership.objects.get(equipment=equipment, member=picked)
        response = client.post(reverse("hub_equipment_staff_remove", args=[equipment.slug, staff.pk]))
        assert response.status_code == 302
        equipment.refresh_from_db()
        assert equipment.payee is None

    def it_leaves_the_pick_when_another_manager_is_removed():
        equipment = _priced()
        picked = _manager_of(equipment)
        other = _manager_of(equipment, "Other Quillfeather")
        Equipment.objects.filter(pk=equipment.pk).update(payee=picked)
        EquipmentStaffMembership.objects.get(equipment=equipment, member=other).delete()
        equipment.refresh_from_db()
        assert equipment.payee == picked

    def it_leaves_another_items_pick_alone():
        equipment = _priced()
        picked = _manager_of(equipment)
        elsewhere = _priced(name="Elsewhere Press", payee=picked)
        EquipmentStaffMembershipFactory(equipment=elsewhere, member=picked)
        Equipment.objects.filter(pk=equipment.pk).update(payee=picked)
        EquipmentStaffMembership.objects.get(equipment=equipment, member=picked).delete()
        elsewhere.refresh_from_db()
        assert elsewhere.payee == picked


def describe_the_status_line():
    def it_is_absent_while_payouts_are_off(client: Client):
        member = _login(client, "statusoff", "Sami Wrenfield")
        equipment = _priced(payee=member)
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        assert "data-payee-status" not in _card(client, equipment)

    def it_tells_the_picked_person_to_set_up_payouts(client: Client):
        _payouts()
        member = _login(client, "statusself", "Sami Wrenfield")
        equipment = _priced(payee=member)
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        card = _card(client, equipment)
        assert f"x-show=\"payee === '{member.pk}'\"" in card
        assert 'data-payee-status="self_not_set_up"' in card
        assert "Your share is paid by hand at month end until you" in card
        assert f'href="{reverse("hub_user_settings")}?tab=payouts">set up payouts</a>' in card

    def it_names_the_picked_person_to_another_manager(client: Client):
        _payouts()
        equipment = _priced()
        viewer = _login(client, "statusother", "Viewer Quillfeather")
        EquipmentStaffMembershipFactory(equipment=equipment, member=viewer)
        picked = _manager_of(equipment)
        Equipment.objects.filter(pk=equipment.pk).update(payee=picked)
        _connect(picked, PayoutAccount.Status.NEEDS_INFO)  # started signup but not done still reads as not set up
        card = _card(client, equipment)
        assert 'data-payee-status="not_set_up"' in card
        assert "Sami hasn't set up payouts yet, so their share is paid by hand at month end." in card

    def it_shows_payouts_on_once_connected(client: Client):
        _payouts()
        member = _login(client, "statuson", "Sami Wrenfield")
        equipment = _priced(payee=member)
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        _connect(member)
        card = _card(client, equipment)
        assert 'data-payee-status="on"' in card
        assert "Payouts on" in card
        assert "paid by hand" not in card


def _index(client: Client) -> str:
    return client.get(reverse("hub_equipment_index")).content.decode()


def describe_the_reservations_page_nudge():
    def it_names_the_item_for_the_picked_payee_of_one_priced_item(client: Client):
        _payouts()
        member = _login(client, "nudgeone", "Sami Wrenfield")
        _priced(payee=member)
        content = _index(client)
        assert 'data-payouts-nudge="reservations"' in content
        assert "Get paid a few days after each reservation" in content
        assert (
            "your share of each paid reservation on the Quillmarsh Router is sent 48 hours after it starts." in content
        )
        assert content.index("data-payouts-nudge") < content.index('aria-label="Reservations views"')

    def it_says_equipment_you_manage_for_two_or_more(client: Client):
        _payouts()
        member = _login(client, "nudgetwo", "Sami Wrenfield")
        _priced(payee=member)
        _priced(name="Second Router", pricing=Equipment.Pricing.DONATION, payee=member)
        assert "each paid reservation on equipment you manage is sent" in _index(client)

    def it_stays_away_from_everyone_else(client: Client):
        member = _login(client, "nudgenone", "Sami Wrenfield")
        free = _priced(name="Free Saw", pricing=Equipment.Pricing.FREE, payee=member)
        assert "data-payouts-nudge" not in _index(client)  # payouts off
        _payouts()
        assert "data-payouts-nudge" not in _index(client)  # only a free item picks them
        EquipmentStaffMembershipFactory(equipment=_priced(name="Unpicked Router"), member=member)
        assert "data-payouts-nudge" not in _index(client)  # a manager who is not the payee
        Equipment.objects.filter(pk=free.pk).update(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500)
        assert "data-payouts-nudge" in _index(client)
        _connect(member)
        assert "data-payouts-nudge" not in _index(client)  # signup finished


def describe_the_payouts_tab_copy():
    def it_reads_what_and_date_and_lists_a_reservation_row(client: Client):
        _payouts()
        member = _login(client, "tabcopy", "Sami Wrenfield")
        equipment = _priced(payee=member)
        EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        start = timezone.now() - timedelta(days=2)
        reservation = EquipmentReservationFactory(
            equipment=equipment,
            member=MemberFactory(full_legal_name="Jane Doe", preferred_name=""),
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            status=EquipmentReservation.Status.CONFIRMED,
            amount_paid_cents=5000,
            stripe_payment_id="pi_tab",
        )
        content = client.get(reverse("hub_user_settings") + "?tab=payouts").content.decode()
        assert "<th>What</th><th>Date</th><th>Status</th>" in content
        assert "Class or orientation" not in content
        assert (
            "your share of every paid class, orientation or reservation you run lands in your account a few days "
            "after it happens." in content
        )
        assert 'Quillmarsh Router<span class="pl-payouts-sub">2 hours, Jane D.</span>' in content
        assert reservation.pk

    def it_reads_the_empty_state_for_all_three_kinds(client: Client):
        _payouts()
        member = _login(client, "tabempty", "Sami Wrenfield")
        EquipmentStaffMembershipFactory(equipment=_priced(), member=member)
        content = client.get(reverse("hub_user_settings") + "?tab=payouts").content.decode()
        assert (
            "No earnings yet. When someone pays for a class, orientation or reservation you run, it shows here "
            "with the day it will be sent." in content
        )
