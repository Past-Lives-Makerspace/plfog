"""BDD specs for orientation rows in the Payments panel and their refund modal endpoints."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from billing.models import PaymentRefund
from billing.payments_panel import PanelWindow, build_payments_ledger
from membership.models import AdminCapability, OrientationBooking
from tests.membership.factories import (
    OrientationTypeFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _window() -> PanelWindow:
    today = timezone.localdate()
    return PanelWindow(start=today - timedelta(days=7), end=today + timedelta(days=1))


def _paid_booking(**overrides):
    settings_obj = GuildOrientationSettingsFactory()
    OrientationTypeFactory(guild=settings_obj.guild, price_cents=1500)
    slot = OrientationSlotFactory(guild=settings_obj.guild)
    defaults = {"slot": slot, "amount_paid_cents": 1500, "stripe_payment_id": "pi_panel_1"}
    defaults.update(overrides)
    return OrientationBookingFactory(**defaults)


def _refunds_user(username: str) -> User:
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member.user = user
    member.save(update_fields=["user"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)
    return user


def describe_orientation_rows():
    def it_builds_a_row_for_a_paid_booking():
        booking = _paid_booking()
        ledger = build_payments_ledger(window=_window(), source="orientation")
        assert len(ledger.rows) == 1
        row = ledger.rows[0]
        assert row.source_kind == "orientation"
        assert row.item == f"Orientation — {booking.guild.name}"
        assert row.amount_cents == 1500
        assert row.status == "paid"
        assert row.can_refund is True
        assert row.item_url == reverse("hub_guild_detail", args=[booking.guild.slug])
        assert row.booking_url == reverse("hub_orientation_respond", args=[booking.pk])

    def it_excludes_pending_payment_holds():
        _paid_booking(status=OrientationBooking.Status.PENDING_PAYMENT)
        ledger = build_payments_ledger(window=_window(), source="orientation")
        assert ledger.rows == ()

    def it_excludes_free_bookings():
        OrientationBookingFactory()
        ledger = build_payments_ledger(window=_window(), source="orientation")
        assert ledger.rows == ()

    def it_maps_refund_states_to_status_badges():
        refunded = _paid_booking()
        PaymentRefund.objects.create(
            orientation_booking=refunded, amount_cents=1500, status=PaymentRefund.Status.SUCCEEDED
        )
        failed = _paid_booking(stripe_payment_id="pi_panel_2")
        PaymentRefund.objects.create(orientation_booking=failed, amount_cents=1500, status=PaymentRefund.Status.FAILED)
        ledger = build_payments_ledger(window=_window(), source="orientation")
        statuses = {row.source_pk: row.status for row in ledger.rows}
        assert statuses[refunded.pk] == "refunded"
        assert statuses[failed.pk] == "refund_failed"

    def it_links_the_payer_for_admin_viewers_only():
        booking = _paid_booking()
        admin_row = build_payments_ledger(window=_window(), source="orientation", viewer_is_admin=True).rows[0]
        plain_row = build_payments_ledger(window=_window(), source="orientation", viewer_is_admin=False).rows[0]
        assert admin_row.payer_url == reverse("hub_admin_member_edit", args=[booking.member_id])
        assert plain_row.payer_url is None

    def it_lands_in_the_merged_all_sources_ledger():
        _paid_booking()
        ledger = build_payments_ledger(window=_window(), source="all")
        assert any(row.source_kind == "orientation" for row in ledger.rows)
        assert ledger.collected_cents == 1500


def describe_orientation_refund_endpoints():
    def it_serves_the_refund_form_to_refund_authority(client: Client):
        _refunds_user("panel1")
        booking = _paid_booking()
        client.login(username="panel1", password="pass")
        response = client.get(reverse("billing_orientation_refund_form", args=[booking.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert f"Orientation — {booking.guild.name}" in content
        assert "Issue Refund" in content

    def it_serves_the_retry_variant_when_the_latest_attempt_failed(client: Client):
        _refunds_user("panel2")
        booking = _paid_booking()
        refund = PaymentRefund.objects.create(
            orientation_booking=booking, amount_cents=1500, status=PaymentRefund.Status.FAILED
        )
        client.login(username="panel2", password="pass")
        content = client.get(reverse("billing_orientation_refund_form", args=[booking.pk])).content.decode()
        assert "Retry Refund" in content
        assert reverse("billing_payment_refund_retry", args=[refund.pk]) in content

    def it_403s_without_refund_authority(client: Client):
        member = MemberFactory()
        with mute_signals(post_save):
            user = User.objects.create_user(username="panel3", password="pass")
        member.user = user
        member.save(update_fields=["user"])
        booking = _paid_booking()
        client.login(username="panel3", password="pass")
        assert client.get(reverse("billing_orientation_refund_form", args=[booking.pk])).status_code == 403

    @patch("billing.stripe_utils.create_refund", return_value={"id": "re_pan_1", "status": "succeeded", "amount": 1500})
    def it_issues_a_refund_through_the_post_endpoint(mock_create, client: Client):
        _refunds_user("panel4")
        booking = _paid_booking()
        client.login(username="panel4", password="pass")
        response = client.post(
            reverse("billing_orientation_refund", args=[booking.pk]),
            {"amount": "15.00", "reason": "goodwill"},
        )
        assert response.status_code == 204
        refund = PaymentRefund.objects.get(orientation_booking=booking)
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.amount_cents == 1500
        booking.refresh_from_db()
        # Money and scheduling stay independent — the booking is not cancelled.
        assert booking.status == OrientationBooking.Status.REQUESTED

    def it_rerenders_the_form_on_validation_errors(client: Client):
        _refunds_user("panel5")
        booking = _paid_booking()
        client.login(username="panel5", password="pass")
        response = client.post(
            reverse("billing_orientation_refund", args=[booking.pk]),
            {"amount": "99.00", "reason": ""},
        )
        assert response.status_code == 200
        assert "between $0.01 and $15.00" in response.content.decode()
        assert not PaymentRefund.objects.filter(orientation_booking=booking).exists()


def describe_pending_refund_rows():
    def it_shows_a_pending_refund_with_its_age(client: Client):
        booking = _paid_booking()
        PaymentRefund.objects.create(
            orientation_booking=booking, amount_cents=1500, status=PaymentRefund.Status.PENDING
        )
        row = build_payments_ledger(window=_window(), source="orientation").rows[0]
        assert row.status == "refund_pending"
        assert row.pending_age != ""

    @patch("billing.stripe_utils.create_refund", return_value={"id": "re_pend_1", "status": "pending", "amount": 1500})
    def it_toasts_the_processing_message_when_stripe_answers_pending(mock_create, client: Client):
        _refunds_user("panel6")
        booking = _paid_booking()
        client.login(username="panel6", password="pass")
        response = client.post(
            reverse("billing_orientation_refund", args=[booking.pk]),
            {"amount": "15.00", "reason": ""},
        )
        assert response.status_code == 204
        refund = PaymentRefund.objects.get(orientation_booking=booking)
        assert refund.status == PaymentRefund.Status.PENDING
