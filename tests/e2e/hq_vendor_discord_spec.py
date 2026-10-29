"""End-to-end: Check Discord on Vendor Tables (#1014). An admin opens Discord Channel beside the vendor sheet and saves
the channel's id (one that is not digits comes back refused, the box open); Check Discord then appears beside Import
CSV, and pressing it loads the new posters over the list. Skip takes one away; Add as Request puts the other on the
list with the counts moved, and the panel says nothing is new. At a phone's width and a desktop's the page with the
panel never scrolls sideways.

The read is ``hq.vendor_discord.read_channel``, replaced here: the live server runs in this process, so nothing
reaches Discord. The service worker is blocked because it fetches navigations past ``page.route``; every wait is on
what the page shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from hq import vendor_discord
from hq.models import HubCard, VendorFloor, VendorTable
from hq.vendor_discord import ChannelRead
from membership.models import Member
from tests.hq.factories import EventHubFactory, HubCardFactory
from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "hq-vendor-discord-admin@example.com"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"
CHANNEL = "1234567890123456789"


def _message(message_id: int, author: dict[str, Any], content: str, at: str) -> dict[str, Any]:
    return {"id": str(message_id), "type": 0, "author": author, "content": content, "timestamp": at, "mentions": []}


MOSS = {"id": "7001", "username": "spec.moss", "global_name": "Moss Studio"}
WREN = {"id": "7002", "username": "specwren", "global_name": "Wren"}
MESSAGES = (
    _message(12, WREN, "Could I get a table? Prints and stickers, 6 ft.", "2026-10-26T18:05:00+00:00"),
    _message(11, MOSS, "Lovely market last year, see you all there!", "2026-10-25T02:40:00+00:00"),
)


@pytest.fixture
def browser_context_args(browser_context_args):
    """Block the service worker: it fetches a form navigation itself, past ``page.route``."""
    return {**browser_context_args, "service_workers": "block"}


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


@pytest.fixture
def discord() -> Iterator[None]:
    def read(channel_id: str) -> ChannelRead:
        return ChannelRead(channel_id=channel_id, name="holiday-bizarre", messages=MESSAGES, nicks={})

    with patch.object(vendor_discord, "read_channel", read):
        yield


def _vendors_page(page, live_server, login_via_code) -> str:
    """The open HQ's Vendor Tables door, an admin signed in; the page's URL."""
    hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
    HubCardFactory(hub=hub, kind=HubCard.Kind.VENDOR_TABLES, title="Vendor Tables", audience=HubCard.Audience.ADMINS)
    MembershipPlanFactory()
    login_via_code(ADMIN_EMAIL)
    member = Member.objects.get(user__username=ADMIN_EMAIL)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return f"{live_server.url}/hq/{hub.slug}/vendors/"


def it_sets_the_channel_then_adds_and_skips_posters(page, live_server, login_via_code, discord):
    url = _vendors_page(page, live_server, login_via_code)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(url)
    expect(page.locator("[data-hq-vt-discord-check]")).to_have_count(0)

    page.locator("[data-hq-vt-discord-channel] summary").click()
    page.get_by_label("Channel id").fill("#holiday-bizarre")
    page.locator("[data-hq-vt-discord-channel-submit]").click()
    expect(page.locator("[data-hq-vt-discord-channel-form]")).to_contain_text("digits only")
    assert not VendorFloor.objects.exists()

    page.get_by_label("Channel id").fill(CHANNEL)
    page.locator("[data-hq-vt-discord-channel-submit]").click()
    page.locator("[data-hq-vt-discord-check]").click()

    panel = page.locator("[data-hq-vt-discord]")
    expect(panel).to_contain_text("New in #holiday-bizarre")
    expect(panel.locator("[data-hq-vt-poster]")).to_have_count(2)
    expect(panel.locator("[data-hq-vt-poster]").first).to_contain_text("Wren")

    page.get_by_role("button", name="Skip Moss Studio").click()
    expect(panel.locator('[data-hq-vt-poster="7001"]')).to_have_count(0)

    page.get_by_role("button", name="Add Wren as a request").click()
    expect(page.locator("[data-hq-vt-discord-empty]")).to_have_text("Nothing new in #holiday-bizarre.")
    table = VendorTable.objects.get()
    assert (table.name, table.request_source) == ("Wren", "Discord")
    expect(page.locator(f'[data-hq-vt-row="{table.pk}"]')).to_be_visible()
    assert page.evaluate(NO_SIDEWAYS_SCROLL)


@pytest.mark.parametrize("width", [390, 1280])
def it_never_scrolls_the_page_sideways_with_the_panel_open(page, live_server, login_via_code, discord, width):
    url = _vendors_page(page, live_server, login_via_code)
    VendorFloor.objects.create(hub=HubCard.objects.get().hub, discord_channel_id=CHANNEL)
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(url)

    page.locator("[data-hq-vt-discord-check]").click()

    expect(page.locator("[data-hq-vt-poster]")).to_have_count(2)
    assert page.evaluate(NO_SIDEWAYS_SCROLL)
