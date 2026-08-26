"""Specs for the verdict Discord DMs — event.approved / changes_requested / declined.

The three per-person verdict events carry the Discord DM channel **default ON** (the
proposer asked a question; the answer must reach them without an opt-in hunt), while
every other seeded event keeps the OFF default. Delivery mechanics live in
``discord_dm_spec`` — here we prove the registry defaults and that an emit with no
stored preference row actually lands a DM for a linked proposer (and quietly doesn't
for an unlinked one).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.events.emit import emit
from core.events.registry import Channel, ChannelDefault, get_event

pytestmark = pytest.mark.django_db

_CHANNELS_URL = "https://discord.com/api/v10/users/@me/channels"
_MESSAGES_URL = "https://discord.com/api/v10/channels/dm42/messages"

_VERDICT_KEYS = ("event.approved", "event.changes_requested", "event.declined")


def _mock_dm_ok():
    chan = respx.post(_CHANNELS_URL).mock(return_value=httpx.Response(200, json={"id": "dm42"}))
    msg = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"id": "m1"}))
    return chan, msg


def describe_verdict_registry_defaults():
    @pytest.mark.parametrize("key", _VERDICT_KEYS)
    def it_offers_discord_dm_on_by_default(key):
        spec = get_event(key).channel(Channel.DISCORD_DM)
        assert spec is not None
        assert spec.default is ChannelDefault.ON
        assert not spec.is_forced

    def it_leaves_the_reviewer_side_submitted_event_off():
        spec = get_event("event.submitted").channel(Channel.DISCORD_DM)
        assert spec is None or spec.default is ChannelDefault.OFF


def describe_emitting_a_verdict():
    @respx.mock
    def it_dms_a_linked_proposer_with_no_stored_preference(settings, linked_member):
        settings.DISCORD_BOT_TOKEN = "tok"
        member = linked_member(discord_user_id="555000111")
        chan, msg = _mock_dm_ok()
        result = emit(
            "event.approved",
            context={"user": member.user},
            title="Your event was approved",
            body="Potluck is live on the calendar.",
        )
        assert chan.called and msg.called
        assert (member.user.pk, Channel.DISCORD_DM) in result.delivered

    def it_skips_the_dm_for_an_unlinked_proposer_but_still_delivers_in_app(settings, linked_member):
        settings.DISCORD_BOT_TOKEN = "tok"
        member = linked_member()  # linked User, no Discord id
        with respx.mock:
            route = respx.post(_CHANNELS_URL)
            result = emit(
                "event.declined",
                context={"user": member.user},
                title="Your event wasn't approved",
                body="See the reviewer's note.",
            )
        assert not route.called
        assert (member.user.pk, Channel.IN_APP) in result.delivered
