"""End-to-end: the Holiday Bizarre HQ and its volunteer sign-up (Track C, parts 1 and 2).

In a real browser: a signed out visitor opens the HQ and sees the public door but not the
admins one, opens Volunteer Sign-up, saves Your Info, signs up for a shift over htmx (the row
flips to Remove Me and the band's figures move in place), then removes themself. A member
signs in through the real login by code flow and finds their name, email and phone already in
Your Info. The service worker is blocked because it fetches navigations past ``page.route``;
every wait is on what the page shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from hq.models import HubCard, ShiftSignup, VolunteerShift
from membership.models import Member
from tests.hq.factories import EventHubFactory, HubCardFactory, VolunteerShiftFactory
from tests.membership.factories import MembershipPlanFactory

MEMBER_EMAIL = "hb-volunteer@example.com"


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


def _seed_hq() -> tuple[HubCard, HubCard, VolunteerShift]:
    """The open HQ with a public Volunteer Sign-up door, an admins only door and two shifts."""
    hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
    volunteers = HubCardFactory(
        hub=hub,
        kind=HubCard.Kind.VOLUNTEERS,
        audience=HubCard.Audience.PUBLIC,
        title="Volunteer Sign-up",
        button_text="Sign up to volunteer",
    )
    ledger = HubCardFactory(hub=hub, title="PL Ledger", audience=HubCard.Audience.ADMINS)
    shift = VolunteerShiftFactory(
        hub=hub, phase=VolunteerShift.Phase.BEFORE, role="Flyer runner", when_text="November", need=6
    )
    VolunteerShiftFactory(hub=hub, phase=VolunteerShift.Phase.DURING, role="Door and check-in", need=2)
    return volunteers, ledger, shift


def _seed_member() -> Member:
    """A member with a login, a preferred name and a phone, for the prefill."""
    MembershipPlanFactory()  # so the user signal provisions the member
    user = get_user_model().objects.create_user(username=MEMBER_EMAIL, email=MEMBER_EMAIL)
    member = user.member
    member.preferred_name = "Lee Organiser"
    member.phone = "503 555 0142"
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["preferred_name", "phone", "status"])
    return member


def describe_a_signed_out_visitor():
    def it_opens_the_hq_saves_their_info_signs_up_and_removes_themself(live_server, page):
        volunteers, ledger, shift = _seed_hq()
        page.goto(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))

        expect(page.locator(f'[data-hq-card-pk="{volunteers.pk}"]')).to_be_visible()
        expect(page.locator(f'[data-hq-card-pk="{ledger.pk}"]')).to_have_count(0)
        page.locator(f'[data-hq-card-pk="{volunteers.pk}"] a[data-hq-card-link]').click()

        expect(page.locator("[data-hq-volunteers]")).to_be_visible()
        expect(page.locator("[data-hq-vol-empty]")).to_be_visible()
        expect(page.locator(f'[data-hq-shift="{shift.pk}"] button[data-hq-shift-signup]')).to_be_disabled()
        expect(page.locator("[data-hq-band-people]")).to_have_text("0")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("0 / 8")

        page.fill('input[name="name"]', "Pat Guest")
        page.fill('input[name="email"]', "pat@example.com")
        page.fill('input[name="phone"]', "503 555 0100")
        page.locator("[data-hq-vol-save]").click()

        expect(page.locator("[data-hq-vol-saved]")).to_be_visible()
        row = page.locator(f'[data-hq-shift="{shift.pk}"]')
        expect(row.locator("button[data-hq-shift-signup]")).to_be_enabled()
        row.locator("button[data-hq-shift-signup]").click()

        expect(row.locator("button[data-hq-shift-remove]")).to_be_visible()
        expect(page.locator("[data-hq-band-people]")).to_have_text("1")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("1 / 8")
        expect(row.locator("[data-hq-shift-filled]")).to_have_text("1 of 6 filled")
        expect(page.locator(".plt-msg").last).to_contain_text("You are on Flyer runner")
        signup = ShiftSignup.objects.get()
        assert (signup.email, signup.name, signup.member) == ("pat@example.com", "Pat Guest", None)

        row.locator("button[data-hq-shift-remove]").click()

        expect(row.locator("button[data-hq-shift-signup]")).to_be_visible()
        expect(page.locator("[data-hq-band-people]")).to_have_text("0")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("0 / 8")
        assert not ShiftSignup.objects.exists()


def describe_a_member():
    def it_finds_their_info_already_filled_in(live_server, page, login_via_code):
        _seed_hq()
        member = _seed_member()
        login_via_code(MEMBER_EMAIL)
        page.goto(live_server.url + reverse("hq:volunteers", args=["holiday-bizarre"]))

        expect(page.locator("[data-hq-vol-prefilled]")).to_be_visible()
        expect(page.locator('input[name="name"]')).to_have_value("Lee Organiser")
        expect(page.locator('input[name="email"]')).to_have_value(member.primary_email)
        expect(page.locator('input[name="phone"]')).to_have_value("503 555 0142")
        expect(page.locator("button[data-hq-shift-signup]").first).to_be_enabled()
