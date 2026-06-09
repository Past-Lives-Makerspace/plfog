"""send_lease_expiry_reminders notifies tenants 30 days out, idempotently."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from classes.factories import UserFactory
from core.models import Notification
from tests.membership.factories import LeaseFactory

pytestmark = pytest.mark.django_db


def describe_send_lease_expiry_reminders():
    def it_notifies_the_tenant_30_days_out():
        # UserFactory triggers ensure_user_has_member signal → ACTIVE Member auto-created.
        user = UserFactory()
        member = user.member  # type: ignore[attr-defined]
        LeaseFactory(tenant_obj=member, end_date=timezone.now().date() + timedelta(days=30))
        call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 1

    def it_is_idempotent():
        user = UserFactory()
        member = user.member  # type: ignore[attr-defined]
        LeaseFactory(tenant_obj=member, end_date=timezone.now().date() + timedelta(days=30))
        call_command("send_lease_expiry_reminders")
        call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 1
