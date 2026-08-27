"""BDD specs for the orientation Checkout webhook handlers and the billing fan-in router."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from core.models import SiteActivity
from membership import webhook_handlers
from membership.models import OrientationBooking, OrientationSlot
from tests.membership.factories import (
    GuildOrientationSettingsFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _event(kind: str = "orientation_booking", *, booking_id=None, payment_status: str = "paid", **extra):
    session = {
        "id": "cs_hook_1",
        "metadata": {"kind": kind},
        "payment_status": payment_status,
        "payment_intent": "pi_hook_1",
        "amount_total": 1500,
        **extra,
    }
    if booking_id is not None:
        session["metadata"]["booking_id"] = str(booking_id)
    return {"data": {"object": session}}


def _hold(**overrides):
    from tests.membership.factories import MemberFactory

    settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
    settings_obj.guild.guild_lead = MemberFactory()
    settings_obj.guild.save(update_fields=["guild_lead"])
    slot = OrientationSlotFactory(guild=settings_obj.guild)
    defaults = {
        "slot": slot,
        "status": OrientationBooking.Status.PENDING_PAYMENT,
        "amount_paid_cents": 1500,
        "stripe_session_id": "cs_hook_1",
    }
    defaults.update(overrides)
    return OrientationBookingFactory(**defaults)


def describe_handle_checkout_session_completed():
    def it_flips_the_hold_to_requested_and_fires_the_full_fan_out():
        hold = _hold()
        mail.outbox.clear()

        webhook_handlers.handle_checkout_session_completed(_event(booking_id=hold.pk))

        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED
        assert hold.stripe_payment_id == "pi_hook_1"
        assert hold.amount_paid_cents == 1500
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_REQUESTED).exists()
        subjects = [m.subject.lower() for m in mail.outbox]
        assert any("request received" in s for s in subjects)  # member email
        assert any("new orientation request" in s for s in subjects)  # lead/orienter email

    def it_stamps_the_webhooks_amount_total_as_canonical():
        hold = _hold(amount_paid_cents=1500)
        webhook_handlers.handle_checkout_session_completed(_event(booking_id=hold.pk, amount_total=1600))
        hold.refresh_from_db()
        assert hold.amount_paid_cents == 1600

    def it_is_idempotent_on_redelivery():
        hold = _hold()
        webhook_handlers.handle_checkout_session_completed(_event(booking_id=hold.pk))
        first_email_count = len(mail.outbox)

        webhook_handlers.handle_checkout_session_completed(_event(booking_id=hold.pk))

        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED
        assert len(mail.outbox) == first_email_count  # status guard: no second send

    def it_dedupes_the_fan_out_even_past_the_status_guard():
        # Force the fan-out to run twice; the emit period makes the emails single-send.
        from membership import orientations

        hold = _hold()
        webhook_handlers.handle_checkout_session_completed(_event(booking_id=hold.pk))
        count = len(mail.outbox)
        orientations._fan_out_request(hold)
        assert len(mail.outbox) == count

    def it_ignores_an_unpaid_session():
        hold = _hold()
        webhook_handlers.handle_checkout_session_completed(_event(booking_id=hold.pk, payment_status="unpaid"))
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT

    def it_logs_a_missing_booking_loudly_without_raising(caplog):
        import logging

        with caplog.at_level(logging.ERROR, logger="membership.webhook_handlers"):
            webhook_handlers.handle_checkout_session_completed(_event(booking_id=999999))
        assert any("no in-app trace" in record.getMessage() for record in caplog.records)

    def it_ignores_other_kinds():
        hold = _hold()
        webhook_handlers.handle_checkout_session_completed(_event(kind="class_registration", booking_id=hold.pk))
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT

    def it_ignores_a_session_with_no_booking_id():
        webhook_handlers.handle_checkout_session_completed(_event())  # no booking_id — logs and returns


def describe_handle_checkout_session_expired():
    def it_deletes_the_still_pending_hold():
        hold = _hold()
        webhook_handlers.handle_checkout_session_expired(_event(booking_id=hold.pk))
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()

    def it_deletes_the_orphan_custom_slot_too():
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
        slot = OrientationSlotFactory(guild=settings_obj.guild, seats=1, source=OrientationSlot.Source.MANUAL)
        hold = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT)
        webhook_handlers.handle_checkout_session_expired(_event(booking_id=hold.pk))
        assert not OrientationSlot.objects.filter(pk=slot.pk).exists()

    def it_never_touches_a_booking_that_already_advanced():
        booking = _hold(status=OrientationBooking.Status.REQUESTED)
        webhook_handlers.handle_checkout_session_expired(_event(booking_id=booking.pk))
        assert OrientationBooking.objects.filter(pk=booking.pk).exists()

    def it_ignores_other_kinds():
        hold = _hold()
        webhook_handlers.handle_checkout_session_expired(_event(kind="class_registration", booking_id=hold.pk))
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()

    def it_ignores_a_session_with_no_booking_id():
        webhook_handlers.handle_checkout_session_expired(_event())


def describe_billing_fan_in_router():
    def it_calls_every_registered_completed_handler_in_turn():
        from unittest.mock import Mock

        from billing import views as billing_views

        first, second = Mock(), Mock()
        event = _event(booking_id=1)
        with patch.object(billing_views, "_CHECKOUT_COMPLETED_HANDLERS", [first, second]):
            billing_views._dispatch_checkout_completed(event)
        first.assert_called_once_with(event)
        second.assert_called_once_with(event)

    def it_registers_the_classes_and_orientation_handlers_for_completed_sessions():
        from billing import views as billing_views
        from classes import webhook_handlers as classes_handlers

        assert billing_views._CHECKOUT_COMPLETED_HANDLERS == [
            classes_handlers.handle_checkout_session_completed,
            webhook_handlers.handle_checkout_session_completed,
        ]

    def it_routes_an_orientation_session_through_the_real_fan_in():
        # The classes handler self-filters on kind and passes the event through untouched.
        from billing import views as billing_views

        hold = _hold()
        billing_views._dispatch_checkout_completed(_event(booking_id=hold.pk))
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED

    def it_routes_expired_sessions_to_the_orientation_handler():
        from billing import views as billing_views

        hold = _hold()
        billing_views._dispatch_checkout_expired(_event(booking_id=hold.pk))
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()

    def it_registers_both_checkout_events_in_the_webhook_map():
        from billing import views as billing_views

        assert (
            billing_views._WEBHOOK_HANDLERS["checkout.session.completed"] is billing_views._dispatch_checkout_completed
        )
        assert billing_views._WEBHOOK_HANDLERS["checkout.session.expired"] is billing_views._dispatch_checkout_expired
