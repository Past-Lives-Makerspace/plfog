"""BDD specs for the dedicated Orientations tab on the guild detail page.

The orientation booking partial moved out of the Guild Calendar tab into its own
Orientations tab, gated on ``show_orientation``. The Guild Calendar keeps a one-line
pointer to it. Legacy deep links (``?tab=orientations`` / ``#guild-orientation``) map to
the new tab via the page's ``x-init``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _member(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    return user


def describe_orientations_tab():
    def it_shows_the_tab_and_panel_when_orientation_is_enabled(client: Client):
        _member("ot1")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        client.login(username="ot1", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content
        # The tab button and its panel both key off section === 'orientations'.
        assert b"section === 'orientations'" in content
        assert b">Orientations</button>" in content
        # The booking partial (its anchor) lives on the page (Alpine keeps every panel in the DOM).
        assert b'id="guild-orientation"' in content

    def it_omits_the_tab_when_orientation_is_disabled(client: Client):
        _member("ot2")
        guild = GuildFactory()  # no orientation settings enabled → show_orientation False
        client.login(username="ot2", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content
        assert b"section === 'orientations'" not in content
        assert b">Orientations</button>" not in content

    def it_maps_the_deep_link_in_x_init_only_when_enabled(client: Client):
        _member("ot3")
        enabled = GuildFactory()
        GuildOrientationSettingsFactory(guild=enabled, is_enabled=True)
        disabled = GuildFactory()
        client.login(username="ot3", password="pass")
        on = client.get(reverse("hub_guild_detail", args=[enabled.slug])).content
        off = client.get(reverse("hub_guild_detail", args=[disabled.slug])).content
        assert b"t === 'orientations'" in on
        assert b"t === 'orientations'" not in off

    def it_points_from_the_calendar_to_the_orientations_tab(client: Client):
        _member("ot4")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        client.login(username="ot4", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content
        assert b"Open the Orientations tab." in content


def describe_per_type_sections():
    """The Orientations tab groups slots by orientation type (issue #282)."""

    def _enabled_guild():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=True)
        return guild

    def it_renders_a_section_per_active_type_with_its_details(client: Client):
        _member("pt1")
        guild = _enabled_guild()
        basics = OrientationTypeFactory(
            guild=guild, name="Shop Basics", duration_minutes=30, description="Doors and dust collection"
        )
        lathe = OrientationTypeFactory(guild=guild, name="Lathe Cert", duration_minutes=90, price_cents=1500)
        OrientationSlotFactory(guild=guild, orientation_type=basics)
        OrientationSlotFactory(guild=guild, orientation_type=lathe)
        client.login(username="pt1", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "Shop Basics" in content
        assert "Lathe Cert" in content
        assert "Doors and dust collection" in content
        assert "30 min" in content
        assert "90 min" in content
        assert "$15" in content  # the paid type's price chip

    def it_hides_a_retired_types_section(client: Client):
        _member("pt2")
        guild = _enabled_guild()
        OrientationTypeFactory(guild=guild, name="Live Walkthrough")
        OrientationTypeFactory(guild=guild, name="Retired Walkthrough", is_active=False)
        client.login(username="pt2", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "Live Walkthrough" in content
        assert "Retired Walkthrough" not in content

    def it_still_offers_the_other_type_when_one_is_completed(client: Client):
        user = _member("pt3")
        guild = _enabled_guild()
        basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
        lathe = OrientationTypeFactory(guild=guild, name="Lathe Cert")
        done = OrientationSlotFactory(guild=guild, orientation_type=basics)
        OrientationBookingFactory(slot=done, member=user.member).mark_completed()
        open_slot = OrientationSlotFactory(guild=guild, orientation_type=lathe)
        client.login(username="pt3", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        # The completed type shows its done state; the other still shows its bookable slot.
        assert "completed this orientation" in content
        assert reverse("hub_orientation_book", args=[open_slot.pk]) in content

    def it_shows_the_every_orientation_done_note_when_all_types_are_complete(client: Client):
        user = _member("pt4")
        guild = _enabled_guild()
        basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
        slot = OrientationSlotFactory(guild=guild, orientation_type=basics)
        OrientationBookingFactory(slot=slot, member=user.member).mark_completed()
        client.login(username="pt4", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "completed every orientation this guild offers" in content

    def it_shows_the_setup_empty_state_with_no_active_types(client: Client):
        _member("pt5")
        guild = _enabled_guild()
        client.login(username="pt5", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "No orientations are set up yet" in content

    def it_puts_the_type_picker_on_the_custom_request_form(client: Client):
        _member("pt6")
        guild = _enabled_guild()
        basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
        client.login(username="pt6", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        form = response.context["custom_request_form"]
        assert list(form.fields["orientation_type"].queryset) == [basics]


def describe_slot_cap():
    def it_no_longer_caps_the_guild_page_at_thirty_slots(client: Client):
        _member("cap1")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
        for hours in range(31):
            starts = timezone.now() + timedelta(days=2, hours=hours)
            OrientationSlotFactory(
                guild=guild, orientation_type=basics, starts_at=starts, ends_at=starts + timedelta(hours=1)
            )
        client.login(username="cap1", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        # No cap for either owner: the five per page pager bounds the view.
        assert len(response.context["orientation_sections"][0]["slots"]) == 31


def describe_query_count():
    def it_renders_a_dozen_slots_in_as_many_queries_as_two(client: Client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        _member("qc1")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
        client.login(username="qc1", password="pass")
        url = reverse("hub_guild_detail", args=[guild.slug])

        def make_slot(hours: int):
            starts = timezone.now() + timedelta(days=2, hours=hours)
            return OrientationSlotFactory(
                guild=guild, orientation_type=basics, starts_at=starts, ends_at=starts + timedelta(hours=1), seats=2
            )

        def count_queries() -> int:
            client.get(url)  # warm the session and per-request caches so both samples are steady state
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(url).status_code == 200
            return len(ctx.captured_queries)

        for hours in (0, 1):
            OrientationBookingFactory(slot=make_slot(hours))
        with_two = count_queries()
        for hours in range(2, 12):
            make_slot(hours)
        assert count_queries() == with_two
