"""End-to-end: Vendor Tables' Import CSV (#1012). An organiser opens Import CSV beside Download CSV, picks a file in
the modal and imports it; the page comes back with the answer over the list and each new row carrying the sheet's
columns (Source, Request source, Requested on, Discord handle) as controls of its own form. At a phone's width and
a desktop's, the page never scrolls sideways (the table scrolls inside its card) and the modal fits the screen.

The service worker is blocked because it fetches navigations past ``page.route``; every wait is on what the page
shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from hq.models import HubCard, VendorTable
from membership.models import Member
from tests.hq.factories import EventHubFactory, HubCardFactory
from tests.membership.factories import GuildFactory, MembershipPlanFactory

ADMIN_EMAIL = "hq-vendor-import-admin@example.com"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"
SHEET = (
    "Name,Guild,Table size,Status,What they sell,Setup time,Table #,Email,Phone,Source,Requested on,"
    "Request source,Discord handle\n"
    '"Spec Juniper","Wood","8 ft","Confirmed","Cutting boards","Fri Dec 4, 3 to 5 PM","7","juniper@example.com","",'
    '"Asked Lee on Oct 1","10/21/2026","Asked Lee","juniper.makes"\n'
    '"Spec Moss","Leather","6 ft","Requested","Wallets","","","","","Asked in Discord","2026-10-24","Discord","N/A"\n'
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


def _vendors_page(page, live_server, login_via_code) -> str:
    """The open HQ's Vendor Tables door, an admin signed in; the page's URL."""
    hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
    HubCardFactory(hub=hub, kind=HubCard.Kind.VENDOR_TABLES, title="Vendor Tables", audience=HubCard.Audience.ADMINS)
    GuildFactory(name="Woodworking Guild")
    MembershipPlanFactory()
    login_via_code(ADMIN_EMAIL)
    member = Member.objects.get(user__username=ADMIN_EMAIL)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return f"{live_server.url}/hq/{hub.slug}/vendors/"


def it_imports_a_csv_through_the_modal_and_shows_the_answer_and_the_new_columns(page, live_server, login_via_code):
    url = _vendors_page(page, live_server, login_via_code)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(url)

    page.locator("[data-hq-vt-import-open]").click()
    modal = page.locator("[data-hq-vt-import-form]")
    expect(modal).to_be_visible()
    page.locator("#hq-vt-import-file").set_input_files(
        files=[{"name": "kate.csv", "mimeType": "text/csv", "buffer": SHEET.encode()}]
    )
    expect(page.locator("[data-hq-vt-import-label]")).to_have_text("kate.csv")
    page.locator("[data-hq-vt-import-submit]").click()

    answer = page.locator("[data-hq-vt-import-answer]")
    expect(answer).to_contain_text("Import done: 2 added, 0 updated, 0 unchanged. 1 problem to check.")
    expect(answer).to_contain_text('Row 3: Guild "Leather" is not one of the active guilds.')
    juniper = VendorTable.objects.get(name="Spec Juniper")
    row = page.locator(f'[data-hq-vt-row="{juniper.pk}"]')
    expect(row.get_by_label("Source for Spec Juniper", exact=True)).to_have_value("Asked Lee on Oct 1")
    expect(row.get_by_label("Request source for Spec Juniper")).to_have_value("Asked Lee")
    expect(row.get_by_label("Requested on for Spec Juniper")).to_have_value("2026-10-21")
    expect(row.get_by_label("Discord handle for Spec Juniper")).to_have_value("juniper.makes")
    assert page.evaluate(NO_SIDEWAYS_SCROLL)


def it_saves_a_new_column_with_the_rows_own_save(page, live_server, login_via_code):
    url = _vendors_page(page, live_server, login_via_code)
    page.set_viewport_size({"width": 1280, "height": 900})
    table = VendorTable.objects.create(hub=HubCard.objects.get().hub, name="Spec Wren")
    page.goto(url)

    row = page.locator(f'[data-hq-vt-row="{table.pk}"]')
    row.get_by_label("Discord handle for Spec Wren").fill("wren.pots")
    row.get_by_label("Requested on for Spec Wren").fill("2026-10-01")
    row.get_by_label("Save Spec Wren").click()

    expect(page.locator(".plt-toast")).to_contain_text("Saved.")
    table.refresh_from_db()
    assert (table.discord_handle, table.requested_on.isoformat()) == ("wren.pots", "2026-10-01")


@pytest.mark.parametrize("width", [390, 1280])
def it_never_scrolls_the_page_sideways_and_the_modal_fits(page, live_server, login_via_code, width):
    url = _vendors_page(page, live_server, login_via_code)
    VendorTable.objects.create(
        hub=HubCard.objects.get().hub,
        name="Spec Wide Studio",
        note="From Kate's sign-up doc. Confirm who in the guild is running this table.",
        request_source="Kate's sign-up doc",
        discord_handle="Ⓩinnia 🎃 [PRNT]",
    )
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(url)

    assert page.evaluate(NO_SIDEWAYS_SCROLL)
    page.locator("[data-hq-vt-import-open]").click()
    box = page.locator("[data-hq-vt-import-form]").bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert page.evaluate(NO_SIDEWAYS_SCROLL)
