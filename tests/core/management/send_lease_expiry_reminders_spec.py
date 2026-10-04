"""send_lease_expiry_reminders notifies tenants 30 days out, idempotently."""

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from classes.factories import UserFactory
from core.models import Notification
from tests.membership.factories import LeaseFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _at(hour: int, minute: int = 0) -> datetime:
    """Today at ``hour`` Portland time, as the real ``timezone.now()`` returns it (UTC)."""
    return timezone.make_aware(datetime.combine(timezone.localdate(), time(hour, minute))).astimezone(UTC)


@pytest.fixture(autouse=True)
def _mid_morning() -> Iterator[None]:
    """Reminders go out from 9 AM Portland time, so every case runs at 10 unless it says otherwise."""
    with patch("django.utils.timezone.now", return_value=_at(10)):
        yield


def describe_send_lease_expiry_reminders():
    def it_notifies_the_tenant_30_days_out():
        # UserFactory triggers ensure_user_has_member signal → ACTIVE Member auto-created.
        user = UserFactory()
        member = user.member  # type: ignore[attr-defined]
        LeaseFactory(tenant_obj=member, end_date=timezone.localdate() + timedelta(days=30))
        call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 1

    def it_is_idempotent():
        user = UserFactory()
        member = user.member  # type: ignore[attr-defined]
        LeaseFactory(tenant_obj=member, end_date=timezone.localdate() + timedelta(days=30))
        call_command("send_lease_expiry_reminders")
        call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 1

    def it_skips_tenant_with_no_user():
        # MemberFactory() creates a Member without a linked User (user=None).
        member = MemberFactory()
        LeaseFactory(tenant_obj=member, end_date=timezone.localdate() + timedelta(days=30))
        call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 0

    def it_waits_for_the_morning_after_the_date_turns_over():
        user = UserFactory()
        LeaseFactory(tenant_obj=user.member, end_date=timezone.localdate() + timedelta(days=30))  # type: ignore[attr-defined]
        for early in (_at(0, 30), _at(8, 59)):
            with patch("django.utils.timezone.now", return_value=early):
                call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 0

        with patch("django.utils.timezone.now", return_value=_at(9)):
            call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 1
