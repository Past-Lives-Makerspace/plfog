"""End-to-end: the .ics download links under the boosted hub body really download.

A member reported the event page's "Add to calendar" button broken (Safari 26.2 on
macOS). It was broken everywhere: the ``hub/base.html`` body is ``hx-boost="true"``, so
htmx took the click, fetched the .ics over XHR and swapped the raw calendar text into the
body with the .ics URL in the address bar. The Community Calendar's "Download .ics (one
time)" link did the same; its ``download`` attribute changes nothing, htmx 2.0.4 does not
look at it. The Django-client specs cannot see any of this; each response is a perfectly
good ``text/calendar`` attachment. Only a real click on the rendered page, with htmx
loaded and the body processed, follows the path a visitor takes. Both anchors now carry
``hx-boost="false"`` (FRONTEND.md rule 24); this spec is the lock on them.

The service-worker scenario lets the PWA worker control the page first (an anonymous
visitor who has been to the front door has one; ``sw.js`` answers every navigation from
the network), so a download that has to pass through its ``fetch`` handler is covered
too. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.urls import reverse
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

ADD_TO_CALENDAR = 'a.hub-btn:has-text("Add to calendar")'
CALENDAR_EXPORT = 'a.pl-calendar-export__item:has-text("Download .ics")'
MEMBER_EMAIL = "ics-download@example.com"
# htmx stamps an element's private data with initHash once it has processed it; on the body
# that means every anchor beneath it has been boosted or skipped. Before that a click is a
# plain navigation and the spec would pass for the wrong reason.
BODY_PROCESSED = "() => !!(document.body['htmx-internal-data'] && document.body['htmx-internal-data'].initHash)"


def _open_event_page(page, live_server):
    event = CommunityEventFactory(community=True, title="Potluck", location="Common Area")
    page.goto(live_server.url + reverse("hub_event_detail", args=[event.pk]))
    page.wait_for_function(BODY_PROCESSED)
    return event


def _click_and_report(page, link, filename: str) -> tuple[bool, str]:
    """Click the link; return (a download fired, what the page did instead)."""
    requests: list[str] = []
    page.on(
        "request",
        lambda req: requests.append(req.resource_type) if req.url.endswith(filename) else None,
    )
    try:
        with page.expect_download(timeout=3000):
            link.click()
    except PlaywrightTimeoutError:
        page.wait_for_timeout(300)
        body = page.evaluate("() => document.body.innerText.slice(0, 120)")
        return False, f"url={page.url} request_types={requests} body_starts={body!r}"
    return True, f"request_types={requests}"


def describe_add_to_calendar_on_the_public_event_page():
    def it_downloads_the_ics_for_an_anonymous_visitor(live_server, page):
        _open_event_page(page, live_server)
        downloaded, report = _click_and_report(page, page.locator(ADD_TO_CALENDAR), "event.ics")
        assert downloaded, f"no download fired; {report}"

    def it_downloads_the_ics_when_the_service_worker_controls_the_page(live_server, page):
        page.goto(live_server.url + reverse("home"))
        page.wait_for_function("() => navigator.serviceWorker && !!navigator.serviceWorker.controller")
        _open_event_page(page, live_server)
        assert page.evaluate("() => !!navigator.serviceWorker.controller"), "service worker lost control"
        downloaded, report = _click_and_report(page, page.locator(ADD_TO_CALENDAR), "event.ics")
        assert downloaded, f"no download fired; {report}"


def describe_download_ics_on_the_community_calendar():
    def it_downloads_the_combined_ics_for_a_member(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the login signal provisions the member
        login_via_code(MEMBER_EMAIL)
        page.goto(live_server.url + reverse("hub_community_calendar"))
        page.wait_for_function(BODY_PROCESSED)
        page.locator(".pl-calendar-export button").click()
        downloaded, report = _click_and_report(page, page.locator(CALENDAR_EXPORT), "export.ics")
        assert downloaded, f"no download fired; {report}"
