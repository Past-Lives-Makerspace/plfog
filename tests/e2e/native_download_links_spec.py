"""End-to-end: inside the native app, download links are hidden and Subscribe stays.

The Capacitor shells drop a navigation to an attachment (Android sets no DownloadListener,
iOS no WKDownloadDelegate), so a download link in the app is dead. ``static/js/native-downloads.js``
hides every link marked ``data-pl-download`` when the Capacitor bridge reports a native
platform and does nothing in a browser. The bridge is stubbed here the way the script reads it:
``window.Capacitor.isNativePlatform()`` returning true, installed before any page script runs,
which is exactly what the real bridge injects into the remote page. The last scenario reaches
the calendar through a boosted click, so the ``htmx:afterSettle`` re-run is what hides the
export there. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.urls import reverse
from playwright.sync_api import expect

from core.models import SiteConfiguration
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

NATIVE_STUB = "window.Capacitor = { isNativePlatform: function () { return true; } };"
ADD_TO_CALENDAR = 'a.hub-btn:has-text("Add to calendar")'
SUBSCRIBE_BUTTON = '.pl-calendar-export button:has-text("Subscribe")'
EXPORT_ITEM = 'a.pl-calendar-export__item:has-text("Download .ics")'
MEMBER_ROWS = 'a.pl-calendar-export__item:has-text("Member calendar")'
MEMBER_EMAIL = "native-downloads@example.com"


def describe_download_links_inside_the_native_app():
    def it_hides_add_to_calendar_for_an_anonymous_scanner(live_server, page):
        page.add_init_script(NATIVE_STUB)
        event = CommunityEventFactory(community=True, title="Potluck")
        page.goto(live_server.url + reverse("hub_event_detail", args=[event.pk]))
        # Rendered (the server render is identical either way), hidden by the script.
        expect(page.locator(ADD_TO_CALENDAR)).to_have_count(1)
        expect(page.locator(ADD_TO_CALENDAR)).to_be_hidden()

    def it_leaves_add_to_calendar_alone_in_a_browser(live_server, page):
        event = CommunityEventFactory(community=True, title="Potluck")
        page.goto(live_server.url + reverse("hub_event_detail", args=[event.pk]))
        expect(page.locator(ADD_TO_CALENDAR)).to_be_visible()

    def it_hides_the_one_time_export_but_keeps_subscribe_after_a_boosted_arrival(live_server, page, login_via_code):
        page.add_init_script(NATIVE_STUB)
        MembershipPlanFactory()  # so the login signal provisions the member
        config = SiteConfiguration.load()
        config.member_google_calendar_id = "memid@group.calendar.google.com"
        config.save()
        event = CommunityEventFactory(community=True, title="Potluck")
        login_via_code(MEMBER_EMAIL)

        page.goto(live_server.url + reverse("hub_event_detail", args=[event.pk]))
        # A member gets Subscribe in place of Add to calendar, in the app as in a browser.
        expect(page.locator(ADD_TO_CALENDAR)).to_have_count(0)
        expect(page.locator(SUBSCRIBE_BUTTON)).to_be_visible()

        # Arrive at the calendar the boosted way, so afterSettle is what hides the export.
        page.locator('a.hub-btn:has-text("View the Calendar")').click()
        page.wait_for_url(re.compile(r"/calendar/$"))
        page.locator(SUBSCRIBE_BUTTON).click()
        expect(page.locator(EXPORT_ITEM)).to_have_count(1)
        expect(page.locator(EXPORT_ITEM)).to_be_hidden()
        expect(page.locator(MEMBER_ROWS)).to_have_count(2)  # Apple Calendar and Google Calendar
        expect(page.locator(MEMBER_ROWS).first).to_be_visible()
