"""Notify tenants 30 days before a lease end_date. Daily cron; idempotent per lease."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import notifications
from core.models import ScheduledNotificationMarker
from membership.models import Lease, Member


class Command(BaseCommand):
    help = "Dispatch 'lease expiring' notifications for leases ending in 30 days."

    def handle(self, *args: Any, **options: Any) -> None:
        target = timezone.now().date() + timedelta(days=30)
        leases = Lease.objects.filter(end_date=target)
        sent = 0
        for lease in leases:
            key = f"lease_expiring:{lease.pk}"
            _, created = ScheduledNotificationMarker.objects.get_or_create(key=key)
            if not created:
                continue
            tenant = lease.tenant
            user = getattr(tenant, "user", None) if isinstance(tenant, Member) else None
            if user is None:
                continue
            notifications.dispatch(
                "lease_expiring",
                [user],
                title="Your space lease is expiring",
                body=f"Your lease for {lease.space} ends on {lease.end_date:%b %d, %Y}.",
                url="/",
            )
            sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} lease-expiry reminder(s)."))
