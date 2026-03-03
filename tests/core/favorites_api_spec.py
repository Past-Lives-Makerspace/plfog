"""BDD-style tests for the favorites toggle API."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from membership.models import FavoriteEvent, Guild

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def user():
    return User.objects.create_user(username="favuser", password="test")


@pytest.fixture()
def guild():
    return Guild.objects.create(name="Test", slug="test")


def describe_favorites_toggle():
    def it_requires_auth(client, guild):
        ct = ContentType.objects.get_for_model(Guild)
        response = client.post(
            reverse("favorites_toggle"),
            json.dumps({"content_type_id": ct.pk, "object_id": guild.pk}),
            content_type="application/json",
        )
        assert response.status_code == 302

    def it_creates_favorite(client, user, guild):
        client.login(username="favuser", password="test")
        ct = ContentType.objects.get_for_model(Guild)
        response = client.post(
            reverse("favorites_toggle"),
            json.dumps({"content_type_id": ct.pk, "object_id": guild.pk}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["favorited"] is True
        assert FavoriteEvent.objects.filter(user=user).count() == 1

    def it_removes_favorite_on_second_toggle(client, user, guild):
        client.login(username="favuser", password="test")
        ct = ContentType.objects.get_for_model(Guild)
        payload = json.dumps({"content_type_id": ct.pk, "object_id": guild.pk})
        client.post(reverse("favorites_toggle"), payload, content_type="application/json")
        response = client.post(reverse("favorites_toggle"), payload, content_type="application/json")
        data = json.loads(response.content)
        assert data["favorited"] is False
        assert FavoriteEvent.objects.filter(user=user).count() == 0
