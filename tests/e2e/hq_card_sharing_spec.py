"""End-to-end: HQ card sharing. An admin shares the Ledger with a member as Team from the HQ page's Share panel;
that member then finds Holiday Bizarre in the sidebar, sees only the Ledger card on the HQ, logs an entry, and sees
no budget line, approval or price editor (those stay admin only).

In a real browser: the admin opens the Share modal (loaded over htmx), types the member's name into the Team picker,
picks them, saves (one POST) and sees the card's chip change in place with no reload. Then the member signs in
through the real login by code flow. The service worker is blocked because it fetches navigations past
``page.route``; every wait is on what the page shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from hq.models import BudgetLine, HubCard, LedgerEntry
from membership.models import Member
from tests.hq.factories import BudgetLineFactory, EventHubFactory, HubCardFactory
from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "hq-share-admin@example.com"
MEMBER_EMAIL = "hq-share-team@example.com"


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


def _person(email: str, name: str, *, admin: bool = False) -> Member:
    """A member with a login (the user signal provisions the member) and a preferred name."""
    MembershipPlanFactory()
    user = get_user_model().objects.create_user(username=email, email=email)
    member = user.member
    member.preferred_name = name
    member.status = Member.Status.ACTIVE
    if admin:
        member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["preferred_name", "status", "fog_role"])
    return member


def _seed() -> tuple[HubCard, HubCard, BudgetLine]:
    """The open HQ with the Ledger (admins only, nobody on it) and a Marketing door the member is not given."""
    hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
    ledger = HubCardFactory(hub=hub, kind=HubCard.Kind.LEDGER, title="PL Ledger", audience=HubCard.Audience.ADMINS)
    marketing = HubCardFactory(
        hub=hub, kind=HubCard.Kind.MARKETING, title="Marketing Tracker", audience=HubCard.Audience.ADMINS
    )
    line = BudgetLineFactory(hub=hub, name="Kegs", budget_cents=60_000)
    return ledger, marketing, line


def describe_sharing_the_ledger_with_a_member_as_team():
    def it_gives_them_the_hq_in_the_sidebar_the_ledger_alone_and_no_settings(live_server, page, login_via_code):
        ledger, marketing, line = _seed()
        _person(ADMIN_EMAIL, "Lee Admin", admin=True)
        member = _person(MEMBER_EMAIL, "Kate Ledgerkeeper")

        # The admin shares the Ledger from the HQ page's Share panel.
        login_via_code(ADMIN_EMAIL)
        page.goto(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))
        chip = page.locator(f"#hq-share-chip-{ledger.pk}")
        expect(chip).to_have_text("Admins only")
        page.locator(f'[data-hq-card-pk="{ledger.pk}"] [data-hq-card-share]').click()

        form = page.locator(f'[data-hq-share-form="{ledger.pk}"]')
        expect(form).to_be_visible()
        # A Team-only door: Admins only is its one audience, and it has no Can open list.
        expect(form.locator('input[name="audience"]')).to_have_count(1)
        expect(form.locator('[data-hq-people="can_open"]')).to_have_count(0)
        team = form.locator('[data-hq-people="team"]')
        team.locator("[data-hq-people-input]").fill("Ledgerkeeper")
        option = team.locator(f'[data-hq-person-option="{member.pk}"]')
        expect(option).to_be_visible()
        option.click()
        expect(team.locator(f'[data-hq-people-chips] [data-hq-person="{member.pk}"]')).to_contain_text(
            "Kate Ledgerkeeper"
        )
        form.locator("[data-hq-share-save]").click()

        expect(chip).to_have_text("Admins only · 1 person")
        expect(form).to_be_hidden()
        assert list(HubCard.objects.get(pk=ledger.pk).team.all()) == [member]

        # The member signs in and finds the HQ in the sidebar.
        page.context.clear_cookies()
        login_via_code(MEMBER_EMAIL)
        page.goto(live_server.url + reverse("hub_home"))
        entry = page.locator('nav.hub-sidebar__nav a[data-nav="hq"]')
        expect(entry).to_be_visible()
        entry.click()

        # Only the Ledger card is on their HQ.
        expect(page.locator(f'[data-hq-card-pk="{ledger.pk}"]')).to_be_visible()
        expect(page.locator(f'[data-hq-card-pk="{marketing.pk}"]')).to_have_count(0)
        expect(page.locator("[data-hq-card-pk]")).to_have_count(1)
        expect(page.locator("[data-hq-card-share]")).to_have_count(0)
        page.locator(f'[data-hq-card-pk="{ledger.pk}"] a[data-hq-card-link]').click()

        # The Team's work is there; the settings are not.
        log = page.locator("[data-hq-lg-log-form]")
        expect(log).to_be_visible()
        for setting in (
            "[data-hq-lg-approval-form]",
            "[data-hq-lg-prices-form]",
            "[data-hq-lg-ticket-prices-form]",
            "[data-hq-lg-line-add-form]",
            "[data-hq-lg-line-edit]",
            "[data-hq-lg-line-form]",
        ):
            expect(page.locator(setting)).to_have_count(0)

        log.locator('select[name="entry-line"]').select_option(str(line.pk))
        log.locator('input[name="entry-source"]').fill("Cascade Brewing")
        log.locator('input[name="entry-amount"]').fill("250")
        log.locator("[data-hq-lg-log-submit]").click()

        expect(page.locator(".plt-msg").last).to_contain_text("Added to the ledger.")
        entry_row = LedgerEntry.objects.get()
        assert (entry_row.source, entry_row.amount_cents, entry_row.created_by) == ("Cascade Brewing", 25_000, member)
