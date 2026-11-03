"""End-to-end: inside the native app, a same-host new-tab link opens in the app (#1102).

The iOS shell hands every new-window navigation to Safari (Capacitor's
``WebViewDelegationHandler.createWebViewWith`` calls ``UIApplication.shared.open``), and Safari
has no session, so the class composer's Preview landed on the emailed code login.
``static/js/native-new-tab-links.js`` opens a same-host ``target="_blank"`` link in the page
itself when the Capacitor bridge reports a native platform, and does nothing in a browser. The
bridge is stubbed the way the script reads it, ``window.Capacitor.isNativePlatform()`` returning
true, installed before any page script runs, which is what the real bridge injects into the
remote page. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from typing import cast

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

NATIVE_STUB = "window.Capacitor = { isNativePlatform: function () { return true; } };"
TEACHER = "native-new-tab@example.com"
PREVIEW_BUTTON = '.pl-composer-bar a:has-text("Preview")'
OTHER_HOST = "https://elsewhere.example.org/"


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _composer(page: Page, live_server, login_via_code) -> ClassOffering:
    """Sign the owning instructor in and open the composer on a saved draft."""
    MembershipPlanFactory()  # so the user signal provisions the member the instructor factory updates
    user = UserFactory(username=TEACHER, email=TEACHER)
    instructor = cast(Member, InstructorFactory(user=user, full_legal_name="Nia Native", instructor_slug="nia-native"))
    offering = cast(
        ClassOffering,
        ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT, ready=True),
    )
    login_via_code(TEACHER)
    page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
    expect(page.locator(PREVIEW_BUTTON)).to_be_visible()
    return offering


def _decisions(page: Page, hrefs: list[tuple[str, str]]) -> list[bool]:
    """Click a fresh ``target="_blank"`` link per (href, extra attribute) and report whether
    the script cancelled the click. A window listener runs after the script's document
    listener, records ``defaultPrevented`` and then cancels the click's default action, so no
    click opens a tab or follows its link. A click the script took has already called
    ``location.assign``, though, so the page leaves for that URL once the batch returns; a
    caller that expects one waits for it."""
    return cast(
        list[bool],
        page.evaluate(
            """(cases) => {
                const seen = [];
                const record = (e) => { seen.push(e.defaultPrevented); e.preventDefault(); };
                window.addEventListener("click", record);
                for (const [href, attr] of cases) {
                    const a = document.createElement("a");
                    a.href = href;
                    a.target = "_blank";
                    if (attr) a.setAttribute(attr, "");
                    a.textContent = "probe";
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                }
                window.removeEventListener("click", record);
                return seen;
            }""",
            [list(case) for case in hrefs],
        ),
    )


def describe_new_tab_links_inside_the_native_app():
    def it_opens_the_composer_preview_in_the_same_page_without_a_popup(live_server, page, login_via_code):
        page.add_init_script(NATIVE_STUB)
        offering = _composer(page, live_server, login_via_code)
        popups: list[Page] = []
        page.on("popup", lambda popup: popups.append(popup))

        page.locator(PREVIEW_BUTTON).click()

        preview = reverse("classes:class_preview", kwargs={"pk": offering.pk})
        page.wait_for_url(re.compile(re.escape(preview) + "$"))
        # Still signed in: the preview itself, not the code login.
        expect(page.locator("body")).to_contain_text(offering.title)
        assert "/accounts/login" not in page.url
        assert popups == []
        assert page.context.pages == [page]

    def it_leaves_a_link_to_another_host_to_open_a_new_page(live_server, page, login_via_code):
        page.add_init_script(NATIVE_STUB)
        page.context.route(OTHER_HOST + "**", lambda route: route.fulfill(body="elsewhere", content_type="text/html"))
        _composer(page, live_server, login_via_code)
        composer_url = page.url
        page.evaluate(
            """(href) => {
                const a = document.createElement("a");
                a.href = href; a.target = "_blank"; a.id = "pl-other-host"; a.textContent = "Elsewhere";
                document.body.appendChild(a);
            }""",
            OTHER_HOST,
        )

        with page.expect_popup() as popup_info:
            page.locator("#pl-other-host").click()

        popup = popup_info.value
        popup.wait_for_load_state()
        assert popup.url == OTHER_HOST
        assert page.url == composer_url

    def it_only_takes_same_host_web_links_that_are_not_downloads_or_marked_for_a_new_window(
        live_server, page, login_via_code
    ):
        page.add_init_script(NATIVE_STUB)
        offering = _composer(page, live_server, login_via_code)
        same_host = reverse("classes:class_preview", kwargs={"pk": offering.pk})

        decisions = _decisions(
            page,
            [
                (same_host, ""),
                (OTHER_HOST, ""),
                ("mailto:someone@example.com", ""),
                ("tel:+15035550100", ""),
                (same_host, "download"),
                (same_host, "data-pl-download"),
                (same_host, "data-pl-new-window"),
            ],
        )

        assert decisions == [True, False, False, False, False, False, False]
        # The one click it took sends the page there, in place.
        page.wait_for_url(re.compile(re.escape(same_host) + "$"))


def describe_new_tab_links_in_a_browser():
    def it_still_opens_the_composer_preview_in_a_new_page(live_server, page, login_via_code):
        offering = _composer(page, live_server, login_via_code)
        composer_url = page.url

        with page.expect_popup() as popup_info:
            page.locator(PREVIEW_BUTTON).click()

        popup = popup_info.value
        preview = reverse("classes:class_preview", kwargs={"pk": offering.pk})
        popup.wait_for_url(re.compile(re.escape(preview) + "$"))
        assert page.url == composer_url

    def it_cancels_nothing(live_server, page, login_via_code):
        offering = _composer(page, live_server, login_via_code)
        same_host = reverse("classes:class_preview", kwargs={"pk": offering.pk})

        assert _decisions(page, [(same_host, ""), (OTHER_HOST, "")]) == [False, False]
