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

The org map editor (issue #382) is the same class of defect one file over, so it is covered
here too. ``space_map_editor.js`` did all of its wiring inside a ``DOMContentLoaded``
listener; that event fired once on the original document and never again, so every boosted
arrival left the editor inert — no drag, no add marker — while a hard load looked perfect.
Its second scenario guards the other side of the fix: the listeners bound to ``document``
and ``document.body`` survive a boosted swap, so re-running the boot block would stack one
more of each per visit.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import cast

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import ConsoleMessage, Error, expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import MapHotspot, Member
from tests.membership.factories import (
    FloorplanFactory,
    MapHotspotFactory,
    MembershipPlanFactory,
    SpaceFactory,
)

EMAIL = "boosted-teacher@example.com"
FRAME = ".cropper-container"
CARD_PHOTOS = ".pl-card-focus__frame .cls-img"
FOCUS_INPUT = "[data-card-focus-input]"
# The first slider on the Photos step is "Up and down" (posY).
FOCUS_RANGE = '[data-composer-step="2"] input.pl-card-focus__range'
CSRF_META = 'meta[name="csrf-token"]'

ADMIN_EMAIL = "boosted-admin@example.com"
MAP_EDITOR = ".pl-map-editor"
EDITOR_MARKER = "[data-editor-marker]"
EDITOR_STATUS = "[data-editor-status]"
MARKER_ORIGIN = (Decimal("10.00"), Decimal("10.00"))  # MapHotspotFactory's x and y
DONE_LINK = "a.pl-map-edit__done"  # the editor's own way back, inside the boosted content


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

    def it_sends_the_current_csrf_token_with_an_htmx_request_after_a_boost(live_server, page, login_via_code):
        # hub_boot.js reads the token from <meta name="csrf-token"> per request. Django masks
        # the token on every render, so the boosted page's meta differs from the first page's
        # and head-support has to have replaced it; the header must carry the new one.
        offering = _seed_draft()
        login_via_code(EMAIL)
        page.goto(f"{live_server.url}{reverse('classes:teach_dashboard')}")
        token_before = page.locator(CSRF_META).get_attribute("content")
        edit_path = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        page.locator(f'a[href="{edit_path}"]').first.click()
        page.wait_for_url(re.compile(re.escape(edit_path)))

        expect(page.locator(CSRF_META)).to_have_count(1)
        token = page.locator(CSRF_META).get_attribute("content")
        assert token and token != token_before

        # A header-only htmx POST, the way hx-post buttons work: no form, no body.
        dismiss_url = f"{live_server.url}{reverse('hub_onboarding_dismiss')}"
        with page.expect_response(lambda r: r.url == dismiss_url and r.request.method == "POST") as posted:
            page.evaluate("url => htmx.ajax('POST', url, { swap: 'none' })", dismiss_url)
        response = posted.value
        assert response.request.headers["x-csrftoken"] == token
        assert response.status == 200

        # Control: the same POST with no token is what Django refuses.
        bare_status = page.evaluate("url => fetch(url, { method: 'POST' }).then(r => r.status)", dismiss_url)
        assert bare_status == 403


def _seed_admin_with_a_marked_floor() -> MapHotspot:
    """An admin who may edit the map, plus one floor carrying one draggable marker.

    The floor deliberately has no underlay image: the editor drags against the drawn canvas,
    so an image-less floor is the normal case and the scenario needs no media route.
    """
    MembershipPlanFactory()  # so the user signal can provision the member this then promotes
    user = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL)
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["fog_role", "status"])
    member.sync_user_permissions()
    floor = FloorplanFactory(name="Ground Floor", image="")
    return cast(MapHotspot, MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="A9")))


def _boosted_click(page, path: str, *, selector: str | None = None) -> None:
    """Follow an in-content link the way a member does — an hx-boost body swap, not a load.

    ``page.goto()`` would be a full document load, which re-fires ``DOMContentLoaded`` and so
    cannot see this bug at all. Neither can a click that turns out to be a full load, and one
    is easy to pick by accident: the sidebar nav carries ``hx-boost="false"``, so its copy of
    a link reloads the document while the in-content copy of the same href swaps. That is why
    the click is verified rather than assumed — a stamp on ``window`` before the click is
    still there afterwards only if the document survived.

    The trailing anchor matters too: ``/spaces/`` is a prefix of ``/spaces/map/edit/``, so an
    unanchored pattern would match the page already open and wait for nothing.
    """
    page.evaluate("() => { window.__plBoostStamp = true; }")
    page.locator(selector or f'a[href="{path}"]').first.click()
    page.wait_for_url(re.compile(re.escape(path) + "$"))
    assert page.evaluate("() => Boolean(window.__plBoostStamp)"), (
        f"the click to {path} replaced the document instead of boosting it; "
        "a full load re-fires DOMContentLoaded and cannot see this bug"
    )


def _drag(page, locator, dx: float, dy: float) -> None:
    """Press on an element's centre, move by (dx, dy), release — a real pointer drag."""
    box = locator.bounding_box()
    assert box, "the marker is not laid out, so there is nothing to drag"
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + dx, start_y + dy, steps=8)
    page.mouse.up()


def describe_boosted_arrival_at_the_org_map_editor():
    def it_leaves_the_map_editor_draggable_with_no_alpine_errors(live_server, page, login_via_code):
        hotspot = _seed_admin_with_a_marked_floor()
        errors = _watch_for_errors(page)
        login_via_code(ADMIN_EMAIL)

        # Arrive the way an admin does: Spaces, then the boosted "Edit the map" link.
        page.goto(f"{live_server.url}{reverse('hub_spaces')}")
        _boosted_click(page, reverse("hub_org_map_edit"))
        expect(page.locator(MAP_EDITOR)).to_be_visible()

        # Interactive, not merely rendered. Dragging the tile runs the stage's pointer
        # handlers and POSTs the new box, which is the whole editor in one gesture.
        marker = page.locator(EDITOR_MARKER).first
        expect(marker).to_be_visible()
        _drag(page, marker, 60, 40)
        expect(page.locator(EDITOR_STATUS)).to_have_text("Position saved.")

        hotspot.refresh_from_db()
        assert (hotspot.x, hotspot.y) != MARKER_ORIGIN
        assert errors == [], "\n".join(errors)

    def it_still_boots_the_editor_on_a_hard_load(live_server, page, login_via_code):
        # Criterion 2. The boot no longer waits for DOMContentLoaded, and a hard load is the
        # one path where that event does still fire — so it is the path a later
        # "simplification" back to a single listener would break without the boosted
        # scenarios noticing.
        hotspot = _seed_admin_with_a_marked_floor()
        errors = _watch_for_errors(page)
        login_via_code(ADMIN_EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_org_map_edit')}")
        marker = page.locator(EDITOR_MARKER).first
        expect(marker).to_be_visible()
        _drag(page, marker, 60, 40)
        expect(page.locator(EDITOR_STATUS)).to_have_text("Position saved.")

        hotspot.refresh_from_db()
        assert (hotspot.x, hotspot.y) != MARKER_ORIGIN
        assert errors == [], "\n".join(errors)

    def it_survives_the_browser_back_button(live_server, page, login_via_code):
        """Back is an htmx history restore, and the per-node ready keys must not survive it.

        htmx caches a snapshot by serializing the body's innerHTML and re-executes the
        scripts it restores, so ``boot()`` genuinely runs again here. If the ready key were a
        ``data-`` attribute it would be captured in that snapshot, the restored nodes would
        arrive already claimed, and the editor would come back dead: this file's own bug,
        reintroduced one navigation later. A property on the element is not markup, so it
        never serializes.

        The database is the only witness worth trusting. The restored snapshot still carries
        the *first* drag's "Position saved." in the status line, so asserting on that text
        passes whether or not anything is wired.
        """
        hotspot = _seed_admin_with_a_marked_floor()
        errors = _watch_for_errors(page)
        login_via_code(ADMIN_EMAIL)

        edit_path = reverse("hub_org_map_edit")
        spaces_path = reverse("hub_spaces")
        page.goto(f"{live_server.url}{spaces_path}")
        _boosted_click(page, edit_path)
        _drag(page, page.locator(EDITOR_MARKER).first, 60, 40)
        expect(page.locator(EDITOR_STATUS)).to_have_text("Position saved.")
        hotspot.refresh_from_db()
        before = (hotspot.x, hotspot.y)

        _boosted_click(page, spaces_path, selector=DONE_LINK)
        page.go_back()
        page.wait_for_url(re.compile(re.escape(edit_path) + "$"))
        expect(page.locator(MAP_EDITOR)).to_be_visible()

        # setAttribute lowercases, so compare case insensitively or a revert to attributes
        # would slip straight past this.
        restored = page.content().lower()
        leaked = [k for k in ("plstageready", "pladdmarkerready", "pladdrowready") if k in restored]
        assert not leaked, f"ready keys reached the history snapshot as markup: {leaked}"

        _drag(page, page.locator(EDITOR_MARKER).first, 50, 35)
        expect(page.locator(EDITOR_MARKER).first).not_to_have_attribute("style", re.compile("left: 10%"))
        page.wait_for_timeout(2000)
        hotspot.refresh_from_db()
        assert (hotspot.x, hotspot.y) != before, "the marker did not move after Back, so the restored editor is inert"
        assert errors == [], "\n".join(errors)

    def it_binds_the_modal_close_listener_once_across_repeat_arrivals(live_server, page, login_via_code):
        """Criterion 3: arriving twice must not stack the ``close-marker-edit`` handler.

        That listener sits on ``document.body``, which a boosted swap keeps — only the body's
        contents are replaced — so a boot block that re-runs unguarded leaves one more
        handler behind per visit, and one saved marker then fires ``close-modal`` once per
        accumulated handler. Two arrivals is the smallest case that can tell a real guard
        from a scope-local boolean, which hx-boost rebuilds as ``false`` every time it
        re-runs the file — and both arrivals have to land in the *same* document, or the
        scope-local version looks correct too.

        Counted by dispatching the trigger and tallying what comes back rather than by
        enumerating bindings: page JavaScript cannot list an element's listeners, and the
        dispatch count is the thing the defect actually corrupts.
        """
        _seed_admin_with_a_marked_floor()
        errors = _watch_for_errors(page)
        login_via_code(ADMIN_EMAIL)

        edit_path = reverse("hub_org_map_edit")
        spaces_path = reverse("hub_spaces")
        page.goto(f"{live_server.url}{spaces_path}")
        _boosted_click(page, edit_path)
        expect(page.locator(MAP_EDITOR)).to_be_visible()
        # "Done — view the map" on the editor itself. The sidebar's Spaces link is
        # hx-boost="false", and a full load there would hand arrival two a fresh document,
        # where even a broken guard looks correct.
        _boosted_click(page, spaces_path, selector=DONE_LINK)
        _boosted_click(page, edit_path)
        expect(page.locator(MAP_EDITOR)).to_be_visible()

        fired = page.evaluate(
            """() => {
                let fired = 0;
                const tally = () => { fired += 1; };
                window.addEventListener('close-modal', tally);
                document.body.dispatchEvent(new CustomEvent('close-marker-edit'));
                window.removeEventListener('close-modal', tally);
                return fired;
            }"""
        )
        assert fired == 1, f"one close-marker-edit fired close-modal {fired} times after two boosted arrivals"

        # Still interactive on the second arrival, not just un-stacked.
        marker = page.locator(EDITOR_MARKER).first
        _drag(page, marker, 40, 30)
        expect(page.locator(EDITOR_STATUS)).to_have_text("Position saved.")
        assert errors == [], "\n".join(errors)
