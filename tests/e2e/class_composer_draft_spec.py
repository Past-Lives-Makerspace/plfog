"""End-to-end: the composer keeps a browser copy of what was typed (issue #368, item 3c).

The whole point of this feature is what happens between page loads, which the Python suite
cannot see: whether a refresh finds the typing again, whether the offer brings back fields
from more than one step, whether a save makes the copy go away, whether Discard really
discards, and whether a second member on the same machine is ever shown the first one's
work. It drives the real ``static/js/composer_draft.js`` against the real composer and the
real ``localStorage``. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from typing import cast

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "draft-teacher@example.com"
OTHER_EMAIL = "draft-other-teacher@example.com"
ROOT = ".pl-composer[data-composer-draft-key]"
NOTICE = "[data-composer-draft]"
OFFER = '[data-composer-draft-line="offer"]'
KEPT = '[data-composer-draft-line="kept"]'
RESTORE = "[data-composer-draft-restore]"
DISCARD = "[data-composer-draft-discard]"
NEXT = "#composer-form .pl-composer-bar button:has-text('Next')"
SAVE_DRAFT = '#composer-form button[type="submit"]'
TITLE = "A Forge of One's Own"
DESCRIPTION = "Two evenings at the forge, starting from a cold anvil and a bar of mild steel."
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _seed_instructor(email: str = EMAIL) -> Member:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=email)
    slug = email.split("@")[0]
    return cast(Member, InstructorFactory(user=user, full_legal_name="Draft Teacher", instructor_slug=slug))


def _seed_draft(instructor: Member) -> ClassOffering:
    return cast(
        ClassOffering, ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT, ready=True)
    )


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before the teardown truncates the tables.

    Same hazard, and the same fix, as ``tests/e2e/class_composer_steps_spec.py``: these
    scenarios save the form and load step 5's preview iframe, so one can end with a request
    still in flight, and the live server thread still holds that request's row locks when
    ``transactional_db`` truncates. The truncate loses, as a ``DeadlockDetected`` error with
    no assertion failure behind it. Depending on ``transactional_db`` is what orders this:
    pytest finalises a fixture before the ones it depends on, so the settle always runs
    first. The Playwright error is swallowed because this is housekeeping and must never
    turn a real assertion failure into a confusing teardown error.
    """
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _settle(page) -> None:
    """Two animation frames: Alpine shows and hides an x-show pane on the next frame, not in the click."""
    page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")


def _step(page, n: int):
    return page.locator(f'[data-composer-step="{n}"]')


def _open_create(page, live_server) -> None:
    page.goto(f"{live_server.url}{reverse('classes:teach_class_create')}")
    expect(_step(page, 1)).to_be_visible()


def _open_edit(page, live_server, offering: ClassOffering) -> None:
    page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
    expect(_step(page, 1)).to_be_visible()


def _kept(page) -> None:
    """Wait for the notice to say the copy is written, which is the debounce having fired."""
    expect(page.locator(KEPT)).to_be_visible()


def _stored(page) -> dict:
    """The record the composer wrote for this page, straight out of ``localStorage``."""
    key = page.locator(ROOT).get_attribute("data-composer-draft-key")
    return page.evaluate("(key) => JSON.parse(window.localStorage.getItem(key) || 'null')", key)


def _type_a_class(page) -> None:
    """Fill step 1 and step 2, so a restore has to reach across a pane that is not on screen."""
    page.locator("#id_title").fill(TITLE)
    page.locator("#id_category").select_option(index=1)
    page.locator("#id_description").fill(DESCRIPTION)
    page.locator("#id_price_cents").fill("80")
    page.locator(NEXT).click()
    _settle(page)
    expect(_step(page, 2)).to_be_visible()
    page.locator("#id_video_url").fill(VIDEO)
    _kept(page)


def _expect_the_class_is_back(page) -> None:
    expect(page.locator("#id_title")).to_have_value(TITLE)
    expect(page.locator("#id_description")).to_have_value(DESCRIPTION)
    expect(page.locator("#id_price_cents")).to_have_value("80")
    expect(page.locator("#id_video_url")).to_have_value(VIDEO)
    assert page.locator("#id_category").input_value() != ""


def describe_a_refresh_mid_wizard():
    def it_offers_what_was_typed_and_brings_back_every_field_from_every_step(live_server, page, login_via_code):
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        _type_a_class(page)

        page.reload()
        expect(_step(page, 1)).to_be_visible()

        # Nothing is restored behind anyone's back: the page comes back as the server rendered it.
        expect(page.locator("#id_title")).to_have_value("")
        expect(page.locator(OFFER)).to_be_visible()
        expect(page.locator(OFFER)).to_contain_text("We kept what you typed in this browser at")
        expect(page.locator(OFFER)).to_contain_text("Photos and files were not kept.")

        page.locator(RESTORE).click()

        _expect_the_class_is_back(page)
        expect(page.locator(OFFER)).to_be_hidden()
        expect(page.locator(KEPT)).to_be_visible()

    def it_takes_down_a_stale_reason_under_a_field_it_fills_in(live_server, page, login_via_code):
        # The per step check flags the empty title the reload left behind; restoring fills it,
        # and composer_validation.js reconciles on the input event the restore dispatches.
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        page.locator("#id_title").fill(TITLE)
        _kept(page)

        page.reload()
        expect(_step(page, 1)).to_be_visible()
        page.locator(NEXT).click()
        _settle(page)
        expect(page.locator('[data-live-error="id_title-error"]')).to_have_count(1)

        page.locator(RESTORE).click()

        expect(page.locator("#id_title")).to_have_value(TITLE)
        expect(page.locator('[data-live-error="id_title-error"]')).to_have_count(0)
        expect(page.locator("#id_title")).not_to_have_attribute("aria-invalid", "true")

    def it_keeps_the_typed_fields_and_nothing_a_widget_owns(live_server, page, login_via_code):
        # Files cannot go in localStorage at all, and the hero crop, the scheduler's session
        # rows and the card focus are hidden inputs their widget writes: restoring one behind
        # the widget's back would leave the widget showing something else. A crop especially,
        # since it belongs to a photo that may have been replaced since.
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        page.locator("#id_title").fill(TITLE)
        page.locator("#id_description").fill(DESCRIPTION)
        _kept(page)

        record = _stored(page)

        assert set(record["values"]) == {"title", "description"}
        assert record["values"]["title"] == TITLE
        # Not vacuous: every one of these really is on the page, and none of them is kept.
        for name in ("hero_crop", "card_focus", "csrfmiddlewaretoken", "sessions-TOTAL_FORMS", "image"):
            assert page.locator(f'[name="{name}"]').count() == 1, name
            assert name not in record["values"], name


def describe_a_save():
    def it_forgets_the_copy_so_a_later_visit_is_not_offered_stale_text(live_server, page, login_via_code):
        # Create mode keeps its copy under a key of its own and the save lands on the edit URL
        # under another, so this also drives the hand off between the two.
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        create_key = page.locator(ROOT).get_attribute("data-composer-draft-key")
        _type_a_class(page)

        page.locator(SAVE_DRAFT).click()
        page.wait_for_url(re.compile(r"/edit/"))

        expect(page.locator(NOTICE)).to_be_hidden()
        assert page.evaluate("(key) => window.localStorage.getItem(key)", create_key) is None
        _open_create(page, live_server)
        expect(page.locator(NOTICE)).to_be_hidden()
        expect(page.locator("#id_title")).to_have_value("")

    def it_forgets_the_copy_on_the_class_it_just_saved(live_server, page, login_via_code):
        offering = _seed_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        page.locator("#id_title").fill(TITLE)
        _kept(page)

        page.locator(SAVE_DRAFT).click()
        page.wait_for_url(re.compile(r"step="))

        expect(page.locator(NOTICE)).to_be_hidden()
        page.reload()
        expect(page.locator(NOTICE)).to_be_hidden()
        expect(page.locator("#id_title")).to_have_value(TITLE)


def describe_discard():
    def it_drops_the_copy_and_stops_offering_it(live_server, page, login_via_code):
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        page.locator("#id_title").fill(TITLE)
        _kept(page)

        page.reload()
        expect(page.locator(OFFER)).to_be_visible()
        page.locator(DISCARD).click()

        expect(page.locator(NOTICE)).to_be_hidden()
        assert _stored(page) is None
        page.reload()
        expect(page.locator(NOTICE)).to_be_hidden()
        expect(page.locator("#id_title")).to_have_value("")


def describe_a_boosted_arrival():
    def it_keeps_working_on_a_composer_that_arrived_as_a_body_swap(live_server, page, login_via_code):
        # hub/base.html boosts the body, so Manage My Classes to Edit is an htmx swap and the
        # script tag runs again on each arrival. The re-run has to re-point the one set of
        # document listeners at the page that just arrived; a second closure would leave them
        # bound to the page that left, and nothing typed here would ever be kept. That is
        # issue #378's failure mode, and only a real second arrival shows it.
        offering = _seed_draft(_seed_instructor())
        login_via_code(EMAIL)
        edit_path = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        detail_path = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        page.goto(f"{live_server.url}{reverse('classes:teach_dashboard')}")
        page.locator(f'a[href="{edit_path}"]').first.click()
        page.wait_for_url(re.compile(re.escape(edit_path)))
        page.locator(f'a[href="{detail_path}"]').first.click()
        page.wait_for_url(re.compile(re.escape(detail_path)))
        page.locator(f'a[href="{edit_path}"]').first.click()
        page.wait_for_url(re.compile(re.escape(edit_path)))

        page.locator("#id_title").fill(TITLE)

        _kept(page)
        assert _stored(page)["values"]["title"] == TITLE


def describe_a_shared_browser():
    def it_never_offers_one_member_the_draft_of_another(live_server, page, login_via_code):
        _seed_instructor()
        other = _seed_instructor(OTHER_EMAIL)
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        page.locator("#id_title").fill(TITLE)
        _kept(page)
        mine = page.locator(ROOT).get_attribute("data-composer-draft-key")

        page.context.clear_cookies()
        login_via_code(other.user.username)
        _open_create(page, live_server)

        expect(page.locator(NOTICE)).to_be_hidden()
        expect(page.locator("#id_title")).to_have_value("")
        assert page.locator(ROOT).get_attribute("data-composer-draft-key") != mine
        # Not vacuous: the first member's copy is still sitting in this browser, unoffered.
        assert page.evaluate("(key) => window.localStorage.getItem(key)", mine) is not None
