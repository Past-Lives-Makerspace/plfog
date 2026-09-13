"""End-to-end: arriving at the class composer through a boosted link (issue #378, part 2).

``hub/base.html`` boosts the whole body, so Manage My Classes to Edit is an htmx body
swap, not a page load. On production v1.56.0 that arrival logged ``cardFocus is not
defined`` and ``update is not defined`` and left the card focus sliders dead until a hard
refresh: htmx and Alpine were loaded from inside ``<body>``, every boosted swap re ran
both, and the fresh Alpine initialised the swapped tree before ``card_focus.js`` had
registered its component. The Python suite renders templates and the other composer
specs ``page.goto()`` straight to the page, so only a real boosted click sees this.

The scenario collects every uncaught page error and every Alpine console warning from
before the first navigation, arrives the way a host does, opens the Photos step, and then
proves the widgets on it are alive: a slider move runs ``update()`` and the cropper frame
mounts. Needs the network for Cropper.js. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import json
import re
from typing import cast

from django.urls import reverse
from playwright.sync_api import ConsoleMessage, Error, expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "boosted-teacher@example.com"
FRAME = ".cropper-container"
CARD_PHOTOS = ".pl-card-focus__frame .cls-img"
FOCUS_INPUT = "[data-card-focus-input]"
# The first slider on the Photos step is "Up and down" (posY).
FOCUS_RANGE = '[data-composer-step="2"] input.pl-card-focus__range'


def _seed_draft() -> ClassOffering:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=EMAIL)
    instructor = cast(
        Member, InstructorFactory(user=user, full_legal_name="Boosted Teacher", instructor_slug="boosted-teacher")
    )
    return cast(
        ClassOffering,
        ClassOfferingFactory(
            instructor=instructor,
            status=ClassOffering.Status.DRAFT,
            ready=True,
            image__width=1200,
            image__height=1200,
        ),
    )


def _watch_for_errors(page) -> list[str]:
    """Every uncaught exception, plus every Alpine warning, from now on.

    Alpine reports an expression error twice: a ``console.warn`` naming the expression and
    the element, then the error itself rethrown on a timeout, which the browser surfaces as
    an uncaught page error. Both are collected so the failure message names the element.
    """
    seen: list[str] = []

    def on_console(message: ConsoleMessage) -> None:
        if message.type in {"error", "warning"} and "Alpine" in message.text:
            seen.append(f"console.{message.type}: {message.text}")

    def on_page_error(error: Error) -> None:
        seen.append(f"pageerror: {error.message}")

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    return seen


def describe_boosted_arrival_at_the_composer():
    def it_leaves_the_card_focus_and_cropper_alive_with_no_alpine_errors(
        live_server, page, login_via_code, serve_media
    ):
        offering = _seed_draft()
        errors = _watch_for_errors(page)
        login_via_code(EMAIL)

        # Arrive the way a host does: Manage My Classes, then the boosted Edit link.
        edit_path = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        page.goto(f"{live_server.url}{reverse('classes:teach_dashboard')}")
        page.locator(f'a[href="{edit_path}"]').first.click()
        page.wait_for_url(re.compile(re.escape(edit_path)))
        page.locator('[data-step-tab="2"]').click()

        # The cropper frame mounts on the boosted page, and the arrival threw nothing.
        expect(page.locator(FRAME)).to_be_visible()
        assert errors == [], "\n".join(errors)

        # The card focus widget is alive: a slider move runs update(), which writes the
        # override the form posts and moves the photo in every card frame.
        photo = page.locator(CARD_PHOTOS).first
        expect(photo).to_have_attribute("style", re.compile(r"object-position: 50% 50%"))
        expect(page.locator(FOCUS_INPUT)).to_have_value("")
        page.locator(FOCUS_RANGE).first.evaluate(
            "el => { el.value = 80; el.dispatchEvent(new Event('input', { bubbles: true })) }"
        )
        expect(page.locator(FOCUS_INPUT)).to_have_value(re.compile(r"\S"))
        assert json.loads(page.locator(FOCUS_INPUT).input_value()) == {"x": 50, "y": 80}
        expect(photo).to_have_attribute("style", re.compile(r"object-position: 50% 80%"))

        assert errors == [], "\n".join(errors)
