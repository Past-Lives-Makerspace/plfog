"""BDD specs for #1129 on the member booking pages: a lapsed Orientor's times are never offered.

The guild page and the Orientations page hide a slot or open window whose orientor is no
longer an Active member, before the daily job retires it. Assertions anchor on URLs and
markup ids, never on copy (STANDARDS.md, Testing Traps).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str) -> None:
    MembershipPlanFactory()
    User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    client.login(username=username, password="pass")


@pytest.fixture
def lapsed_times() -> dict[str, object]:
    """A guild whose one Orientor has a slot and an open window, then lapses to Former."""
    orientor = MemberFactory()
    guild = GuildFactory(guild_lead=orientor)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    orientation_type = OrientationTypeFactory(guild=guild, name="Lapse Basics", duration_minutes=60)
    slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type, orienter=orientor)
    window = OrientationAvailabilityBlockFactory(guild=guild, orienter=orientor)
    return {"orientor": orientor, "guild": guild, "type": orientation_type, "slot": slot, "window": window}


def _markers(times: dict[str, object]) -> tuple[str, str]:
    slot_marker = reverse("hub_orientation_book", args=[times["slot"].pk])
    window_marker = reverse("hub_orientation_block_starts", args=[times["window"].pk, times["type"].pk])
    return slot_marker, window_marker


def _lapse(times: dict[str, object]) -> None:
    Member.objects.filter(pk=times["orientor"].pk).update(status=Member.Status.FORMER)


def describe_the_guild_page():
    def it_offers_an_active_orientors_times(client: Client, lapsed_times):
        _login(client, "stale_g1")
        content = client.get(reverse("hub_guild_detail", args=[lapsed_times["guild"].slug])).content.decode()
        slot_marker, window_marker = _markers(lapsed_times)
        assert slot_marker in content
        assert window_marker in content

    def it_hides_them_once_the_orientor_is_no_longer_active(client: Client, lapsed_times):
        _login(client, "stale_g2")
        _lapse(lapsed_times)
        content = client.get(reverse("hub_guild_detail", args=[lapsed_times["guild"].slug])).content.decode()
        slot_marker, window_marker = _markers(lapsed_times)
        assert slot_marker not in content
        assert window_marker not in content


def describe_the_orientations_page():
    def it_offers_an_active_orientors_times(client: Client, lapsed_times):
        _login(client, "stale_o1")
        content = client.get(reverse("hub_orientations")).content.decode()
        slot_marker, window_marker = _markers(lapsed_times)
        assert slot_marker in content
        assert window_marker in content

    def it_hides_them_once_the_orientor_is_no_longer_active(client: Client, lapsed_times):
        _login(client, "stale_o2")
        _lapse(lapsed_times)
        content = client.get(reverse("hub_orientations")).content.decode()
        slot_marker, window_marker = _markers(lapsed_times)
        assert slot_marker not in content
        assert window_marker not in content

    def it_refuses_a_posted_booking_on_the_hidden_slot(client: Client, lapsed_times):
        _login(client, "stale_o3")
        _lapse(lapsed_times)
        client.post(reverse("hub_orientation_book", args=[lapsed_times["slot"].pk]))
        assert not lapsed_times["slot"].bookings.exists()
