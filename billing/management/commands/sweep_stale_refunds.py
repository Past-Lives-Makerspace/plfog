"""Nightly sweep for refunds stuck PENDING with no Stripe id.

A PENDING :class:`billing.models.PaymentRefund` with a BLANK ``stripe_refund_id``
means the process died between row-create and Stripe answering — nothing will
ever settle it. This command routes each such row (older than 24 hours) through
the refund service's normal failure path — FAILED stamp, activity row, and the
Billing Administrators alert — with a reason pointing at the panel's Retry
action, which lights up the normal recovery path. Rows PENDING WITH a Stripe id
are left alone — the ``refund.updated`` webhook owns their fate.

Wired into the nightly Render cron alongside the other sync jobs.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import PaymentRefund
from billing.refunds import fail_pending_refund

STALE_AFTER = timedelta(hours=24)
STALE_REASON = "Never reached Stripe. Retry from the Payments panel."


class Command(BaseCommand):
    """Fail id-less refunds stuck PENDING for over 24 hours, with full bookkeeping."""

    help = "Mark PENDING refunds with no Stripe id, older than 24 hours, as FAILED so they can be retried."

    def handle(self, *args: Any, **options: Any) -> None:
        cutoff = timezone.now() - STALE_AFTER
        stale = PaymentRefund.objects.filter(
            status=PaymentRefund.Status.PENDING,
            stripe_refund_id="",
            created_at__lt=cutoff,
        )
        # Iterate the (tiny) set through the service's failure path rather than a
        # bulk .update(): the activity row and billing-admin alert must fire for
        # process-death refunds exactly like any other failure.
        swept = sum(1 for refund in stale if fail_pending_refund(refund, failure_reason=STALE_REASON))
        self.stdout.write(f"Swept {swept} stale pending refund(s) to FAILED.")
