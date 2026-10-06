"""Specs for the ``/schedule-orientation`` slash command handler (membership.discord_commands)."""

from __future__ import annotations

import pytest

from core.events.discord_commands import dispatch
from membership.discord_commands import SCHEDULE_ORIENTATION
from membership.models import OrientationBooking
from tests.membership.factories import GuildFactory, GuildOrientationSettingsFactory, OrientationSlotFactory

pytestmark = pytest.mark.django_db


def _interaction(*options: dict) -> dict:
    return {
        "type": 2,
        "data": {"name": "schedule-orientation", "options": list(options)},
        "member": {"user": {"id": "555"}},
    }


def describe_schedule_orientation_command_definition():
    def it_is_open_to_everyone_ephemeral_and_immediate():
        assert (SCHEDULE_ORIENTATION.requires_link, SCHEDULE_ORIENTATION.ephemeral, SCHEDULE_ORIENTATION.defer) == (
            False,
            True,
            False,
        )

    def it_takes_no_options():
        assert SCHEDULE_ORIENTATION.to_api_dict()["options"] == []


def describe_dispatch():
    def it_links_to_the_orientations_page(rf, settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        result = dispatch(_interaction(), rf.post("/"))
        assert result["data"]["content"] == (
            "Book an orientation on the Orientations page: https://members.example/orientations/"
        )
        assert result["data"]["flags"] == 64

    def it_books_nothing_even_when_old_options_arrive(rf, linked_member):
        member = linked_member(discord_user_id="555")
        guild = GuildFactory(name="Blacksmithing")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
        result = dispatch(
            _interaction({"name": "guild", "value": guild.name}, {"name": "slot", "value": str(slot.pk)}),
            rf.post("/"),
        )
        assert "Orientations page" in result["data"]["content"]
        assert not OrientationBooking.objects.filter(member=member).exists()
