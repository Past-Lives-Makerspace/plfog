"""Specs for the ⚙ End-poll affordance (membership.discord_commands).

Covers the gear row on the ``/poll`` reply (beside the native poll payload), the creator /
admin end path (respx asserts the expire URL, the **type-5 ephemeral** callback, and the
ephemeral "Poll closed." followup — never a PATCH against the poll message itself), the
stranger refusal, the already-ended branch, and the malformed custom_id guard.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from factory.django import mute_signals

from core.events.discord_interactions import error_reply
from membership.discord_commands import _poll, _poll_component
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

_CALLBACK_URL = "https://discord.com/api/v10/interactions/intA/tokB/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/appX/tokB/messages/@original"
_EXPIRE_URL = "https://discord.com/api/v10/channels/chan1/polls/msg1/expire"


def _linked_member(**kwargs) -> Member:
    member = MemberFactory(**kwargs)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"poll_u_{member.pk}", email=f"poll_u_{member.pk}@example.com")
    member.user = user
    member.save(update_fields=["user"])
    return member


def _discord_settings(settings) -> None:
    settings.DISCORD_BOT_TOKEN = "bot"
    settings.DISCORD_CLIENT_ID = "appX"


def _poll_interaction(**options: object) -> dict:
    opts = [{"name": name, "value": value} for name, value in options.items()]
    return {"data": {"options": opts}}


def _end_click(member, creator_pk) -> dict:
    interaction = {
        "id": "intA",
        "token": "tokB",
        "channel_id": "chan1",
        "message": {"id": "msg1"},
        "data": {"custom_id": f"poll:end:{creator_pk}"},
    }
    return _poll_component(interaction, member)


def describe_the_gear_row_on_the_poll_reply():
    def it_carries_an_end_button_beside_the_poll_payload():
        member = _linked_member()
        result = _poll(_poll_interaction(question="Best night?", answers="Fri; Sat"), member)
        assert "poll" in result["data"]  # the native poll rides along
        gear = result["data"]["components"][0]["components"][0]
        assert gear["label"] == "⚙"
        assert gear["custom_id"] == f"poll:end:{member.pk}"

    def it_keeps_the_no_ping_gate_on_the_public_reply():
        member = _linked_member()
        result = _poll(_poll_interaction(question="Best night?", answers="Fri; Sat"), member)
        assert result["data"]["allowed_mentions"] == {"parse": []}
        assert result["data"]["flags"] == 0  # public, not ephemeral


def describe_ending_a_poll():
    @respx.mock
    def it_lets_the_creator_end_it_and_confirms_ephemerally(settings):
        _discord_settings(settings)
        callback = respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        expire = respx.post(_EXPIRE_URL).mock(return_value=httpx.Response(200, json={}))
        followup = respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))
        creator = _linked_member()

        assert _end_click(creator, creator.pk) == {}
        # A type-5 ephemeral ack (NOT type 6 — a poll message cannot be edited).
        assert json.loads(callback.calls.last.request.content) == {"type": 5, "data": {"flags": 64}}
        assert expire.called  # the poll is expired via its own endpoint, never a message PATCH
        payload = json.loads(followup.calls.last.request.content)
        assert payload["content"] == "Poll closed."
        assert payload["allowed_mentions"] == {"parse": []}

    @respx.mock
    def it_lets_a_fog_admin_end_someone_elses_poll(settings):
        _discord_settings(settings)
        respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        expire = respx.post(_EXPIRE_URL).mock(return_value=httpx.Response(200, json={}))
        respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))
        admin = _linked_member(fog_role=Member.FogRole.ADMIN)
        creator = _linked_member()

        assert _end_click(admin, creator.pk) == {}
        assert expire.called

    @respx.mock
    def it_reports_already_ended_when_the_expire_call_fails(settings):
        _discord_settings(settings)
        respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        respx.post(_EXPIRE_URL).mock(return_value=httpx.Response(400, json={"code": 520003}))
        followup = respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))
        creator = _linked_member()

        assert _end_click(creator, creator.pk) == {}
        assert json.loads(followup.calls.last.request.content)["content"] == "This poll has already ended."

    def it_refuses_a_stranger_without_touching_discord():
        with respx.mock:  # no routes mocked — any HTTP call fails the spec
            result = _end_click(_linked_member(), _linked_member().pk)
        assert result["type"] == 4
        assert "Only the person who started this poll or an admin" in result["data"]["content"]

    @pytest.mark.parametrize("custom_id", ["poll:end:x", "poll:stop:1", "poll:end", "poll:end:1:2"])
    def it_error_replies_on_a_malformed_custom_id(custom_id):
        interaction = {"id": "intA", "token": "tokB", "data": {"custom_id": custom_id}}
        assert _poll_component(interaction, _linked_member()) == error_reply()
