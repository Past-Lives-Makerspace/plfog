"""Specs for the ``/link`` slash command — the connect on-ramp.

``/link`` makes **no outbound HTTP** (a single indexed DB lookup + a URL build), so unlike
the deferred flow these specs need no ``respx``. The handler branches are exercised directly
(``_link(interaction, member)``); the security + error-path cases go through ``dispatch`` /
the real interactions view, proving the whole path stays a valid 200.

Discord's availability gate (``discord_oauth.is_configured``) is monkeypatched per test so the
branch under test is deterministic regardless of the container's env.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse
from nacl.signing import SigningKey

from core.events import discord_oauth
from core.events.discord_commands import dispatch
from core.events.discord_interactions import error_reply
from hub.discord_commands import _NOT_CONFIGURED, _link
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _configured(monkeypatch, value=True):
    monkeypatch.setattr(discord_oauth, "is_configured", lambda: value)


def describe_handle_link():
    def describe_when_discord_not_configured():
        def it_replies_with_the_friendly_not_configured_message(monkeypatch):
            _configured(monkeypatch, False)
            result = _link({"user": {"id": "1"}}, None)
            assert result["data"]["content"] == _NOT_CONFIGURED
            assert result["data"]["flags"] == 64

        def it_never_hands_out_a_connect_link(monkeypatch):
            _configured(monkeypatch, False)
            result = _link({"user": {"id": "1"}}, None)
            assert "components" not in result["data"]
            assert "/discord/link/" not in result["data"]["content"]

    def describe_when_caller_already_linked():
        def it_greets_them_by_display_name(monkeypatch, linked_member):
            _configured(monkeypatch)
            member = linked_member(discord_user_id="555", preferred_name="Rowan")
            result = _link({"member": {"user": {"id": "555"}}}, member)
            assert "Rowan" in result["data"]["content"]
            assert result["data"]["flags"] == 64

        def it_nudges_whats_on(monkeypatch, linked_member):
            _configured(monkeypatch)
            member = linked_member(discord_user_id="555", preferred_name="Rowan")
            result = _link({"member": {"user": {"id": "555"}}}, member)
            assert "/whats-on" in result["data"]["content"]

        def it_does_not_hand_out_a_connect_link(monkeypatch, linked_member):
            _configured(monkeypatch)
            member = linked_member(discord_user_id="555", preferred_name="Rowan")
            result = _link({"member": {"user": {"id": "555"}}}, member)
            assert "components" not in result["data"]
            assert "/discord/link/" not in result["data"]["content"]

    def describe_when_caller_not_linked():
        def it_replies_with_the_connect_link(monkeypatch):
            _configured(monkeypatch)
            result = _link({"user": {"id": "999"}}, None)
            button = result["data"]["components"][0]["components"][0]
            assert button["label"] == "Connect Discord"
            assert button["style"] == 5
            assert button["url"].endswith("/discord/link/")

        def it_uses_the_absolute_hub_discord_link_start_url(monkeypatch, settings):
            _configured(monkeypatch)
            settings.MEMBER_BASE_URL = "https://members.example"
            result = _link({"user": {"id": "999"}}, None)
            expected = f"{settings.MEMBER_BASE_URL}{reverse('hub_discord_link_start')}"
            assert result["data"]["components"][0]["components"][0]["url"] == expected
            assert expected in result["data"]["content"]

        def it_reply_is_ephemeral(monkeypatch):
            _configured(monkeypatch)
            result = _link({"user": {"id": "999"}}, None)
            assert result["data"]["flags"] == 64

    def describe_security():
        def it_never_calls_link_discord(monkeypatch, rf):
            # A fresh unlinked member is STILL unlinked after /link — the command links no one.
            _configured(monkeypatch)
            member = MemberFactory(discord_user_id="")
            interaction = {"type": 2, "data": {"name": "link"}, "member": {"user": {"id": "abc123"}}}
            dispatch(interaction, rf.post("/"))
            member.refresh_from_db()
            assert member.discord_is_linked is False

        def it_resolves_by_discord_user_id_not_username(monkeypatch, rf, linked_member):
            _configured(monkeypatch)
            linked_member(discord_user_id="654", preferred_name="Ash")
            # Matched by exact id → the already-linked greeting, even with an unrelated username.
            matched = dispatch(
                {"type": 2, "data": {"name": "link"}, "member": {"user": {"id": "654", "username": "someone-else"}}},
                rf.post("/"),
            )
            assert "Ash" in matched["data"]["content"]
            assert "components" not in matched["data"]
            # A username that merely looks like the handle, on a different id, does NOT resolve.
            unmatched = dispatch(
                {"type": 2, "data": {"name": "link"}, "member": {"user": {"id": "000", "username": "Ash"}}},
                rf.post("/"),
            )
            assert unmatched["data"]["components"][0]["components"][0]["url"].endswith("/discord/link/")

    def describe_when_the_handler_raises():
        def it_returns_the_friendly_error_reply_not_a_500(monkeypatch, rf):
            def _boom():
                raise RuntimeError("kaboom")

            monkeypatch.setattr(discord_oauth, "is_configured", _boom)
            result = dispatch({"type": 2, "data": {"name": "link"}, "user": {"id": "1"}}, rf.post("/"))
            assert result == error_reply()

        def it_still_returns_a_valid_200_interaction_response(monkeypatch, client, settings):
            def _boom():
                raise RuntimeError("kaboom")

            monkeypatch.setattr(discord_oauth, "is_configured", _boom)
            key = SigningKey.generate()
            settings.DISCORD_INTERACTIONS_PUBLIC_KEY = key.verify_key.encode().hex()
            body = json.dumps({"type": 2, "id": "i1", "token": "t1", "data": {"name": "link"}, "user": {"id": "1"}})
            timestamp = "1531988931"
            signature = key.sign(timestamp.encode() + body.encode()).signature.hex()
            response = client.post(
                reverse("discord_interactions"),
                data=body,
                content_type="application/json",
                HTTP_X_SIGNATURE_ED25519=signature,
                HTTP_X_SIGNATURE_TIMESTAMP=timestamp,
            )
            assert response.status_code == 200
            assert "went wrong" in response.json()["data"]["content"]


def describe_LINK_command():
    def it_does_not_require_a_link_so_it_serves_unlinked_members():
        from hub.discord_commands import LINK

        assert LINK.requires_link is False
        assert LINK.ephemeral is True
        assert LINK.defer is False

    def it_is_registered_and_reachable_by_name():
        from core.events.discord_commands import all_commands

        assert "link" in [c.name for c in all_commands()]
