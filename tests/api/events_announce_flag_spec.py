"""BDD specs for ``POST /api/v1/events/``'s optional ``announce`` flag.

Absent or ``true`` announces as it always has; ``false`` puts the event on the calendars
without the member announcement, and only a caller who may administer events may ask for
that (``api.permissions.MayPublishWithoutAnnouncing``). The ``announce`` action releases a
held event's hold as it sends, and an update never touches the hold.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from api.permissions import MayPublishWithoutAnnouncing
from core.models import EventDelivery
from membership.models import AdminCapability, CommunityEvent, Member
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


def _ledger(event: CommunityEvent) -> int:
    """Every delivery of the announcement, on any channel: bell, email, push and Discord."""
    return EventDelivery.objects.filter(period=f"event:{event.pk}:published").count()


def _held(api_client) -> CommunityEvent:
    response = api_client.post("/api/v1/events/", _payload(announce=False), format="json")
    assert response.status_code == status.HTTP_201_CREATED
    return CommunityEvent.objects.get(title="API Bazaar")


@pytest.fixture
def emits(monkeypatch) -> list[str]:
    """Every ``emit`` call's event key: the one road to a bell, an email or a Discord post."""
    # ``core.events`` re-exports the function under the module's own name, so import by path.
    emit_module = importlib.import_module("core.events.emit")
    real = emit_module.emit
    calls: list[str] = []

    def recording(key: str, *args: object, **kwargs: object) -> object:
        calls.append(key)
        return real(key, *args, **kwargs)

    monkeypatch.setattr(emit_module, "emit", recording)
    return calls


@pytest.fixture
def reader():
    """A signed-in member for a site-wide announcement to reach."""
    from django.contrib.auth.models import User

    MembershipPlanFactory()
    return User.objects.create_user(
        username="api_reader", email="api_reader@example.com", password="x", last_login=timezone.now()
    )


def describe_the_announce_flag():
    def it_announces_when_the_field_is_absent(admin_client, reader):
        response = admin_client.post("/api/v1/events/", _payload(), format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert "announce" not in response.data
        event = CommunityEvent.objects.get(title="API Bazaar")
        assert event.announcement_held is False
        assert _bells(event) == 1

    def it_publishes_quietly_for_an_admin_token(admin_client, admin_member: Member, reader):
        assert not (admin_member.user.is_staff or admin_member.user.is_superuser)
        event = _held(admin_client)
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.announcement_held is True
        assert event.sync_state == CommunityEvent.SyncState.PENDING
        assert _bells(event) == 0

    def it_publishes_quietly_for_a_superuser_token(api_client, reader):
        superuser = User.objects.create_superuser(username="api_root", email="root@example.com", password="x")
        api_client.force_authenticate(superuser)
        assert _held(api_client).announcement_held is True

    def it_announces_on_an_explicit_true_from_any_admin(admin_client, reader):
        response = admin_client.post("/api/v1/events/", _payload(announce=True), format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert _bells(CommunityEvent.objects.get(title="API Bazaar")) == 1

    def describe_on_an_update():
        @pytest.mark.parametrize("method", ["put", "patch"])
        def it_keeps_the_hold_and_sends_nothing_when_true_is_sent(method, admin_client, reader, emits):
            event = _held(admin_client)
            emits.clear()
            body = (
                _payload(title="Renamed", announce=True) if method == "put" else {"title": "Renamed", "announce": True}
            )
            response = getattr(admin_client, method)(f"/api/v1/events/{event.pk}/", body, format="json")
            assert response.status_code == status.HTTP_200_OK
            assert "announce" not in response.data
            event.refresh_from_db()
            assert (event.title, event.announcement_held) == ("Renamed", True)
            assert event.announcement_owed_since is None
            assert emits == []
            assert _ledger(event) == 0


def describe_the_announce_action():
    def it_releases_a_held_event_and_sends_it_once(admin_client, reader, emits):
        event = _held(admin_client)
        response = admin_client.post(f"/api/v1/events/{event.pk}/announce/")
        assert response.status_code == status.HTTP_200_OK
        event.refresh_from_db()
        assert event.announcement_held is False
        assert _bells(event) == 1
        assert event.announce_now() is False  # a later Announce now on Manage Events
        assert _bells(event) == 1
        assert len(emits) == 1

    def it_refuses_a_held_event_that_is_not_live_yet(admin_client, reader):
        publish_at = (timezone.now() + timedelta(days=2)).isoformat()
        admin_client.post("/api/v1/events/", _payload(announce=False, publish_at=publish_at), format="json")
        event = CommunityEvent.objects.get(title="API Bazaar")
        assert event.moderation_state == CommunityEvent.ModerationState.SCHEDULED
        response = admin_client.post(f"/api/v1/events/{event.pk}/announce/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Only a published event" in str(response.data)
        event.refresh_from_db()
        assert event.announcement_held is True
        assert _ledger(event) == 0

    def it_announces_an_event_that_is_not_held_as_it_always_did(admin_client, reader, emits):
        admin_client.post("/api/v1/events/", _payload(), format="json")
        event = CommunityEvent.objects.get(title="API Bazaar")
        emits.clear()
        response = admin_client.post(f"/api/v1/events/{event.pk}/announce/")
        assert response.status_code == status.HTTP_200_OK
        assert len(emits) == 1
        event.refresh_from_db()
        assert event.announcement_held is False


class _View:
    def __init__(self, action: str) -> None:
        self.action = action


def _allowed(user: User, data: object, *, action: str = "create", fmt: str = "json") -> bool:
    raw = APIRequestFactory().post("/api/v1/events/", data, format=fmt)
    force_authenticate(raw, user=user)
    return MayPublishWithoutAnnouncing().has_permission(Request(raw, parsers=_parsers()), _View(action))


def _parsers() -> list:
    from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

    return [JSONParser(), FormParser(), MultiPartParser()]


def describe_may_publish_without_announcing():
    def it_refuses_false_from_someone_who_does_not_administer_events(member_member: Member):
        assert _allowed(member_member.user, {"announce": False}) is False

    def it_refuses_false_spelled_as_the_serializer_reads_it(member_member: Member):
        assert _allowed(member_member.user, {"announce": "false"}, fmt="multipart") is False

    def it_admits_false_from_an_admin(admin_member: Member):
        assert _allowed(admin_member.user, {"announce": False}) is True

    def it_admits_false_from_a_calendar_administrator(member_member: Member):
        member_member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
        assert _allowed(member_member.user, {"announce": False}) is True

    def it_admits_false_from_a_superuser_with_no_member(db):
        with mute_signals(post_save):  # no auto-provisioned Member: the admin role comes from is_superuser
            superuser = User.objects.create_superuser(username="perm_root", email="perm_root@example.com", password="x")
        assert not Member.objects.filter(user=superuser).exists()
        assert _allowed(superuser, {"announce": False}) is True

    @pytest.mark.parametrize("data", [{}, {"announce": True}, {"announce": ["false"]}, {"announce": "maybe"}])
    def it_passes_a_create_that_does_not_ask_to_stay_quiet(data, member_member: Member):
        assert _allowed(member_member.user, data) is True

    def it_passes_a_body_that_is_not_an_object(member_member: Member):
        assert _allowed(member_member.user, [{"announce": False}]) is True

    def it_leaves_every_other_action_alone(member_member: Member):
        assert _allowed(member_member.user, {"announce": False}, action="announce") is True
