"""End to end: a guild lead cancels several Upcoming Times at once (issue #574).

The selection, the bar, the live confirm sentence and the hidden form are all Alpine on the
page, so only a real browser proves the whole chain: tick two of three slots, the bar counts
them, Cancel selected opens the confirm reading "Cancel 2 times?", and confirming lands back on
the Orientations tab with one slot left. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

LEAD_EMAIL = "bulk-cancel-lead@example.com"


def describe_bulk_cancel_on_upcoming_times():
    def it_cancels_the_ticked_times_and_keeps_the_rest(live_server, page, login_via_code):
        # A plan must exist so the login signal auto-creates the member the lead seat needs.
        MembershipPlanFactory()
        login_via_code(LEAD_EMAIL)
        user = get_user_model().objects.get(username=LEAD_EMAIL)
        lead = Member.objects.get(user=user)
        guild = GuildFactory(name="Print Guild", guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Press Orientation", duration_minutes=60)
        slots = []
        for days in (2, 3, 4):
            start = timezone.now() + timedelta(days=days)
            slots.append(
                OrientationSlotFactory(
                    guild=guild, orientation_type=orientation_type, starts_at=start, ends_at=start + timedelta(hours=1)
                )
            )

        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")
        rows = page.locator("[data-time-key]")
        expect(rows).to_have_count(3)
        bar = page.locator(".pl-slot-admin__bar")
        expect(bar).to_be_hidden()

        # Tick the first two; the bar appears and counts them.
        page.locator(f'[data-time-key="slot:{slots[0].pk}"] .pl-slot-admin__pick').check()
        page.locator(f'[data-time-key="slot:{slots[1].pk}"] .pl-slot-admin__pick').check()
        expect(bar).to_be_visible()
        expect(bar).to_contain_text("2 selected")

        # Cancel selected opens the one confirm, whose sentence reads the live selection.
        bar.get_by_role("button", name="Cancel selected", exact=True).click()
        modal = page.locator(".pl-modal", has_text="Cancel the selected times?")
        expect(modal).to_be_visible()
        expect(modal).to_contain_text("Cancel 2 times? Nobody is booked on them.")
        modal.get_by_role("button", name="Cancel selected", exact=True).click()

        # The POST redirects back to the tab (a boosted body swap on the same URL), so the
        # observable is the card itself: one row left, and the success message on the page.
        expect(page.locator("[data-time-key]")).to_have_count(1)
        expect(page.locator("body")).to_contain_text("Cancelled 2 times.")
        expect(page).to_have_url(re.compile(r"tab=orientations"))
        expect(page.locator(f'[data-time-key="slot:{slots[2].pk}"]')).to_have_count(1)

        assert [slot.pk for slot in guild.orientation_slots.upcoming()] == [slots[2].pk]
        for slot in slots[:2]:
            slot.refresh_from_db()
            assert slot.is_cancelled is True
