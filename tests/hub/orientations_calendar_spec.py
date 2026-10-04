"""BDD specs for the Orientations page's Calendar view (#502 part 3).

The calendar reuses the guild page's shell with a legend of its own: one chip per guild
that owns a listed type, plus Makerspace for an item no guild owns. Assertions anchor on
markup (ids, data attributes, URLs, the chip and legend classes) and factory names, never
on copy a changelog entry could also carry (STANDARDS.md, Testing Traps).
"""

from __future__ import annotations

import re
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from hub.calendar_entries import ORIENTATION_PK_OFFSET
from hub.calendar_pages import orientations_calendar_context
from membership.models import OrientationBooking, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

PAGE = "/orientations/"
EVENTS = "/orientations/calendar/events/"


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user


def _guild(name: str, **kwargs: object) -> object:
    guild = GuildFactory(name=name, **kwargs)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    return guild


def _slot(orientation_type: OrientationType, **kwargs: object) -> object:
    """A bookable slot tomorrow morning, local time (the same UTC date), so it sits in this window."""
    starts_at = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    kwargs.setdefault("starts_at", starts_at)
    kwargs.setdefault("ends_at", starts_at + timedelta(hours=1))
    return OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type, **kwargs)


def _legend(content: bytes) -> list[str]:
    """The legend row's chip labels, in order."""
    text = content.decode()
    row = text[text.index('class="pl-calendar-filters"') : text.index('id="pl-calendar-events-area"')]
    return re.findall(r">\s*([^<>]+?)\s*</button>", row)


def _chip_titles(content: bytes) -> list[str]:
    return re.findall(r'<span class="pl-calendar-grid__chip-title">([^<]*)</span>', content.decode())


def describe_the_tabs():
    def it_opens_on_the_list_with_the_calendar_left_to_load_later(client: Client):
        _login(client, "oc_tabs")
        content = client.get(PAGE).content
        assert b"plListCalendar('list')" in content
        assert b'aria-label="Orientations views"' in content
        assert f'data-calendar-src="{EVENTS}?shell=1"'.encode() in content
        assert b'id="pl-calendar-events-area"' not in content

    def it_builds_no_calendar_for_the_list_view(client: Client):
        _login(client, "oc_no_queries")
        with mock.patch("hub.orientations_views.orientations_calendar_context") as build:
            assert client.get(PAGE).status_code == 200
        build.assert_not_called()

    def it_opens_on_the_calendar_for_view_calendar(client: Client):
        _login(client, "oc_open")
        content = client.get(f"{PAGE}?view=calendar").content
        assert b"plListCalendar('calendar')" in content
        assert b'id="pl-calendar-events-area"' in content
        assert b"data-calendar-src" not in content

    def it_keeps_every_card_on_the_list_pane_when_the_calendar_opens(client: Client):
        _login(client, "oc_cards")
        orientation_type = OrientationTypeFactory(guild=_guild("Cards Guild"), name="Cards Type")
        content = client.get(f"{PAGE}?view=calendar").content.decode()
        assert content.index('x-ref="listPane"') < content.index(f'id="orientation-type-{orientation_type.pk}"')

    def it_salts_the_calendar_keys_with_orientations(client: Client):
        _login(client, "oc_keys")
        content = client.get(f"{PAGE}?view=calendar").content
        assert b"guildCalFiltersOff-orientations" in content
        assert b"guildCalFiltersOff-None" not in content
        assert b"guildCalFiltersOff-'" not in content


def describe_the_legend():
    def it_has_one_chip_per_owning_guild_then_makerspace(client: Client):
        _login(client, "oc_legend")
        wood = _guild("Woodworking Guild", calendar_color="#c9823a")
        metal = _guild("Metal Guild")
        OrientationTypeFactory(guild=wood, name="Shop Basics")
        OrientationTypeFactory(guild=wood, name="Second Wood Type")
        OrientationTypeFactory(guild=None, equipment=EquipmentFactory(name="Lathe", guild=metal), name="Lathe")
        OrientationTypeFactory(guild=None, equipment=EquipmentFactory(name="Dock"), name="Dock Basics")
        GuildFactory(name="Ceramics Guild")  # owns no listed type: no chip
        content = client.get(f"{PAGE}?view=calendar").content
        assert _legend(content) == ["Metal Guild", "Woodworking Guild", "Makerspace"]
        assert f"isActive('{wood.pk}')".encode() in content
        assert b"--filter-color: #c9823a;" in content
        assert b"isActive('makerspace')" in content
        assert b"--filter-color: var(--hub-text-muted);" in content

    def it_has_no_makerspace_chip_without_a_standalone_items_type(client: Client):
        _login(client, "oc_no_makerspace")
        OrientationTypeFactory(guild=_guild("Only Guild"))
        assert _legend(client.get(f"{PAGE}?view=calendar").content) == ["Only Guild"]

    def it_draws_a_guilds_logo_on_its_chip(client: Client):
        _login(client, "oc_logo")
        guild = _guild("Ceramics Guild")
        assert guild.logo_prefix
        OrientationTypeFactory(guild=guild)
        content = client.get(f"{PAGE}?view=calendar").content
        assert f"img/guild_logos/{guild.logo_prefix}_color.svg".encode() in content

    def it_leaves_out_the_guild_calendars_own_chips(client: Client):
        _login(client, "oc_no_guild_chips")
        OrientationTypeFactory(guild=_guild("Plain Guild"))
        content = client.get(f"{PAGE}?view=calendar").content
        assert b"isActive('orientation')" not in content
        assert b"isActive('community')" not in content

    def it_files_no_chip_for_a_retired_type_a_member_still_holds(client: Client):
        user = _login(client, "oc_pinned")
        retired = OrientationTypeFactory(guild=_guild("Retired Guild"), is_active=False)
        OrientationBookingFactory(slot=_slot(retired), member=user.member, status=OrientationBooking.Status.CONFIRMED)
        content = client.get(f"{PAGE}?view=calendar").content
        assert _legend(content) == []
        # The List view still pins the member's card, as part 2 built it.
        assert f'id="orientation-type-{retired.pk}"'.encode() in content


def describe_the_events_partial():
    def it_requires_login(client: Client):
        response = client.get(EVENTS)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_is_named_for_the_page(client: Client):
        assert reverse("hub_orientations_calendar_events") == EVENTS

    def it_puts_the_type_on_the_chip_and_the_owner_and_seats_in_the_list(client: Client):
        _login(client, "oc_partial")
        orientation_type = OrientationTypeFactory(guild=_guild("Partial Guild"), name="Partial Basics")
        slot = _slot(orientation_type, seats=3)
        content = client.get(EVENTS).content
        assert b"pl-calendar-grid--week" in content
        assert set(_chip_titles(content)) == {"Partial Basics"}
        assert b"Partial Basics \xc2\xb7 Partial Guild \xc2\xb7 3 seats left" in content
        assert f'data-event-pk="{ORIENTATION_PK_OFFSET + slot.pk}"'.encode() in content

    def it_links_each_entry_to_its_types_card_in_the_same_tab(client: Client):
        _login(client, "oc_link")
        orientation_type = OrientationTypeFactory(guild=_guild("Link Guild"), name="Link Type")
        _slot(orientation_type)
        content = client.get(EVENTS).content.decode()
        card_path = f"/orientations/#orientation-type-{orientation_type.pk}"
        link = re.search(rf'<a href="{re.escape(card_path)}"[^>]*>', content)
        assert link is not None
        assert "target=" not in link.group(0)

    def it_shows_the_slots_time_range_and_its_owner_line(client: Client):
        _login(client, "oc_range")
        item = EquipmentFactory(name="Lathe", guild=_guild("Woodworking Guild"))
        orientation_type = OrientationTypeFactory(guild=None, equipment=item, name="Lathe Orientation")
        _slot(orientation_type)
        content = client.get(EVENTS).content
        assert b"10:00 AM \xe2\x80\x93 11:00 AM" in content
        assert b">Lathe \xc2\xb7 Woodworking Guild</div>" in content

    def it_leaves_out_a_full_slot_and_a_cancelled_one(client: Client):
        _login(client, "oc_absent")
        orientation_type = OrientationTypeFactory(guild=_guild("Absent Guild"), name="Absent Type")
        full = _slot(orientation_type, seats=1)
        OrientationBookingFactory(slot=full, status=OrientationBooking.Status.CONFIRMED)
        cancelled = _slot(orientation_type, is_cancelled=True)
        content = client.get(EVENTS).content
        for slot in (full, cancelled):
            assert f'data-event-pk="{ORIENTATION_PK_OFFSET + slot.pk}"'.encode() not in content
        assert _chip_titles(content) == []

    def it_drops_the_guild_calendars_feed_notes(client: Client):
        _login(client, "oc_notes")
        content = client.get(EVENTS).content
        assert b"pl-calendar-list-note" not in content
        assert b"pl-calendar-empty__hint" not in content
        assert b"pl-calendar-empty" in content

    def it_navigates_by_week_and_month_through_itself(client: Client):
        _login(client, "oc_nav")
        later = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(weeks=5)
        orientation_type = OrientationTypeFactory(guild=_guild("Nav Guild"), name="Later Type")
        _slot(orientation_type, starts_at=later, ends_at=later + timedelta(hours=1))
        assert _chip_titles(client.get(EVENTS).content) == []
        content = client.get(f"{EVENTS}?month_offset=1").content
        assert "Later Type" in _chip_titles(content)
        assert f"{EVENTS}?week_offset=0&amp;month_offset=2".encode() in content

    def it_treats_garbage_offsets_as_the_current_window(client: Client):
        _login(client, "oc_garbage")
        content = client.get(f"{EVENTS}?week_offset=soon&month_offset=1").content
        assert b'"week_offset": 0, "month_offset": 0, "event_page": 1' in content

    def it_returns_the_whole_shell_for_the_first_load(client: Client):
        _login(client, "oc_shell")
        OrientationTypeFactory(guild=_guild("Shell Guild"))
        content = client.get(f"{EVENTS}?shell=1").content
        assert b"guildCalFiltersOff-orientations" in content
        assert _legend(content) == ["Shell Guild"]
        assert b'id="pl-calendar-events-area"' in content


def describe_query_counts():
    def _seed(names: range) -> None:
        for n in names:
            guild = _guild(f"Count Guild {n}")
            orientation_type = OrientationTypeFactory(guild=guild, name=f"Count Type {n}")
            _slot(orientation_type)

    def it_builds_the_calendar_in_three_queries_for_one_type_or_six(django_assert_num_queries):
        _seed(range(1))
        orientations_calendar_context()  # warm the per process caches
        with django_assert_num_queries(3):
            assert len(orientations_calendar_context()["legend"]) == 1
        _seed(range(1, 6))
        with django_assert_num_queries(3):
            cal = orientations_calendar_context()
        assert len(cal["legend"]) == 6
        assert len(cal["month_events"]) == 6

    def it_answers_the_partial_in_the_same_queries_for_one_type_or_six(client: Client, django_assert_num_queries):
        _login(client, "oc_count")
        _seed(range(1))
        client.get(EVENTS)  # warm the per process caches
        with CaptureQueriesContext(connection) as one:
            client.get(EVENTS)
        _seed(range(1, 6))
        with django_assert_num_queries(len(one.captured_queries)):
            content = client.get(EVENTS).content
        assert len(_chip_titles(content)) >= 6


def describe_the_guild_page_calendar():
    def it_still_salts_its_keys_with_the_guild(client: Client):
        _login(client, "oc_guild_page")
        guild = _guild("Unchanged Guild")
        _slot(OrientationTypeFactory(guild=guild))
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content
        assert f"guildCalFiltersOff-{guild.pk}".encode() in content
        assert f"'guildCalFilters-{guild.pk}', 'guildCalCommunityRollout-{guild.pk}'".encode() in content
        assert b"guildCalFiltersOff-orientations" not in content
        # Its own legend keeps the Orientation and Events chips.
        assert b"isActive('orientation')" in content
        assert b"isActive('community')" in content
