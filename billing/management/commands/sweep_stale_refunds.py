"""Nightly sweep for refunds stuck PENDING with no Stripe id.

A PENDING :class:`billing.models.PaymentRefund` with a BLANK ``stripe_refund_id``
means the process died between row-create and Stripe answering — nothing will
ever settle it. This command flips such rows (older than 24 hours) to FAILED
with a reason pointing at the panel's Retry action, which lights up the normal
recovery path. Rows PENDING WITH a Stripe id are left alone — the
``refund.updated`` webhook owns their fate.

Wired into the nightly Render cron alongside the other sync jobs.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import PaymentRefund

STALE_AFTER = timedelta(hours=24)
STALE_REASON = "Never reached Stripe. Retry from the Payments panel."


class Command(BaseCommand):
    """Flip id-less refunds stuck PENDING for over 24 hours to FAILED."""

    help = "Mark PENDING refunds with no Stripe id, older than 24 hours, as FAILED so they can be retried."

    def handle(self, *args: Any, **options: Any) -> None:
        cutoff = timezone.now() - STALE_AFTER
        swept = PaymentRefund.objects.filter(
            status=PaymentRefund.Status.PENDING,
            stripe_refund_id="",
            created_at__lt=cutoff,
        ).update(
            status=PaymentRefund.Status.FAILED,
            failure_reason=STALE_REASON,
            settled_at=timezone.now(),
        )
        self.stdout.write(f"Swept {swept} stale pending refund(s) to FAILED.")
