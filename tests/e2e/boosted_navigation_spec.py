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

Browser Back is the third theme (issue #383), and it is the same defect read from the other
end: htmx builds its history snapshot by serializing the body's innerHTML, so everything
Alpine and Quill rendered into that body is captured as inert markup and replayed with no
handlers behind it. The scenarios at the foot of this file cover the three shapes that
took: duplicated ``x-for`` output in the class composer, a rich-text mount restored already
claimed and silently dead, and a settle listener that stacks one per arrival.
"""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from io import StringIO
from typing import cast

from django.contrib.auth.models import User
from django.core.management import call_command
from django.urls import reverse
from playwright.sync_api import ConsoleMessage, Error, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import MapHotspot, Member, WikiPage
from tests.features import turn_on
from tests.membership.factories import (
    EquipmentFactory,
    FloorplanFactory,
    GuildFactory,
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


def _wait_for_move(page, hotspot, before, timeout_ms: int = 8000) -> bool:
    """Poll the row until the editor's position POST lands, or give up.

    A fixed sleep here is a flake vector under CI load. The database is the witness on
    purpose: the restored history snapshot still carries the previous drag's "Position saved."
    and its already-moved coordinate, so every assertion available in the page passes whether
    or not anything is actually wired.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        hotspot.refresh_from_db()
        if (hotspot.x, hotspot.y) != before:
            return True
        page.wait_for_timeout(100)
    return False


def describe_the_history_restore_guards():
    """Back is a refetch now, so the two hub_boot guards finally have to cover it.

    Both listen on ``htmx:beforeSwap``, which htmx's cache-miss loader never fires: it
    swaps directly and fires only its own ``historyCacheMiss*`` events. Before the hub
    stopped keeping a history cache these cases could not arise, because Back made no
    request at all. Issue #383.
    """

    def it_loads_the_login_page_for_real_when_the_session_expired(live_server, page, login_via_code):
        """Without the guard, htmx pastes the login page into the hub document.

        The refetch is redirected to login, XHR follows it silently, and htmx sees a 200.
        The tell is the address bar: a swap leaves it on the page that was asked for, a
        real load moves it to the login page.
        """
        _seed_admin_with_a_marked_floor()
        login_via_code(ADMIN_EMAIL)

        # Back has to land on a login-required page for the session to matter, so the
        # editor is where we start and /spaces/ (which is public) is where we leave to.
        edit_path = reverse("hub_org_map_edit")
        page.goto(f"{live_server.url}{edit_path}")
        page.evaluate("() => { window.__plExpiredMarker = true; }")
        _boosted_click(page, reverse("hub_spaces"), selector=DONE_LINK)

        # Expire the session the way time would, leaving the tab open.
        page.context.clear_cookies()

        page.go_back()
        page.wait_for_url(re.compile(r"/accounts/login/"), timeout=10000)
        assert "/accounts/login/" in page.url, (
            f"Back landed on {page.url} rather than the login page; the login response was "
            "swapped into the hub document instead of being loaded"
        )
        # The login form being present proves nothing: it is present in the broken case
        # too, sitting inside the hub document. What separates a load from a swap is
        # whether the document survived, so the marker stamped before the Back is the
        # witness.
        survived = page.evaluate("() => Boolean(window.__plExpiredMarker)")
        assert not survived, "the login page was swapped into the hub document, not loaded"
        assert "next=" in page.url, f"the return path was dropped from {page.url}"

    def it_loads_the_real_error_page_when_the_target_is_gone(live_server, page, login_via_code):
        """Without the guard, htmx swaps nothing and Back is simply dead.

        The popstate has already moved the address bar, so the member is left looking at
        the previous screen under the gone page's URL, with nothing to say what happened
        and no branded 404 to navigate out of.

        Everything here is waited on with ``wait_for_function`` rather than ``expect``:
        the guard navigates a beat after ``go_back()`` returns, and an ``expect`` started
        before that pins itself to the outgoing execution context and polls it until it
        times out.
        """
        offering = _seed_draft()
        login_via_code(EMAIL)

        detail_path = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        dashboard_path = reverse("classes:teach_dashboard")
        page.goto(f"{live_server.url}{detail_path}")
        expect(page).to_have_url(re.compile(re.escape(detail_path) + "$"))
        page.evaluate("() => { window.__plGoneMarker = true; }")

        # A boosted click, because that is what puts the entry into htmx's history and so
        # makes Back a restore rather than a plain browser back.
        _boosted_click(page, dashboard_path)
        offering.delete()

        page.go_back()

        try:
            # The marker is stamped on the document the Back returns to, so losing it
            # means a real load replaced the document rather than htmx swapping into it.
            page.wait_for_function("() => !window.__plGoneMarker", timeout=15000)
            # And the load has to be the branded 404, which is the thing the guard exists
            # to reach: htmx never swaps a non-2xx, so without it this never arrives.
            page.wait_for_function("() => document.title.indexOf('Page not found') !== -1", timeout=15000)
        except PlaywrightTimeoutError:  # pragma: no cover - only on a real regression
            raise AssertionError(
                f"Back left the previous document in place under {detail_path} with title "
                f"{page.title()!r}, which is the dead Back this guard exists to prevent"
            ) from None

        expect(page).to_have_url(re.compile(re.escape(detail_path) + "$"))


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
        assert _wait_for_move(page, hotspot, before), (
            "the marker did not move after Back, so the restored editor is inert"
        )
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


# --- Browser Back (issue #383) ---------------------------------------------------------
#
# Back is an htmx history restore, and htmx builds its snapshot by serializing the body's
# innerHTML. The hub's body is not the markup the server sent: Alpine and Quill render into
# it, so the snapshot captures their OUTPUT and the restore replays it as inert markup with
# every live handler gone. The three scenarios below are the three shapes that took:
# duplicated Alpine output, a rich-text mount that came back claimed and dead, and the
# listener that stacks once per arrival. hub_boot.js answers the first by keeping no
# history cache at all; rich-editor-init.js answers the other two.

WIKI_EMAIL = "boosted-wiki@example.com"
RTE_MOUNT = ".pl-rte[data-rte-for]"
SESSION_TIMES = "#session-add-time option"
SESSION_DURATIONS = "#session-add-duration option"
SESSION_ROWS = ".session-cal__list-item"
# Alpine sets _x_dataStack on an x-data element when it initialises it. It is a property,
# so it is absent from the restored snapshot: waiting on it is the exact moment the
# restored tree became live, and counting before it would count the snapshot alone and pass
# on a page that is about to double.
COMPOSER_ALPINE_READY = """() => {
    const el = document.querySelector('.session-cal');
    return Boolean(el && el._x_dataStack);
}"""
# The mount has been claimed by THIS document's init. Quill's own marks (ql-container on
# the mount, a toolbar beside it) cannot be used after Back, because the snapshot carries
# them: they say an editor was mounted once, not that this one is live. plRteReady is a
# property, so it is absent from the snapshot and present only where the init has run.
RTE_MOUNT_CLAIMED = """() => {
    const mount = document.querySelector('.pl-rte[data-rte-for]');
    return Boolean(mount && mount.plRteReady);
}"""


def _seed_admin_with_a_wiki_page() -> WikiPage:
    """An admin who may edit the wiki, and one machine page carrying a rich-text editor."""
    MembershipPlanFactory()  # so the user signal can provision the member this then promotes
    turn_on("wiki")
    user = User.objects.create_user(username=WIKI_EMAIL, email=WIKI_EMAIL)
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["fog_role", "status"])
    member.sync_user_permissions()
    EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Woodworking"))
    call_command("seed_wiki_machine_pages", stdout=StringIO())
    return cast(WikiPage, WikiPage.objects.get(title="Table Saw"))


def describe_browser_back_into_the_class_composer():
    def it_restores_the_composer_without_duplicating_what_alpine_rendered(
        live_server, page, login_via_code, serve_media
    ):
        """Back must not double the scheduler, and the sidebar click after it must not throw.

        The issue read this as the sidebar's own ``@click`` evaluating against a data stack
        the restore never rebuilt. It is not: measured here, the sidebar click contributes
        nothing at all, and every error arrives at the restore itself. The composer's
        session scheduler is an ``x-for`` over times, durations and booked sessions; the
        snapshot captures that expansion as plain elements, and on the restore Alpine both
        initialises those orphans outside the loop that made them AND renders the template
        again from data on top of them. One Back took the start-time menu from 32 options to
        64, the duration menu from 8 to 16, listed the one scheduled session twice, and
        threw 180 uncaught errors.

        The sidebar click stays in the scenario because it is the sequence the issue
        reported and because it is a full page load (the nav carries ``hx-boost="false"``),
        which is the moment a restored page's errors would surface if any were owed.
        """
        offering = _seed_draft()
        errors = _watch_for_errors(page)
        login_via_code(EMAIL)

        edit_path = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        page.goto(f"{live_server.url}{reverse('classes:teach_dashboard')}")
        page.locator(f'a[href="{edit_path}"]').first.click()
        page.wait_for_url(re.compile(re.escape(edit_path)))
        page.wait_for_function(COMPOSER_ALPINE_READY)
        before = {
            "times": page.locator(SESSION_TIMES).count(),
            "durations": page.locator(SESSION_DURATIONS).count(),
            "rows": page.locator(SESSION_ROWS).count(),
        }
        assert before["times"] and before["durations"] and before["rows"], before

        # Leave through the composer's own Cancel link, then come back.
        page.locator("#composer-form").get_by_role("link", name="Cancel").click()
        page.wait_for_url(lambda url: edit_path not in url)
        page.wait_for_load_state("networkidle")
        page.go_back()
        page.wait_for_url(re.compile(re.escape(edit_path)))
        page.wait_for_function(COMPOSER_ALPINE_READY)

        after = {
            "times": page.locator(SESSION_TIMES).count(),
            "durations": page.locator(SESSION_DURATIONS).count(),
            "rows": page.locator(SESSION_ROWS).count(),
        }
        assert after == before, f"Back duplicated what Alpine rendered: {before} became {after}"
        assert errors == [], "\n".join(errors[:5])

        # The reported sequence, finished: a sidebar link after the restore.
        home_path = reverse("hub_home")
        page.locator(f'.hub-sidebar__nav a[href="{home_path}"]').first.click()
        page.wait_for_url(re.compile(re.escape(home_path) + "$"))
        assert errors == [], "\n".join(errors[:5])


def describe_browser_back_onto_a_rich_text_editor():
    def it_leaves_the_editor_able_to_type_through_to_the_field(live_server, page, login_via_code):
        """The restored editor has to be a live Quill, not a screenshot of one.

        ``plRteInitAll`` used to claim a mount with ``mount.dataset.rteReady``, and dataset
        writes a real attribute. Attributes round-trip through the history snapshot, so on
        Back every restored mount arrived already claimed and the init no-opped. Nothing
        looked wrong: the snapshot carries the toolbar, the container and a contenteditable
        body, so a member types happily into markup nothing is listening to and loses every
        word at save. Asserting the editor is *visible* passes against exactly that, which
        is why the assertion here is that typing reaches the hidden field the form posts.
        """
        wiki_page = _seed_admin_with_a_wiki_page()
        errors = _watch_for_errors(page)
        login_via_code(WIKI_EMAIL)

        page_path = wiki_page.get_absolute_url()
        edit_path = reverse("hub_wiki_edit", args=[wiki_page.slug])
        page.goto(f"{live_server.url}{page_path}")
        _boosted_click(page, edit_path)
        # A Quill mark, not ours, so this waits the same on a tree with the old attribute
        # key and the check below reports the revert instead of timing out on the property.
        expect(page.locator(".ql-editor").first).to_be_visible()

        # setAttribute lowercases, so compare case insensitively or a revert to dataset
        # would slip straight past this.
        assert "rte-ready" not in page.content().lower(), "the ready key is markup again, so Back will serialize it"

        _boosted_click(page, page_path, selector=f'.pl-wp-breadcrumbs a[href="{page_path}"]')
        page.wait_for_load_state("networkidle")
        page.go_back()
        page.wait_for_url(re.compile(re.escape(edit_path) + "$"))
        page.wait_for_function(RTE_MOUNT_CLAIMED)

        field_id = page.locator(RTE_MOUNT).first.get_attribute("data-rte-for")
        typed = "Keep the blade guard on."
        page.locator(".ql-editor").first.click()
        page.keyboard.type(typed)
        expect(page.locator(f"#{field_id}")).to_have_value(re.compile(re.escape(typed)))
        assert errors == [], "\n".join(errors[:5])

    def it_binds_one_settle_listener_across_repeat_arrivals(live_server, page, login_via_code):
        """Criterion 2 and 3: one listener per document, and it still serves a fresh swap.

        ``rich-editor-init.js`` is loaded from the body on six hub pages, and document
        outlives a boosted swap, so an unguarded registration leaves one more
        ``htmx:afterSettle`` listener behind per arrival. Two arrivals is the smallest case
        that can tell a real guard from a scope-local boolean, which htmx rebuilds as
        ``false`` every time it re-runs the file — and both arrivals have to land in the
        *same* document, or the scope-local version looks correct too.

        Counted by tallying calls rather than by enumerating bindings, because page
        JavaScript cannot list an element's listeners and the call count is the thing the
        defect actually multiplies. The tally works by swapping ``window.plRteInitAll``,
        which only sees the call because the listener is a wrapper that dispatches through
        the current definition; a direct reference would pin the first arrival's closure for
        the life of the document, and would report zero here.

        The same dispatch proves the guard did not disable the path it guards: a mount that
        an htmx swap brings into an already loaded page still gets an editor. That is one
        ``if`` away from being lost, and no boosted scenario would notice.
        """
        wiki_page = _seed_admin_with_a_wiki_page()
        errors = _watch_for_errors(page)
        login_via_code(WIKI_EMAIL)

        page_path = wiki_page.get_absolute_url()
        edit_path = reverse("hub_wiki_edit", args=[wiki_page.slug])
        page.goto(f"{live_server.url}{page_path}")
        _boosted_click(page, edit_path)
        page.wait_for_function(RTE_MOUNT_CLAIMED)
        _boosted_click(page, page_path, selector=f'.pl-wp-breadcrumbs a[href="{page_path}"]')
        _boosted_click(page, edit_path)
        page.wait_for_function(RTE_MOUNT_CLAIMED)

        outcome = page.evaluate(
            """() => {
                const holder = document.createElement('div');
                holder.innerHTML =
                    '<textarea id="pl-rte-settle-probe" hidden>Swapped in.</textarea>'
                    + '<div class="pl-rte" data-rte-for="pl-rte-settle-probe"></div>';
                document.body.appendChild(holder);
                const real = window.plRteInitAll;
                let calls = 0;
                window.plRteInitAll = function () { calls += 1; return real.apply(this, arguments); };
                document.dispatchEvent(new CustomEvent('htmx:afterSettle'));
                window.plRteInitAll = real;
                const mounted = holder.querySelectorAll('.ql-editor').length;
                holder.remove();
                return { calls: calls, mounted: mounted };
            }"""
        )
        assert outcome["calls"] == 1, f"one settle ran the rich-editor init {outcome['calls']} times after two arrivals"
        assert outcome["mounted"] == 1, "the guard stopped a swapped-in editor from initialising"
        assert errors == [], "\n".join(errors[:5])
