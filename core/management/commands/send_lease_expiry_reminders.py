"""Notify tenants 30 days before a lease end_date. Daily cron; idempotent per lease.

Runs on the event spine: one ``lease_expiring`` :func:`core.events.emit.emit` per due
lease. Idempotency is the spine's :class:`core.models.EventDelivery` ledger — the
``lease:<pk>:expiring`` period claims the slot so a re-run never double-notifies
(replacing the old per-job ``ScheduledNotificationMarker`` dedupe). The
``lease_tenant`` resolver finds the member tenant's linked user; a guild-tenant lease
or a tenant with no usable account resolves to nobody and is skipped automatically.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.events.emit import emit
from membership.models import Lease, Member


class Command(BaseCommand):
    help = "Dispatch 'lease expiring' notifications for leases ending in 30 days."

    def handle(self, *args: Any, **options: Any) -> None:
        target = timezone.now().date() + timedelta(days=30)
        leases = Lease.objects.filter(end_date=target)
        sent = 0
        for lease in leases:
            tenant = lease.tenant
            member = tenant if isinstance(tenant, Member) else None
            if member is None or member.user is None:
                continue
            result = emit(
                "lease_expiring",
                target=lease,
                context={"member": member},
                title="Your space lease is expiring",
                body=f"Your lease for {lease.space} ends on {lease.end_date:%b %d, %Y}.",
                url="/",
                period=f"lease:{lease.pk}:expiring",
            )
            if result.delivery_count:
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} lease-expiry reminder(s)."))
