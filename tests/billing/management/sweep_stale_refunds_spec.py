"""BDD specs for the sweep_stale_refunds management command."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from billing.management.commands.sweep_stale_refunds import STALE_REASON
from billing.models import PaymentRefund
from tests.billing.factories import PaymentRefundFactory

pytestmark = pytest.mark.django_db


def _aged(refund: PaymentRefund, hours: int) -> PaymentRefund:
    """Backdate a refund row's created_at (auto_now_add blocks it at create time)."""
    PaymentRefund.objects.filter(pk=refund.pk).update(created_at=timezone.now() - timedelta(hours=hours))
    refund.refresh_from_db()
    return refund


def describe_sweep_stale_refunds():
    def it_fails_an_id_less_pending_row_older_than_a_day():
        stale = _aged(PaymentRefundFactory(stripe_refund_id="", status=PaymentRefund.Status.PENDING), hours=25)
        out = StringIO()

        call_command("sweep_stale_refunds", stdout=out)

        stale.refresh_from_db()
        assert stale.status == PaymentRefund.Status.FAILED
        assert stale.failure_reason == STALE_REASON  # points the admin at the panel's Retry path
        assert stale.settled_at is not None
        assert "Swept 1 stale pending refund(s) to FAILED." in out.getvalue()

    def it_runs_the_full_failure_bookkeeping_not_a_bare_update():
        # A process-death refund must be as loud as a Stripe failure: the
        # activity row and the Billing Administrators alert both fire.
        from django.contrib.auth.models import User
        from django.core import mail
        from django.db.models.signals import post_save
        from factory.django import mute_signals

        from classes.models import CmsActivity
        from membership.models import AdminCapability
        from tests.membership.factories import MemberFactory

        member = MemberFactory(_pre_signup_email="sweepbiller@example.com")
        with mute_signals(post_save):
            user = User.objects.create_user(username="sweepbiller", email="sweepbiller@example.com")
        member.user = user
        member.save(update_fields=["user"])
        member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        stale = _aged(PaymentRefundFactory(stripe_refund_id="", status=PaymentRefund.Status.PENDING), hours=25)
        mail.outbox.clear()

        call_command("sweep_stale_refunds", stdout=StringIO())

        assert CmsActivity.objects.filter(
            kind=CmsActivity.Kind.REGISTRATION_REFUND_FAILED, registration=stale.registration
        ).exists()
        alerts = [m for m in mail.outbox if "sweepbiller@example.com" in m.to]
        assert len(alerts) == 1
        assert STALE_REASON in alerts[0].body

    def it_leaves_a_pending_row_that_reached_stripe_alone():
        # A row WITH a Stripe id is refund.updated's responsibility, however old.
        reached = _aged(
            PaymentRefundFactory(stripe_refund_id="re_reached", status=PaymentRefund.Status.PENDING), hours=48
        )

        call_command("sweep_stale_refunds", stdout=StringIO())

        reached.refresh_from_db()
        assert reached.status == PaymentRefund.Status.PENDING

    def it_leaves_a_fresh_id_less_pending_row_alone():
        fresh = _aged(PaymentRefundFactory(stripe_refund_id="", status=PaymentRefund.Status.PENDING), hours=2)

        call_command("sweep_stale_refunds", stdout=StringIO())

        fresh.refresh_from_db()
        assert fresh.status == PaymentRefund.Status.PENDING


def describe_fail_pending_refund():
    def it_leaves_a_row_that_settled_under_the_lock_untouched():
        # The race guard: between the sweep's queryset and the lock, the
        # refund.updated webhook may have settled the row — nothing to fail.
        from billing.refunds import fail_pending_refund

        settled = PaymentRefundFactory(stripe_refund_id="", status=PaymentRefund.Status.SUCCEEDED)

        assert fail_pending_refund(settled, failure_reason=STALE_REASON) is False
        settled.refresh_from_db()
        assert settled.status == PaymentRefund.Status.SUCCEEDED
        assert settled.failure_reason == ""
