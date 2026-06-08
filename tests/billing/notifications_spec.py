"""BDD-style tests for billing notifications."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core import mail

from billing.models import TabCharge
from billing.notifications import notify_admin_charge_failed, send_receipt
from core.models import TransactionalEmailLog
from tests.billing.factories import TabChargeFactory, TabEntryFactory, TabFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_send_receipt():
    def it_sends_itemized_receipt_email():
        member = MemberFactory(_pre_signup_email="member@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("50.00"))
        TabEntryFactory(tab=tab, tab_charge=charge, description="Laser time", amount=Decimal("30.00"))
        TabEntryFactory(tab=tab, tab_charge=charge, description="Wood glue", amount=Decimal("20.00"))

        send_receipt(charge)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "Receipt" in email.subject
        assert "$50.00" in email.subject
        assert "Laser time" in email.body
        assert "Wood glue" in email.body
        assert "member@example.com" in email.to

    def it_logs_receipt_with_correct_trigger_kind():
        member = MemberFactory(_pre_signup_email="log@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("40.00"))

        send_receipt(charge)

        assert TransactionalEmailLog.objects.filter(trigger_kind="billing.receipt").exists()
        log = TransactionalEmailLog.objects.get(trigger_kind="billing.receipt")
        assert log.to_email == "log@example.com"
        assert log.status == TransactionalEmailLog.Status.SENT

    def it_sets_receipt_sent_at():
        member = MemberFactory(_pre_signup_email="sent@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("25.00"))

        assert charge.receipt_sent_at is None

        send_receipt(charge)

        charge.refresh_from_db()
        assert charge.receipt_sent_at is not None

    def it_skips_when_member_has_no_email():
        member = MemberFactory(_pre_signup_email="")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("25.00"))

        send_receipt(charge)

        assert len(mail.outbox) == 0
        assert not TransactionalEmailLog.objects.filter(trigger_kind="billing.receipt").exists()


def describe_notify_admin_charge_failed():
    def it_sends_failure_notification():
        member = MemberFactory(_pre_signup_email="member@example.com", preferred_name="Jane")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.FAILED,
            amount=Decimal("75.00"),
            failure_reason="Card declined",
        )

        notify_admin_charge_failed(charge)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "Failed charge" in email.subject
        assert "Jane" in email.subject
        assert "Card declined" in email.body

    def it_logs_failure_with_correct_trigger_kind():
        member = MemberFactory(_pre_signup_email="member@example.com", preferred_name="Alex")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.FAILED,
            amount=Decimal("30.00"),
            failure_reason="Insufficient funds",
        )

        notify_admin_charge_failed(charge)

        assert TransactionalEmailLog.objects.filter(trigger_kind="billing.charge_failed_admin").exists()
        log = TransactionalEmailLog.objects.get(trigger_kind="billing.charge_failed_admin")
        assert log.status == TransactionalEmailLog.Status.SENT
