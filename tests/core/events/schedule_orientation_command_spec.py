"""Specs for the ``/schedule-orientation`` slash command handler (membership.discord_commands)."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
import respx

from core.events.discord_commands import dispatch
from membership.discord_commands import SCHEDULE_ORIENTATION, _schedule_orientation
from membership.models import OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _guild(name: str = "Blacksmithing", **settings_kwargs) -> object:
    guild = GuildFactory(name=name)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True, **settings_kwargs)
    return guild


def _interaction(*, guild: object | None = None, channel_id: str | None = None, **options: object) -> dict:
    opts: list[dict] = []
    if guild is not None:
        opts.append({"name": "guild", "value": guild.name})
    opts += [{"name": name, "value": value} for name, value in options.items()]
    interaction: dict = {"data": {"options": opts}}
    if channel_id is not None:
        interaction["channel_id"] = channel_id
    return interaction


def _content(member: object, **kwargs: object) -> str:
    return _schedule_orientation(_interaction(**kwargs), member)["data"]["content"]


def describe_schedule_orientation_command_definition():
    def it_is_gated_ephemeral_and_deferred():
        assert SCHEDULE_ORIENTATION.name == "schedule-orientation"
        assert (SCHEDULE_ORIENTATION.requires_link, SCHEDULE_ORIENTATION.ephemeral, SCHEDULE_ORIENTATION.defer) == (
            True,
            True,
            True,
        )
        assert {o["name"] for o in SCHEDULE_ORIENTATION.options} == {"guild", "slot", "date", "time", "note"}


def describe_guild_resolution():
    def it_asks_which_guild_when_none_resolves(linked_member):
        member = linked_member()
        content = _content(member, channel_id="unknown")
        assert "Which guild?" in content

    def it_resolves_the_guild_from_the_channel(linked_member):
        member = linked_member()
        guild = _guild(name="Fibers")
        guild.discord_channel_id = "chan-fibers"
        guild.save(update_fields=["discord_channel_id"])
        OrientationSlotFactory(guild=guild, enabled_settings=False)
        content = _schedule_orientation({"channel_id": "chan-fibers", "data": {"options": []}}, member)["data"][
            "content"
        ]
        assert "Fibers" in content


def describe_gates():
    def it_reports_when_the_guild_is_not_accepting(linked_member):
        member = linked_member()
        guild = _guild(is_closed=True)
        assert "isn't taking orientation requests" in _content(member, guild=guild)

    def it_reports_when_the_member_is_already_oriented(linked_member):
        member = linked_member()
        guild = _guild()
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
        OrientationBookingFactory(
            slot=slot, member=member, status=OrientationBooking.Status.CONFIRMED, is_completed=True
        )
        assert "already oriented" in _content(member, guild=guild)

    def it_reports_when_an_open_request_already_exists(linked_member):
        member = linked_member()
        guild = _guild()
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
        OrientationBookingFactory(slot=slot, member=member, status=OrientationBooking.Status.REQUESTED)
        assert "already have an orientation request in" in _content(member, guild=guild)


def describe_the_slot_picker():
    def it_lists_bookable_slots_with_their_numbers_when_no_choice_is_given(linked_member):
        member = linked_member()
        guild = _guild()
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False, location="Studio B")
        content = _content(member, guild=guild)
        assert f"`{slot.pk}`" in content
        assert "Studio B" in content

    def it_shows_the_picker_when_both_a_slot_and_a_custom_time_are_given(linked_member):
        member = linked_member()
        guild = _guild()
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
        content = _content(member, guild=guild, slot=str(slot.pk), date="2099-01-01", time="10:00")
        assert f"`{slot.pk}`" in content

    def describe_with_no_posted_slots():
        def it_invites_a_custom_time_when_allowed(linked_member):
            member = linked_member()
            guild = _guild(allow_custom_requests=True)
            assert "propose your own" in _content(member, guild=guild).lower()

        def it_points_to_the_guild_page_when_custom_is_not_allowed(linked_member):
            member = linked_member()
            guild = _guild(allow_custom_requests=False)
            assert "No orientation times are posted yet" in _content(member, guild=guild)


def describe_booking_a_posted_slot():
    def it_requests_the_slot_and_confirms(linked_member):
        member = linked_member()
        guild = _guild()
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False, location="Front desk")

        content = _content(member, guild=guild, slot=str(slot.pk), note="thanks")

        assert "Orientation requested — Blacksmithing" in content
        assert "Front desk" in content
        booking = OrientationBooking.objects.get(member=member, slot=slot)
        assert booking.member_note == "thanks"

    def it_re_lists_when_the_slot_number_is_not_numeric(linked_member):
        member = linked_member()
        guild = _guild()
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
        content = _content(member, guild=guild, slot="abc")
        assert "didn't recognize that slot number" in content
        assert f"`{slot.pk}`" in content

    def it_re_lists_when_the_slot_is_unknown_or_unavailable(linked_member):
        member = linked_member()
        guild = _guild()
        OrientationSlotFactory(guild=guild, enabled_settings=False)
        content = _content(member, guild=guild, slot="999999")
        assert "isn't available anymore" in content

    def describe_a_booking_race():
        def it_surfaces_the_error_and_re_lists(linked_member):
            member = linked_member()
            guild = _guild()
            slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
            with patch(
                "membership.orientations.request_orientation",
                side_effect=OrientationError("This orientation slot is not available to book."),
            ):
                content = _content(member, guild=guild, slot=str(slot.pk))
            assert "not available to book" in content


def describe_proposing_a_custom_time():
    def it_creates_a_custom_request_and_confirms(linked_member):
        member = linked_member()
        guild = _guild(allow_custom_requests=True)

        content = _content(member, guild=guild, date="2099-05-20", time="5:30pm")

        assert "Proposed:" in content
        assert OrientationSlot.objects.filter(guild=guild, source=OrientationSlot.Source.MANUAL).exists()
        assert OrientationBooking.objects.filter(member=member, guild=guild).exists()

    def it_refuses_when_custom_requests_are_not_allowed(linked_member):
        member = linked_member()
        guild = _guild(allow_custom_requests=False)
        content = _content(member, guild=guild, date="2099-05-20", time="17:30")
        assert "only takes posted times" in content

    def it_reports_an_incomplete_custom_time(linked_member):
        member = linked_member()
        guild = _guild(allow_custom_requests=True)
        assert "couldn't read that time" in _content(member, guild=guild, date="2099-05-20")

    def it_reports_an_unparseable_custom_time(linked_member):
        member = linked_member()
        guild = _guild(allow_custom_requests=True)
        assert "couldn't read that time" in _content(member, guild=guild, date="not-a-date", time="nope")


_CALLBACK_URL = "https://discord.com/api/v10/interactions/intA/tokB/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/appX/tokB/messages/@original"


def describe_dispatch_integration():
    def it_shows_the_connect_prompt_for_an_unlinked_member(rf):
        interaction = {
            "type": 2,
            "data": {"name": "schedule-orientation", "options": []},
            "member": {"user": {"id": "000"}},
        }
        result = dispatch(interaction, rf.post("/"))
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].endswith("/discord/link/")

    @respx.mock
    def it_defers_then_delivers_the_result_via_followup(rf, settings, linked_member):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.DISCORD_CLIENT_ID = "appX"
        settings.MEMBER_BASE_URL = "https://members.example"
        member = linked_member(discord_user_id="555")
        guild = _guild()
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
        callback = respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        followup = respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))

        interaction = {
            "type": 2,
            "id": "intA",
            "token": "tokB",
            "data": {
                "name": "schedule-orientation",
                "options": [{"name": "guild", "value": guild.name}, {"name": "slot", "value": str(slot.pk)}],
            },
            "member": {"user": {"id": "555"}},
        }

        result = dispatch(interaction, rf.post("/"))

        assert result == {}
        # Deferred ack fired first (type 5, ephemeral), then the followup carried the reply.
        assert json.loads(callback.calls.last.request.content) == {"type": 5, "data": {"flags": 64}}
        assert "Orientation requested" in json.loads(followup.calls.last.request.content)["content"]
        assert OrientationBooking.objects.filter(member=member, slot=slot).exists()
