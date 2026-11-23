"""End-to-end: the Holiday Bizarre HQ and its volunteer sign-up (Track C, parts 1 and 2).

In a real browser, as a guild officer signed in through the real login by code flow (#1307: the
HQ is for admins and guild officers): they open the HQ and see every door, open Volunteer
Sign-up with their name, email and phone already in Your Info, sign up for a shift over htmx
(the row flips to Remove Me and the band's figures move in place), then remove themself; and the
RSVP card goes to the event page and back. A signed out visitor lands on the login page. The
service worker is blocked because it fetches navigations past ``page.route``; every wait is on
what the page shows. Run with ``pytest -m e2e`` on PostgreSQL. #1307 removed the wine wall.
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
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

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
    """The open HQ with a Volunteer Sign-up door, the ledger door and two shifts."""
    hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
    volunteers = HubCardFactory(
        hub=hub,
        kind=HubCard.Kind.VOLUNTEERS,
        title="Volunteer Sign-up",
        button_text="Sign up to volunteer",
    )
    ledger = HubCardFactory(hub=hub, title="PL Ledger")
    shift = VolunteerShiftFactory(
        hub=hub, phase=VolunteerShift.Phase.BEFORE, role="Flyer runner", when_text="November", need=6
    )
    VolunteerShiftFactory(hub=hub, phase=VolunteerShift.Phase.DURING, role="Door and check-in", need=2)
    return volunteers, ledger, shift


def _seed_member() -> Member:
    """A guild lead with a login, a preferred name and a phone, for the prefill: a guild officer runs the HQ."""
    MembershipPlanFactory()  # so the user signal provisions the member
    user = get_user_model().objects.create_user(username=MEMBER_EMAIL, email=MEMBER_EMAIL)
    member = user.member
    member.preferred_name = "Lee Organiser"
    member.phone = "503 555 0142"
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["preferred_name", "phone", "status"])
    GuildFactory(guild_lead=member)
    return member


def describe_a_signed_out_visitor():
    def it_is_sent_to_the_login_page(live_server, page):
        _seed_hq()
        page.goto(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))

        expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=/hq/holiday-bizarre/")


def describe_a_guild_officer():
    def it_opens_every_door_finds_their_info_filled_in_signs_up_and_removes_themself(live_server, page, login_via_code):
        volunteers, ledger, shift = _seed_hq()
        member = _seed_member()
        login_via_code(MEMBER_EMAIL)
        page.goto(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))

        expect(page.locator(f'[data-hq-card-pk="{volunteers.pk}"]')).to_be_visible()
        expect(page.locator(f'[data-hq-card-pk="{ledger.pk}"]')).to_be_visible()
        page.locator(f'[data-hq-card-pk="{volunteers.pk}"] a[data-hq-card-link]').click()

        expect(page.locator("[data-hq-volunteers]")).to_be_visible()
        expect(page.locator("[data-hq-vol-prefilled]")).to_be_visible()
        expect(page.locator('input[name="name"]')).to_have_value("Lee Organiser")
        expect(page.locator('input[name="email"]')).to_have_value(member.primary_email)
        expect(page.locator('input[name="phone"]')).to_have_value("503 555 0142")
        expect(page.locator("[data-hq-band-people]")).to_have_text("0")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("0 / 8")
        row = page.locator(f'[data-hq-shift="{shift.pk}"]')
        expect(row.locator("button[data-hq-shift-signup]")).to_be_enabled()
        row.locator("button[data-hq-shift-signup]").click()

        expect(row.locator("button[data-hq-shift-remove]")).to_be_visible()
        expect(page.locator("[data-hq-band-people]")).to_have_text("1")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("1 / 8")
        expect(row.locator("[data-hq-shift-filled]")).to_have_text("1 of 6 filled")
        expect(page.locator(".plt-msg").last).to_contain_text("You are on Flyer runner")
        signup = ShiftSignup.objects.get()
        assert (signup.email, signup.member) == (member.primary_email, member)

        row.locator("button[data-hq-shift-remove]").click()

        expect(row.locator("button[data-hq-shift-signup]")).to_be_visible()
        expect(page.locator("[data-hq-band-people]")).to_have_text("0")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("0 / 8")
        assert not ShiftSignup.objects.exists()


def describe_the_rsvp_door():
    def it_goes_from_the_rsvp_card_to_the_event_page_and_back_to_the_hq(live_server, page, login_via_code):
        _seed_member()
        login_via_code(MEMBER_EMAIL)
        hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
        hub.event = CommunityEventFactory(community=True, title="Holiday Bizarre")
        hub.save()
        rsvp = HubCardFactory(hub=hub, kind=HubCard.Kind.RSVP)
        page.goto(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))

        page.locator(f'[data-hq-card-pk="{rsvp.pk}"] a[data-hq-card-link]').click()

        expect(page.locator("[data-event-page]")).to_be_visible()
        expect(page).to_have_url(live_server.url + reverse("hub_event_detail", args=[hub.event.pk]) + "?from=hq")
        back = page.locator("[data-event-hq-back]")
        expect(back).to_have_text("\u2190 Holiday Bizarre HQ")
        back.click()

        expect(page.locator("[data-event-page]")).to_have_count(0)
        expect(page).to_have_url(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))
