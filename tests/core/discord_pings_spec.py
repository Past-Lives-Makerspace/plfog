"""queue_discord_pings (#943): Discord @everyone and guild pings wait for review in Spotlight.

Discord is mocked with respx: the server's channels and roles, and each channel's messages
after its cursor. Nothing here reaches the network.
"""

from __future__ import annotations

import logging
from io import StringIO
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from django.core.management import call_command

from core.discord_pings import queue_discord_pings
from core.events.discord_dm import API_BASE
from core.html_sanitize import ANNOUNCEMENT_MAX_CHARS, rich_html_to_text
from core.integrations.discord_channel import MESSAGES_PAGE_LIMIT, channel_messages_after
from core.models import Announcement, DiscordChannelCursor, QueuedDiscordMessage, SiteConfiguration
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

SERVER = "1000"
CERAMICS_ROLE = "2001"
JEWELRY_ROLE = "2002"
OTHER_ROLE = "2003"
GENERAL = "10"
HIDDEN = "11"
CERAMICS_CHANNEL = "12"
MEMBER_ROLE = "3000"
VIEW = 1 << 10
# Members see a channel through the Member role; a hidden one denies that role View Channel.
VIEW_DENIED = [{"id": MEMBER_ROLE, "type": 0, "allow": "0", "deny": str(VIEW)}]


def _channel(channel_id: str, last: str | None, *, hidden: bool = False, kind: int = 0) -> dict[str, Any]:
    return {
        "id": channel_id,
        "type": kind,
        "name": f"chan-{channel_id}",
        "last_message_id": last,
        "permission_overwrites": VIEW_DENIED if hidden else [],
    }


def _message(message_id: str, content: str, **overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": message_id,
        "type": 0,
        "content": content,
        "author": {"id": "555", "username": "dee_user", "global_name": "Discord Dee"},
        "mention_everyone": "@everyone" in content or "@here" in content,
        "mention_roles": [],
        "mentions": [],
        "timestamp": "2026-10-26T18:00:00.000000+00:00",
    }
    message.update(overrides)
    return message


ROLES = [
    {"id": SERVER, "name": "@everyone", "permissions": "0"},
    {"id": MEMBER_ROLE, "name": "Member", "permissions": str(VIEW)},
    {"id": CERAMICS_ROLE, "name": "Ceramics", "permissions": "0"},
    {"id": JEWELRY_ROLE, "name": "Jewelry", "permissions": "0"},
    {"id": OTHER_ROLE, "name": "Kiln Crew", "permissions": "0"},
]


@pytest.fixture(autouse=True)
def discord(settings: Any) -> None:
    settings.DISCORD_BOT_TOKEN = "tok"
    config = SiteConfiguration.load()
    config.discord_server_id = SERVER
    config.discord_member_role_id = MEMBER_ROLE
    config.save()


@pytest.fixture
def ceramics() -> Any:
    return GuildFactory(name="Ceramics Guild", discord_role_ids=[CERAMICS_ROLE], discord_channel_id=CERAMICS_CHANNEL)


def _mock_server(
    channels: list[dict[str, Any]], pages: dict[str, list[list[dict[str, Any]]]], roles: list[dict[str, Any]] = ROLES
) -> dict[str, list[str]]:
    """Mock the server; each channel answers its pages in turn. Returns the ``after`` each read asked for."""
    asked: dict[str, list[str]] = {}
    respx.get(f"{API_BASE}/guilds/{SERVER}/channels").mock(return_value=httpx.Response(200, json=channels))
    respx.get(f"{API_BASE}/guilds/{SERVER}/roles").mock(return_value=httpx.Response(200, json=roles))
    for channel_id, answers in pages.items():
        remaining = list(answers)

        def answer(request: httpx.Request, channel_id: str = channel_id, remaining: list = remaining) -> httpx.Response:
            asked.setdefault(channel_id, []).append(request.url.params["after"])
            return httpx.Response(200, json=list(reversed(remaining.pop(0))))

        respx.get(f"{API_BASE}/channels/{channel_id}/messages").mock(side_effect=answer)
    return asked


def _read_from(channel_id: str, last: str) -> None:
    DiscordChannelCursor.objects.create(channel_id=channel_id, last_message_id=last)


def describe_queue_discord_pings():
    def describe_when_it_cannot_run():
        @respx.mock
        def it_does_nothing_without_a_server_id():
            config = SiteConfiguration.load()
            config.discord_server_id = " "
            config.save()

            assert queue_discord_pings() == 0
            assert not respx.calls

        @respx.mock
        def it_does_nothing_with_a_blank_bot_token(settings: Any):
            settings.DISCORD_BOT_TOKEN = ""

            assert queue_discord_pings() == 0
            assert not respx.calls

    def describe_first_run():
        @respx.mock
        def it_records_each_text_channels_newest_message_and_queues_nothing():
            channels = [_channel(GENERAL, "50"), _channel("13", None, kind=5), _channel("14", "9", kind=2)]
            _mock_server(channels, {})

            assert queue_discord_pings() == 0

            cursors = dict(DiscordChannelCursor.objects.values_list("channel_id", "last_message_id"))
            assert cursors == {GENERAL: "50", "13": "0"}
            assert QueuedDiscordMessage.objects.count() == 0

        @respx.mock
        def it_reads_nothing_in_a_channel_with_no_new_message():
            _read_from(GENERAL, "50")
            asked = _mock_server([_channel(GENERAL, "50")], {GENERAL: []})

            assert queue_discord_pings() == 0
            assert asked == {}

    def describe_an_everyone_ping():
        @respx.mock
        def it_queues_it_drafted_credited_and_never_live():
            _read_from(GENERAL, "50")
            _mock_server([_channel(GENERAL, "51")], {GENERAL: [[_message("51", "@everyone Kiln is  full tonight")]]})

            assert queue_discord_pings() == 1

            row = QueuedDiscordMessage.objects.get()
            assert row.state == QueuedDiscordMessage.State.WAITING
            assert row.original_text == "Kiln is full tonight"
            assert rich_html_to_text(row.line) == "Kiln is full tonight (Discord Dee)"
            assert row.discord_url == f"https://discord.com/channels/{SERVER}/{GENERAL}/51"
            assert row.sender is None and row.sender_name == "Discord Dee" and row.sender_discord_id == "555"
            assert list(row.guilds.all()) == []
            assert DiscordChannelCursor.objects.get(channel_id=GENERAL).last_message_id == "51"
            assert Announcement.objects.count() == 0

        @respx.mock
        def it_credits_the_linked_member_by_name():
            MemberFactory(discord_user_id="555", preferred_name="Jay Holtman")
            _read_from(GENERAL, "50")
            _mock_server([_channel(GENERAL, "51")], {GENERAL: [[_message("51", "@everyone Clean up")]]})

            queue_discord_pings()

            row = QueuedDiscordMessage.objects.get()
            assert rich_html_to_text(row.line) == "Clean up (Jay Holtman)"
            assert row.credit == "Jay Holtman"

        @respx.mock
        def it_falls_back_to_the_username_without_a_display_name():
            _read_from(GENERAL, "50")
            author = {"id": "556", "username": "plain_user", "global_name": None}
            _mock_server([_channel(GENERAL, "51")], {GENERAL: [[_message("51", "@everyone Hi", author=author)]]})

            queue_discord_pings()

            assert QueuedDiscordMessage.objects.get().credit == "plain_user"

        @respx.mock
        def it_skips_a_here_ping_and_a_ping_with_no_words():
            _read_from(GENERAL, "50")
            pages = {GENERAL: [[_message("51", "@here Pizza"), _message("52", "@everyone")]]}
            _mock_server([_channel(GENERAL, "52")], pages)

            assert queue_discord_pings() == 0
            assert DiscordChannelCursor.objects.get(channel_id=GENERAL).last_message_id == "52"

    def describe_a_guild_ping():
        @respx.mock
        def it_leads_with_the_guild_and_reads_every_token_as_a_name(ceramics: Any):
            GuildFactory(name="Jewelry Guild", discord_role_ids=[JEWELRY_ROLE])
            _read_from(GENERAL, "50")
            content = (
                f"<@&{CERAMICS_ROLE}> <@&{JEWELRY_ROLE}> Ask <@777> or <@!778> in <#{GENERAL}> "
                f"<:kiln:42> <a:spin:43>\nwith <@&{OTHER_ROLE}> <@&9999> <@123> <#8888>"
            )
            mentions = [{"id": "777", "username": "amy", "global_name": "Amy"}, {"id": "778", "username": "bo"}]
            message = _message(
                "51", content, mention_roles=[CERAMICS_ROLE, JEWELRY_ROLE, OTHER_ROLE], mentions=mentions
            )
            _mock_server([_channel(GENERAL, "51")], {GENERAL: [[message]]})

            queue_discord_pings()

            row = QueuedDiscordMessage.objects.get()
            expected = f"Ask @Amy or @bo in #chan-{GENERAL} :kiln: :spin:\nwith @Kiln Crew @role @someone #channel"
            assert row.original_text == expected
            assert rich_html_to_text(row.line).startswith("Ceramics, Jewelry: Ask @Amy")
            assert sorted(row.guilds.values_list("name", flat=True)) == ["Ceramics Guild", "Jewelry Guild"]

        @respx.mock
        def it_skips_a_role_that_is_no_guilds(ceramics: Any):
            _read_from(GENERAL, "50")
            message = _message("51", f"<@&{OTHER_ROLE}> hi", mention_roles=[OTHER_ROLE])
            _mock_server([_channel(GENERAL, "51")], {GENERAL: [[message]]})

            assert queue_discord_pings() == 0

    def describe_who_sent_it():
        @respx.mock
        def it_never_queues_a_bot_a_webhook_or_a_system_message():
            _read_from(GENERAL, "50")
            bot = {"id": "9", "username": "fog", "bot": True}
            pages = {
                GENERAL: [
                    [
                        _message("51", "@everyone From the bot", author=bot),
                        _message("52", "@everyone From a hook", webhook_id="77"),
                        _message("53", "@everyone pinned", type=6),
                    ]
                ]
            }
            _mock_server([_channel(GENERAL, "53")], pages)

            assert queue_discord_pings() == 0

    def describe_hidden_channels():
        @respx.mock
        def it_does_not_read_a_hidden_channel_that_is_no_guilds(ceramics: Any):
            _read_from(HIDDEN, "50")
            asked = _mock_server([_channel(HIDDEN, "60", hidden=True)], {HIDDEN: []})

            assert queue_discord_pings() == 0
            assert asked == {}
            assert DiscordChannelCursor.objects.get(channel_id=HIDDEN).last_message_id == "60"

        @respx.mock
        def it_queues_only_that_guilds_ping_in_its_own_hidden_channel(ceramics: Any):
            _read_from(CERAMICS_CHANNEL, "50")
            pages = {
                CERAMICS_CHANNEL: [
                    [
                        _message("51", "@everyone not for the portal"),
                        _message("52", f"<@&{CERAMICS_ROLE}> Glaze day", mention_roles=[CERAMICS_ROLE]),
                    ]
                ]
            }
            _mock_server([_channel(CERAMICS_CHANNEL, "52", hidden=True)], pages)

            assert queue_discord_pings() == 1
            assert QueuedDiscordMessage.objects.get().original_text == "Glaze day"

    def describe_who_can_see_a_channel():
        def _roles(everyone: int, member: int) -> list[dict[str, Any]]:
            return [
                {"id": SERVER, "name": "@everyone", "permissions": str(everyone)},
                {"id": MEMBER_ROLE, "name": "Member", "permissions": str(member)},
            ]

        def _overwrite(role_id: str, *, allow: int = 0, deny: int = 0) -> dict[str, Any]:
            return {"id": role_id, "type": 0, "allow": str(allow), "deny": str(deny)}

        def _queued_with(overwrites: list[dict[str, Any]], roles: list[dict[str, Any]]) -> int:
            _read_from(GENERAL, "50")
            channel = {**_channel(GENERAL, "51"), "permission_overwrites": overwrites}
            _mock_server([channel], {GENERAL: [[_message("51", "@everyone Hi")]]}, roles)
            return queue_discord_pings()

        @respx.mock
        def it_reads_a_channel_everyone_is_denied_but_members_are_granted():
            assert _queued_with([_overwrite(SERVER, deny=VIEW), _overwrite(MEMBER_ROLE, allow=VIEW)], _roles(0, 0)) == 1

        @respx.mock
        def it_lets_an_everyone_deny_on_the_channel_override_the_member_roles_base_view():
            # Discord applies the channel's @everyone overwrite after base permissions.
            assert _queued_with([_overwrite(SERVER, deny=VIEW)], _roles(0, VIEW)) == 0

        @respx.mock
        def it_reads_a_channel_with_no_overwrites_through_the_member_roles_base_view():
            assert _queued_with([], _roles(0, VIEW)) == 1

        @respx.mock
        def it_skips_a_channel_that_denies_the_member_role():
            assert _queued_with([_overwrite(MEMBER_ROLE, deny=VIEW)], _roles(VIEW, VIEW)) == 0

        @respx.mock
        def it_reads_a_channel_whose_everyone_overwrite_allows_view():
            assert _queued_with([_overwrite(SERVER, allow=VIEW)], _roles(0, 0)) == 1

        @respx.mock
        def it_skips_a_channel_no_one_is_granted():
            assert _queued_with([], _roles(0, 0)) == 0

        @respx.mock
        def it_reads_everything_when_members_are_administrators():
            assert _queued_with([_overwrite(MEMBER_ROLE, deny=VIEW)], _roles(0, 1 << 3)) == 1

        @respx.mock
        def it_reads_nothing_without_a_member_role_id(caplog: Any):
            config = SiteConfiguration.load()
            config.discord_member_role_id = " "
            config.save()

            with caplog.at_level(logging.WARNING, logger="core.discord_pings"):
                assert queue_discord_pings() == 0

            assert not respx.calls
            assert "no Discord member role id" in caplog.text

    def describe_a_channel_that_fails():
        @respx.mock
        def it_logs_it_and_reads_the_channels_after_it(caplog: Any):
            _read_from(HIDDEN, "40")
            _read_from(GENERAL, "50")
            respx.get(f"{API_BASE}/guilds/{SERVER}/channels").mock(
                return_value=httpx.Response(200, json=[_channel(HIDDEN, "41"), _channel(GENERAL, "51")])
            )
            respx.get(f"{API_BASE}/guilds/{SERVER}/roles").mock(return_value=httpx.Response(200, json=ROLES))
            respx.get(f"{API_BASE}/channels/{HIDDEN}/messages").mock(
                return_value=httpx.Response(403, json={"message": "Missing Access"})
            )
            respx.get(f"{API_BASE}/channels/{GENERAL}/messages").mock(
                return_value=httpx.Response(200, json=[_message("51", "@everyone Hi")])
            )

            with caplog.at_level(logging.WARNING, logger="core.discord_pings"):
                assert queue_discord_pings() == 1

            assert f"channel {HIDDEN} skipped this tick" in caplog.text
            assert DiscordChannelCursor.objects.get(channel_id=HIDDEN).last_message_id == "40"

    def describe_overlapping_runs():
        @respx.mock
        def it_keeps_a_cursor_another_run_recorded_first():
            _mock_server([_channel(GENERAL, "51")], {GENERAL: [[_message("51", "@everyone Hi")]]})
            real = DiscordChannelCursor.objects.all

            def recorded_meanwhile() -> Any:
                rows = real()
                list(rows)  # this run saw no cursor...
                DiscordChannelCursor.objects.create(channel_id=GENERAL, last_message_id="50")  # ...then one landed
                return rows

            with patch.object(DiscordChannelCursor.objects, "all", side_effect=recorded_meanwhile):
                assert queue_discord_pings() == 1

            assert DiscordChannelCursor.objects.get(channel_id=GENERAL).last_message_id == "51"

    def describe_empty_text():
        @respx.mock
        def it_logs_once_and_queues_nothing(ceramics: Any, caplog: Any):
            _read_from(GENERAL, "50")
            pages = {
                GENERAL: [
                    [
                        _message("51", "", mention_everyone=True),
                        _message("52", "", mention_roles=[CERAMICS_ROLE]),
                    ]
                ]
            }
            _mock_server([_channel(GENERAL, "52")], pages)

            with caplog.at_level(logging.WARNING, logger="core.discord_pings"):
                assert queue_discord_pings() == 0

            warnings = [record.getMessage() for record in caplog.records]
            assert len(warnings) == 1
            assert "2 ping(s) came back with no text" in warnings[0]

    def describe_never_twice():
        @respx.mock
        def it_leaves_an_already_queued_message_alone():
            QueuedDiscordMessage.objects.create(
                discord_message_id="51",
                discord_channel_id=GENERAL,
                discord_url="https://discord.com/channels/1/10/51",
                original_text="first",
                line="<p>first</p>",
                sender_discord_id="555",
                sender_name="Dee",
                sent_at="2026-10-26T18:00:00Z",
                state=QueuedDiscordMessage.State.DISCARDED,
            )
            _read_from(GENERAL, "50")
            _mock_server([_channel(GENERAL, "51")], {GENERAL: [[_message("51", "@everyone again")]]})

            assert queue_discord_pings() == 0
            assert QueuedDiscordMessage.objects.get().original_text == "first"

    def describe_paging():
        @respx.mock
        def it_reads_on_after_a_full_page():
            _read_from(GENERAL, "1000")
            full = [_message(str(1001 + n), "chat") for n in range(MESSAGES_PAGE_LIMIT)]
            rest = [_message("1200", "@everyone last")]
            asked = _mock_server([_channel(GENERAL, "1200")], {GENERAL: [full, rest]})

            assert queue_discord_pings() == 1
            assert asked[GENERAL] == ["1000", "1100"]

        @respx.mock
        def it_stops_when_a_page_comes_back_empty():
            _read_from(GENERAL, "50")
            asked = _mock_server([_channel(GENERAL, "60")], {GENERAL: [[]]})

            assert queue_discord_pings() == 0
            assert asked[GENERAL] == ["50"]
            assert DiscordChannelCursor.objects.get(channel_id=GENERAL).last_message_id == "50"

        @respx.mock
        def it_reads_at_most_five_pages_a_tick():
            _read_from(GENERAL, "0")
            pages = [
                [_message(str(page * 1000 + n + 1), "chat") for n in range(MESSAGES_PAGE_LIMIT)] for page in range(6)
            ]
            asked = _mock_server([_channel(GENERAL, "99999")], {GENERAL: pages})

            queue_discord_pings()

            assert len(asked[GENERAL]) == 5

    def describe_rate_limits():
        @respx.mock
        def it_stops_the_tick_and_keeps_the_cursors_already_moved(caplog: Any):
            _read_from(GENERAL, "50")
            _read_from("13", "70")
            respx.get(f"{API_BASE}/guilds/{SERVER}/channels").mock(
                return_value=httpx.Response(200, json=[_channel(GENERAL, "51"), _channel("13", "71")])
            )
            respx.get(f"{API_BASE}/guilds/{SERVER}/roles").mock(return_value=httpx.Response(200, json=ROLES))
            respx.get(f"{API_BASE}/channels/{GENERAL}/messages").mock(
                return_value=httpx.Response(200, json=[_message("51", "@everyone first")])
            )
            respx.get(f"{API_BASE}/channels/13/messages").mock(return_value=httpx.Response(429, json={}))

            with caplog.at_level(logging.WARNING, logger="core.discord_pings"):
                assert queue_discord_pings() == 1

            assert "rate limited" in caplog.text
            assert DiscordChannelCursor.objects.get(channel_id=GENERAL).last_message_id == "51"
            assert DiscordChannelCursor.objects.get(channel_id="13").last_message_id == "70"
            assert QueuedDiscordMessage.objects.count() == 1


def describe_channel_messages_after():
    @respx.mock
    def it_asks_for_a_full_page_after_the_cursor_and_returns_oldest_first(settings: Any):
        route = respx.get(f"{API_BASE}/channels/5/messages").mock(
            return_value=httpx.Response(200, json=[{"id": "300"}, {"id": "21"}, {"id": "100"}])
        )

        assert [message["id"] for message in channel_messages_after("5", "20")] == ["21", "100", "300"]
        assert route.calls.last.request.url.params["after"] == "20"
        assert route.calls.last.request.url.params["limit"] == str(MESSAGES_PAGE_LIMIT)


def describe_timestamps():
    @respx.mock
    def it_reads_every_timestamp_style_as_a_pacific_date_and_time():
        _read_from(GENERAL, "50")
        # 1793412000 is Fri Oct 30 2026, 7 PM Pacific; 1793413800 is 7:30 PM.
        content = "@everyone Potluck <t:1793412000> or <t:1793412000:R>, doors <t:1793413800:t>"
        _mock_server([_channel(GENERAL, "51")], {GENERAL: [[_message("51", content)]]})

        queue_discord_pings()

        assert QueuedDiscordMessage.objects.get().original_text == (
            "Potluck Fri Oct 30, 7 PM or Fri Oct 30, 7 PM, doors Fri Oct 30, 7:30 PM"
        )


def describe_draft_line():
    url = "https://discord.com/channels/1/2/3"

    def it_shows_a_two_line_message_that_fits_whole_on_one_line():
        text = "Please make sure to wash your space before you leave the studio tonight.\nThe kiln is loaded."
        line = QueuedDiscordMessage.draft_line(text, ["Ceramics"], "Jay Holtman", url)

        assert "[...]" not in line
        assert rich_html_to_text(line) == (
            "Ceramics: Please make sure to wash your space before you leave the studio tonight. "
            "The kiln is loaded. (Jay Holtman)"
        )

    def it_shows_a_short_message_whole_with_no_more_link():
        line = QueuedDiscordMessage.draft_line("Wash your  space", [], "Jay", url)

        assert line == "<p>Wash your space (Jay)</p>"

    def it_leads_with_every_pinged_guild():
        assert rich_html_to_text(QueuedDiscordMessage.draft_line("Hi", ["Ceramics", "Jewelry"], "Jay", url)) == (
            "Ceramics, Jewelry: Hi (Jay)"
        )

    def it_cuts_a_long_message_at_a_word_with_a_link_to_discord():
        text = " ".join(["glaze"] * 60)
        line = QueuedDiscordMessage.draft_line(text, ["Ceramics"], "Jay Holtman", url)
        visible = rich_html_to_text(line)

        assert len(visible) <= ANNOUNCEMENT_MAX_CHARS
        assert visible.startswith("Ceramics: glaze glaze")
        assert visible.endswith("glaze [...] (Jay Holtman)")
        assert f'<a href="{url}" rel="noopener noreferrer" target="_blank">[...]</a>' in line

    def it_shows_a_message_that_fills_the_line_exactly_whole():
        text = "x" * (ANNOUNCEMENT_MAX_CHARS - len(" (Jay)"))

        assert "[...]" not in QueuedDiscordMessage.draft_line(text, [], "Jay", url)

    def it_cuts_one_long_word_where_it_must():
        visible = rich_html_to_text(QueuedDiscordMessage.draft_line("y" * 300, [], "Jay", url))

        assert len(visible) == ANNOUNCEMENT_MAX_CHARS
        assert visible.endswith("y [...] (Jay)")

    def it_escapes_what_the_sender_typed():
        line = QueuedDiscordMessage.draft_line("<b>bold</b> & more", [], "<i>Dee</i>", url)

        assert "<b>" not in line and "<i>" not in line
        assert rich_html_to_text(line) == "<b>bold</b> & more (<i>Dee</i>)"


def describe_the_queue():
    def it_holds_only_the_waiting_ones_oldest_first():
        from core.factories import QueuedDiscordMessageFactory

        newer = QueuedDiscordMessageFactory(sent_at="2026-10-26T12:00:00Z")
        older = QueuedDiscordMessageFactory(sent_at="2026-10-25T12:00:00Z")
        QueuedDiscordMessageFactory(state=QueuedDiscordMessage.State.APPROVED)
        QueuedDiscordMessageFactory(state=QueuedDiscordMessage.State.DISCARDED)

        assert list(QueuedDiscordMessage.objects.waiting()) == [older, newer]

    def it_names_itself_and_its_cursor():
        from core.factories import QueuedDiscordMessageFactory

        row = QueuedDiscordMessageFactory(discord_message_id="42")
        cursor = DiscordChannelCursor.objects.create(channel_id="7", last_message_id="9")

        assert str(row) == "Discord message 42 (Waiting for review)"
        assert str(cursor) == "Channel 7 read to 9"

    def it_sanitizes_the_line_on_the_way_out():
        from core.factories import QueuedDiscordMessageFactory

        row = QueuedDiscordMessageFactory(line="<p>Hi<script>x()</script></p>")

        assert row.line_html == "<p>Hi</p>"


def describe_the_command():
    @respx.mock
    def it_says_how_many_it_queued():
        _read_from(GENERAL, "50")
        _mock_server([_channel(GENERAL, "51")], {GENERAL: [[_message("51", "@everyone Hi")]]})
        out = StringIO()

        call_command("queue_discord_pings", stdout=out)

        assert out.getvalue().strip() == "Queued 1 Discord message(s) for review."
