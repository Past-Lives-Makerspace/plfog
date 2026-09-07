"""Specs for the ``discord_interactions`` view — the Discord Interactions Endpoint URL.

Signs requests with a throwaway ed25519 keypair (``nacl.signing.SigningKey``) and drives
them through the unauthenticated test client, proving the middleware routes the POST to the
view without gating (no login, only the ed25519 signature decides). The only non-2xx the
view ever returns is the 401 for a bad/missing/unconfigured signature.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse
from nacl.signing import SigningKey

from core.events import discord_commands
from core.events.discord_commands import SlashCommand
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

_TS = "1531988931"
_URL = reverse("discord_interactions")


def _keypair() -> tuple[SigningKey, str]:
    key = SigningKey.generate()
    return key, key.verify_key.encode().hex()


@pytest.fixture
def signer(settings) -> SigningKey:
    """A signing key whose public key is configured as the endpoint's verify key."""
    key, public_hex = _keypair()
    settings.DISCORD_INTERACTIONS_PUBLIC_KEY = public_hex
    return key


@pytest.fixture(autouse=True)
def _restore_registry():
    snapshot = dict(discord_commands._REGISTRY)
    yield
    discord_commands._REGISTRY.clear()
    discord_commands._REGISTRY.update(snapshot)


def _post(client, key, payload, *, sign_body=None, headers=True):
    body = json.dumps(payload).encode()
    extra = {}
    if headers:
        signed = sign_body if sign_body is not None else body
        extra = {
            "HTTP_X_SIGNATURE_ED25519": key.sign(_TS.encode() + signed).signature.hex(),
            "HTTP_X_SIGNATURE_TIMESTAMP": _TS,
        }
    return client.post(_URL, data=body, content_type="application/json", **extra)


def describe_discord_interactions_view():
    def it_pongs_a_valid_ping(client, signer):
        response = _post(client, signer, {"type": 1})
        assert response.status_code == 200
        assert response.json() == {"type": 1}

    def it_returns_401_on_a_bad_signature(client, signer):
        # Signature is computed over a different body than the one sent.
        response = _post(client, signer, {"type": 1}, sign_body=b"tampered")
        assert response.status_code == 401

    def it_returns_401_when_signature_headers_are_missing(client, signer):
        response = _post(client, signer, {"type": 1}, headers=False)
        assert response.status_code == 401

    def it_returns_401_when_the_public_key_is_blank(client, signer, settings):
        settings.DISCORD_INTERACTIONS_PUBLIC_KEY = ""
        response = _post(client, signer, {"type": 1})
        assert response.status_code == 401

    def it_dispatches_a_command_for_a_linked_member(client, signer):
        MemberFactory(discord_user_id="555", preferred_name="Rowan")
        payload = {
            "type": 2,
            "id": "i1",
            "token": "t1",
            "data": {"name": "fog-ping"},
            "member": {"user": {"id": "555"}},
        }
        response = _post(client, signer, payload)
        assert response.status_code == 200
        body = response.json()
        assert body["type"] == 4
        assert body["data"]["flags"] == 64
        assert "Rowan" in body["data"]["content"]

    def it_returns_the_unlinked_prompt_for_an_unlinked_member(client, signer):
        payload = {
            "type": 2,
            "id": "i1",
            "token": "t1",
            "data": {"name": "fog-ping"},
            "member": {"user": {"id": "000"}},
        }
        response = _post(client, signer, payload)
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["flags"] == 64
        button = body["data"]["components"][0]["components"][0]
        assert button["url"].startswith("http")
        assert button["url"].endswith("/discord/link/")

    def it_returns_error_reply_when_a_handler_raises(client, signer):
        def _boom(interaction, member):
            raise RuntimeError("kaboom")

        discord_commands.register(SlashCommand(name="boom", description="d", handler=_boom, requires_link=False))
        payload = {"type": 2, "id": "i1", "token": "t1", "data": {"name": "boom"}, "user": {"id": "1"}}
        response = _post(client, signer, payload)
        assert response.status_code == 200
        assert "went wrong" in response.json()["data"]["content"]

    def it_returns_error_reply_for_an_unknown_command(client, signer):
        payload = {"type": 2, "id": "i1", "token": "t1", "data": {"name": "ghost"}, "user": {"id": "1"}}
        response = _post(client, signer, payload)
        assert response.status_code == 200
        assert "went wrong" in response.json()["data"]["content"]

    def it_acks_an_unsupported_interaction_type_with_an_empty_200(client, signer):
        # Type 4 (autocomplete) is unrouted; 1/2/3/5 are PING/command/component/modal.
        response = _post(client, signer, {"type": 4, "data": {"name": "x"}})
        assert response.status_code == 200
        assert response.content == b""

    def it_reaches_the_view_unauthenticated(client, signer):
        # Proves the surface/host middleware routes the POST to the view without a login;
        # only the signature decides (a bad one → 401, not a redirect or 403).
        response = _post(client, signer, {"type": 1}, sign_body=b"x")
        assert response.status_code == 401
        assert not response.get("Location")
