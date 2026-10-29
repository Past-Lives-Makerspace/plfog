"""Check Discord (#1014): the Team reads the Holiday Bizarre channel on demand and adds or skips each new poster.

Covers the one read (``hq.vendor_discord.read_channel``, through respx, never the network) with its deadline, its
fallback when the member list is refused and why a read fails; the pure rule (bots, webhooks and joins left out,
people already on the list left out by username, display name or server nickname against a table's name or Discord
handle, people once added left out by their Discord id, grouping and order, mentions, the note and the date); Add as
Request and Skip, which act on the read the check kept and never read Discord again, with the double press guard;
the page and the panel; and the gates (an admin sets the channel; the Team checks, adds and skips). Every other spec
replaces ``read_channel``. Every name, id and post is made up (this repo is public)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from core.integrations import discord_channel
from core.integrations.discord_channel import DiscordTimeUpError
from hq import services, vendor_discord
from hq.models import HubCard, VendorDiscordDecision, VendorFloor, VendorTable
from hq.vendor_discord import (
    CANNOT_SEE,
    NO_ANSWER,
    NO_BOT,
    NO_CHANNEL,
    NOT_TEXT,
    NOTE_MAX,
    SLOW_DOWN,
    TOO_SLOW,
    ChannelRead,
    DiscordUnreadable,
    display_name,
    listed_keys,
    new_posters,
    read_channel,
    readable,
)
from hq.views import DISCORD_NO_CHANNEL, DISCORD_POSTER_GONE, DISCORD_STALE
from hub.view_as import ROLE_MEMBER
from tests.hq.conftest import signed_in
from tests.hq.factories import EventHubFactory, HubCardFactory, VendorFloorFactory, VendorTableFactory

pytestmark = pytest.mark.django_db

CHANNEL = "1234567890123456789"
OTHER_CHANNEL = "1234567890123456000"
SERVER = "9876543210987654321"
API = "https://discord.com/api/v10"

MOSS = {"id": "7001", "username": "spec.moss", "global_name": "Moss Studio"}
WREN = {"id": "7002", "username": "specwren", "global_name": "Wren"}
FERN = {"id": "7003", "username": "spec_fern", "global_name": None}
ORGANISER = {"id": "7004", "username": "spec.organiser", "global_name": "Spec Organiser"}
FOG_BOT = {"id": "7999", "username": "Fog Bot", "bot": True}
# Wren's server nickname, as the sheet writes her handle.
WREN_NICK = "Ⓦren 🎃 [PRNT]"


def _message(
    message_id: int,
    author: dict[str, Any],
    content: str = "",
    *,
    at: str = "2026-10-24T18:30:00+00:00",
    mentions: tuple[dict[str, Any], ...] = (),
    kind: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    """One message as ``GET /channels/{id}/messages`` sends it (the fields the rule reads)."""
    return {
        "id": str(message_id),
        "type": kind,
        "author": author,
        "content": content,
        "timestamp": at,
        "mentions": list(mentions),
        **extra,
    }


def _read(*messages: dict[str, Any], nicks: dict[str, str] | None = None, name: str = "holiday-bizarre") -> ChannelRead:
    return ChannelRead(channel_id=CHANNEL, name=name, messages=messages, nicks=nicks or {})


def _names(posters: list[vendor_discord.Poster]) -> list[str]:
    return [poster.name for poster in posters]


def _messages(response: Any) -> list[tuple[str, str]]:
    return [(message.level_tag, str(message)) for message in get_messages(response.wsgi_request)]


def _toast(response: Any) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    """A kept read lives in the cache, which outlives each spec's database rollback."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def kate(make_member):
    return make_member(preferred_name="Kate Lindqvist")


@pytest.fixture
def card(open_hub, kate) -> HubCard:
    return HubCardFactory(
        hub=open_hub,
        kind=HubCard.Kind.VENDOR_TABLES,
        title="Vendor Tables",
        audience=HubCard.Audience.ADMINS,
        team=[kate],
    )


@pytest.fixture
def hub(card):
    return card.hub


@pytest.fixture
def channel(hub) -> VendorFloor:
    """The hub with its Discord channel set and no space map."""
    return VendorFloorFactory(hub=hub, capacity=None, discord_channel_id=CHANNEL)


@pytest.fixture
def kate_client(kate) -> Client:
    return signed_in(kate)


class FakeDiscord:
    """The channel as the Fog Bot would read it: replaces ``hq.vendor_discord.read_channel`` so no spec reaches
    Discord. ``messages`` and ``nicks`` are what the next read sees; ``error`` makes it fail with that reason."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.nicks: dict[str, str] = {}
        self.name = "holiday-bizarre"
        self.error = ""
        self.nicknames_read = True
        self.reads: list[str] = []

    def __call__(self, channel_id: str) -> ChannelRead:
        self.reads.append(channel_id)
        if self.error:
            raise DiscordUnreadable(self.error)
        return ChannelRead(
            channel_id=channel_id,
            name=self.name,
            messages=tuple(self.messages),
            nicks=dict(self.nicks),
            nicknames_read=self.nicknames_read,
        )


@pytest.fixture
def discord() -> Iterator[FakeDiscord]:
    fake = FakeDiscord()
    with patch.object(vendor_discord, "read_channel", fake):
        yield fake


def _check_url(hub: Any) -> str:
    return reverse("hq:vendor_discord_check", args=[hub.slug])


def _add_url(hub: Any) -> str:
    return reverse("hq:vendor_discord_add", args=[hub.slug])


def _skip_url(hub: Any) -> str:
    return reverse("hq:vendor_discord_skip", args=[hub.slug])


def _channel_url(hub: Any) -> str:
    return reverse("hq:vendor_discord_channel", args=[hub.slug])


def _page(hub: Any) -> str:
    return reverse("hq:vendors", args=[hub.slug])


def _choice(author: dict[str, Any], *message_ids: int, show: str = "all") -> dict[str, Any]:
    return {"author": author["id"], "message": [str(message_id) for message_id in message_ids], "filter": show}


HTMX = {"HTTP_HX_REQUEST": "true"}


def _check(client: Client, hub: Any, **params: str) -> str:
    """Press Check Discord over htmx; the panel."""
    return client.get(_check_url(hub), params, **HTMX).content.decode()


def describe_reading_the_channel():
    @pytest.fixture
    def token(settings):
        settings.DISCORD_BOT_TOKEN = "tok"

    def _channel_route(status: int = 200, **info: Any) -> respx.Route:
        body = {"id": CHANNEL, "name": "holiday-bizarre", "type": 0, "guild_id": SERVER, **info}
        return respx.get(f"{API}/channels/{CHANNEL}").mock(return_value=httpx.Response(status, json=body))

    def _messages_route(*messages: dict[str, Any]) -> respx.Route:
        return respx.get(f"{API}/channels/{CHANNEL}/messages").mock(
            return_value=httpx.Response(200, json=list(messages))
        )

    @respx.mock
    def it_reads_the_name_the_newest_messages_and_the_nicknames(token):
        _channel_route()
        _messages_route(
            _message(2, {**WREN, "avatar": "abc"}, "Table please", mentions=({**MOSS, "avatar": "x"},), pinned=False)
        )
        respx.get(f"{API}/guilds/{SERVER}/members").mock(
            return_value=httpx.Response(
                200, json=[{"user": WREN, "nick": WREN_NICK}, {"user": MOSS, "nick": None}, {"user": FERN}]
            )
        )

        read = read_channel(CHANNEL)

        assert (read.name, read.label, read.nicknames_read) == ("holiday-bizarre", "#holiday-bizarre", True)
        assert read.messages == (_message(2, WREN, "Table please", mentions=(MOSS,)),)
        assert read.nicks == {WREN["id"]: WREN_NICK}

    @respx.mock
    def it_keeps_a_webhooks_mark_on_its_messages(token):
        _channel_route()
        _messages_route(_message(2, ORGANISER, "From the website", webhook_id="555"))
        respx.get(f"{API}/guilds/{SERVER}/members").mock(return_value=httpx.Response(200, json=[]))

        assert read_channel(CHANNEL).messages[0]["webhook_id"] == "555"

    def it_names_a_channel_without_a_name_by_its_id():
        assert _read(name="").label == f"#{CHANNEL}"

    def it_says_so_when_this_site_has_no_bot_token_and_asks_nothing(settings):
        settings.DISCORD_BOT_TOKEN = ""
        with respx.mock(assert_all_called=False) as router:
            route = router.get(url__startswith=API)
            with pytest.raises(DiscordUnreadable, match=NO_BOT):
                read_channel(CHANNEL)
        assert not route.called

    @pytest.mark.parametrize(
        ("status", "why"), [(403, CANNOT_SEE), (404, NO_CHANNEL), (429, SLOW_DOWN), (500, NO_ANSWER)]
    )
    @respx.mock
    def it_says_why_discord_refused(token, status, why):
        _channel_route(status)

        with pytest.raises(DiscordUnreadable) as raised:
            read_channel(CHANNEL)

        assert str(raised.value) == why

    @pytest.mark.parametrize("info", [{"type": 2}, {"type": 1, "guild_id": None}, {"type": 4}])
    @respx.mock
    def it_says_an_id_that_is_no_text_channel_is_not_one(token, info):
        _channel_route(**info)

        with pytest.raises(DiscordUnreadable, match=NOT_TEXT):
            read_channel(CHANNEL)

    @respx.mock
    def it_says_discord_did_not_answer_on_a_network_error(token):
        respx.get(f"{API}/channels/{CHANNEL}").mock(side_effect=httpx.ConnectError("boom"))

        with pytest.raises(DiscordUnreadable, match=NO_ANSWER):
            read_channel(CHANNEL)

    @respx.mock
    def it_matches_without_nicknames_when_the_member_list_is_refused(token):
        _channel_route()
        _messages_route(_message(2, WREN, "Table please"))
        respx.get(f"{API}/guilds/{SERVER}/members").mock(return_value=httpx.Response(403, json={}))

        read = read_channel(CHANNEL)

        assert (read.nicknames_read, read.nicks, len(read.messages)) == (False, {}, 1)

    @respx.mock
    def it_says_slow_down_when_the_member_list_is_rate_limited(token):
        _channel_route()
        _messages_route()
        respx.get(f"{API}/guilds/{SERVER}/members").mock(return_value=httpx.Response(429, json={}))

        with pytest.raises(DiscordUnreadable, match=SLOW_DOWN):
            read_channel(CHANNEL)

    @respx.mock
    def it_says_discord_was_slow_once_the_read_runs_past_its_time(token, monkeypatch):
        monkeypatch.setattr(vendor_discord, "READ_SECONDS", 0.0)
        route = _channel_route()

        with pytest.raises(DiscordUnreadable, match=TOO_SLOW):
            read_channel(CHANNEL)

        assert not route.called

    @respx.mock
    def it_hands_every_call_the_same_deadline_and_matches_without_nicknames_past_it(token):
        info = {"name": "holiday-bizarre", "type": 0, "guild_id": SERVER}
        with (
            patch.object(discord_channel, "channel_info", return_value=info) as channel_info,
            patch.object(discord_channel, "channel_messages_newest", return_value=[]) as messages,
            patch.object(discord_channel, "server_members", side_effect=DiscordTimeUpError("late")) as members,
            patch.object(vendor_discord.time, "monotonic", return_value=1000.0),
        ):
            read = read_channel(CHANNEL)

        deadlines = {
            call.kwargs["deadline"] for call in (channel_info.call_args, messages.call_args, members.call_args)
        }
        assert deadlines == {1000.0 + vendor_discord.READ_SECONDS}
        assert vendor_discord.READ_SECONDS <= 20
        assert read.nicknames_read is False


def describe_who_is_listed():
    def it_groups_by_person_newest_poster_first_with_their_posts_newest_first():
        read = _read(
            _message(10, MOSS, "Could I get a table?"),
            _message(11, WREN, "Wren here, 6 ft please"),
            _message(12, MOSS, "Mugs and planters"),
            _message(13, FERN, "Table for me too"),
        )

        posters = new_posters(read, listed=set(), handled=set())

        assert _names(posters) == ["spec_fern", "Moss Studio", "Wren"]
        assert [post.text for post in posters[1].posts] == ["Mugs and planters", "Could I get a table?"]

    def it_leaves_out_bots_webhooks_and_system_messages():
        read = _read(
            _message(10, FOG_BOT, "New class posted"),
            _message(11, ORGANISER, "Shared from the website", webhook_id="555"),
            _message(12, WREN, "", kind=7),
            _message(13, MOSS, "Replying with my table", kind=19),
        )

        assert _names(new_posters(read, listed=set(), handled=set())) == ["Moss Studio"]

    @pytest.mark.parametrize(
        ("name", "handle"),
        [
            ("Someone Else", "spec.moss"),
            ("Someone Else", "@SPEC.MOSS"),
            ("moss studio", ""),
            ("Someone Else", "Ⓦren 🎃 [PRNT]"),
            ("Ｓｐｅｃ＿ｆｅｒｎ", "N/A"),
        ],
    )
    def it_leaves_out_people_already_on_the_list_by_name_or_handle(name, handle):
        read = _read(
            _message(10, MOSS, "a"), _message(11, WREN, "b"), _message(12, FERN, "c"), nicks={"7002": WREN_NICK}
        )
        listed = listed_keys([(name, handle)])

        names = _names(new_posters(read, listed=listed, handled=set()))

        assert len(names) == 2

    def it_drops_a_leading_at_on_the_posters_names_too():
        spec_mel = {"id": "7010", "username": "specmel", "global_name": "@Spec Mel"}
        read = _read(_message(10, spec_mel, "Table please"), nicks={"7010": "@mel"})

        assert new_posters(read, listed=listed_keys([("Mel", "")]), handled=set()) == []
        assert new_posters(read, listed=listed_keys([("Spec Mel", "")]), handled=set()) == []

    def it_never_matches_a_blank_handle():
        assert listed_keys([("Spec Vendor", ""), ("Spec Other", "@")]) == {"spec vendor", "spec other"}

    def it_leaves_out_posts_already_handled_and_people_with_nothing_new():
        read = _read(_message(10, MOSS, "old"), _message(11, MOSS, "new"), _message(12, WREN, "handled"))

        posters = new_posters(read, listed=set(), handled={"10", "12"})

        assert _names(posters) == ["Moss Studio"]
        assert [post.text for post in posters[0].posts] == ["new"]

    def it_leaves_out_someone_once_added_whatever_their_name_now():
        read = _read(_message(10, WREN, "One more thing"), nicks={"7002": "Wren | Prints"})

        assert new_posters(read, listed=set(), handled=set(), added={"7002"}) == []

    def it_names_a_poster_by_nickname_then_display_name_then_username():
        read = _read(
            _message(10, WREN, "a"), _message(11, MOSS, "b"), _message(12, FERN, "c"), nicks={"7002": WREN_NICK}
        )

        posters = new_posters(read, listed=set(), handled=set())

        assert _names(posters) == ["spec_fern", "Moss Studio", WREN_NICK]
        assert posters[2].username == "specwren"

    def it_passes_over_a_name_of_invisible_characters():
        assert display_name(WREN, {"7002": "​‎"}) == "Wren"
        assert display_name({**FERN, "global_name": "​"}, {}) == "spec_fern"
        assert display_name({"id": "7020", "username": "​"}, {}) == "​"


def describe_a_posts_text():
    def it_shows_a_mention_as_the_name_the_server_shows():
        read = _read(nicks={"7002": WREN_NICK})

        text = readable("Ask <@7002> or <@!7001> about <@7333>", [WREN, MOSS], read)

        assert text == f"Ask @{WREN_NICK} or @Moss Studio about @someone"

    def it_shows_a_custom_emoji_by_its_name():
        assert readable("Yes please <:pl_heart:123> <a:spin:456>", [], _read()) == "Yes please :pl_heart: :spin:"

    def it_shows_roles_channels_and_timestamps_as_plain_words():
        text = readable(f"<@&555> see <#{CHANNEL}> and <#42>, doors <t:1793840400:F>", [], _read())

        assert text == "@role see #holiday-bizarre and #channel, doors Wed Nov 4, 5 PM"


def describe_a_posters_request():
    def it_dates_the_request_by_the_first_post_in_portland():
        read = _read(
            _message(10, MOSS, "first", at="2026-10-24T03:00:00+00:00"),
            _message(11, MOSS, "second", at="2026-10-25T18:00:00+00:00"),
        )

        assert new_posters(read, listed=set(), handled=set())[0].first_posted_on == date(2026, 10, 23)

    def it_joins_the_posts_oldest_first_and_leaves_out_blank_ones():
        read = _read(_message(10, MOSS, "Hi  there"), _message(11, MOSS, ""), _message(12, MOSS, "Mugs\nand planters"))

        poster = new_posters(read, listed=set(), handled=set())[0]

        assert poster.note == "Hi there · Mugs and planters"

    def it_cuts_a_long_note_to_the_field_with_an_ellipsis():
        read = _read(_message(10, MOSS, "x" * 700))

        note = new_posters(read, listed=set(), handled=set())[0].note

        assert len(note) == NOTE_MAX
        assert note.endswith("…")


def describe_the_page():
    def it_has_no_check_discord_without_a_channel(hub, kate_client):
        body = kate_client.get(_page(hub)).content.decode()

        assert "data-hq-vt-discord-check" not in body
        assert 'id="hq-vt-discord"' not in body

    def it_offers_the_team_check_discord_once_a_channel_is_set(hub, channel, kate_client, discord):
        body = kate_client.get(_page(hub), {"filter": "confirmed"}).content.decode()

        assert f'hx-get="{_check_url(hub)}"' in body
        assert '<input type="hidden" name="filter" value="confirmed">' in body.split("data-hq-vt-discord-form")[1]
        assert '<div id="hq-vt-discord" hidden></div>' in body
        assert discord.reads == []

    def it_shows_the_channel_field_to_admins_only(hub, channel, kate_client, admin_client):
        assert "data-hq-vt-discord-channel-form" not in kate_client.get(_page(hub)).content.decode()

        body = admin_client.get(_page(hub)).content.decode()

        assert f'action="{_channel_url(hub)}"' in body
        assert f'value="{CHANNEL}"' in body


def describe_check_discord():
    def it_lists_new_posters_with_their_posts_dates_and_buttons(hub, channel, kate_client, discord):
        discord.messages = [
            _message(10, MOSS, "Could I get a table? <@7004>", at="2026-10-24T03:15:00+00:00", mentions=(ORGANISER,)),
            _message(11, WREN, "6 ft please"),
        ]
        discord.nicks = {"7002": WREN_NICK}

        response = kate_client.get(_check_url(hub), **HTMX)

        body = response.content.decode()
        assert response.status_code == 200
        assert "pl-hq-vt__board" not in body
        assert 'id="hq-vt-discord"' in body
        assert "New in #holiday-bizarre" in body
        assert body.index(WREN_NICK) < body.index("Moss Studio")
        assert "@spec.moss" in body
        assert "Oct 23, 8:15 PM" in body
        assert "Could I get a table? @Spec Organiser" in body
        assert 'aria-label="Add Moss Studio as a request"' in body
        assert 'aria-label="Skip Moss Studio"' in body
        assert 'name="message" value="10"' in body
        assert "data-hq-vt-discord-no-nicks" not in body
        assert discord.reads == [CHANNEL]

    def it_says_nothing_is_new_in_the_channel(hub, channel, kate_client, discord):
        discord.messages = [_message(10, FOG_BOT, "New class posted")]

        assert "Nothing new in #holiday-bizarre." in _check(kate_client, hub)

    def it_says_when_nicknames_could_not_be_read(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please")]
        discord.nicknames_read = False

        body = _check(kate_client, hub)

        assert "data-hq-vt-discord-no-nicks" in body
        assert 'data-hq-vt-poster="7001"' in body

    def it_says_why_the_channel_could_not_be_read_without_a_server_error(hub, channel, kate_client, discord):
        discord.error = CANNOT_SEE

        response = kate_client.get(_check_url(hub), **HTMX)

        assert response.status_code == 200
        body = response.content.decode()
        assert "data-hq-vt-discord-error" in body
        assert f"Discord could not be read. {CANNOT_SEE}" in body
        assert f"New in #{CHANNEL}" in body

    def it_says_there_is_no_channel_to_check(hub, kate_client, discord):
        body = _check(kate_client, hub)

        assert DISCORD_NO_CHANNEL in body
        assert discord.reads == []

    def it_keeps_the_lists_filter_for_add(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please")]

        body = _check(kate_client, hub, filter="confirmed")

        assert '<input type="hidden" name="filter" value="confirmed">' in body

    def it_renders_the_page_with_the_panel_without_htmx(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please")]

        body = kate_client.get(_check_url(hub)).content.decode()

        assert "data-hq-vt-board" in body
        assert 'data-hq-vt-poster="7001"' in body
        assert '<div id="hq-vt-discord" hidden></div>' not in body

    def it_is_the_teams_only(hub, channel, make_member, discord):
        stranger = signed_in(make_member())

        assert stranger.get(_check_url(hub)).status_code == 403
        assert discord.reads == []

    def it_reads_only(hub, channel, kate_client, discord):
        assert kate_client.post(_check_url(hub)).status_code == 405


def describe_add_as_request():
    def it_adds_the_poster_with_every_field_and_they_leave_the_list(hub, channel, kate, kate_client, discord):
        discord.messages = [
            _message(10, WREN, "Hi! Could I get a table?", at="2026-10-24T03:00:00+00:00"),
            _message(11, WREN, "Prints and stickers", at="2026-10-26T18:00:00+00:00"),
            _message(12, MOSS, "Me too"),
        ]
        discord.nicks = {"7002": WREN_NICK}
        _check(kate_client, hub)

        response = kate_client.post(_add_url(hub), _choice(WREN, 10, 11), **HTMX)

        table = VendorTable.objects.get()
        assert (table.name, table.discord_handle, table.request_source) == (WREN_NICK, WREN_NICK, "Discord")
        assert table.requested_on == date(2026, 10, 23)
        assert table.note == "Hi! Could I get a table? · Prints and stickers"
        assert (table.status, table.created_by, table.hub) == (VendorTable.Status.REQUESTED, kate, hub)
        assert _toast(response) == {"message": f"{WREN_NICK} added.", "type": "success"}
        body = response.content.decode()
        assert "data-hq-vt-board" in body
        assert f'data-hq-vt-row="{table.pk}"' in body
        assert 'data-hq-vt-poster="7002"' not in body
        assert 'data-hq-vt-poster="7001"' in body
        decisions = VendorDiscordDecision.objects.order_by("message_id")
        assert [(row.message_id, row.author_id, row.action, row.decided_by) for row in decisions] == [
            ("10", "7002", "added", kate),
            ("11", "7002", "added", kate),
        ]

    def it_never_reads_discord_again(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please"), _message(11, WREN, "Hi")]
        _check(kate_client, hub)

        kate_client.post(_add_url(hub), _choice(MOSS, 10), **HTMX)
        kate_client.post(_skip_url(hub), _choice(WREN, 11), **HTMX)

        assert discord.reads == [CHANNEL]
        assert VendorDiscordDecision.objects.count() == 2

    def it_keeps_the_lists_filter_in_its_answer(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please")]
        _check(kate_client, hub, filter="confirmed")

        body = kate_client.post(_add_url(hub), _choice(MOSS, 10, show="confirmed"), **HTMX).content.decode()

        assert 'data-hq-vt-filter="confirmed" aria-current="true"' in body
        assert f'data-hq-vt-row="{VendorTable.objects.get().pk}"' in body

    def it_waitlists_the_request_when_the_map_is_full(hub, channel, kate_client, discord):
        channel.capacity = 1
        channel.save()
        VendorTableFactory(hub=hub, status=VendorTable.Status.CONFIRMED)
        discord.messages = [_message(10, MOSS, "Table please")]
        _check(kate_client, hub)

        response = kate_client.post(_add_url(hub), _choice(MOSS, 10), **HTMX)

        assert hub.vendor_tables.get(name="Moss Studio").status == VendorTable.Status.WAITLIST
        assert _toast(response)["message"] == "Moss Studio added to the waitlist (tables are full)."

    def it_adds_only_the_posts_that_were_shown(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please"), _message(11, MOSS, "Posted after the check")]
        _check(kate_client, hub)

        kate_client.post(_add_url(hub), _choice(MOSS, 10), **HTMX)

        assert VendorTable.objects.get().note == "Table please"
        assert list(VendorDiscordDecision.objects.values_list("message_id", flat=True)) == ["10"]

    def it_adds_one_table_when_pressed_twice(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please")]
        _check(kate_client, hub)

        kate_client.post(_add_url(hub), _choice(MOSS, 10), **HTMX)
        response = kate_client.post(_add_url(hub), _choice(MOSS, 10), **HTMX)

        assert VendorTable.objects.count() == 1
        assert _toast(response) == {"message": DISCORD_POSTER_GONE, "type": "info"}

    def it_never_adds_someone_twice_after_a_nickname_change(hub, channel, kate_client, discord):
        discord.messages = [_message(10, WREN, "Table please")]
        _check(kate_client, hub)
        kate_client.post(_add_url(hub), _choice(WREN, 10), **HTMX)

        discord.messages.append(_message(11, WREN, "Can I bring a rack?"))
        discord.nicks = {"7002": "Wren | Prints"}
        body = _check(kate_client, hub)
        response = kate_client.post(_add_url(hub), _choice(WREN, 11), **HTMX)

        assert 'data-hq-vt-poster="7002"' not in body
        assert _toast(response)["message"] == DISCORD_POSTER_GONE
        assert VendorTable.objects.count() == 1

    def it_refuses_posts_that_are_no_longer_listed(hub, channel, kate, discord):
        discord.messages = [_message(10, MOSS, "Table please")]
        read = vendor_discord.read_channel(CHANNEL)

        with pytest.raises(services.DiscordPosterGone):
            services.add_discord_poster(hub, read, author_id=MOSS["id"], message_ids=["99"], member=kate)
        services.skip_discord_poster(hub, read, author_id=MOSS["id"], message_ids=["10"], member=kate)
        with pytest.raises(services.DiscordPosterGone):
            services.add_discord_poster(hub, read, author_id=MOSS["id"], message_ids=["10"], member=kate)

        assert VendorTable.objects.count() == 0
        assert VendorDiscordDecision.objects.count() == 1

    def it_writes_nothing_without_a_kept_check(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please")]

        response = kate_client.post(_add_url(hub), _choice(MOSS, 10), **HTMX)

        assert VendorTable.objects.count() == 0
        assert discord.reads == []
        assert _toast(response) == {"message": DISCORD_STALE, "type": "info"}
        assert "data-hq-vt-discord-error" in response.content.decode()

    def it_writes_nothing_once_the_channel_changed_since_the_check(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please")]
        _check(kate_client, hub)
        services.set_vendor_discord_channel(hub, OTHER_CHANNEL)

        response = kate_client.post(_add_url(hub), _choice(MOSS, 10), **HTMX)

        assert VendorTable.objects.count() == 0
        assert _toast(response)["message"] == DISCORD_STALE

    def it_says_to_check_again_when_the_channel_was_removed(hub, channel, kate_client, discord):
        services.set_vendor_discord_channel(hub, "")

        body = kate_client.post(_add_url(hub), _choice(MOSS, 10), **HTMX).content.decode()

        assert "New in Discord" in body

    def it_renders_the_page_with_the_panel_after_a_plain_post(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please"), _message(11, WREN, "Hi")]
        _check(kate_client, hub)

        response = kate_client.post(_add_url(hub), _choice(MOSS, 10))

        assert response.status_code == 200
        assert _messages(response) == [("success", "Moss Studio added.")]
        assert 'data-hq-vt-poster="7002"' in response.content.decode()
        assert discord.reads == [CHANNEL]

    def it_is_the_teams_only(hub, channel, make_member, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Table please")]
        _check(kate_client, hub)
        stranger = signed_in(make_member())

        assert stranger.post(_add_url(hub), _choice(MOSS, 10)).status_code == 403
        assert VendorTable.objects.count() == 0

    def it_is_a_post(hub, channel, kate_client):
        assert kate_client.get(_add_url(hub)).status_code == 405

    def it_is_a_404_on_a_hub_without_vendor_tables(admin_client, discord):
        other = EventHubFactory(slug="spec-other-hub")

        assert admin_client.post(_add_url(other), _choice(MOSS, 10)).status_code == 404
        assert admin_client.post(_skip_url(other), _choice(MOSS, 10)).status_code == 404
        assert admin_client.get(_check_url(other)).status_code == 404


def describe_skip():
    def it_hides_the_poster_until_they_post_again(hub, channel, kate, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Lovely market last year!"), _message(11, WREN, "Table please")]
        _check(kate_client, hub)

        response = kate_client.post(_skip_url(hub), _choice(MOSS, 10), **HTMX)

        body = response.content.decode()
        assert _toast(response) == {"message": "Skipped Moss Studio.", "type": "success"}
        assert body.lstrip().startswith("<section")
        assert 'data-hq-vt-poster="7001"' not in body
        assert 'data-hq-vt-poster="7002"' in body
        assert VendorTable.objects.count() == 0
        decision = VendorDiscordDecision.objects.get()
        assert (decision.action, decision.decided_by) == (VendorDiscordDecision.Action.SKIPPED, kate)
        assert str(decision) == "10: Skipped"

        discord.messages.append(_message(12, MOSS, "Actually, can I get a table?"))
        body = _check(kate_client, hub)

        assert 'data-hq-vt-poster="7001"' in body
        assert 'data-hq-vt-post="12"' in body
        assert 'data-hq-vt-post="10"' not in body

    def it_says_so_when_someone_else_got_there_first(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Hi")]
        _check(kate_client, hub)
        kate_client.post(_skip_url(hub), _choice(MOSS, 10), **HTMX)

        response = kate_client.post(_skip_url(hub), _choice(MOSS, 10), **HTMX)

        assert _toast(response) == {"message": DISCORD_POSTER_GONE, "type": "info"}
        assert VendorDiscordDecision.objects.count() == 1

    def it_writes_nothing_without_a_kept_check(hub, channel, kate_client, discord):
        response = kate_client.post(_skip_url(hub), _choice(MOSS, 10), **HTMX)

        assert _toast(response) == {"message": DISCORD_STALE, "type": "info"}
        assert VendorDiscordDecision.objects.count() == 0

    def it_renders_the_page_with_the_panel_after_a_plain_post(hub, channel, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Hi")]
        _check(kate_client, hub)

        response = kate_client.post(_skip_url(hub), _choice(MOSS, 10))

        assert response.status_code == 200
        assert _messages(response) == [("success", "Skipped Moss Studio.")]
        assert "data-hq-vt-discord-empty" in response.content.decode()

    def it_is_the_teams_only(hub, channel, make_member, kate_client, discord):
        discord.messages = [_message(10, MOSS, "Hi")]
        _check(kate_client, hub)
        stranger = signed_in(make_member())

        assert stranger.post(_skip_url(hub), _choice(MOSS, 10)).status_code == 403
        assert VendorDiscordDecision.objects.count() == 0

    def it_is_a_post(hub, channel, kate_client):
        assert kate_client.get(_skip_url(hub)).status_code == 405


def describe_setting_the_channel():
    def it_saves_the_channel_id_for_an_admin(hub, admin_client):
        response = admin_client.post(_channel_url(hub), {"discord_channel_id": f"  {CHANNEL} "})

        assert response.status_code == 302
        assert _messages(response) == [("success", "Discord channel saved.")]
        assert VendorFloor.objects.get(hub=hub).discord_channel_id == CHANNEL

    def it_refuses_anything_but_digits_and_saves_nothing(hub, admin_client):
        response = admin_client.post(_channel_url(hub), {"discord_channel_id": "#holiday-bizarre"})

        assert response.status_code == 200
        body = response.content.decode()
        assert "digits only" in body
        assert '<details class="pl-disclosure pl-hq-vt__disclosure" open data-hq-vt-discord-channel>' in body
        assert not VendorFloor.objects.filter(hub=hub).exists()

    def it_clears_the_channel_when_saved_blank(hub, channel, admin_client):
        response = admin_client.post(_channel_url(hub), {"discord_channel_id": ""})

        assert _messages(response) == [("success", "Discord channel removed.")]
        assert VendorFloor.objects.get(hub=hub).discord_channel_id == ""
        assert VendorFloor.objects.get(hub=hub).capacity is None

    def it_reads_the_actual_role_so_a_previewing_admin_still_saves_it(hub, admin_client):
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        assert admin_client.post(_channel_url(hub), {"discord_channel_id": CHANNEL}).status_code == 302

    def it_is_admins_only_never_the_team(hub, kate_client):
        assert kate_client.post(_channel_url(hub), {"discord_channel_id": CHANNEL}).status_code == 403
        assert not VendorFloor.objects.filter(hub=hub).exists()

    def it_is_a_post(hub, admin_client):
        assert admin_client.get(_channel_url(hub)).status_code == 405
