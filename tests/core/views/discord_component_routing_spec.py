"""Specs for the ``discord_interactions`` view's type-3 (MESSAGE_COMPONENT) routing.

Kept apart from ``discord_interactions_view_spec.py`` deliberately: that file signs
requests with PyNaCl and is skipped on images without it. Here ``verify_signature`` is
monkeypatched instead (the signature gate itself is proven by the signed-request suite),
so the routing specs run everywhere.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from core.events import discord_commands
from core.events.discord_commands import ComponentHandler, register_component

pytestmark = pytest.mark.django_db

_URL = reverse("discord_interactions")


@pytest.fixture(autouse=True)
def _restore_component_registry():
    snapshot = dict(discord_commands._COMPONENT_REGISTRY)
    yield
    discord_commands._COMPONENT_REGISTRY.clear()
    discord_commands._COMPONENT_REGISTRY.update(snapshot)


@pytest.fixture
def verified(monkeypatch):
    """Treat every request as carrying a valid Discord signature."""
    monkeypatch.setattr("core.events.discord_interactions.verify_signature", lambda *args: True)


def _post(client, payload):
    return client.post(_URL, data=json.dumps(payload).encode(), content_type="application/json")


def describe_discord_interactions_component_routing():
    def it_routes_a_type_3_interaction_to_the_component_dispatcher(client, verified):
        register_component(
            ComponentHandler(
                prefix="routed",
                handler=lambda interaction, member: {"type": 7, "data": {"content": "flipped"}},
                requires_link=False,
            )
        )
        payload = {"type": 3, "data": {"custom_id": "routed:2:-:"}, "user": {"id": "1"}}
        response = _post(client, payload)
        assert response.status_code == 200
        assert response.json() == {"type": 7, "data": {"content": "flipped"}}

    def it_returns_the_error_reply_json_for_an_unknown_prefix(client, verified):
        response = _post(client, {"type": 3, "data": {"custom_id": "ghost:1"}, "user": {"id": "1"}})
        assert response.status_code == 200
        assert "went wrong" in response.json()["data"]["content"]

    def it_still_acks_an_unrouted_type_with_an_empty_200(client, verified):
        response = _post(client, {"type": 5, "data": {}})
        assert response.status_code == 200
        assert response.content == b""

    def it_still_401s_when_the_signature_fails(client, monkeypatch):
        monkeypatch.setattr("core.events.discord_interactions.verify_signature", lambda *args: False)
        response = _post(client, {"type": 3, "data": {"custom_id": "routed:1"}})
        assert response.status_code == 401
