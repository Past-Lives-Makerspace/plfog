""" "Today" is Portland's date wherever a day is decided, not the UTC one.

The UTC date runs a day ahead from 5 PM Pacific (4 PM in winter), so reading "today" off
``timezone.now().date()`` dropped tonight's events from Home's Upcoming list, ended a space
agreement on the evening before its last day, and sent the 30 day reminder a day early, on
the evening before. The
clock is frozen at 17:30 Pacific on day D, when the UTC date is already D + 1, the repo's
way: by patching ``django.utils.timezone.now``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from classes.factories import UserFactory
from core.models import Notification
from hub.home import build_home_context
from tests.membership.factories import (
    CommunityEventFactory,
    LeaseFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

DAY = timezone.localdate() + timedelta(days=2)
FROZEN_NOW = timezone.make_aware(datetime.combine(DAY, time(17, 30))).astimezone(UTC)


@pytest.fixture(autouse=True)
def _evening() -> Iterator[None]:
    assert FROZEN_NOW.date() == DAY + timedelta(days=1), "the frozen clock must sit past UTC midnight"
    with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
        yield


def describe_home_upcoming():
    def it_keeps_tonights_event():
        MembershipPlanFactory()
        member = MemberFactory()
        tonight = timezone.make_aware(datetime.combine(DAY, time(19, 0)))
        CommunityEventFactory(community=True, title="Open Shop Night", starts_at=tonight)

        upcoming = build_home_context(member)["upcoming"]

        assert [item.title for item in upcoming] == ["Open Shop Night"]


def describe_space_agreements():
    def it_is_active_on_the_evening_of_its_last_day():
        lease = LeaseFactory(start_date=DAY - timedelta(days=30), end_date=DAY)

        assert lease.is_active is True
        assert list(lease.tenant.active_leases) == [lease]

    def it_is_not_active_on_the_evening_before_it_starts():
        lease = LeaseFactory(start_date=DAY + timedelta(days=1), end_date=None)

        assert lease.is_active is False
        assert list(lease.tenant.active_leases) == []

    def it_sends_the_reminder_thirty_portland_days_out():
        member = UserFactory().member  # type: ignore[attr-defined]
        LeaseFactory(tenant_obj=member, end_date=DAY + timedelta(days=30))

        call_command("send_lease_expiry_reminders")

        assert Notification.objects.filter(trigger="lease_expiring").count() == 1
