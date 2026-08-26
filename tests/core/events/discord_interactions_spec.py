"""Specs for the Discord Interactions infrastructure: signature verify, reply
builders, and the deferred-response REST helpers.

A throwaway ed25519 keypair (``nacl.signing.SigningKey``) signs payloads; the matching
public key is supplied to :func:`verify_signature` directly (no settings needed at this
layer). All Discord REST is mocked with ``respx`` — never the network.
"""

from __future__ import annotations

import httpx
import respx
from nacl.signing import SigningKey

from core.events import discord_interactions as di

_TS = "1531988931"
_BODY = b'{"type":1,"hello":"world"}'


def _keypair() -> tuple[SigningKey, str]:
    """A fresh signing key and its public key as hex."""
    key = SigningKey.generate()
    return key, key.verify_key.encode().hex()


def _sign(key: SigningKey, timestamp: str, body: bytes) -> str:
    """Discord's signature over ``timestamp + body`` as hex."""
    return key.sign(timestamp.encode() + body).signature.hex()


def describe_verify_signature():
    def it_accepts_a_valid_signature():
        key, public_hex = _keypair()
        assert di.verify_signature(public_hex, _sign(key, _TS, _BODY), _TS, _BODY) is True

    def it_rejects_a_signature_from_a_different_key():
        key, public_hex = _keypair()
        other, _ = _keypair()
        assert di.verify_signature(public_hex, _sign(other, _TS, _BODY), _TS, _BODY) is False

    def it_rejects_a_tampered_body():
        key, public_hex = _keypair()
        signature = _sign(key, _TS, _BODY)
        assert di.verify_signature(public_hex, signature, _TS, _BODY + b"tampered") is False

    def it_rejects_a_mismatched_timestamp():
        key, public_hex = _keypair()
        signature = _sign(key, _TS, _BODY)
        assert di.verify_signature(public_hex, signature, "9999999999", _BODY) is False

    def it_rejects_a_missing_signature_header():
        _, public_hex = _keypair()
        assert di.verify_signature(public_hex, "", _TS, _BODY) is False

    def it_rejects_a_missing_timestamp_header():
        key, public_hex = _keypair()
        assert di.verify_signature(public_hex, _sign(key, _TS, _BODY), "", _BODY) is False

    def it_rejects_a_malformed_hex_signature():
        _, public_hex = _keypair()
        assert di.verify_signature(public_hex, "not-hex-zz", _TS, _BODY) is False

    def it_rejects_a_blank_public_key():
        key, _ = _keypair()
        assert di.verify_signature("", _sign(key, _TS, _BODY), _TS, _BODY) is False


def describe_is_configured():
    def it_is_true_when_the_public_key_is_set(settings):
        settings.DISCORD_INTERACTIONS_PUBLIC_KEY = "abc123"
        assert di.is_configured() is True

    def it_is_false_when_the_public_key_is_blank(settings):
        settings.DISCORD_INTERACTIONS_PUBLIC_KEY = ""
        assert di.is_configured() is False


def describe_reply_builders():
    def it_pongs_with_type_1():
        assert di.pong() == {"type": 1}

    def it_builds_an_ephemeral_reply_by_default():
        result = di.reply("hi")
        assert result["type"] == 4
        assert result["data"]["content"] == "hi"
        assert result["data"]["flags"] == 64

    def it_builds_a_public_reply_with_flag_zero():
        assert di.reply("hi", ephemeral=False)["data"]["flags"] == 0

    def it_omits_embeds_and_components_when_not_given():
        data = di.reply("hi")["data"]
        assert "embeds" not in data
        assert "components" not in data

    def it_includes_embeds_and_components_when_given():
        data = di.reply("hi", embeds=[{"title": "t"}], components=[{"type": 1}])["data"]
        assert data["embeds"] == [{"title": "t"}]
        assert data["components"] == [{"type": 1}]

    def it_builds_a_deferred_ack_with_the_ephemeral_flag():
        assert di.deferred_ack() == {"type": 5, "data": {"flags": 64}}

    def it_builds_a_public_deferred_ack():
        assert di.deferred_ack(ephemeral=False) == {"type": 5, "data": {"flags": 0}}

    def it_builds_an_ephemeral_unlinked_reply_with_a_link_button():
        result = di.unlinked_reply("https://members.example/discord/link/")
        assert result["data"]["flags"] == 64
        button = result["data"]["components"][0]["components"][0]
        assert button["style"] == 5
        assert button["url"] == "https://members.example/discord/link/"
        # A markdown-link fallback lives in the content too.
        assert "https://members.example/discord/link/" in result["data"]["content"]

    def it_builds_an_ephemeral_error_reply():
        result = di.error_reply()
        assert result["data"]["flags"] == 64
        assert "went wrong" in result["data"]["content"]


_CALLBACK_URL = "https://discord.com/api/v10/interactions/int123/tok456/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/app789/tok456/messages/@original"


def describe_ack_deferred():
    @respx.mock
    def it_posts_a_type_5_ack_and_returns_true(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        route = respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        assert di.ack_deferred("int123", "tok456", ephemeral=True) is True
        assert route.called
        import json

        assert json.loads(route.calls.last.request.content) == {"type": 5, "data": {"flags": 64}}

    @respx.mock
    def it_returns_false_on_a_non_2xx(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(403, text="forbidden"))
        assert di.ack_deferred("int123", "tok456") is False

    @respx.mock
    def it_returns_false_on_a_network_error(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        respx.post(_CALLBACK_URL).mock(side_effect=httpx.ConnectError("no route"))
        assert di.ack_deferred("int123", "tok456") is False


def describe_send_followup():
    @respx.mock
    def it_patches_the_original_message_and_returns_true(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        settings.DISCORD_CLIENT_ID = "app789"
        route = respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m1"}))
        assert di.send_followup("tok456", content="done") is True
        import json

        assert json.loads(route.calls.last.request.content) == {"content": "done"}

    @respx.mock
    def it_includes_embeds_when_given(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        settings.DISCORD_CLIENT_ID = "app789"
        route = respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m1"}))
        di.send_followup("tok456", content="done", embeds=[{"title": "t"}])
        import json

        assert json.loads(route.calls.last.request.content)["embeds"] == [{"title": "t"}]

    @respx.mock
    def it_returns_false_on_a_non_2xx(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        settings.DISCORD_CLIENT_ID = "app789"
        respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(500, text="boom"))
        assert di.send_followup("tok456", content="done") is False

    @respx.mock
    def it_returns_false_on_a_network_error(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        settings.DISCORD_CLIENT_ID = "app789"
        respx.patch(_FOLLOWUP_URL).mock(side_effect=httpx.ConnectError("no route"))
        assert di.send_followup("tok456", content="done") is False


def describe_deferred_update_ack():
    def it_is_a_bare_type_6_body():
        assert di.deferred_update_ack() == {"type": 6}


def describe_ack_component_deferred():
    @respx.mock
    def it_posts_a_type_6_ack_and_returns_true(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        route = respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        assert di.ack_component_deferred("int123", "tok456") is True
        assert route.called
        import json

        assert json.loads(route.calls.last.request.content) == {"type": 6}

    @respx.mock
    def it_returns_false_on_a_non_2xx(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(403, text="forbidden"))
        assert di.ack_component_deferred("int123", "tok456") is False

    @respx.mock
    def it_returns_false_on_a_network_error(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        respx.post(_CALLBACK_URL).mock(side_effect=httpx.ConnectError("no route"))
        assert di.ack_component_deferred("int123", "tok456") is False


def describe_update_message():
    def it_builds_a_type_7_body_with_only_content_by_default():
        result = di.update_message("edited")
        assert result == {"type": 7, "data": {"content": "edited"}}

    def it_includes_embeds_components_and_allowed_mentions_when_given():
        data = di.update_message(
            "edited",
            embeds=[{"title": "t"}],
            components=[{"type": 1}],
            allowed_mentions={"parse": []},
        )["data"]
        assert data["embeds"] == [{"title": "t"}]
        assert data["components"] == [{"type": 1}]
        assert data["allowed_mentions"] == {"parse": []}


_EXPIRE_URL = "https://discord.com/api/v10/channels/chan/polls/msg/expire"


def describe_expire_poll():
    @respx.mock
    def it_returns_true_on_a_2xx(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        route = respx.post(_EXPIRE_URL).mock(return_value=httpx.Response(200, json={}))
        assert di.expire_poll("chan", "msg") is True
        assert route.calls.last.request.headers["Authorization"] == "Bot bot-token"

    @respx.mock
    def it_returns_false_on_a_non_2xx(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        respx.post(_EXPIRE_URL).mock(return_value=httpx.Response(400, json={"code": 520003}))
        assert di.expire_poll("chan", "msg") is False

    @respx.mock
    def it_returns_false_on_a_network_error(settings):
        settings.DISCORD_BOT_TOKEN = "bot-token"
        respx.post(_EXPIRE_URL).mock(side_effect=httpx.ConnectError("no route"))
        assert di.expire_poll("chan", "msg") is False


def describe_modal_builders():
    def it_builds_a_type_9_modal():
        result = di.modal("myform", "My Form", [{"type": 10, "content": "hi"}])
        assert result == {
            "type": 9,
            "data": {"custom_id": "myform", "title": "My Form", "components": [{"type": 10, "content": "hi"}]},
        }

    def it_wraps_a_child_in_a_label_and_adds_a_description_only_when_given():
        child = {"type": 4, "custom_id": "q"}
        assert di.modal_label("Q", child) == {"type": 18, "label": "Q", "component": child}
        assert di.modal_label("Q", child, description="hint")["description"] == "hint"

    def it_builds_a_text_display():
        assert di.text_display("note") == {"type": 10, "content": "note"}

    def it_builds_a_minimal_short_text_input():
        assert di.text_input("q") == {"type": 4, "custom_id": "q", "style": 1, "required": True}

    def it_includes_the_optional_text_input_keys_when_given():
        comp = di.text_input("q", style=2, placeholder="p", value="v", required=False, min_length=1, max_length=9)
        assert comp == {
            "type": 4,
            "custom_id": "q",
            "style": 2,
            "required": False,
            "placeholder": "p",
            "value": "v",
            "min_length": 1,
            "max_length": 9,
        }

    def it_builds_a_string_select():
        options = [{"label": "A", "value": "a"}]
        assert di.string_select("s", options) == {"type": 3, "custom_id": "s", "options": options, "required": True}

    def it_builds_a_checkbox_unchecked_by_default():
        assert di.checkbox("c") == {"type": 23, "custom_id": "c", "required": False}

    def it_marks_a_checkbox_checked_by_default():
        assert di.checkbox("c", default=True, required=True) == {
            "type": 23,
            "custom_id": "c",
            "required": True,
            "value": True,
        }


def describe_parse_modal_values():
    def it_reads_label_wrapped_text_inputs_and_selects():
        interaction = {
            "data": {
                "components": [
                    {"type": 18, "component": {"custom_id": "q", "value": "Hello"}},
                    {"type": 18, "component": {"custom_id": "dur", "values": ["24"]}},
                ]
            }
        }
        assert di.parse_modal_values(interaction) == {"q": "Hello", "dur": ["24"]}

    def it_reads_the_legacy_action_row_shape():
        interaction = {"data": {"components": [{"type": 1, "components": [{"custom_id": "q", "value": "Hi"}]}]}}
        assert di.parse_modal_values(interaction) == {"q": "Hi"}

    def it_skips_a_child_without_a_custom_id():
        interaction = {"data": {"components": [{"type": 18, "component": {"value": "orphan"}}]}}
        assert di.parse_modal_values(interaction) == {}

    def it_skips_a_child_carrying_neither_value_nor_values():
        # An unchecked checkbox Discord may echo with no values key at all.
        interaction = {"data": {"components": [{"type": 18, "component": {"custom_id": "c"}}]}}
        assert di.parse_modal_values(interaction) == {}
