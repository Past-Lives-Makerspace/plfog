"""End-to-end: per step validation on the class composer (issue #368, item 1).

Every field of every step is in the DOM and one pane is on screen at a time, so what the
browser does with a required field on a hidden step is exactly what the Python suite cannot
see: whether Next stops on the step with the gap, whether the reason lands next to the
field and clears once it is fixed, whether Back and the step tabs stay free, and whether the
Submit confirm and Save Draft jump to a gap on another step instead of opening a modal
over it (or posting a form the server would only bounce). This drives the real
``static/js/composer_validation.js`` and the Alpine root in
``templates/classes/_components/class_composer.html``. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from typing import cast

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.forms import PRICE_FLOOR_MESSAGE
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "steps-teacher@example.com"
REQUIRED = "This field is required."
NEXT = "#composer-form .pl-composer-bar button:has-text('Next')"
BACK = "#composer-form .pl-composer-bar button:has-text('Back')"
SAVE_DRAFT = '#composer-form button[type="submit"]'
SUBMIT = "#composer-form .pl-composer-bar button:has-text('Submit for Review')"
SUBMIT_MODAL = ".pl-modal-backdrop:has-text('Submit This Class for Review?')"


def _seed_instructor() -> Member:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=EMAIL)
    return cast(Member, InstructorFactory(user=user, full_legal_name="Steps Teacher", instructor_slug="steps-teacher"))


def _seed_ready_draft(instructor: Member) -> ClassOffering:
    return cast(
        ClassOffering, ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT, ready=True)
    )


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before the teardown truncates the tables.

    Step 5 lazy loads a preview iframe and Save Draft posts the form, so a scenario can end
    with a request still in flight. The live server thread still holds that request's row
    locks when ``transactional_db`` truncates, and the truncate is the one that loses:
    ``psycopg.errors.DeadlockDetected``, surfaced as an ERROR with no assertion failure. It
    is timing rather than ordering, so it reproduces on some machines and not others, and CI
    runs this file on every PR.

    Depending on ``transactional_db`` is what orders this: pytest finalises a fixture before
    the ones it depends on, so the settle always runs before the truncate. It lives in a
    teardown rather than at the end of each scenario so a scenario added later cannot bring
    the flake back by forgetting the line. A Playwright error is swallowed on purpose: this
    is housekeeping, and a page left broken by a failing assertion must not turn that
    failure into a confusing teardown error.
    """
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _settle(page) -> None:
    """Two animation frames: Alpine shows and hides an x-show pane on the next frame, not in the click.

    Without this a negative assertion straight after a click (step 2 still hidden, the modal
    still closed) passes before the page has moved, and proves nothing.
    """
    page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")


def _step(page, n: int):
    return page.locator(f'[data-composer-step="{n}"]')


def _tab(page, n: int):
    return page.locator(f'[data-step-tab="{n}"]')


def _live_error(page, control_id: str):
    return page.locator(f'[data-live-error="{control_id}-error"] .pl-field-error')


def _errors_under(page, control_id: str):
    """Every field error under that control, whoever wrote it: Django renders its list as a
    sibling of the input and the live one is inserted as a sibling too, so one selector sees
    both and a count of two is the stacking bug."""
    return page.locator(f"#{control_id} ~ .pl-field-errors .pl-field-error")


def _open_create(page, live_server) -> None:
    page.goto(f"{live_server.url}{reverse('classes:teach_class_create')}")
    expect(_step(page, 1)).to_be_visible()


def _open_edit(page, live_server, offering: ClassOffering) -> None:
    page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
    expect(_step(page, 1)).to_be_visible()


def _expect_refused_on(page, n: int, control_id: str) -> None:
    """The composer stayed on (or came back to) step ``n``, focused the control, and said why next to it."""
    expect(_step(page, n)).to_be_visible()
    control = page.locator(f"#{control_id}")
    expect(control).to_be_focused()
    expect(control).to_have_attribute("aria-invalid", "true")
    expect(control).to_have_attribute("aria-describedby", re.compile(rf"\b{control_id}-error\b"))
    expect(_live_error(page, control_id)).to_have_text(REQUIRED)


def _expect_clean(page, control_id: str) -> None:
    control = page.locator(f"#{control_id}")
    expect(_live_error(page, control_id)).to_have_count(0)
    expect(control).not_to_have_attribute("aria-invalid", "true")
    expect(control).not_to_have_attribute("aria-describedby", re.compile(rf"\b{control_id}-error\b"))


def describe_next():
    def it_stays_on_the_step_with_the_gap_names_it_inline_and_clears_it_once_typed(live_server, page, login_via_code):
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)

        page.locator(NEXT).click()
        _settle(page)

        expect(_step(page, 1)).to_be_visible()
        expect(_step(page, 2)).to_be_hidden()
        _expect_refused_on(page, 1, "id_title")

        page.locator("#id_title").type("Forge Basics")
        _expect_clean(page, "id_title")
        # Still on step 1: fixing the field is not the same as pressing Next.
        expect(_step(page, 1)).to_be_visible()

    def it_walks_a_valid_class_through_every_step(live_server, page, login_via_code):
        # Create mode on purpose: steps 3 and 4 are untouched, so this proves Next never blocks
        # on the defaults the server renders (capacity, discount, scheduling) or on the
        # optional fields and the empty formsets.
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        page.locator("#id_title").fill("Forge Basics")
        page.locator("#id_category").select_option(index=1)
        page.locator("#id_price_cents").fill("80")

        for n in (2, 3, 4, 5):
            page.locator(NEXT).click()
            _settle(page)
            expect(_step(page, n)).to_be_visible()
            expect(_step(page, n - 1)).to_be_hidden()
        expect(page.locator("[data-live-invalid]")).to_have_count(0)

    def it_never_blocks_on_a_half_typed_date_in_the_scheduler(live_server, page, login_via_code):
        # session-add-date is the scheduler's own picker: no name, never posted, it only feeds
        # the hidden sessions-N-* inputs. A half typed date makes it report badInput, and that
        # is exactly what an interrupted person leaves behind on the row they add dates with.
        # No save can be refused for it, so Next must not stop for it either.
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 3).click()
        expect(_step(page, 3)).to_be_visible()
        page.locator("#session-add-date").type("12")
        # Not vacuous: the control really is invalid, the walk simply has no business reading it.
        assert page.evaluate("() => document.getElementById('session-add-date').validity.badInput")

        page.locator(NEXT).click()
        _settle(page)

        expect(_step(page, 4)).to_be_visible()
        expect(_step(page, 3)).to_be_hidden()
        expect(page.locator("[data-live-invalid]")).to_have_count(0)

    def it_never_blocks_on_a_short_description(live_server, page, login_via_code):
        # Description length is a readiness item (submit), not a form rule: Next lets it through.
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        page.locator("#id_title").fill("Forge Basics")
        page.locator("#id_category").select_option(index=1)
        page.locator("#id_description").fill("Short.")
        page.locator("#id_price_cents").fill("80")

        page.locator(NEXT).click()
        _settle(page)

        expect(_step(page, 2)).to_be_visible()


def describe_back_and_tabs():
    def it_never_blocks_back_from_an_invalid_step(live_server, page, login_via_code):
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 3).click()
        expect(_step(page, 3)).to_be_visible()
        page.locator("#id_capacity").fill("")

        page.locator(BACK).click()
        _settle(page)

        expect(_step(page, 2)).to_be_visible()
        expect(_step(page, 3)).to_be_hidden()
        expect(page.locator("[data-live-invalid]")).to_have_count(0)

    def it_never_blocks_a_tab_click_from_an_invalid_step(live_server, page, login_via_code):
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)

        _tab(page, 5).click()
        _settle(page)

        expect(_step(page, 5)).to_be_visible()
        expect(_step(page, 1)).to_be_hidden()
        expect(page.locator("[data-live-invalid]")).to_have_count(0)


def describe_the_submit_confirm():
    def it_jumps_to_the_gap_on_another_step_instead_of_opening_the_modal(live_server, page, login_via_code):
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        page.locator("#id_price_cents").fill("")
        _tab(page, 5).click()
        expect(_step(page, 5)).to_be_visible()

        page.locator(SUBMIT).click()
        _settle(page)

        expect(page.locator(SUBMIT_MODAL)).to_be_hidden()
        expect(_step(page, 5)).to_be_hidden()
        _expect_refused_on(page, 1, "id_price_cents")

    def it_opens_the_modal_for_a_valid_class(live_server, page, login_via_code):
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 5).click()

        page.locator(SUBMIT).click()
        _settle(page)

        expect(page.locator(SUBMIT_MODAL)).to_be_visible()
        expect(_step(page, 5)).to_be_visible()


def describe_save_draft():
    def it_jumps_to_the_gap_instead_of_posting(live_server, page, login_via_code):
        # A draft save with a required field empty is a round trip the server can only bounce
        # (and in create mode it would drop a picked hero photo on the way), so the button
        # points at the gap first, exactly like Next.
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 3).click()
        page.locator("#id_capacity").fill("")
        _tab(page, 1).click()
        expect(_step(page, 1)).to_be_visible()
        url_before = page.url

        page.locator(SAVE_DRAFT).click()
        _settle(page)

        _expect_refused_on(page, 3, "id_capacity")
        assert page.url == url_before

    def it_posts_a_valid_draft(live_server, page, login_via_code):
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 3).click()
        page.locator("#id_capacity").fill("9")

        page.locator(SAVE_DRAFT).click()

        page.wait_for_url(re.compile(r"step=3"))
        offering.refresh_from_db()
        assert offering.capacity == 9


def describe_formset_rows():
    def it_never_blocks_on_an_added_row_left_blank(live_server, page, login_via_code):
        # Django skips an untouched extra formset row (empty_permitted); so does Next.
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 4).click()
        expect(_step(page, 4)).to_be_visible()
        page.get_by_role("button", name="+ Add a question").click()
        added = page.locator("#faq-rows .hub-card").last
        expect(added.locator("textarea")).to_have_value("")

        page.locator(NEXT).click()
        _settle(page)

        expect(_step(page, 5)).to_be_visible()

    def it_leaves_a_half_filled_row_to_the_server_which_refuses_it_at_save(live_server, page, login_via_code):
        # Django renders formset rows without `required`, so the browser has no rule to read and
        # Next lets the row through; the save comes back on step 4 with the reason under the
        # answer, the way a failed save always has.
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 4).click()
        page.get_by_role("button", name="+ Add a question").click()
        added = page.locator("#faq-rows .hub-card").last
        added.locator("input[type='text']").fill("Do I need my own tools?")
        answer_id = added.locator("textarea").get_attribute("id")
        assert answer_id

        page.locator(NEXT).click()
        _settle(page)
        expect(_step(page, 5)).to_be_visible()

        page.locator(SAVE_DRAFT).click()

        expect(page.locator(".pl-composer-errors")).to_be_visible()
        expect(_step(page, 4)).to_be_visible()
        answer = page.locator(f"#{answer_id}")
        expect(answer).to_have_attribute("aria-invalid", "true")
        expect(answer.locator("xpath=following-sibling::ul[1]/li")).to_have_text(REQUIRED)
        assert offering.faqs.count() == 0


def describe_a_server_message_and_a_live_one():
    def it_keeps_one_reason_under_the_control_instead_of_stacking_them(live_server, page, login_via_code):
        # The $1.00 floor is a server rule with no rendered attribute behind it, so 50c passes
        # the client and comes back as Django's own message under the price. Clearing the field
        # and pressing Next replaces that message rather than inserting a second list above it:
        # two lists is two reasons fighting over one aria-invalid, and clearing the live one
        # would drop the flag while the server's text stayed on screen.
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        page.locator("#id_price_cents").fill("0.5")

        page.locator(SAVE_DRAFT).click()

        expect(page.locator(".pl-composer-errors")).to_be_visible()
        expect(_step(page, 1)).to_be_visible()
        expect(_errors_under(page, "id_price_cents")).to_have_text([PRICE_FLOOR_MESSAGE])
        expect(page.locator("#id_price_cents")).to_have_attribute("aria-invalid", "true")

        page.locator("#id_price_cents").fill("")
        page.locator(NEXT).click()
        _settle(page)

        _expect_refused_on(page, 1, "id_price_cents")
        expect(_errors_under(page, "id_price_cents")).to_have_text([REQUIRED])
        # Django's own describedby token went with its message; only the live one is left.
        expect(page.locator("#id_price_cents")).not_to_have_attribute(
            "aria-describedby", re.compile(r"\bid_price_cents_error\b")
        )

        page.locator("#id_price_cents").fill("80")

        _expect_clean(page, "id_price_cents")
        expect(_errors_under(page, "id_price_cents")).to_have_count(0)
