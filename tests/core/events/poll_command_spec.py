"""Specs for the ``/poll`` slash command (membership.discord_commands).

Covers the command definition, the answers-splitting helper, leading-emoji extraction
(unicode and custom Discord tokens, with the empty-remainder rules), every ephemeral
validation reply, the public happy-path poll payload, the ``reply(poll=…)`` kwarg, and
the dispatch + ``/guide`` integration. No Discord REST is touched: ``/poll`` posts its
poll straight from the interaction response (``defer=False``), so there is nothing to mock.
"""

from __future__ import annotations

import pytest

from core.events import discord_interactions as di
from core.events.discord_commands import _guide, all_commands, dispatch
from membership.discord_commands import (
    _POLL_ANSWER_MAX,
    _POLL_DUPLICATE,
    _POLL_QUESTION_TOO_LONG,
    _POLL_TOO_FEW,
    _POLL_TOO_MANY,
    POLL,
    _answer_media,
    _emoji_prefix,
    _poll,
    _split_answers,
)

pytestmark = pytest.mark.django_db

# Multi-codepoint emoji written with explicit escapes so the ZWJ / skin-tone / regional /
# variation-selector joiners are unambiguous in source.
_FAMILY = "\U0001f468‍\U0001f469‍\U0001f467"  # man + ZWJ + woman + ZWJ + girl
_THUMB_TONED = "\U0001f44d\U0001f3fd"  # thumbs-up + medium skin tone
_FLAG_US = "\U0001f1fa\U0001f1f8"  # regional-indicator U + S
_SUN_VS16 = "☀️"  # sun + variation selector 16


def _interaction(**options: object) -> dict:
    opts = [{"name": name, "value": value} for name, value in options.items()]
    return {"data": {"options": opts}}


def _run(member: object, **options: object) -> dict:
    return _poll(_interaction(**options), member)


def _assert_ephemeral_no_poll(result: dict) -> None:
    assert result["data"]["flags"] == 64  # ephemeral — only the invoker sees the rejection
    assert "poll" not in result["data"]  # nothing was posted to the channel


# --- Command definition -------------------------------------------------------


def describe_command_definition():
    def it_is_link_gated_public_and_not_deferred():
        assert POLL.name == "poll"
        assert (POLL.requires_link, POLL.ephemeral, POLL.defer) == (True, False, False)
        assert POLL.scope == "guild"

    def it_exposes_question_and_answers_first_then_optionals():
        opts = POLL.to_api_dict()["options"]
        assert [o["name"] for o in opts] == ["question", "answers", "duration", "multiselect"]
        required = [o.get("required", False) for o in opts]
        assert required[:2] == [True, True]
        assert not any(required[2:])

    def it_offers_the_documented_duration_choices_as_integers():
        duration = next(o for o in POLL.to_api_dict()["options"] if o["name"] == "duration")
        assert duration["type"] == 4  # INTEGER — a string option with int values fails registration
        assert [c["value"] for c in duration["choices"]] == [1, 4, 8, 24, 72, 168, 336, 768]
        assert all(isinstance(c["value"], int) for c in duration["choices"])
        default = next(c for c in duration["choices"] if c["value"] == 24)
        assert "(default)" in default["name"]

    def it_makes_multiselect_a_boolean_option():
        multiselect = next(o for o in POLL.to_api_dict()["options"] if o["name"] == "multiselect")
        assert multiselect["type"] == 5
        assert multiselect.get("required", False) is False


# --- Answers splitting --------------------------------------------------------


def describe_split_answers():
    def it_splits_on_semicolons():
        assert _split_answers("Alien; Clue; The Thing") == ["Alien", "Clue", "The Thing"]

    def it_splits_on_pipes_when_there_is_no_semicolon():
        assert _split_answers("Alien|Clue|The Thing") == ["Alien", "Clue", "The Thing"]

    def it_prefers_semicolons_so_a_pipe_survives_inside_an_answer():
        assert _split_answers("Alien; Clue|The Thing") == ["Alien", "Clue|The Thing"]

    def it_trims_each_piece():
        assert _split_answers("  Alien ;  Clue  ") == ["Alien", "Clue"]

    def it_drops_empty_segments():
        assert _split_answers("Alien;;Clue;") == ["Alien", "Clue"]


# --- Emoji extraction ---------------------------------------------------------


def describe_answer_media():
    def it_leaves_a_plain_answer_untouched():
        assert _answer_media("Alien") == {"text": "Alien"}

    def it_pulls_a_leading_unicode_emoji_into_the_icon():
        assert _answer_media("🎬 Alien") == {"text": "Alien", "emoji": {"name": "🎬"}}

    def it_reads_a_dingbat_range_emoji():
        assert _answer_media("✅ Yes") == {"text": "Yes", "emoji": {"name": "✅"}}

    def it_keeps_a_skin_tone_modifier_with_its_base():
        assert _answer_media(f"{_THUMB_TONED} Great") == {"text": "Great", "emoji": {"name": _THUMB_TONED}}

    def it_keeps_a_variation_selector_with_its_base():
        assert _answer_media(f"{_SUN_VS16} Sunny") == {"text": "Sunny", "emoji": {"name": _SUN_VS16}}

    def it_keeps_a_regional_indicator_flag_whole():
        assert _answer_media(f"{_FLAG_US} USA") == {"text": "USA", "emoji": {"name": _FLAG_US}}

    def it_keeps_a_zwj_family_sequence_whole():
        assert _answer_media(f"{_FAMILY} Family") == {"text": "Family", "emoji": {"name": _FAMILY}}

    def it_keeps_an_emoji_only_answer_as_text_with_no_emoji_field():
        assert _answer_media("🎬") == {"text": "🎬"}

    def it_preserves_an_emoji_only_answer_including_trailing_whitespace():
        assert _answer_media("🎬  ") == {"text": "🎬  "}

    def it_pulls_a_leading_custom_emoji_token_into_the_icon():
        assert _answer_media("<:thing:123> Yes") == {"text": "Yes", "emoji": {"id": "123"}}

    def it_reads_an_animated_custom_emoji_token():
        assert _answer_media("<a:party:42> Party") == {"text": "Party", "emoji": {"id": "42"}}

    def it_uses_the_token_name_when_the_answer_is_only_a_custom_token():
        assert _answer_media("<:alien:999>") == {"text": "alien", "emoji": {"id": "999"}}


def describe_emoji_prefix():
    def it_is_empty_for_an_empty_string():
        assert _emoji_prefix("") == ""

    def it_is_empty_when_the_answer_does_not_start_with_an_emoji():
        assert _emoji_prefix("Alien") == ""

    def it_stops_at_the_first_non_emoji_character():
        assert _emoji_prefix("🎬 Alien") == "🎬"

    def it_captures_a_zwj_sequence_whole():
        assert _emoji_prefix(f"{_FAMILY} Family") == _FAMILY


# --- Validation (each ephemeral, nothing posted) ------------------------------


def describe_validation():
    def it_rejects_fewer_than_two_answers(linked_member):
        result = _run(linked_member(), question="Which?", answers="Alien")
        assert result["data"]["content"] == _POLL_TOO_FEW
        _assert_ephemeral_no_poll(result)

    def it_rejects_more_than_ten_answers(linked_member):
        answers = "; ".join(f"Choice {n}" for n in range(11))
        result = _run(linked_member(), question="Which?", answers=answers)
        assert result["data"]["content"] == _POLL_TOO_MANY
        _assert_ephemeral_no_poll(result)

    def it_rejects_an_answer_longer_than_the_limit(linked_member):
        long_answer = "x" * (_POLL_ANSWER_MAX + 1)
        result = _run(linked_member(), question="Which?", answers=f"{long_answer}; Clue")
        assert f"{_POLL_ANSWER_MAX} characters" in result["data"]["content"]
        assert 'Shorten "' in result["data"]["content"]
        _assert_ephemeral_no_poll(result)

    def it_measures_the_answer_limit_after_stripping_the_emoji(linked_member):
        # 55 x's plus a leading emoji is 57 raw characters but exactly 55 after extraction,
        # so it is accepted — proving the limit applies to the post-extraction text.
        exactly_max = "x" * _POLL_ANSWER_MAX
        result = _run(linked_member(), question="Which?", answers=f"🎬 {exactly_max}; Clue")
        assert "poll" in result["data"]

    def it_rejects_a_question_longer_than_the_limit(linked_member):
        result = _run(linked_member(), question="q" * 301, answers="Alien; Clue")
        assert result["data"]["content"] == _POLL_QUESTION_TOO_LONG
        _assert_ephemeral_no_poll(result)

    def it_rejects_duplicate_answers(linked_member):
        result = _run(linked_member(), question="Which?", answers="Alien; Alien")
        assert result["data"]["content"] == _POLL_DUPLICATE
        _assert_ephemeral_no_poll(result)

    def it_detects_duplicates_after_emoji_extraction(linked_member):
        result = _run(linked_member(), question="Which?", answers="🎬 Alien; Alien")
        assert result["data"]["content"] == _POLL_DUPLICATE
        _assert_ephemeral_no_poll(result)


# --- Happy path ---------------------------------------------------------------


def describe_happy_path():
    def it_posts_a_public_poll_with_the_default_duration_and_multiselect_off(linked_member):
        member = linked_member(preferred_name="Nova")
        result = _run(member, question="Which movie?", answers="Alien; Clue; The Thing")

        assert result["type"] == 4
        assert result["data"]["flags"] == 0  # public — a poll must be votable in the channel
        poll = result["data"]["poll"]
        assert poll["question"] == {"text": "Which movie?"}
        assert poll["answers"] == [
            {"poll_media": {"text": "Alien"}},
            {"poll_media": {"text": "Clue"}},
            {"poll_media": {"text": "The Thing"}},
        ]
        assert poll["duration"] == 24
        assert poll["allow_multiselect"] is False

    def it_credits_the_asker_and_labels_the_duration_and_pick_mode(linked_member):
        member = linked_member(preferred_name="Nova")
        content = _run(member, question="Which movie?", answers="Alien; Clue")["data"]["content"]
        assert "Nova" in content
        assert "1 day" in content
        assert "pick one" in content

    def it_honors_an_explicit_duration_and_multiselect(linked_member):
        member = linked_member(preferred_name="Nova")
        result = _run(member, question="Which?", answers="Alien; Clue", duration=72, multiselect=True)
        poll = result["data"]["poll"]
        assert poll["duration"] == 72
        assert poll["allow_multiselect"] is True
        content = result["data"]["content"]
        assert "3 days" in content
        assert "pick any" in content

    def it_carries_answer_emoji_into_the_poll_media(linked_member):
        member = linked_member(preferred_name="Nova")
        result = _run(member, question="Which?", answers="🎬 Alien; <:fire:7> Clue")
        assert result["data"]["poll"]["answers"] == [
            {"poll_media": {"text": "Alien", "emoji": {"name": "🎬"}}},
            {"poll_media": {"text": "Clue", "emoji": {"id": "7"}}},
        ]


# --- reply(poll=…) kwarg ------------------------------------------------------


def describe_reply_poll_kwarg():
    def it_includes_the_poll_only_when_given():
        poll = {"question": {"text": "q"}}
        assert di.reply("x", ephemeral=False, poll=poll)["data"]["poll"] == poll

    def it_omits_the_poll_key_by_default():
        assert "poll" not in di.reply("x")["data"]


# --- Guide + dispatch integration ---------------------------------------------


def describe_guide_listing():
    def it_lists_poll_among_the_commands():
        assert "poll" in [cmd.name for cmd in all_commands()]

    def it_describes_poll_in_the_guide_embed():
        embed = _guide({}, None)["data"]["embeds"][0]
        assert "/poll" in embed["description"]


def describe_dispatch_integration():
    def it_shows_the_connect_prompt_for_an_unlinked_member(rf):
        interaction = {
            "type": 2,
            "data": {"name": "poll", "options": []},
            "member": {"user": {"id": "000"}},
        }
        result = dispatch(interaction, rf.post("/"))
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].endswith("/discord/link/")
