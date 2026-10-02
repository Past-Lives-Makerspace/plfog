"""End-to-end: the event editor keeps saying what a save and each reminder will send.

Two live parts that a Django-client spec cannot see. The Save button is relabelled by Alpine
as the schedule toggle and the "Announce at" time change, and the reminder toggles are swapped
by htmx for a copy with fresh send times whenever the start changes, keeping any toggle the
person had already flipped. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from tests.membership.factories import GuildFactory, MembershipPlanFactory

LEAD_EMAIL = "send-times-lead@example.com"
SAVE = "#event-save"
TOGGLES = "#event-send-toggles"


def _seed_lead() -> int:
    MembershipPlanFactory()  # so the user signal provisions the member this then makes a lead
    user = User.objects.create_user(username=LEAD_EMAIL, email=LEAD_EMAIL)
    return GuildFactory(guild_lead=user.member).pk


def _evening(days: int) -> datetime:
    return (timezone.localtime() + timedelta(days=days)).replace(hour=18, minute=0, second=0, microsecond=0)


def _time(at: datetime) -> str:
    return f"{at.strftime('%a, %b %-d')} at {at.strftime('%-I:%M %p')}"


def describe_the_event_editor():
    def it_names_what_the_save_will_do(live_server, page, login_via_code):
        guild_pk = _seed_lead()
        login_via_code(LEAD_EMAIL)
        page.goto(live_server.url + reverse("hub_guild_event_add", args=[guild_pk]))
        expect(page.locator(SAVE)).to_have_text("Save and announce")

        page.locator(".pl-toggle-row", has_text="Schedule the announcement for later").locator(".pl-toggle").click()
        page.fill("#id_publish_at", _evening(3).strftime("%Y-%m-%dT%H:%M"))
        page.locator("#id_publish_at").dispatch_event("change")
        expect(page.locator(SAVE)).to_have_text("Save and schedule")

        page.locator(".pl-toggle-row", has_text="Schedule the announcement for later").locator(".pl-toggle").click()
        expect(page.locator(SAVE)).to_have_text("Save and announce")

    def it_times_each_reminder_for_the_start_being_typed(live_server, page, login_via_code):
        guild_pk = _seed_lead()
        login_via_code(LEAD_EMAIL)
        page.goto(live_server.url + reverse("hub_guild_event_add", args=[guild_pk]))
        expect(page.locator(TOGGLES)).to_contain_text("Send members a reminder 7 days before it starts.")

        page.locator(".pl-toggle-row", has_text="Remind 1d").locator(".pl-toggle").click()
        start = _evening(10)
        page.fill("#id_starts_at", start.strftime("%Y-%m-%dT%H:%M"))
        page.locator("#id_starts_at").dispatch_event("change")

        expect(page.locator(TOGGLES)).to_contain_text(_time(start - timedelta(days=7)))
        expect(page.locator(TOGGLES)).to_contain_text(_time(start - timedelta(days=1)))
        expect(page.locator('input[name="remind_1d"]')).to_be_checked()
        expect(page.locator('input[name="remind_7d"]')).not_to_be_checked()
