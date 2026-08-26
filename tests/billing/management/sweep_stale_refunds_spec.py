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
