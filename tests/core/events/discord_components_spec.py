"""Specs for the type-7 ``update_message()`` component-response builder.

Lives apart from ``discord_interactions_spec.py`` deliberately: that file imports PyNaCl
(for signature specs) and is skipped on images without it, while this builder is pure dict
assembly and should run everywhere.
"""

from __future__ import annotations

from core.events.discord_interactions import update_message


def describe_update_message():
    def it_builds_a_type_7_update():
        result = update_message("hi")
        assert result["type"] == 7
        assert result["data"]["content"] == "hi"

    def it_omits_embeds_and_components_when_not_given():
        data = update_message("hi")["data"]
        assert "embeds" not in data
        assert "components" not in data

    def it_includes_embeds_and_components_when_given():
        data = update_message("", embeds=[{"title": "t"}], components=[{"type": 1}])["data"]
        assert data["embeds"] == [{"title": "t"}]
        assert data["components"] == [{"type": 1}]

    def it_never_carries_a_flags_key():
        # Flags are immutable on an existing message — an ephemeral browse stays ephemeral.
        assert "flags" not in update_message("hi", embeds=[], components=[])["data"]
