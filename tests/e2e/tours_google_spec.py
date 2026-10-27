"""End-to-end: a Tour Guide connects their Google Calendar on My Hours, picks a calendar and disconnects;
an admin sees each guide's calendar on the guide picker (#951).

Google is mocked on both legs. In the browser, ``page.route`` takes over Connect: it fetches our
own redirect to Google without following it and answers with our callback, carrying a code and the
state the server put in the session (Playwright never routes a redirect hop, and every request to
accounts.google.com is aborted). On the
server, ``respx`` answers the token, calendar list, events and revoke calls; the live server runs
in this process, so its ``httpx`` calls hit the mock. Service workers are blocked so every request
goes through ``page.route``. ``CAPTURE_951_DIR=<dir>`` also saves dark and light pictures at 1100
and 390 pixels wide. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import respx
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourCalendarConnectionFactory, TourHoursFactory
from tours import google
from tours.models import TourCalendarConnection

GUIDE_EMAIL = "tour-guide-google@example.com"
ADMIN_EMAIL = "tour-admin-google@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_951_DIR")
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}
TOURS_CAL = "tours@group.calendar.google.com"


@pytest.fixture
def browser_context_args(browser_context_args):
    """No service worker: it would fetch around ``page.route`` and reach the real Google."""
    return {**browser_context_args, "service_workers": "block"}


def _capture(page, name: str) -> None:
    """Save dark and light pictures at desktop and phone widths, when CAPTURE_951_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        # A phone opens a page with the sidebar drawer closed.
        _set_drawer(page, is_open=size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(600)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"951-{name}-{size_name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _set_drawer(page, *, is_open: bool) -> None:
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _member(email: str, name: str, **fields: object) -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    for field, value in fields.items():
        setattr(member, field, value)
    member.save()
    return member


def _guide(email: str = GUIDE_EMAIL, name: str = "Gina Guide") -> Member:
    member = _member(email, name)
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


@pytest.fixture
def google_stub(settings, live_server, page) -> Iterator[respx.MockRouter]:
    """Google's consent screen in the browser, and its token, calendar and revoke calls on the server."""
    settings.GOOGLE_OAUTH_CLIENT_ID = "e2e-client"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "e2e-secret"
    settings.MEMBER_BASE_URL = live_server.url

    def consent(route, request) -> None:
        # Playwright routes no redirect hop, so take our own Connect's answer without following it:
        # the session keeps the state, and Google "answers" with the callback at once.
        sent = route.fetch(max_redirects=0)
        location = sent.headers["location"]
        assert location.startswith(google.AUTHORIZE_URL), location
        query = parse_qs(urlsplit(location).query)
        back = f"{query['redirect_uri'][0]}?{urlencode({'code': 'e2e-code', 'state': query['state'][0]})}"
        route.fulfill(status=302, headers={"Location": back})

    page.route(f"**{reverse('tours:google_connect')}", consent)
    # A safety net: nothing in the browser ever reaches the real Google.
    page.route("https://accounts.google.com/**", lambda route, request: route.abort())
    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        router.post(google.TOKEN_URL).respond(
            200,
            json={
                "access_token": "e2e-access",
                "refresh_token": "e2e-refresh",
                "expires_in": 3600,
                "scope": " ".join(google.SCOPES),
            },
        )
        router.get(f"{google.API_URL}/users/me/calendarList").respond(
            200,
            json={
                "items": [
                    {"id": GUIDE_EMAIL, "summary": "Gina Guide", "primary": True},
                    {"id": TOURS_CAL, "summary": "Tours"},
                ]
            },
        )
        router.get(url__startswith=f"{google.API_URL}/calendars/").respond(200, json={"items": []})
        router.post(google.REVOKE_URL).respond(200)
        yield router


def describe_google_calendar_on_my_hours():
    def it_connects_picks_a_calendar_and_disconnects(live_server, page, login_via_code, google_stub):
        guide = _guide()
        TourHoursFactory(guide=guide)
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:hours')}")
        card = page.locator("[data-tour-gcal]")
        assert card.get_attribute("data-tour-gcal") == "none"
        _capture(page, "not-connected")

        # Connect: off to Google (mocked) and straight back to My Hours, connected.
        with page.expect_navigation(url=f"**{reverse('tours:hours')}"):
            page.locator("[data-tour-gcal-connect]").click()
        page.locator('[data-tour-gcal="connected"]').wait_for()
        page.locator("[data-tour-gcal-account]").get_by_text(GUIDE_EMAIL).wait_for()
        connection = TourCalendarConnection.objects.get(guide=guide)
        assert (connection.refresh_token, connection.calendar_id) == ("e2e-refresh", "primary")
        _capture(page, "connected")

        # Pick the Tours calendar and save.
        page.locator('select[name="calendar"]').select_option(TOURS_CAL)
        with page.expect_navigation():
            page.locator("[data-tour-gcal-save]").click()
        page.locator('select[name="calendar"]').wait_for()
        assert page.locator('select[name="calendar"]').input_value() == TOURS_CAL
        assert TourCalendarConnection.objects.get(guide=guide).calendar_id == TOURS_CAL

        # At a phone's width nothing scrolls sideways.
        page.set_viewport_size(PHONE)
        _set_drawer(page, is_open=False)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size(DESKTOP)
        _set_drawer(page, is_open=True)

        # Disconnect asks first, then revokes and deletes.
        page.locator('[data-tour-gcal="connected"] [data-tour-gcal-disconnect]').click()
        dialog = page.get_by_role("dialog").filter(has_text="Disconnect Google Calendar?")
        dialog.wait_for(state="visible")
        assert TourCalendarConnection.objects.exists()
        _capture(page, "disconnect-confirm")
        with page.expect_navigation():
            dialog.get_by_role("button", name="Disconnect", exact=True).click()
        page.locator('[data-tour-gcal="none"]').wait_for()
        assert not TourCalendarConnection.objects.exists()
        assert any(str(call.request.url) == google.REVOKE_URL for call in google_stub.calls)

    def it_shows_reconnect_when_google_stopped_accepting_the_calendar(live_server, page, login_via_code, google_stub):
        guide = _guide()
        TourCalendarConnectionFactory(
            guide=guide,
            google_email=GUIDE_EMAIL,
            status=TourCalendarConnection.Status.BROKEN,
            broken_at=timezone.now(),
            refresh_token="",
            access_token="",
        )
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:hours')}")
        page.locator("[data-tour-gcal-broken]").wait_for()
        assert page.locator("[data-tour-gcal-reconnect]").is_visible()
        _capture(page, "broken")

        with page.expect_navigation(url=f"**{reverse('tours:hours')}"):
            page.locator("[data-tour-gcal-reconnect]").click()
        page.locator('[data-tour-gcal="connected"]').wait_for()
        assert TourCalendarConnection.objects.get(guide=guide).status == TourCalendarConnection.Status.CONNECTED

    def it_names_each_guides_calendar_on_the_admins_guide_picker(live_server, page, login_via_code, google_stub):
        _member(ADMIN_EMAIL, "Ada Admin", fog_role=Member.FogRole.ADMIN)
        connected = _guide("cora@example.com", "Cora Connected")
        TourCalendarConnectionFactory(guide=connected, google_email="cora@gmail.com", busy_checked_at=timezone.now())
        broken = _guide("bo@example.com", "Bo Broken")
        TourCalendarConnectionFactory(
            guide=broken, status=TourCalendarConnection.Status.BROKEN, broken_at=timezone.now()
        )
        _guide("nell@example.com", "Nell None")
        login_via_code(ADMIN_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:hours')}?guide={connected.pk}")
        options = page.locator("#tour-guide-picker option").all_inner_texts()
        assert "Cora Connected · Calendar connected" in options
        assert "Bo Broken · Calendar needs reconnecting" in options
        assert "Nell None · No calendar" in options
        card = page.locator('[data-tour-gcal="connected"]')
        card.get_by_text("cora@gmail.com").wait_for()
        assert card.locator("[data-tour-gcal-disconnect]").count() == 0
        _capture(page, "admin-picker")
