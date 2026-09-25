"""End-to-end: per step validation on the class composer (issue #368, item 1).

Every field of every step is in the DOM and one pane is on screen at a time, so what the
browser does with a required field on a hidden step is exactly what the Python suite cannot
see: whether Next stops on the step with the gap, whether the reason lands next to the
field and clears once it is fixed, whether Back and the step tabs stay free, and whether the
Submit confirm and Save Draft jump to a gap on another step instead of opening a modal
over it (or posting a form the server would only bounce), and whether the one rule that is
not an attribute, the gallery's minimum of one photo (#424), holds Next on the Photos step and
lets Save Draft through. This drives the real ``static/js/composer_validation.js`` and the
Alpine root in ``templates/classes/_components/class_composer.html``. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import pytest
from django.urls import reverse
from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.forms import PRICE_FLOOR_MESSAGE
from classes.models import READINESS_MIN_DESCRIPTION_CHARS, ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "steps-teacher@example.com"
REQUIRED = "This field is required."
GALLERY_MESSAGE = "Add at least one gallery photo."
GALLERY_FILE_INPUT = "#gallery-file-input"
NEXT = "#composer-form .pl-composer-bar button:has-text('Next')"
BACK = "#composer-form .pl-composer-bar button:has-text('Back')"
SAVE_DRAFT = '#composer-form button[type="submit"]'
SUBMIT = "#composer-form .pl-composer-bar button:has-text('Submit for Review')"
SUBMIT_MODAL = ".pl-modal-backdrop:has-text('Submit This Class for Review?')"


def _seed_instructor() -> Member:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=EMAIL)
    return cast(Member, InstructorFactory(user=user, full_legal_name="Steps Teacher", instructor_slug="steps-teacher"))


def _seed_ready_draft(instructor: Member, **traits: object) -> ClassOffering:
    return cast(
        ClassOffering,
        ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT, ready=True, **traits),
    )


def _png(path: Path) -> Path:
    """A real, tiny PNG for the gallery pick: the zones filter on ``file.type`` and the saved class upload decodes it."""
    Image.new("RGB", (8, 8), (40, 90, 160)).save(path, "PNG")
    return path


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

    This leans on the hub chrome doing no polling: no ``setInterval``, ``EventSource``,
    ``WebSocket`` or ``hx-trigger="every"`` on these pages. Add one and ``networkidle``
    never arrives, every teardown quietly eats the timeout below, and the file goes from
    about a minute to about four with nothing reported. Wait for a different signal then.
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


def _expect_refused_on(page, n: int, control_id: str, message: str = REQUIRED) -> None:
    """The composer stayed on (or came back to) step ``n``, focused the control, and said why next to it."""
    expect(_step(page, n)).to_be_visible()
    control = page.locator(f"#{control_id}")
    expect(control).to_be_focused()
    expect(control).to_have_attribute("aria-invalid", "true")
    expect(control).to_have_attribute("aria-describedby", re.compile(rf"\b{control_id}-error\b"))
    expect(_live_error(page, control_id)).to_have_text(message)


def _expect_refused_at_the_gallery(page: Page, container_id: str) -> None:
    """Step 2 stayed, step 3 never showed, the gallery container took the focus a control would, and the
    refusal is the very next element after it, in the field error markup the rest of the composer uses."""
    _expect_refused_on(page, 2, container_id, GALLERY_MESSAGE)
    expect(_step(page, 3)).to_be_hidden()
    expect(page.locator(f"#{container_id} + ul.pl-field-errors > li.pl-field-error[role='alert']")).to_have_text(
        GALLERY_MESSAGE
    )


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

    def it_walks_a_valid_class_through_every_step(live_server, page, login_via_code, tmp_path):
        # Create mode on purpose: steps 3 and 4 are untouched, so this proves Next never blocks
        # on the defaults the server renders (capacity, discount, scheduling) or on the
        # optional fields and the empty formsets. Step 2 gets the one photo it asks for (#424).
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        page.locator("#id_title").fill("Forge Basics")
        page.locator("#id_category").select_option(index=1)
        page.locator("#id_price_cents").fill("80")

        page.locator(NEXT).click()
        _settle(page)
        expect(_step(page, 2)).to_be_visible()
        expect(_step(page, 1)).to_be_hidden()
        page.locator(GALLERY_FILE_INPUT).set_input_files(str(_png(tmp_path / "gallery.png")))
        expect(page.locator("#gallery-preview-grid .cls-image-cell")).to_have_count(1)

        for n in (3, 4, 5):
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


def describe_the_description_count():
    def it_counts_what_was_typed_toward_the_minimum_and_says_when_it_is_long_enough(live_server, page, login_via_code):
        # The counter (#425) is server markup static/js/composer_description_count.js paints into on
        # boot and on every keystroke, with the readiness rule's own arithmetic: a run of spaces is
        # one, and a bracketed phrase counts like any other words because the class page shows it.
        # Thirty typed characters read thirty; crossing the minimum changes the line. Anchored on
        # the hook, never on copy elsewhere.
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        counter = page.locator("[data-description-count]")
        expect(counter).to_have_text(f"0 of {READINESS_MIN_DESCRIPTION_CHARS} characters")

        page.locator("#id_description").fill("Make a coat hook from one bar.")

        expect(counter).to_have_text(f"30 of {READINESS_MIN_DESCRIPTION_CHARS} characters")

        page.locator("#id_description").type("   Bring <safety glasses>.")

        expect(counter).to_have_text("54 characters. Long enough.")


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


def describe_the_gallery_minimum():
    def it_holds_next_on_photos_with_no_gallery_photo_and_lets_go_once_one_lands(
        live_server, page, login_via_code, tmp_path
    ):
        # The one Next rule that is not an attribute (#424). A saved class with no gallery photo
        # stops on step 2 with the reason right after the gallery, and the reason goes the
        # moment the upload's card lands: the composer-gallery-changed event, not the file
        # input's change, because the upload is a fetch and the card arrives after that event.
        offering = _seed_ready_draft(_seed_instructor(), gallery=0)
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 2).click()
        expect(_step(page, 2)).to_be_visible()

        page.locator(NEXT).click()
        _settle(page)

        _expect_refused_at_the_gallery(page, "gallery-manager")

        page.locator(GALLERY_FILE_INPUT).set_input_files(str(_png(tmp_path / "gallery.png")))

        expect(page.locator("#gallery-grid .cls-image-cell")).to_have_count(1)
        _expect_clean(page, "gallery-manager")
        # Still on step 2: adding the photo is not the same as pressing Next.
        expect(_step(page, 2)).to_be_visible()
        assert offering.gallery_images.count() == 1

        page.locator(NEXT).click()
        _settle(page)

        expect(_step(page, 3)).to_be_visible()
        expect(_step(page, 2)).to_be_hidden()

    def it_advances_from_photos_with_a_gallery_photo(live_server, page, login_via_code):
        # The factory's default class carries one gallery photo, which is all the gate asks.
        offering = _seed_ready_draft(_seed_instructor())
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 2).click()
        expect(_step(page, 2)).to_be_visible()

        page.locator(NEXT).click()
        _settle(page)

        expect(_step(page, 3)).to_be_visible()
        expect(_step(page, 2)).to_be_hidden()
        expect(page.locator("[data-live-invalid]")).to_have_count(0)

    def it_leaves_save_draft_free_of_it(live_server, page, login_via_code):
        # A draft may be incomplete: Save Draft from step 2 with no gallery photo posts and comes
        # back to step 2 with nothing flagged. Submit keeps the server's readiness check as the
        # real gate (classes/spec/views/class_composer_spec.py).
        offering = _seed_ready_draft(_seed_instructor(), gallery=0)
        login_via_code(EMAIL)
        _open_edit(page, live_server, offering)
        _tab(page, 2).click()
        expect(_step(page, 2)).to_be_visible()

        page.locator(SAVE_DRAFT).click()

        page.wait_for_url(re.compile(r"step=2"))
        expect(_step(page, 2)).to_be_visible()
        expect(page.locator("[data-live-invalid]")).to_have_count(0)
        assert offering.gallery_images.count() == 0

    def it_advances_on_a_new_class_once_a_file_is_picked(live_server, page, login_via_code, tmp_path):
        # Create mode has no upload endpoint yet: the picked files ride the form to save, so the
        # gate counts the local preview cards under #gallery-create the same way.
        _seed_instructor()
        CategoryFactory()
        login_via_code(EMAIL)
        _open_create(page, live_server)
        page.locator("#id_title").fill("Forge Basics")
        page.locator("#id_category").select_option(index=1)
        page.locator("#id_price_cents").fill("80")
        page.locator(NEXT).click()
        _settle(page)
        expect(_step(page, 2)).to_be_visible()

        page.locator(NEXT).click()
        _settle(page)

        _expect_refused_at_the_gallery(page, "gallery-create")

        page.locator(GALLERY_FILE_INPUT).set_input_files(str(_png(tmp_path / "gallery.png")))

        expect(page.locator("#gallery-preview-grid .cls-image-cell")).to_have_count(1)
        _expect_clean(page, "gallery-create")

        page.locator(NEXT).click()
        _settle(page)

        expect(_step(page, 3)).to_be_visible()
        expect(_step(page, 2)).to_be_hidden()


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
