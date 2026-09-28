"""BDD specs for the late cancellation fee service and model (#456, parts 2 and 3).

``charge_if_late`` on a late self cancel of each kind and not on an early one, the once
only guard, the site switch off, ``start_fee_checkout``'s session and attempt counter,
``mark_paid`` idempotent and race-safe with the receipt, the landing reconcile,
``unpaid_fee_for``, the model's labels and constraint, the activity kinds; and part 3's
``waive`` (each allowed role, the refusals, a paid fee refused, the block lifting, the
open session expired, the activity row, the email once).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail, signing
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from billing import late_fees
from billing.models import LateCancellationFee
from core.models import Notification, SiteActivity, SiteConfiguration
from hub.view_as import ViewAs
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

_SESSION = {"id": "cs_fee_1", "url": "https://checkout.stripe.example/cs_fee_1"}


def _site(*, enabled: bool = True) -> SiteConfiguration:
    config = SiteConfiguration.load()
    config.late_cancel_fees_enabled = enabled
    config.save()
    return config


def _member_with_user(username: str) -> Member:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com").member


def _reservation(*, fee_cents: int = 1500, hours_ahead: int = 3, member: Member | None = None) -> EquipmentReservation:
    starts = timezone.now() + timedelta(hours=hours_ahead)
    return EquipmentReservationFactory(
        equipment=EquipmentFactory(name="CNC Router", late_cancel_fee_cents=fee_cents),
        member=member or MemberFactory(),
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
    )


def _confirmed_booking(
    *, fee_cents: int = 1500, hours_ahead: int = 3, member: Member | None = None
) -> OrientationBooking:
    settings_obj = GuildOrientationSettingsFactory(late_cancel_fee_cents=fee_cents)
    starts = timezone.now() + timedelta(hours=hours_ahead)
    slot = OrientationSlotFactory(guild=settings_obj.guild, starts_at=starts, ends_at=starts + timedelta(hours=1))
    return OrientationBookingFactory(
        slot=slot, member=member or MemberFactory(), status=OrientationBooking.Status.CONFIRMED
    )


def describe_charge_if_late():
    def it_charges_a_late_self_cancel_of_a_reservation():
        _site()
        member = _member_with_user("cil_res")
        reservation = _reservation(member=member)

        fee = late_fees.charge_if_late(reservation)

        assert fee is not None
        assert fee.reservation == reservation
        assert fee.orientation_booking is None
        assert fee.member == member
        assert fee.amount_cents == 1500
        assert fee.status == LateCancellationFee.Status.UNPAID
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.LATE_FEE_CHARGED)
        assert row.actor == member.user
        assert row.target == fee
        assert row.payload == {"amount_cents": 1500, "item": fee.item_label}

    def it_charges_a_late_self_cancel_of_a_guild_orientation_booking():
        _site()
        booking = _confirmed_booking()
        fee = late_fees.charge_if_late(booking)
        assert fee is not None
        assert fee.orientation_booking == booking
        assert fee.reservation is None
        assert fee.amount_cents == 1500

    def it_charges_an_equipment_owned_booking_at_the_equipments_fee():
        _site()
        equipment = EquipmentFactory(late_cancel_fee_cents=2500)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        starts = timezone.now() + timedelta(hours=3)
        slot = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=starts,
            ends_at=starts + timedelta(hours=1),
        )
        booking = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        fee = late_fees.charge_if_late(booking)
        assert fee is not None
        assert fee.amount_cents == 2500

    def it_charges_nothing_for_an_early_cancel():
        _site()
        assert late_fees.charge_if_late(_reservation(hours_ahead=30)) is None
        assert late_fees.charge_if_late(_confirmed_booking(hours_ahead=30)) is None
        assert not LateCancellationFee.objects.exists()
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_CHARGED).exists()

    def it_charges_nothing_while_the_site_switch_is_off():
        _site(enabled=False)
        assert late_fees.charge_if_late(_reservation()) is None
        assert late_fees.charge_if_late(_confirmed_booking()) is None
        assert not LateCancellationFee.objects.exists()

    def it_charges_nothing_when_the_owner_sets_no_fee():
        _site()
        assert late_fees.charge_if_late(_reservation(fee_cents=0)) is None
        assert not LateCancellationFee.objects.exists()

    def it_charges_once_under_two_calls():
        _site()
        reservation = _reservation()
        first = late_fees.charge_if_late(reservation)
        second = late_fees.charge_if_late(reservation)
        assert first is not None
        assert second is not None
        assert first.pk == second.pk
        assert LateCancellationFee.objects.count() == 1
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_CHARGED).count() == 1

    def it_measures_lateness_at_the_moment_it_is_given():
        _site()
        reservation = _reservation(hours_ahead=30)
        # Seen from 28 hours later, the same reservation starts in two hours: late.
        fee = late_fees.charge_if_late(reservation, now=timezone.now() + timedelta(hours=28))
        assert fee is not None


def describe_start_fee_checkout():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_mints_a_session_tagged_late_cancel_fee_with_an_attempt_keyed_idempotency_key(mock_create):
        member = _member_with_user("sfc_mint")
        fee = LateCancellationFeeFactory(orientation_booking=OrientationBookingFactory(member=member))

        url = late_fees.start_fee_checkout(fee)

        assert url == _SESSION["url"]
        fee.refresh_from_db()
        assert fee.stripe_session_id == "cs_fee_1"
        assert fee.checkout_attempts == 1
        kwargs = mock_create.call_args.kwargs
        assert kwargs["amount_cents"] == 1500
        assert kwargs["product_name"] == f"Late cancellation fee: {fee.item_label}"
        assert kwargs["customer_email"] == member.primary_email
        assert kwargs["metadata"] == {"kind": "late_cancel_fee", "fee_id": str(fee.pk)}
        assert kwargs["idempotency_key"] == f"late-fee-{fee.pk}-1"
        assert "expires_at" not in kwargs
        # Both landings carry a token that reads back to this fee.
        for key, name in (("success_url", "hub_late_fee_return"), ("cancel_url", "hub_late_fee_checkout_cancelled")):
            path = kwargs[key].removeprefix("https://").split("/", 1)[1]
            token = f"/{path}".removeprefix(reverse(name, args=["x"]).rsplit("x/", 1)[0]).rstrip("/")
            assert late_fees.read_checkout_token(token).pk == fee.pk
            assert kwargs[key].startswith("http")

    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_mints_a_fresh_session_on_every_call_and_expires_the_previous_one(mock_create, mock_expire):
        fee = LateCancellationFeeFactory()
        late_fees.start_fee_checkout(fee)
        assert not mock_expire.called  # nothing to expire before the first mint
        mock_create.return_value = {"id": "cs_fee_2", "url": "https://checkout.stripe.example/cs_fee_2"}
        url = late_fees.start_fee_checkout(fee)
        assert url.endswith("cs_fee_2")
        # The first session is closed at the source, so two live pages can never both pay one fee.
        mock_expire.assert_called_once_with(session_id="cs_fee_1")
        fee.refresh_from_db()
        assert fee.stripe_session_id == "cs_fee_2"
        assert fee.checkout_attempts == 2
        assert mock_create.call_args.kwargs["idempotency_key"] == f"late-fee-{fee.pk}-2"

    @patch("billing.stripe_utils.expire_checkout_session", side_effect=RuntimeError("stripe down"))
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_still_mints_when_the_previous_session_cannot_be_expired(mock_create, mock_expire, caplog):
        fee = LateCancellationFeeFactory(stripe_session_id="cs_old")
        with caplog.at_level(logging.INFO, logger="billing.late_fees"):
            url = late_fees.start_fee_checkout(fee)
        assert url == _SESSION["url"]
        mock_expire.assert_called_once_with(session_id="cs_old")
        assert "Could not expire the previous Checkout session" in caplog.text
        fee.refresh_from_db()
        assert fee.stripe_session_id == "cs_fee_1"

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_a_fee_that_is_not_unpaid(mock_create):
        fee = LateCancellationFeeFactory(status=LateCancellationFee.Status.PAID)
        with pytest.raises(ValueError, match="not unpaid"):
            late_fees.start_fee_checkout(fee)
        assert not mock_create.called
        fee.refresh_from_db()
        assert fee.checkout_attempts == 0

    def it_rejects_a_tampered_or_stale_token():
        with pytest.raises(signing.BadSignature):
            late_fees.read_checkout_token("not-a-real-token")
        fee = LateCancellationFeeFactory()
        token = late_fees.make_checkout_token(fee)
        fee.delete()
        with pytest.raises(LateCancellationFee.DoesNotExist):
            late_fees.read_checkout_token(token)


def describe_mark_paid():
    def _unpaid(username: str = "mp_member") -> LateCancellationFee:
        member = _member_with_user(username)
        return LateCancellationFeeFactory(orientation_booking=OrientationBookingFactory(member=member))

    def it_flips_unpaid_to_paid_stamps_the_ids_logs_and_sends_the_receipt():
        fee = _unpaid()
        mail.outbox.clear()

        outcome = late_fees.mark_paid(fee, payment_intent="pi_fee_1", session_id="cs_fee_1", amount_total=1500)

        assert outcome == "paid"
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID
        assert fee.stripe_payment_id == "pi_fee_1"
        assert fee.stripe_session_id == "cs_fee_1"
        assert fee.paid_at is not None
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.LATE_FEE_PAID)
        assert row.actor == fee.member.user
        assert row.payload["amount_cents"] == 1500
        receipt = mail.outbox[0]
        assert receipt.to == [fee.member.primary_email]
        assert receipt.subject == "Your late cancellation fee is paid"
        assert "$15.00" in receipt.body
        assert fee.item_label in receipt.body
        assert late_fees.pay_url(fee) in receipt.body
        assert "[missing:" not in receipt.body
        assert "[missing:" not in receipt.alternatives[0][0]
        bell = Notification.objects.get(user=fee.member.user, trigger="billing.late_fee_paid")
        assert bell.url == reverse("hub_late_fee_detail", args=[fee.pk])

    def it_keeps_a_session_id_the_fee_already_had():
        fee = _unpaid("mp_keep")
        fee.stripe_session_id = "cs_first"
        fee.save(update_fields=["stripe_session_id"])
        late_fees.mark_paid(fee, payment_intent="pi_fee_2", session_id="cs_second", amount_total=1500)
        fee.refresh_from_db()
        assert fee.stripe_session_id == "cs_first"

    def it_pays_once_under_a_second_call():
        fee = _unpaid("mp_twice")
        mail.outbox.clear()
        late_fees.mark_paid(fee, payment_intent="pi_fee_3", session_id="cs_fee_3", amount_total=1500)
        assert (
            late_fees.mark_paid(fee, payment_intent="pi_fee_3", session_id="cs_fee_3", amount_total=1500) == "already"
        )
        assert len(mail.outbox) == 1
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_PAID).count() == 1

    def it_reports_already_for_a_waived_fee():
        fee = LateCancellationFeeFactory(status=LateCancellationFee.Status.WAIVED)
        assert late_fees.mark_paid(fee, payment_intent="pi_x", session_id="cs_x", amount_total=1500) == "already"
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.WAIVED

    def it_reports_gone_for_a_deleted_fee():
        fee = LateCancellationFeeFactory()
        fee.delete()
        assert late_fees.mark_paid(fee, payment_intent="pi_x", session_id="cs_x", amount_total=1500) == "gone"

    def it_logs_an_amount_that_does_not_match_the_fee(caplog):
        fee = _unpaid("mp_mismatch")
        with caplog.at_level(logging.WARNING, logger="billing.late_fees"):
            late_fees.mark_paid(fee, payment_intent="pi_fee_4", session_id="cs_fee_4", amount_total=1600)
        assert "1600 cents against a 1500 cent fee" in caplog.text
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID


def describe_reconcile_landed_checkout():
    def _retrieved(**overrides: Any) -> dict[str, Any]:
        session: dict[str, Any] = {
            "id": "cs_fee_1",
            "url": "https://checkout.stripe.example/cs_fee_1",
            "status": "open",
            "payment_status": "unpaid",
            "payment_intent": "",
            "amount_total": None,
        }
        session.update(overrides)
        return session

    def it_reports_pending_with_no_session_to_check():
        fee = LateCancellationFeeFactory()
        assert late_fees.reconcile_landed_checkout(fee) == "pending"

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_reports_unknown_when_stripe_is_unreachable(mock_retrieve):
        fee = LateCancellationFeeFactory(stripe_session_id="cs_fee_1")
        assert late_fees.reconcile_landed_checkout(fee) == "unknown"
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.UNPAID

    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_reports_pending_while_stripe_says_unpaid(mock_retrieve):
        mock_retrieve.return_value = _retrieved()
        fee = LateCancellationFeeFactory(stripe_session_id="cs_fee_1")
        assert late_fees.reconcile_landed_checkout(fee) == "pending"

    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_marks_paid_when_stripe_says_paid(mock_retrieve):
        mock_retrieve.return_value = _retrieved(
            status="complete", payment_status="paid", payment_intent="pi_land_1", amount_total=1500
        )
        member = _member_with_user("rlc_paid")
        fee = LateCancellationFeeFactory(
            orientation_booking=OrientationBookingFactory(member=member), stripe_session_id="cs_fee_1"
        )
        assert late_fees.reconcile_landed_checkout(fee) == "paid"
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID
        assert fee.stripe_payment_id == "pi_land_1"


def describe_unpaid_fee_for():
    def it_returns_the_oldest_unpaid_fee():
        member = MemberFactory()
        older = LateCancellationFeeFactory(orientation_booking=OrientationBookingFactory(member=member))
        LateCancellationFeeFactory(
            for_reservation=True, reservation=EquipmentReservationFactory(member=member, status="cancelled")
        )
        assert late_fees.unpaid_fee_for(member) == older

    def it_ignores_paid_waived_and_refunded_fees_and_other_members():
        member = MemberFactory()
        for status in (
            LateCancellationFee.Status.PAID,
            LateCancellationFee.Status.WAIVED,
            LateCancellationFee.Status.REFUNDED,
        ):
            LateCancellationFeeFactory(orientation_booking=OrientationBookingFactory(member=member), status=status)
        LateCancellationFeeFactory()  # someone else's unpaid fee
        assert late_fees.unpaid_fee_for(member) is None


def describe_pay_line():
    def it_names_the_amount_and_the_absolute_link_to_the_fees_page():
        fee = LateCancellationFeeFactory()
        line = late_fees.pay_line(fee)
        url = late_fees.pay_url(fee)
        assert url.startswith("http")
        assert url.endswith(reverse("hub_late_fee_detail", args=[fee.pk]))
        assert line == (
            f"A $15.00 late cancellation fee applies to this cancellation. "
            f"Pay it at {url} and you'll get a receipt once it's paid."
        )

    def it_builds_the_html_twin_with_a_real_link_marked_safe():
        from django.utils.safestring import SafeString

        fee = LateCancellationFeeFactory()
        html = late_fees.pay_html(fee)
        assert isinstance(html, SafeString)
        assert html == (
            "A $15.00 late cancellation fee applies to this cancellation. "
            f"<a href=\"{late_fees.pay_url(fee)}\">Pay the late fee</a> and you'll get a receipt once it's paid."
        )


def describe_LateCancellationFee():
    def it_labels_an_orientation_fee_by_type_and_date_and_links_the_owner_page():
        settings_obj = GuildOrientationSettingsFactory()
        lathe = OrientationTypeFactory(guild=settings_obj.guild, name="Lathe")
        starts = timezone.make_aware(timezone.datetime(2026, 9, 12, 14, 0))
        slot = OrientationSlotFactory(
            guild=settings_obj.guild, orientation_type=lathe, starts_at=starts, ends_at=starts + timedelta(hours=1)
        )
        fee = LateCancellationFeeFactory(orientation_booking=OrientationBookingFactory(slot=slot))
        assert fee.item_label == "Lathe orientation, Sat Sep 12"
        assert fee.amount_display == "$15.00"
        assert fee.target == fee.orientation_booking
        assert fee.owner_page_path() == reverse("hub_guild_detail", args=[settings_obj.guild.slug])
        assert str(fee) == "Unpaid late cancellation fee, $15.00: Lathe orientation, Sat Sep 12"
        assert fee.is_unpaid is True
        assert fee.is_paid is False

    def it_labels_a_reservation_fee_by_equipment_and_date_and_links_the_equipment_page():
        starts = timezone.make_aware(timezone.datetime(2026, 9, 12, 14, 0))
        reservation = EquipmentReservationFactory(
            equipment=EquipmentFactory(name="CNC Router"), starts_at=starts, ends_at=starts + timedelta(hours=1)
        )
        fee = LateCancellationFeeFactory(for_reservation=True, reservation=reservation, amount_cents=3750)
        assert fee.item_label == "CNC Router reservation, Sat Sep 12"
        assert fee.amount_display == "$37.50"
        assert fee.target == reservation
        assert fee.owner_page_path() == reverse("hub_equipment_detail", args=[reservation.equipment.slug])
        assert fee.member == reservation.member

    def it_refuses_a_row_with_both_sources_or_neither():
        member = MemberFactory()
        booking = OrientationBookingFactory(member=member)
        reservation = EquipmentReservationFactory(member=member)
        with pytest.raises(IntegrityError), transaction.atomic():
            LateCancellationFee.objects.create(
                member=member, orientation_booking=booking, reservation=reservation, amount_cents=1500
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            LateCancellationFee.objects.create(member=member, amount_cents=1500)

    def it_refuses_a_second_fee_on_the_same_source():
        fee = LateCancellationFeeFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            LateCancellationFee.objects.create(
                member=fee.member, orientation_booking=fee.orientation_booking, amount_cents=1500
            )


def describe_activity_kinds():
    def it_declares_the_charged_and_paid_kinds_with_member_facing_labels():
        assert SiteActivity.Kind.LATE_FEE_CHARGED.label == "Late cancellation fee charged"
        assert SiteActivity.Kind.LATE_FEE_PAID.label == "Late cancellation fee paid"
        row = SiteActivity.log(SiteActivity.Kind.LATE_FEE_CHARGED, target=LateCancellationFeeFactory())
        assert row.get_kind_display() == "Late cancellation fee charged"

    def it_declares_the_waived_and_refunded_kinds_with_member_facing_labels():
        assert SiteActivity.Kind.LATE_FEE_WAIVED.label == "Late cancellation fee waived"
        assert SiteActivity.Kind.LATE_FEE_REFUNDED.label == "Late cancellation fee refunded"
        row = SiteActivity.log(SiteActivity.Kind.LATE_FEE_WAIVED, target=LateCancellationFeeFactory())
        assert row.get_kind_display() == "Late cancellation fee waived"


# --- Part 3: waive -------------------------------------------------------------------------


def _active_user(username: str) -> User:
    """A signed-in-able user whose member is ACTIVE (what the permission helpers read)."""
    member = _member_with_user(username)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return member.user


def _admin_user(username: str) -> User:
    user = _active_user(username)
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _request(user: User) -> HttpRequest:
    """A request carrying the same ``view_as`` the middleware would attach."""
    request = RequestFactory().get("/")
    request.user = user
    request.view_as = ViewAs.for_request(request)  # type: ignore[attr-defined]
    return request


def _guild_fee(*, guild: Guild | None = None, member: Member | None = None) -> LateCancellationFee:
    """An unpaid fee on a cancelled guild orientation booking."""
    settings_obj = (
        GuildOrientationSettingsFactory(guild=guild) if guild is not None else GuildOrientationSettingsFactory()
    )
    slot = OrientationSlotFactory(guild=settings_obj.guild)
    booking = OrientationBookingFactory(
        slot=slot, member=member or MemberFactory(), status=OrientationBooking.Status.CANCELLED
    )
    return LateCancellationFeeFactory(orientation_booking=booking)


def _equipment_orientation_fee(equipment: Equipment, *, member: Member | None = None) -> LateCancellationFee:
    """An unpaid fee on a cancelled booking of an orientation the equipment owns."""
    orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
    slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
    booking = OrientationBookingFactory(
        slot=slot, member=member or MemberFactory(), status=OrientationBooking.Status.CANCELLED
    )
    return LateCancellationFeeFactory(orientation_booking=booking)


def _reservation_fee(equipment: Equipment, *, member: Member | None = None) -> LateCancellationFee:
    """An unpaid fee on a cancelled reservation of the equipment."""
    reservation = EquipmentReservationFactory(
        equipment=equipment, member=member or MemberFactory(), status=EquipmentReservation.Status.CANCELLED
    )
    return LateCancellationFeeFactory(for_reservation=True, reservation=reservation)


def describe_governing_owner():
    def it_is_the_guild_for_a_guild_orientation_fee():
        fee = _guild_fee()
        assert late_fees.governing_owner(fee) == fee.orientation_booking.guild

    def it_is_the_equipment_for_an_equipment_owned_orientation_fee():
        equipment = EquipmentFactory(name="Laser Cutter")
        assert late_fees.governing_owner(_equipment_orientation_fee(equipment)) == equipment

    def it_is_the_equipment_for_a_reservation_fee():
        equipment = EquipmentFactory(name="CNC Router")
        assert late_fees.governing_owner(_reservation_fee(equipment)) == equipment


def describe_can_waive():
    def it_lets_an_admin_waive_any_fee():
        admin = _admin_user("cw_admin")
        equipment = EquipmentFactory()
        for fee in (_guild_fee(), _equipment_orientation_fee(equipment), _reservation_fee(equipment)):
            assert late_fees.can_waive(_request(admin), fee) is True

    def it_lets_the_governing_guilds_staff_and_lead_waive_an_orientation_fee():
        staff = _active_user("cw_staff")
        lead = _active_user("cw_lead")
        guild = GuildFactory(name="Woodshop", guild_lead=lead.member)
        GuildStaffMembershipFactory(guild=guild, member=staff.member)
        fee = _guild_fee(guild=guild)
        assert late_fees.can_waive(_request(staff), fee) is True
        assert late_fees.can_waive(_request(lead), fee) is True

    def it_lets_the_governing_equipments_managers_waive_its_fees():
        manager = _active_user("cw_manager")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager.member)
        assert late_fees.can_waive(_request(manager), _reservation_fee(equipment)) is True
        assert late_fees.can_waive(_request(manager), _equipment_orientation_fee(equipment)) is True

    def it_refuses_a_lead_of_another_guild():
        other_lead = _active_user("cw_other_lead")
        GuildFactory(name="Art Framing", guild_lead=other_lead.member)
        assert late_fees.can_waive(_request(other_lead), _guild_fee()) is False

    def it_refuses_a_manager_of_another_equipment():
        other_manager = _active_user("cw_other_mgr")
        EquipmentStaffMembershipFactory(member=other_manager.member)
        equipment = EquipmentFactory()
        assert late_fees.can_waive(_request(other_manager), _reservation_fee(equipment)) is False
        assert late_fees.can_waive(_request(other_manager), _equipment_orientation_fee(equipment)) is False

    def it_refuses_the_member_who_owes_the_fee():
        payer = _active_user("cw_payer")
        assert late_fees.can_waive(_request(payer), _guild_fee(member=payer.member)) is False


def describe_waive_refusal():
    def it_names_the_guild_for_a_guild_orientation_fee():
        fee = _guild_fee(guild=GuildFactory(name="Ceramics"))
        assert late_fees.waive_refusal(fee) == "Only Ceramics staff or an admin can waive this fee."

    def it_names_the_equipment_for_its_orientation_and_reservation_fees():
        equipment = EquipmentFactory(name="Laser Cutter")
        expected = "Only the Laser Cutter managers or an admin can waive this fee."
        assert late_fees.waive_refusal(_equipment_orientation_fee(equipment)) == expected
        assert late_fees.waive_refusal(_reservation_fee(equipment)) == expected


def describe_waive():
    def it_forgives_an_unpaid_fee_lifts_the_block_logs_and_emails_the_member_once():
        payer = _active_user("wv_payer").member
        equipment = EquipmentFactory(name="CNC Router")
        fee = _reservation_fee(equipment, member=payer)
        admin = _admin_user("wv_admin")
        admin.member.preferred_name = "Moss Admin"
        admin.member.save(update_fields=["preferred_name"])
        assert late_fees.unpaid_fee_for(payer) == fee
        assert any("late cancellation fee" in blocker for blocker in equipment.booking_blockers(payer))
        mail.outbox.clear()

        late_fees.waive(fee, actor=admin, reason="Family emergency")

        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.WAIVED
        assert fee.waived_by == admin
        assert fee.waived_reason == "Family emergency"
        assert fee.waived_at is not None
        assert fee.is_waived is True
        # The block lifts: unpaid_fee_for reads UNPAID only.
        assert late_fees.unpaid_fee_for(payer) is None
        assert equipment.booking_blockers(payer) == []
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.LATE_FEE_WAIVED)
        assert row.actor == admin
        assert row.target == fee
        assert row.payload == {"amount_cents": 1500, "item": fee.item_label, "reason": "Family emergency"}
        sent = [m for m in mail.outbox if m.subject == "Your late cancellation fee was waived"]
        assert len(sent) == 1
        body = sent[0].body
        assert sent[0].to == [payer.primary_email]
        assert "$15.00" in body
        assert fee.item_label in body
        assert late_fees.pay_url(fee) in body
        assert late_fees._absolute_url(fee.owner_page_path()) in body
        assert "[missing:" not in body
        assert "[missing:" not in sent[0].alternatives[0][0]
        assert "Moss Admin" not in body  # who waived it is never named
        bell = Notification.objects.get(user=payer.user, trigger="billing.late_fee_waived")
        assert bell.url == reverse("hub_late_fee_detail", args=[fee.pk])

    @patch("billing.stripe_utils.expire_checkout_session")
    def it_expires_the_open_checkout_session(mock_expire):
        fee = _guild_fee()
        fee.stripe_session_id = "cs_open"
        fee.save(update_fields=["stripe_session_id"])
        late_fees.waive(fee, actor=_admin_user("wv_expire"), reason="Snow day")
        mock_expire.assert_called_once_with(session_id="cs_open")

    @patch("billing.stripe_utils.expire_checkout_session")
    def it_has_no_session_to_expire_when_none_was_minted(mock_expire):
        late_fees.waive(_guild_fee(), actor=_admin_user("wv_none"), reason="Snow day")
        assert not mock_expire.called

    def it_refuses_a_paid_fee():
        fee = LateCancellationFeeFactory(status=LateCancellationFee.Status.PAID, stripe_payment_id="pi_paid")
        mail.outbox.clear()
        with pytest.raises(ValueError, match="This fee is already paid. Only an unpaid fee can be waived"):
            late_fees.waive(fee, actor=_admin_user("wv_paid"), reason="Oops")
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID
        assert fee.waived_by is None
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_WAIVED).exists()
        assert mail.outbox == []

    def it_refuses_a_second_waive():
        fee = _guild_fee()
        admin = _admin_user("wv_twice")
        late_fees.waive(fee, actor=admin, reason="First")
        with pytest.raises(ValueError, match="This fee is already waived."):
            late_fees.waive(fee, actor=admin, reason="Second")
        fee.refresh_from_db()
        assert fee.waived_reason == "First"
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_WAIVED).count() == 1


def describe_waived_by_name():
    def it_is_blank_until_waived():
        assert LateCancellationFeeFactory().waived_by_name == ""

    def it_is_the_waivers_member_name():
        admin = _admin_user("wbn_member")
        admin.member.preferred_name = "Moss"
        admin.member.save(update_fields=["preferred_name"])
        fee = LateCancellationFeeFactory(status=LateCancellationFee.Status.WAIVED, waived_by=admin)
        assert fee.waived_by_name == "Moss"

    def it_falls_back_to_the_accounts_name_or_email_without_a_member():
        from django.db.models.signals import post_save
        from factory.django import mute_signals

        with mute_signals(post_save):
            named = User.objects.create_user(username="wbn_named", email="named@example.com", first_name="Ada")
            bare = User.objects.create_user(username="wbn_bare", email="bare@example.com")
        assert LateCancellationFeeFactory(waived_by=named).waived_by_name == "Ada"
        assert LateCancellationFeeFactory(waived_by=bare).waived_by_name == "bare@example.com"
