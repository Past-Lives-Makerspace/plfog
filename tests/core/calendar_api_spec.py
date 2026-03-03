"""BDD-style tests for the calendar events API."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from education.models import ClassSession, MakerClass, Orientation, ScheduledOrientation
from membership.models import Guild
from outreach.models import Event

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def guild():
    return Guild.objects.create(name="Ceramics", slug="ceramics")


@pytest.fixture()
def now():
    return timezone.now()


def describe_calendar_events_api():
    def it_returns_json(client, guild, now):
        Event.objects.create(
            name="Open House",
            description="Tour",
            starts_at=now,
            ends_at=now + timezone.timedelta(hours=2),
            location="Main Hall",
            guild=guild,
            created_by=User.objects.create_user(username="e_user", password="test"),
        )
        response = client.get(
            reverse("calendar_events"),
            {
                "start": (now - timezone.timedelta(days=1)).isoformat(),
                "end": (now + timezone.timedelta(days=1)).isoformat(),
            },
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert len(data) >= 1
        assert data[0]["title"] is not None

    def it_filters_by_guild(client, guild, now):
        user = User.objects.create_user(username="f_user", password="test")
        other = Guild.objects.create(name="Tech", slug="tech")
        Event.objects.create(
            name="Ceramics Event",
            description="x",
            starts_at=now,
            ends_at=now + timezone.timedelta(hours=1),
            location="Studio",
            guild=guild,
            created_by=user,
        )
        Event.objects.create(
            name="Tech Event",
            description="x",
            starts_at=now,
            ends_at=now + timezone.timedelta(hours=1),
            location="Lab",
            guild=other,
            created_by=user,
        )
        response = client.get(
            reverse("calendar_events"),
            {
                "guild": "ceramics",
                "start": (now - timezone.timedelta(days=1)).isoformat(),
                "end": (now + timezone.timedelta(days=1)).isoformat(),
            },
        )
        data = json.loads(response.content)
        titles = [e["title"] for e in data]
        assert any("Ceramics Event" in t for t in titles)
        assert not any("Tech Event" in t for t in titles)

    def it_includes_class_sessions(client, guild, now):
        maker_class = MakerClass.objects.create(name="Wheel Throwing", guild=guild, status="published")
        ClassSession.objects.create(
            maker_class=maker_class,
            starts_at=now,
            ends_at=now + timezone.timedelta(hours=2),
        )
        response = client.get(
            reverse("calendar_events"),
            {
                "start": (now - timezone.timedelta(days=1)).isoformat(),
                "end": (now + timezone.timedelta(days=1)).isoformat(),
            },
        )
        data = json.loads(response.content)
        types = [e["type"] for e in data]
        assert "class" in types

    def it_includes_scheduled_orientations(client, guild, now):
        orientation = Orientation.objects.create(name="Safety Orientation", guild=guild, duration_minutes=60)
        ScheduledOrientation.objects.create(
            orientation=orientation,
            scheduled_at=now,
            status="scheduled",
        )
        response = client.get(
            reverse("calendar_events"),
            {
                "start": (now - timezone.timedelta(days=1)).isoformat(),
                "end": (now + timezone.timedelta(days=1)).isoformat(),
            },
        )
        data = json.loads(response.content)
        types = [e["type"] for e in data]
        assert "orientation" in types

    def it_returns_empty_without_date_params(client):
        response = client.get(reverse("calendar_events"))
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data == []
