"""Specs for the ``/poll`` slash command (membership.discord_commands).

Covers the command definition, the answers-splitting helper, leading-emoji extraction
(unicode and custom Discord tokens, with the empty-remainder rules), every ephemeral
validation reply, the public happy-path poll payload, the ``reply(poll=…)`` kwarg, and
the dispatch + ``/guide`` integration. No Discord REST is touched: ``/poll`` posts its
poll straight from the interaction response (``defer=False``), so there is nothing to mock.
"""

from __future__ import annotations

import pytest

from django.core.cache import cache

from core.abuse_limits import record_keyed_attempt
from core.events import discord_interactions as di
from core.events.discord_commands import _guide, all_commands, dispatch
from membership.discord_commands import (
    _POLL_ANSWER_MAX,
    _POLL_DAILY_LIMIT,
    _POLL_HOURLY_LIMIT,
    _POLL_RATE_SCOPE,
    POLL,
    _answer_media,
    _emoji_prefix,
    _poll,
    _poll_edit_component,
    _poll_submit,
    _split_answers,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _label(result: dict, custom_id: str) -> dict:
    """The wrapped input of the Label row whose child has ``custom_id`` (a modal-9 response)."""
    return next(
        row["component"]
        for row in result["data"]["components"]
        if row.get("type") == 18 and row["component"].get("custom_id") == custom_id
    )


# Multi-codepoint emoji written with explicit escapes so the ZWJ / skin-tone / regional /
# variation-selector / tag joiners are unambiguous in source.
_FAMILY = "\U0001f468‍\U0001f469‍\U0001f467"  # man + ZWJ + woman + ZWJ + girl
_THUMB_TONED = "\U0001f44d\U0001f3fd"  # thumbs-up + medium skin tone
_STAR_VS16 = "⭐️"  # star (a curated emoji base) + variation selector 16
_FLAG_US = "\U0001f1fa\U0001f1f8"  # regional-indicator U + S
_FLAG_GB = "\U0001f1ec\U0001f1e7"  # regional-indicator G + B
_SCOTLAND = "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"  # tag-sequence flag
_PENCIL = "✎"  # lower-right pencil — a non-emoji dingbat
_MAHJONG = "\U0001f004"  # mahjong red dragon — excluded by the narrowed base ranges
_DANGLING_ZWJ = "\U0001f3ac‍"  # clapper + a trailing (dangling) ZWJ


def _submit_payload(
    *,
    question: str = "Which movie?",
    answers: str = "Alien\nClue\nThe Thing",
    hours: str = "24",
    multiselect: bool = False,
) -> dict:
    """A Create-a-Poll MODAL_SUBMIT payload (Components-v2 Label rows echo back)."""
    return {
        "data": {
            "custom_id": "pollform",
            "components": [
                {"type": 18, "component": {"custom_id": "question", "value": question}},
                {"type": 18, "component": {"custom_id": "answers", "value": answers}},
                {"type": 18, "component": {"custom_id": "duration", "values": [hours]}},
                {"type": 18, "component": {"custom_id": "multiselect", "values": (["on"] if multiselect else [])}},
            ],
        }
    }


def _run(member: object, **kwargs: object) -> dict:
    return _poll_submit(_submit_payload(**kwargs), member)


def _assert_error_reply(result: dict) -> None:
    assert result["data"]["flags"] == 64  # ephemeral — only the invoker sees the rejection
    assert "poll" not in result["data"]  # nothing was posted to the channel
    assert result["data"]["components"][0]["components"][0]["label"] == "Edit Poll"


# --- Command definition -------------------------------------------------------


def describe_command_definition():
    def it_is_link_gated_and_carries_no_options():
        assert POLL.name == "poll"
        assert (POLL.requires_link, POLL.defer) == (True, False)
        assert POLL.scope == "guild"
        assert POLL.to_api_dict()["options"] == []  # the modal replaces the slash options


def describe_the_modal():
    def it_opens_a_create_a_poll_modal(linked_member):
        result = _poll({"data": {}}, linked_member())
        assert result["type"] == 9  # MODAL
        assert result["data"]["custom_id"] == "pollform"
        assert result["data"]["title"] == "Create a Poll"
        labels = [row["label"] for row in result["data"]["components"] if row["type"] == 18]
        assert labels == ["Question", "Answers", "Voting Stays Open For", "Multiple Choice"]
        # A closing Text Display carries the "cannot be edited" note.
        assert any(row["type"] == 10 for row in result["data"]["components"])

    def it_caps_the_question_input_at_the_native_limit(linked_member):
        assert _label(_poll({"data": {}}, linked_member()), "question")["max_length"] == 300

    def it_preselects_the_default_one_day_duration(linked_member):
        options = _label(_poll({"data": {}}, linked_member()), "duration")["options"]
        default = next(o for o in options if o["value"] == "24")
        assert default["default"] is True
        assert all(o["default"] is False for o in options if o["value"] != "24")


# --- Answers splitting --------------------------------------------------------


def describe_split_answers():
    def it_splits_on_newlines():
        assert _split_answers("Alien\nClue\nThe Thing") == ["Alien", "Clue", "The Thing"]

    def it_trims_each_line():
        assert _split_answers("  Alien \n  Clue  ") == ["Alien", "Clue"]

    def it_drops_blank_lines():
        assert _split_answers("Alien\n\nClue\n") == ["Alien", "Clue"]


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
        assert _answer_media(f"{_STAR_VS16} Star") == {"text": "Star", "emoji": {"name": _STAR_VS16}}

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


def describe_answer_media_conservative_fallbacks():
    def it_leaves_a_non_emoji_dingbat_answer_untouched():
        assert _answer_media(f"{_PENCIL} Draw") == {"text": f"{_PENCIL} Draw"}

    def it_leaves_a_mahjong_tile_answer_untouched():
        assert _answer_media(f"{_MAHJONG} Tile") == {"text": f"{_MAHJONG} Tile"}

    def it_does_not_set_an_emoji_for_two_adjacent_flags():
        answer = f"{_FLAG_US}{_FLAG_GB} Both"
        assert _answer_media(answer) == {"text": answer}

    def it_keeps_a_tag_sequence_flag_answer_as_full_text():
        answer = f"{_SCOTLAND} Scotland"
        assert _answer_media(answer) == {"text": answer}

    def it_keeps_a_tag_flag_only_answer_visible_as_full_text():
        assert _answer_media(_SCOTLAND) == {"text": _SCOTLAND}

    def it_never_leaks_a_dangling_zwj_into_the_icon_or_text():
        assert _answer_media(f"{_DANGLING_ZWJ} Later") == {"text": "Later", "emoji": {"name": "🎬"}}


def describe_emoji_prefix():
    def it_is_empty_for_an_empty_string():
        assert _emoji_prefix("") == ""

    def it_is_empty_when_the_answer_does_not_start_with_an_emoji():
        assert _emoji_prefix("Alien") == ""

    def it_stops_at_the_first_non_emoji_character():
        assert _emoji_prefix("🎬 Alien") == "🎬"

    def it_captures_a_zwj_sequence_whole():
        assert _emoji_prefix(f"{_FAMILY} Family") == _FAMILY

    def it_captures_exactly_one_regional_indicator_pair():
        assert _emoji_prefix(f"{_FLAG_US} USA") == _FLAG_US

    def it_rejects_a_lone_regional_indicator():
        assert _emoji_prefix("\U0001f1fa X") == ""

    def it_rejects_three_or_more_adjacent_regional_indicators():
        assert _emoji_prefix(f"{_FLAG_US}{_FLAG_GB}") == ""

    def it_drops_a_trailing_dangling_zwj():
        assert _emoji_prefix(_DANGLING_ZWJ) == "🎬"


# --- Validation (each ephemeral, nothing posted) ------------------------------


def describe_validation():
    def it_rejects_fewer_than_two_answers(linked_member):
        result = _run(linked_member(), answers="Alien")
        assert (
            result["data"]["content"] == "A poll needs at least 2 answers. You gave 1. Put each answer on its own line."
        )
        _assert_error_reply(result)

    def it_rejects_more_than_ten_answers(linked_member):
        answers = "\n".join(f"Choice {n}" for n in range(11))
        result = _run(linked_member(), answers=answers)
        assert (
            result["data"]["content"] == "Discord caps polls at 10 answers and you gave 11. Trim the list and resubmit."
        )
        _assert_error_reply(result)

    def it_rejects_an_answer_longer_than_the_limit_naming_the_line(linked_member):
        long_answer = "x" * (_POLL_ANSWER_MAX + 1)
        result = _run(linked_member(), answers=f"Clue\n{long_answer}")
        content = result["data"]["content"]
        assert content.startswith("Answer 2 is too long.")
        assert f"{_POLL_ANSWER_MAX} characters" in content
        assert 'Shorten "' in content
        _assert_error_reply(result)

    def it_measures_the_answer_limit_after_stripping_the_emoji(linked_member):
        # 55 x's plus a leading emoji is 57 raw characters but exactly 55 after extraction,
        # so it is accepted — proving the limit applies to the post-extraction text.
        exactly_max = "x" * _POLL_ANSWER_MAX
        result = _run(linked_member(), answers=f"🎬 {exactly_max}\nClue")
        assert "poll" in result["data"]

    def it_rejects_duplicate_answers_quoting_the_identical_text(linked_member):
        result = _run(linked_member(), answers="Alien\nAlien")
        assert result["data"]["content"] == (
            'Two answers come out identical: "Alien". Every answer has to be unique. Edit the list and resubmit.'
        )
        _assert_error_reply(result)

    def it_detects_duplicates_after_emoji_extraction(linked_member):
        result = _run(linked_member(), answers="🎬 Alien\nAlien")
        assert 'identical: "Alien"' in result["data"]["content"]
        _assert_error_reply(result)


# --- Happy path ---------------------------------------------------------------


def describe_happy_path():
    def it_posts_a_public_poll_with_the_default_duration_and_multiselect_off(linked_member):
        member = linked_member(preferred_name="Nova")
        result = _run(member, question="Which movie?", answers="Alien\nClue\nThe Thing")

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
        assert result["data"]["allowed_mentions"] == {"parse": []}

    def it_suppresses_mentions_so_a_display_name_cannot_ping(linked_member):
        member = linked_member(preferred_name="@everyone")
        result = _run(member, answers="Alien\nClue")
        assert "@everyone" in result["data"]["content"]  # the raw text is credited...
        assert result["data"]["allowed_mentions"] == {"parse": []}  # ...but it can never ping

    def it_credits_the_asker_and_labels_the_duration_and_pick_mode(linked_member):
        member = linked_member(preferred_name="Nova")
        content = _run(member, answers="Alien\nClue")["data"]["content"]
        assert "Nova" in content
        assert "24 Hours" in content
        assert "pick one" in content

    def it_honors_an_explicit_duration_and_multiselect(linked_member):
        member = linked_member(preferred_name="Nova")
        result = _run(member, answers="Alien\nClue", hours="72", multiselect=True)
        poll = result["data"]["poll"]
        assert poll["duration"] == 72
        assert poll["allow_multiselect"] is True
        content = result["data"]["content"]
        assert "3 Days" in content
        assert "pick any" in content

    def it_carries_answer_emoji_into_the_poll_media(linked_member):
        member = linked_member(preferred_name="Nova")
        result = _run(member, answers="🎬 Alien\n<:fire:7> Clue")
        assert result["data"]["poll"]["answers"] == [
            {"poll_media": {"text": "Alien", "emoji": {"name": "🎬"}}},
            {"poll_media": {"text": "Clue", "emoji": {"id": "7"}}},
        ]

    def it_falls_back_to_the_default_duration_when_none_is_submitted(linked_member):
        # A payload with no duration select (defensive) defaults to one day.
        payload = {
            "data": {
                "custom_id": "pollform",
                "components": [
                    {"type": 18, "component": {"custom_id": "question", "value": "Q?"}},
                    {"type": 18, "component": {"custom_id": "answers", "value": "Alien\nClue"}},
                ],
            }
        }
        assert _poll_submit(payload, linked_member())["data"]["poll"]["duration"] == 24


# --- Rate limiting ------------------------------------------------------------


def _exhaust_poll_limit(member) -> None:
    for _ in range(_POLL_HOURLY_LIMIT):
        record_keyed_attempt(
            _POLL_RATE_SCOPE, str(member.pk), hourly_limit=_POLL_HOURLY_LIMIT, daily_limit=_POLL_DAILY_LIMIT
        )


def describe_rate_limiting():
    def it_blocks_opening_the_modal_at_the_cap(linked_member):
        member = linked_member()
        _exhaust_poll_limit(member)
        result = _poll({"data": {}}, member)
        assert result["type"] == 4  # the friendly refusal, not the modal
        assert "hit the limit for polls" in result["data"]["content"]

    def it_records_nothing_on_a_validation_failure(linked_member):
        member = linked_member()
        _run(member, answers="Alien")  # too few → posts nothing
        assert cache.get(f"abuse:{_POLL_RATE_SCOPE}:{member.pk}:hourly", 0) == 0

    def it_records_one_on_a_successful_post(linked_member):
        member = linked_member()
        _run(member)  # valid poll posts
        assert cache.get(f"abuse:{_POLL_RATE_SCOPE}:{member.pk}:hourly", 0) == 1


# --- Edit Poll reopen ---------------------------------------------------------


def describe_edit_reopen():
    def it_reopens_the_modal_prefilled_from_the_failed_submission(linked_member):
        member = linked_member()
        error = _run(member, question="Movie night?", answers="Alien")  # too few → cached
        token_id = error["data"]["components"][0]["components"][0]["custom_id"]
        reopened = _poll_edit_component({"data": {"custom_id": token_id}}, member)
        assert reopened["type"] == 9
        assert _label(reopened, "question")["value"] == "Movie night?"
        assert _label(reopened, "answers")["value"] == "Alien"

    def it_reopens_a_blank_modal_when_the_token_expired(linked_member):
        reopened = _poll_edit_component({"data": {"custom_id": "polledit:gone"}}, linked_member())
        assert reopened["type"] == 9
        assert "value" not in _label(reopened, "question")


# --- reply(poll=…) kwarg ------------------------------------------------------


def describe_reply_poll_kwarg():
    def it_includes_the_poll_only_when_given():
        poll = {"question": {"text": "q"}}
        assert di.reply("x", ephemeral=False, poll=poll)["data"]["poll"] == poll

    def it_omits_the_poll_key_by_default():
        assert "poll" not in di.reply("x")["data"]

    def it_gates_mentions_on_the_poll_path():
        data = di.reply("hi @everyone", ephemeral=False, poll={"question": {"text": "q"}})["data"]
        assert data["allowed_mentions"] == {"parse": []}

    def it_leaves_plain_replies_mention_behavior_untouched():
        assert "allowed_mentions" not in di.reply("hi")["data"]


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
