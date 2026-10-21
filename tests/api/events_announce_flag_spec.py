"""BDD specs for ``POST /api/v1/events/``'s optional ``announce`` flag.

Absent or ``true`` announces as it always has; ``false`` puts the event on the calendars
without the member announcement, and only a staff or superuser token may ask for that.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from core.models import EventDelivery
from membership.models import CommunityEvent, Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _payload(**overrides: object) -> dict:
    starts = timezone.now() + timedelta(days=40)
    data: dict = {
        "title": "API Bazaar",
        "event_type": "community",
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=4)).isoformat(),
        "location": "Main Studio",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


def _bells(event: CommunityEvent) -> int:
    return EventDelivery.objects.filter(channel="in_app", period=f"event:{event.pk}:published").count()


@pytest.fixture
def reader():
    """A signed-in member for a site-wide announcement to reach."""
    from django.contrib.auth.models import User

    MembershipPlanFactory()
    return User.objects.create_user(
        username="api_reader", email="api_reader@example.com", password="x", last_login=timezone.now()
    )


@pytest.fixture
def staff_client(admin_client, admin_member: Member):
    admin_member.user.is_staff = True
    admin_member.user.save(update_fields=["is_staff"])
    return admin_client


def describe_the_announce_flag():
    def it_announces_when_the_field_is_absent(staff_client, reader):
        response = staff_client.post("/api/v1/events/", _payload(), format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert "announce" not in response.data
        event = CommunityEvent.objects.get(title="API Bazaar")
        assert event.announcement_held is False
        assert _bells(event) == 1

    def it_publishes_quietly_for_a_staff_token(staff_client, reader):
        response = staff_client.post("/api/v1/events/", _payload(announce=False), format="json")
        assert response.status_code == status.HTTP_201_CREATED
        event = CommunityEvent.objects.get(title="API Bazaar")
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.announcement_held is True
        assert event.sync_state == CommunityEvent.SyncState.PENDING
        assert _bells(event) == 0

    def it_publishes_quietly_for_a_superuser_token(admin_client, admin_member: Member, reader):
        admin_member.user.is_superuser = True
        admin_member.user.save(update_fields=["is_superuser"])
        response = admin_client.post("/api/v1/events/", _payload(announce=False), format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert CommunityEvent.objects.get(title="API Bazaar").announcement_held is True

    def it_refuses_false_from_a_token_that_is_not_staff(admin_client, admin_member: Member):
        assert not (admin_member.user.is_staff or admin_member.user.is_superuser)
        response = admin_client.post("/api/v1/events/", _payload(announce=False), format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not CommunityEvent.objects.filter(title="API Bazaar").exists()

    def it_announces_on_an_explicit_true_from_any_admin(admin_client, reader):
        response = admin_client.post("/api/v1/events/", _payload(announce=True), format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert _bells(CommunityEvent.objects.get(title="API Bazaar")) == 1

    def it_is_ignored_on_an_update(staff_client):
        staff_client.post("/api/v1/events/", _payload(announce=False), format="json")
        event = CommunityEvent.objects.get(title="API Bazaar")
        response = staff_client.put(f"/api/v1/events/{event.pk}/", _payload(title="Renamed"), format="json")
        assert response.status_code == status.HTTP_200_OK
        event.refresh_from_db()
        assert (event.title, event.announcement_held) == ("Renamed", True)
