"""BDD-style tests for billing notifications.

These pin the post-Phase-4 behavior: each sender emits ONE event on the
``core.events.emit`` spine. The receipt / admin-failure email keeps its preserved
shell (same subject + body, same ``TransactionalEmailLog`` trigger_kind, exactly
one email — proving the old double-send is gone), the member's in-app bell row is
written by the resolver, and exactly one ``SiteActivity`` row is logged per event.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from factory.django import mute_signals

from billing.models import TabCharge
from billing.notifications import notify_admin_charge_failed, send_receipt
from core.models import Notification, SiteActivity, TransactionalEmailLog
from membership.models import Member
from tests.billing.factories import TabChargeFactory, TabEntryFactory, TabFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _linked_member(*, email: str = "member@example.com", **member_kwargs) -> Member:
    """A Member with a linked, email-bearing User so the resolver can address it.

    Signals are muted while creating the User so the ``ensure_user_has_member``
    post-save signal does not auto-create a second Member that collides on the
    one-to-one ``user`` key; the User is attached to our own Member instead.
    """
    member = MemberFactory(_pre_signup_email=email, **member_kwargs)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"member_{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    return member


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

    def it_sends_exactly_one_email_no_double_send():
        member = _linked_member(email="once@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("12.00"))

        send_receipt(charge)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["once@example.com"]

    def it_logs_receipt_with_correct_trigger_kind():
        member = MemberFactory(_pre_signup_email="log@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("40.00"))

        send_receipt(charge)

        assert TransactionalEmailLog.objects.filter(trigger_kind="tab_charged").exists()
        log = TransactionalEmailLog.objects.get(trigger_kind="tab_charged")
        assert log.to_email == "log@example.com"
        assert log.status == TransactionalEmailLog.Status.SENT

    def it_writes_member_in_app_row():
        member = _linked_member(email="bell@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("33.00"))

        send_receipt(charge)

        rows = Notification.objects.filter(trigger="tab_charged", user=member.user)
        assert rows.count() == 1
        assert rows.first().body == "$33.00 was charged to your tab."

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
        assert not TransactionalEmailLog.objects.filter(trigger_kind="tab_charged").exists()


def describe_notify_admin_charge_failed():
    def it_emails_the_fog_admins():
        _linked_member(email="admin@example.com", fog_role=Member.FogRole.ADMIN)
        member = _linked_member(email="payer@example.com", preferred_name="Jane")
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
        assert email.to == ["admin@example.com"]
        assert "Failed charge" in email.subject
        assert "Jane" in email.subject
        assert "Card declined" in email.body

    def it_logs_failure_with_correct_trigger_kind():
        _linked_member(email="admin@example.com", fog_role=Member.FogRole.ADMIN)
        member = _linked_member(email="payer@example.com", preferred_name="Alex")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.FAILED,
            amount=Decimal("30.00"),
            failure_reason="Insufficient funds",
        )

        notify_admin_charge_failed(charge)

        assert TransactionalEmailLog.objects.filter(trigger_kind="tab_charge_failed").exists()
        log = TransactionalEmailLog.objects.get(trigger_kind="tab_charge_failed")
        assert log.to_email == "admin@example.com"
        assert log.status == TransactionalEmailLog.Status.SENT

    def it_writes_member_in_app_row():
        _linked_member(email="admin@example.com", fog_role=Member.FogRole.ADMIN)
        member = _linked_member(email="payer@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.FAILED, amount=Decimal("20.00"))

        notify_admin_charge_failed(charge)

        rows = Notification.objects.filter(trigger="tab_charge_failed", user=member.user)
        assert rows.count() == 1

    def it_logs_exactly_one_tab_charge_failed_activity():
        _linked_member(email="admin@example.com", fog_role=Member.FogRole.ADMIN)
        member = _linked_member(email="fail@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.FAILED, amount=Decimal("20.00"))

        notify_admin_charge_failed(charge)

        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TAB_CHARGE_FAILED).count() == 1


def describe_send_receipt_activity():
    def it_logs_exactly_one_tab_charged_activity():
        member = _linked_member(email="receipt@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("60.00"))

        send_receipt(charge)

        activities = SiteActivity.objects.filter(kind=SiteActivity.Kind.TAB_CHARGED)
        assert activities.count() == 1
        # email_log FK linkage is intentionally dropped post-migration: emit logs
        # the activity centrally and does not attach the TransactionalEmailLog row.
        # The email is still independently audited in TransactionalEmailLog.
        assert activities.first().email_log is None
