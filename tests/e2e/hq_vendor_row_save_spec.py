"""End-to-end: a Vendor Tables row saves itself as it changes, with no Save button (#1048).

An organiser picks Confirmed in one row's Status and nothing else; the row is confirmed in the database, the band
counts it, and the HQ's Member tables goal shows it, at desktop width and at 390 px with no sideways scroll. A text
box saves when it is left, while the next box keeps its focus and what is half typed in it; two saves of one row
both land; a stale page and an invalid email are refused in the row. A row renamed to an active member fills its blank
contacts from their record (#1049), and only those boxes come back.

The service worker is blocked because it fetches navigations past ``page.route``; every wait is on what the page
shows (the row's state slot carries the saved status and stamp). Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from hq.models import EventHub, HubCard, HubGoal, VendorTable
from membership.models import Member
from tests.hq.factories import EventHubFactory, HubCardFactory, VendorTableFactory
from tests.membership.factories import MemberFactory, MembershipPlanFactory

ADMIN_EMAIL = "hq-vendor-row-admin@example.com"
PHONE = {"width": 390, "height": 844}


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
def hub(transactional_db) -> EventHub:
    hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
    HubCardFactory(hub=hub, kind=HubCard.Kind.VENDOR_TABLES, title="Vendor Tables", audience=HubCard.Audience.ADMINS)
    HubGoal.objects.create(hub=hub, label="Member tables", kind=HubGoal.Kind.MEMBER_TABLES)
    return hub


@pytest.fixture
def organiser(login_via_code) -> None:
    MembershipPlanFactory()
    login_via_code(ADMIN_EMAIL)
    member = Member.objects.get(user__username=ADMIN_EMAIL)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()


def _open(page: Page, live_server, hub: EventHub) -> None:
    page.goto(f"{live_server.url}/hq/{hub.slug}/vendors/")
    expect(page.locator("[data-hq-vt-band]")).to_be_visible()


def _stamp(page: Page, table: VendorTable):
    return page.locator(f'#hq-vt-state-{table.pk} input[name="vendor-{table.pk}-updated_at"]')


def _confirms_with_no_tap(page: Page, live_server, hub: EventHub, table: VendorTable) -> None:
    confirmed = page.locator('[data-hq-vt-tile="confirmed"] [data-hq-vt-figure]')
    expect(confirmed).to_have_text("1")
    expect(page.locator("[data-hq-vt-save]")).to_have_count(0)
    expect(page.get_by_role("button", name="Save Spec Moss")).to_have_count(0)

    page.locator(f'[data-hq-vt-row="{table.pk}"]').get_by_label("Status for Spec Moss").select_option("confirmed")

    expect(page.locator(f'#hq-vt-state-{table.pk}[data-hq-vt-status="confirmed"]')).to_be_attached()
    expect(confirmed).to_have_text("2")
    table.refresh_from_db()
    assert table.status == VendorTable.Status.CONFIRMED
    page.goto(f"{live_server.url}{hub.get_absolute_url()}")
    goal = page.locator(".pl-hq-goal", has_text="Member tables").locator("[data-hq-goal-count]")
    expect(goal).to_have_text("2")


def it_confirms_a_row_with_no_save_tap(page, live_server, hub, organiser):
    VendorTableFactory(hub=hub, name="Spec Juniper", status=VendorTable.Status.CONFIRMED)
    table = VendorTableFactory(hub=hub, name="Spec Moss")

    _open(page, live_server, hub)
    _confirms_with_no_tap(page, live_server, hub, table)


def it_confirms_a_row_at_phone_width_with_no_sideways_scroll(page, live_server, hub, organiser):
    VendorTableFactory(hub=hub, name="Spec Juniper", status=VendorTable.Status.CONFIRMED)
    table = VendorTableFactory(hub=hub, name="Spec Moss")
    page.set_viewport_size(PHONE)

    _open(page, live_server, hub)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # The table scrolls sideways inside its card; once Status is in view, nothing else needs a tap.
    status = page.locator(f'[data-hq-vt-row="{table.pk}"]').get_by_label("Status for Spec Moss")
    status.scroll_into_view_if_needed()
    box = status.bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"]
    assert page.evaluate("window.scrollX") == 0
    _confirms_with_no_tap(page, live_server, hub, table)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def it_saves_a_text_box_when_it_is_left_and_keeps_the_next_box_as_typed(page, live_server, hub, organiser):
    table = VendorTableFactory(hub=hub, name="Spec Moss", sells="Mugs", discord_handle="")
    other = VendorTableFactory(hub=hub, name="Spec Fern", needs="Power")

    _open(page, live_server, hub)
    row = page.locator(f'[data-hq-vt-row="{table.pk}"]')
    page_stamp = _stamp(page, table).input_value()
    row.get_by_label("Phone for Spec Moss").fill("503-555-0199")
    discord = row.get_by_label("Discord handle for Spec Moss")
    discord.click()  # leaving Phone saves the row; Discord is typed in while the save lands
    discord.press_sequentially("spec_ha")

    expect(_stamp(page, table)).not_to_have_value(page_stamp)
    expect(discord).to_be_focused()
    expect(discord).to_have_value("spec_ha")
    table.refresh_from_db()
    assert (table.phone, table.discord_handle) == ("503-555-0199", "")

    # Typing in another row while this one saves leaves that row's box as typed and unsaved.
    needs = page.locator(f'[data-hq-vt-row="{other.pk}"]').get_by_label("Needs for Spec Fern")
    needs.click()
    needs.fill("Shade")
    stamp = _stamp(page, table).input_value()
    expect(_stamp(page, table)).not_to_have_value(stamp)  # leaving Discord saved Spec Moss again
    expect(needs).to_be_focused()
    expect(needs).to_have_value("Shade")
    table.refresh_from_db()
    other.refresh_from_db()
    assert table.discord_handle == "spec_ha"
    assert other.needs == "Power"


def it_lands_two_saves_of_one_row_in_a_row(page, live_server, hub, organiser):
    table = VendorTableFactory(hub=hub, name="Spec Moss", table_no="")

    _open(page, live_server, hub)
    row = page.locator(f'[data-hq-vt-row="{table.pk}"]')
    row.get_by_label("Status for Spec Moss").select_option("confirmed")
    number = row.get_by_label("Table # for Spec Moss")
    number.fill("12")  # straight after, while the status save may still be in flight
    number.press("Tab")

    expect(page.locator(f'#hq-vt-state-{table.pk}[data-hq-vt-status="confirmed"]')).to_be_attached()
    expect(page.locator("[data-hq-vt-row-error]")).to_have_count(0)
    page.wait_for_load_state("networkidle")
    table.refresh_from_db()
    assert (table.status, table.table_no) == (VendorTable.Status.CONFIRMED, "12")

    stamp = _stamp(page, table).input_value()
    row.get_by_label("Status for Spec Moss").select_option("waitlist")
    expect(_stamp(page, table)).not_to_have_value(stamp)
    expect(page.locator("[data-hq-vt-row-error]")).to_have_count(0)
    table.refresh_from_db()
    assert (table.status, table.table_no) == (VendorTable.Status.WAITLIST, "12")


def it_refuses_a_change_from_an_older_page_and_shows_the_saved_row(page, live_server, hub, organiser):
    table = VendorTableFactory(hub=hub, name="Spec Moss", needs="Power")

    _open(page, live_server, hub)
    saved = VendorTable.objects.get(pk=table.pk)  # someone else saves the row after this page loaded
    saved.needs = "A wall"
    saved.save()
    page.locator(f'[data-hq-vt-row="{table.pk}"]').get_by_label("Status for Spec Moss").select_option("cancelled")

    row = page.locator(f'[data-hq-vt-row="{table.pk}"]')
    expect(row.locator("[data-hq-vt-row-error]")).to_contain_text("Someone else changed this table.")
    expect(row.get_by_label("Needs for Spec Moss")).to_have_value("A wall")
    expect(row.get_by_label("Status for Spec Moss")).to_have_value("requested")
    table.refresh_from_db()
    assert (table.needs, table.status) == ("A wall", VendorTable.Status.REQUESTED)

    # The whole row came back out of band; once htmx has settled it, a change saves again from the fresh row.
    expect(row).not_to_have_class(re.compile(r"htmx-settling"))
    row.get_by_label("Status for Spec Moss").select_option("confirmed")
    expect(page.locator(f'#hq-vt-state-{table.pk}[data-hq-vt-status="confirmed"]')).to_be_attached()
    table.refresh_from_db()
    assert (table.needs, table.status) == ("A wall", VendorTable.Status.CONFIRMED)


def it_shows_an_invalid_email_in_the_row_and_saves_nothing(page, live_server, hub, organiser):
    table = VendorTableFactory(hub=hub, name="Spec Moss", email="ok@spec.example")

    _open(page, live_server, hub)
    row = page.locator(f'[data-hq-vt-row="{table.pk}"]')
    email = row.get_by_label("Email for Spec Moss")
    # The form is novalidate, so the browser never holds the save silently; the server's error is the one shown.
    email.fill("nope")
    email.press("Tab")

    expect(row.locator('[data-hq-vt-field-error="email"]')).to_contain_text("Email for Spec Moss:")
    expect(email).to_have_value("nope")
    table.refresh_from_db()
    assert table.email == "ok@spec.example"


def it_shows_a_member_vendors_filled_contacts_and_keeps_the_box_being_typed_in(page, live_server, hub, organiser):
    """#1049: renaming a row to an active member's name fills its blank phone and Discord handle from their record;
    the answer swaps those two boxes alone, so the box typed in meanwhile keeps its focus and text, and its save
    carries the new stamp and is taken."""
    MemberFactory(full_legal_name="Spec Odile Farrow", phone="503-555-0161", discord_handle="odie.makes", user=None)
    table = VendorTableFactory(hub=hub, name="Spec Placeholder", email="typed@odie.example")

    _open(page, live_server, hub)
    row = page.locator(f'[data-hq-vt-row="{table.pk}"]')
    page_stamp = _stamp(page, table).input_value()
    row.get_by_label("Artist or group for Spec Placeholder").fill("Spec Odile Farrow")
    sells = row.get_by_label("What they sell for Spec Placeholder")
    sells.click()  # leaving Name saves the row; Sells is typed in while the fill lands
    sells.press_sequentially("Rin")

    expect(row.get_by_label("Phone for Spec Placeholder")).to_have_value("503-555-0161")
    expect(row.get_by_label("Discord handle for Spec Placeholder")).to_have_value("odie.makes")
    expect(row.get_by_label("Email for Spec Placeholder")).to_have_value("typed@odie.example")
    expect(_stamp(page, table)).not_to_have_value(page_stamp)
    expect(sells).to_be_focused()
    expect(sells).to_have_value("Rin")

    stamp = _stamp(page, table).input_value()
    sells.press_sequentially("gs")
    sells.press("Tab")
    expect(_stamp(page, table)).not_to_have_value(stamp)
    expect(row.locator("[data-hq-vt-row-error]")).to_have_count(0)
    table.refresh_from_db()
    assert (table.name, table.sells, table.email, table.phone, table.discord_handle) == (
        "Spec Odile Farrow",
        "Rings",
        "typed@odie.example",
        "503-555-0161",
        "odie.makes",
    )
