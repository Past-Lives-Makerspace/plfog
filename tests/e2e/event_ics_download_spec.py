"""End-to-end: the download links under the boosted hub body really download.

A member reported the event page's "Add to calendar" button broken (Safari 26.2 on
macOS). It was broken everywhere, and so was every other link to a download under the
hub: the ``hub/base.html`` body is ``hx-boost="true"``, so htmx took the click, fetched
the file over XHR and swapped its raw text into the body with the file's URL in the
address bar. A ``download`` attribute changes nothing; htmx 2.0.4 does not look at it.
The Django-client specs cannot see any of this: each response is a perfectly good
attachment. Only a real click on the rendered page, with htmx loaded and the body
processed, follows the path a visitor takes.

Every such anchor now carries ``hx-boost="false"`` (FRONTEND.md rule 24), and
``tests/download_links_lint_spec.py`` holds the whole tree to it. This spec drives a real
click on one link of each kind: the public .ics, the combined .ics, a QR image through the
shared Share & Print card, and a streamed CSV export. The service-worker scenario lets the
PWA worker control the page first (an anonymous visitor who has been to the front door has
one; ``sw.js`` answers every navigation from the network), so a download that has to pass
through its ``fetch`` handler is covered too. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from membership.models import Member
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

ADD_TO_CALENDAR = '.pl-calendar-export button:has-text("Add to calendar")'
EVENT_ICS = 'a.pl-calendar-export__item:has-text("Apple Calendar or Outlook")'
CALENDAR_EXPORT = 'a.pl-calendar-export__item:has-text("Download .ics")'
QR_SVG = 'a.hub-btn:has-text("Download QR (SVG)")'
EXPORT_CSV = 'a.hub-btn:has-text("Export CSV")'
MEMBER_EMAIL = "ics-download@example.com"
ADMIN_EMAIL = "ics-download-admin@example.com"
# htmx stamps an element's private data with initHash once it has processed it; on the body
# that means every anchor beneath it has been boosted or skipped. Before that a click is a
# plain navigation and the spec would pass for the wrong reason.
BODY_PROCESSED = "() => !!(document.body['htmx-internal-data'] && document.body['htmx-internal-data'].initHash)"


def _seed_admin() -> User:
    MembershipPlanFactory()  # so the user signal provisions the member this then promotes
    user = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL)
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["fog_role", "status"])
    member.sync_user_permissions()
    return user


def _open(page, live_server, path: str) -> None:
    page.goto(live_server.url + path)
    page.wait_for_function(BODY_PROCESSED)


def _click_and_report(page, link, marker: str) -> tuple[bool, str]:
    """Click the link; return (the browser itself fetched the file, what happened).

    A download is the expected outcome. Playwright's WebKit build renders a CSV or an SVG
    attachment inline instead of raising a download, so the verdict is who made the request
    for the file: the browser, as a ``document`` navigation, passes; htmx, as ``xhr``, is the
    bug. The report carries the address bar and the start of the body, which in the broken
    case is the file's own text.
    """
    requests: list[str] = []
    page.on("request", lambda req: requests.append(req.resource_type) if marker in req.url else None)
    try:
        with page.expect_download(timeout=3000):
            link.click()
        return True, f"download fired; request_types={requests}"
    except PlaywrightTimeoutError:
        page.wait_for_timeout(300)
    browser_fetched = "document" in requests and not ({"xhr", "fetch"} & set(requests))
    body = page.evaluate("() => document.body ? document.body.innerText.slice(0, 120) : null")
    return browser_fetched, f"url={page.url} request_types={requests} body_starts={body!r}"


def describe_add_to_calendar_on_the_public_event_page():
    def it_downloads_the_ics_for_an_anonymous_visitor(live_server, page):
        event = CommunityEventFactory(community=True, title="Potluck", location="Common Area")
        _open(page, live_server, reverse("hub_event_detail", args=[event.pk]))
        page.locator(ADD_TO_CALENDAR).click()
        fetched, report = _click_and_report(page, page.locator(EVENT_ICS), "event.ics")
        assert fetched, f"htmx took the click instead of the browser; {report}"

    def it_downloads_the_ics_when_the_service_worker_controls_the_page(live_server, page):
        event = CommunityEventFactory(community=True, title="Potluck", location="Common Area")
        page.goto(live_server.url + reverse("home"))
        page.wait_for_function("() => navigator.serviceWorker && !!navigator.serviceWorker.controller")
        _open(page, live_server, reverse("hub_event_detail", args=[event.pk]))
        assert page.evaluate("() => !!navigator.serviceWorker.controller"), "service worker lost control"
        page.locator(ADD_TO_CALENDAR).click()
        fetched, report = _click_and_report(page, page.locator(EVENT_ICS), "event.ics")
        assert fetched, f"htmx took the click instead of the browser; {report}"


def describe_download_ics_on_the_community_calendar():
    def it_downloads_the_combined_ics_for_a_member(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the login signal provisions the member
        login_via_code(MEMBER_EMAIL)
        _open(page, live_server, reverse("hub_community_calendar"))
        page.locator(".pl-calendar-export button").click()
        fetched, report = _click_and_report(page, page.locator(CALENDAR_EXPORT), "export.ics")
        assert fetched, f"htmx took the click instead of the browser; {report}"


def describe_download_qr_on_the_event_edit_page():
    def it_downloads_the_svg_for_an_admin(live_server, page, login_via_code):
        """The shared Share & Print card: the same anchors serve class, event and wiki QRs."""
        _seed_admin()
        event = CommunityEventFactory(community=True, title="Potluck")
        login_via_code(ADMIN_EMAIL)
        _open(page, live_server, reverse("hub_event_edit", args=[event.pk]))
        fetched, report = _click_and_report(page, page.locator(QR_SVG), "qr.svg")
        assert fetched, f"htmx took the click instead of the browser; {report}"


def describe_export_csv_on_the_orientations_dashboard():
    def it_downloads_the_csv_for_an_admin(live_server, page, login_via_code):
        """A streamed CSV, the shape every export in the portal takes."""
        _seed_admin()
        login_via_code(ADMIN_EMAIL)
        _open(page, live_server, reverse("hub_orientations_dashboard"))
        fetched, report = _click_and_report(page, page.locator(EXPORT_CSV), "orientations/manage/export/")
        assert fetched, f"htmx took the click instead of the browser; {report}"
