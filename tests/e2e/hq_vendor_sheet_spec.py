"""End-to-end: Vendor Tables pull the vendor sheet (#1013). An admin opens Vendor Sheet beside the space map capacity
and saves the sheet's link (a link that is not a Google Sheet comes back refused, the box open); Pull From Sheet Now
then appears beside Import CSV, and a pull lands back on the page with a toast, the last pull over the list and the
sheet's new row in it. A sheet that cannot be read shows why in the panel. At a phone's width and a desktop's the
page never scrolls sideways.

The fetch is ``hq.vendor_sheet.fetch_sheet_csv``, replaced here: the live server runs in this process, so nothing
reaches Google. The service worker is blocked because it fetches navigations past ``page.route``; every wait is on
what the page shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from hq import vendor_sheet
from hq.models import HubCard, VendorFloor, VendorTable
from hq.vendor_sheet import NOT_SHARED, SheetLink, SheetUnreadable
from membership.models import Member
from tests.hq.factories import EventHubFactory, HubCardFactory
from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "hq-vendor-sheet-admin@example.com"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1SpecVendorSheetIdAbCdEfGhIjKlMnOpQrStUvW/edit#gid=0"
SHEET = "Name,Table size,Status,What they sell\nSpec Juniper,8 ft,Requested,Cutting boards\nSpec Moss,huge,,Wallets\n"


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


class _Sheet:
    error = ""

    def __call__(self, link: SheetLink) -> bytes:
        if self.error:
            raise SheetUnreadable(self.error)
        return SHEET.encode()


@pytest.fixture
def sheet() -> Iterator[_Sheet]:
    fake = _Sheet()
    with patch.object(vendor_sheet, "fetch_sheet_csv", fake):
        yield fake


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


def it_saves_the_link_then_pulls_the_sheet_into_the_list(page, live_server, login_via_code, sheet):
    url = _vendors_page(page, live_server, login_via_code)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(url)
    expect(page.locator("[data-hq-vt-pull]")).to_have_count(0)

    page.locator("[data-hq-vt-sheet] summary").click()
    page.get_by_label("Sheet link").fill("https://example.com/vendors.csv")
    page.locator("[data-hq-vt-sheet-submit]").click()
    expect(page.locator("[data-hq-vt-sheet-form]")).to_contain_text("not a Google Sheets link")
    assert not VendorFloor.objects.exists()

    page.get_by_label("Sheet link").fill(SHEET_URL)
    page.locator("[data-hq-vt-sheet-submit]").click()
    expect(page.locator("[data-hq-vt-pull-when]")).to_contain_text("not pulled yet")

    page.locator("[data-hq-vt-pull]").click()

    expect(page.locator("[data-hq-vt-pull-summary]")).to_have_text(
        "Pulled from the sheet: 2 added, 0 updated, 0 unchanged. 1 problem to check."
    )
    expect(page.locator("[data-hq-vt-pull-problems]")).to_contain_text('Row 3: Table size "huge"')
    expect(page.locator("[data-hq-vt-pull-when]")).to_contain_text("Last pulled")
    juniper = VendorTable.objects.get(name="Spec Juniper")
    expect(page.locator(f'[data-hq-vt-row="{juniper.pk}"]')).to_be_visible()
    assert page.evaluate(NO_SIDEWAYS_SCROLL)


def it_shows_why_the_sheet_could_not_be_read(page, live_server, login_via_code, sheet):
    url = _vendors_page(page, live_server, login_via_code)
    VendorFloor.objects.create(hub=HubCard.objects.get().hub, sheet_url=SHEET_URL)
    sheet.error = NOT_SHARED
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(url)

    page.locator("[data-hq-vt-pull]").click()

    expect(page.locator("[data-hq-vt-pull-error]")).to_contain_text("not shared with anyone with the link")
    assert not VendorTable.objects.exists()


@pytest.mark.parametrize("width", [390, 1280])
def it_never_scrolls_the_page_sideways(page, live_server, login_via_code, sheet, width):
    url = _vendors_page(page, live_server, login_via_code)
    VendorFloor.objects.create(
        hub=HubCard.objects.get().hub,
        sheet_url=SHEET_URL,
        sheet_pull_error=NOT_SHARED,
    )
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(url)

    page.locator("[data-hq-vt-sheet] summary").click()
    expect(page.get_by_label("Sheet link")).to_have_value(SHEET_URL)
    assert page.evaluate(NO_SIDEWAYS_SCROLL)
