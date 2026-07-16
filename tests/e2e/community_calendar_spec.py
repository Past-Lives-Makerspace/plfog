"""End-to-end: the Community Calendar colors a guild's events with its own color.

Guild-colored chips come from synced ``CalendarEvent`` rows (``source="guild"``) —
the iCal read-through cache the nightly sync fills from each guild's calendar feed.
The page reads stored rows only (no upstream fetch at render time), so seeding one
row exercises the whole pipeline: ``source_colors`` maps the guild's pk to its
``calendar_color``, the legend grows a filter chip, and the grid chip renders in
that color. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import CalendarEvent
from tests.membership.factories import GuildFactory, MembershipPlanFactory

MEMBER_EMAIL = "calendar-member@example.com"
GUILD_COLOR = "#aa3366"


def describe_community_calendar_guild_colors():
    def it_renders_the_guild_filter_chip_and_event_in_the_guild_color(live_server, page, login_via_code):
        # A plan must exist so the login signal auto-creates the member.
        MembershipPlanFactory()
        guild = GuildFactory(name="Ceramics Guild", calendar_color=GUILD_COLOR)
        CalendarEvent.objects.create(
            guild=guild,
            source=CalendarEvent.Source.GUILD,
            uid="e2e-glaze-night",
            title="Glaze Night",
            start_dt=timezone.now() + timedelta(days=2),
            end_dt=timezone.now() + timedelta(days=2, hours=2),
            fetched_at=timezone.now(),
        )

        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_community_calendar')}")

        # The guild's filter chip renders, carrying its color (--filter-color drives
        # both the dot and the active state; known guild names render a logo instead
        # of the dot, so assert the button's own style).
        chip = page.locator(".pl-calendar-filter", has_text=guild.name)
        expect(chip).to_be_visible()
        assert GUILD_COLOR in (chip.get_attribute("style") or "")

        # The event's grid chip carries the guild's color. Assert attachment, not
        # visibility: the chip renders in the week and/or month grid depending on
        # which week +2 days lands in, and only one grid view is shown at a time.
        colored_chip = page.locator(f'.pl-calendar-grid__chip[style*="{GUILD_COLOR}"][title*="Glaze Night"]')
        expect(colored_chip.first).to_be_attached()
