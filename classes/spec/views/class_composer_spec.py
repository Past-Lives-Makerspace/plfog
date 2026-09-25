"""BDD specs for the five step class composer (teach and admin twins of one shared template)."""

from __future__ import annotations

import io
import json
import re
from datetime import timedelta
from html import unescape
from html.parser import HTMLParser

import pytest
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from classes.factories import (
    BRACKETED_DESCRIPTION,
    READY_DESCRIPTION,
    CategoryFactory,
    ClassOfferingFactory,
    InstructorFactory,
    UserFactory,
)
from classes.forms import ClassOfferingForm, TeachClassOfferingForm
from classes.models import READINESS_DESCRIPTION_HINT, ClassApproval, ClassOffering, ClassSettings, CmsActivity
from classes.views import COMPOSER_SAVED_LIMIT, COMPOSER_SAVED_SESSION_KEY, _mark_composer_saved
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

Status = ClassOffering.Status

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


class _ComposerParser(HTMLParser):
    """Pulls the composer root's x-data attribute (entity decoded) and the page's visible text."""

    def __init__(self) -> None:
        super().__init__()
        self.x_data: str | None = None
        self.text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if "pl-composer" in (attributes.get("class") or "").split() and self.x_data is None:
            self.x_data = attributes.get("x-data") or ""
        if tag in {"script", "style", "template"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.text.append(data)


class _FormValuesParser(HTMLParser):
    """What a browser would submit from the composer form as rendered: every control at its default."""

    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}
        self._in_form = False
        self._select: str | None = None
        self._select_first: str | None = None
        self._select_chosen = False
        self._textarea: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form" and a.get("id") == "composer-form":
            self._in_form = True
        if not self._in_form:
            return
        name = a.get("name")
        if tag == "input" and name:
            kind = a.get("type", "text")
            if kind in {"file", "submit", "button"}:
                return
            if kind in {"checkbox", "radio"} and "checked" not in a:
                return
            self.values[name] = a.get("value") or ""
        elif tag == "select" and name:
            self._select, self._select_first, self._select_chosen = name, None, False
        elif tag == "option" and self._select:
            value = a.get("value") or ""
            if self._select_first is None:
                self._select_first = value
            if "selected" in a:
                self.values[self._select] = value
                self._select_chosen = True
        elif tag == "textarea" and name:
            self._textarea = name
            self.values[name] = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._select:
            if not self._select_chosen:
                self.values[self._select] = self._select_first or ""
            self._select = None
        elif tag == "textarea":
            self._textarea = None
        elif tag == "form" and self._in_form:
            self._in_form = False

    def handle_data(self, data: str) -> None:
        if self._textarea:
            self.values[self._textarea] += data


def _untouched_form_values(html: str) -> dict[str, str]:
    parser = _FormValuesParser()
    parser.feed(html)
    parser.values.pop("csrfmiddlewaretoken", None)
    return parser.values


def _composer_x_data(html: str) -> str:
    parser = _ComposerParser()
    parser.feed(html)
    assert parser.x_data is not None, "no .pl-composer root found"
    return parser.x_data


def _visible_text(html: str) -> str:
    parser = _ComposerParser()
    parser.feed(html)
    return " ".join(parser.text)


def _ComposerParser_x_data(html: str) -> str:  # noqa: N802  # reads as "the parser's x_data"
    parser = _ComposerParser()
    parser.feed(html)
    assert parser.x_data is not None
    return parser.x_data


def _price_input_value(html: str) -> str:
    """The value attribute of the price input; Django omits the attribute entirely when the value is blank."""
    tag = re.search(r'<input[^>]*name="price_cents"[^>]*>', html)
    assert tag is not None, "no price input rendered"
    match = re.search(r'\bvalue="([^"]*)"', tag.group(0))
    return match.group(1) if match else ""


def _draft_key(html: str) -> str:
    """The localStorage key the composer told the browser to keep its in flight typing under."""
    match = re.search(r'data-composer-draft-key="([^"]+)"', html)
    assert match is not None, "the composer rendered no draft key"
    return match.group(1)


def _draft_baseline(html: str) -> str:
    """The saved values the composer stamped on an unsaved render, still JSON."""
    match = re.search(r'data-composer-draft-baseline="([^"]*)"', html)
    assert match is not None, "the composer stamped no baseline"
    return unescape(match.group(1))


def _draft_notice(html: str) -> str:
    """The draft persistence notice's markup, or "" when the composer rendered none."""
    opener = '<div class="pl-composer-draft" '
    if opener not in html:
        return ""
    return html.partition(opener)[2].partition("</div>")[0]


def _still_missing(html: str) -> str:
    """The Still Missing notice's markup, or "" when the composer rendered none."""
    opener = '<section class="pl-composer-missing '
    if opener not in html:
        return ""
    return html.partition(opener)[2].partition("</section>")[0]


LEGACY_PHOTO = "https://classes.pastlives.space/sites/default/files/glen.jpg"

IMPORTED_PHOTO_NOTE = (
    "This photo came over from the old class site, so the crop box is off for it. "
    "To choose which part shows on the banner, click Preview and use Adjust under the photo. "
    "Upload a new photo to crop it here."
)


def _hero_preview_img(html: str) -> str:
    """The opening tag of the photo inside #hero-preview, or "" when the preview is empty."""
    preview = html.partition('id="hero-preview"')[2].partition("</div>")[0]
    match = re.search(r"<img[^>]*>", preview)
    return match.group(0) if match else ""


def _legacy_note_text(html: str) -> str:
    """The imported photo note's text with whitespace collapsed, or "" when none rendered."""
    match = re.search(r'<p[^>]*id="hero-legacy-note"[^>]*>(.*?)</p>', html, re.DOTALL)
    return " ".join(match.group(1).split()) if match else ""


def _crop_hint_tag(html: str) -> str:
    """The opening tag of the banner pane's crop hint."""
    match = re.search(r'<p[^>]*id="hero-crop-hint"[^>]*>', html)
    assert match is not None, "no crop hint rendered"
    return match.group(0)


def _hero_crop_value(html: str) -> str:
    """The value the rendered hidden hero_crop input would post back."""
    return _untouched_form_values(html)["hero_crop"]


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="composer-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher C", instructor_slug="teacher-c")


def _management() -> dict:
    return {
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }


def _full_payload(category, **extra) -> dict:
    """Every field on the teach form, each with a value the round trip can recognise."""
    payload = {
        "title": "Round Trip",
        "category": category.pk,
        "description": READY_DESCRIPTION,
        "prerequisites": "Bring patience.",
        "materials_included": "All the wood.",
        "materials_to_bring": "Gloves.",
        "safety_requirements": "Eye protection.",
        "age_minimum": "16",
        "age_guardian_note": "Guardians welcome.",
        "price_cents": "80.00",
        "member_discount_pct": "15",
        "capacity": "8",
        "scheduling_model": "flexible",
        "scheduling_type": "series_package",
        "flexible_note": "We will find a time together.",
        "video_url": VIDEO,
        "hero_crop": json.dumps({"x": 10, "y": 20, "w": 320, "h": 180}),
        "card_focus": json.dumps({"x": 30, "y": 70}),
        "action": "save",
        "step": "4",
        **_management(),
    }
    payload.update(extra)
    return payload


def _admin_payload(category, inst, **extra) -> dict:
    payload = _full_payload(category, instructor=inst.pk, is_private="on", private_for_name="The Guild")
    payload.update(extra)
    return payload


def _assert_round_trip(offering: ClassOffering, category) -> None:
    assert offering.title == "Round Trip"
    assert offering.category_id == category.pk
    assert offering.description == READY_DESCRIPTION
    assert offering.prerequisites == "Bring patience."
    assert offering.materials_included == "All the wood."
    assert offering.materials_to_bring == "Gloves."
    assert offering.safety_requirements == "Eye protection."
    assert offering.age_minimum == 16
    assert offering.age_guardian_note == "Guardians welcome."
    assert offering.price_cents == 8000
    assert offering.capacity == 8
    assert offering.scheduling_model == "flexible"
    assert offering.scheduling_type == "series_package"
    assert offering.flexible_note == "We will find a time together."
    assert offering.video_url == VIDEO
    assert (offering.card_focus_x, offering.card_focus_y) == (30, 70)


def _png_upload(name: str, width: int, height: int) -> SimpleUploadedFile:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


# The create-mode cropper measures the original file the browser showed: the left half of a
# 4800x2700 photo, framed 16:9, is this box in original pixels.
LEFT_HALF_OF_4800 = {"x": 0, "y": 0, "w": 2400, "h": 1350}


def _assert_crop_followed_the_downsize(offering: ClassOffering) -> None:
    """save() capped the 4800px photo at 2400; the stored box is the left half of THAT photo.

    A 16:9 frame on the left half of a 16:9 photo covers the top half of that half, so its
    centre sits a quarter in and a quarter down. Unscaled, the box would cover the whole
    stored photo and read 50.0% 50.0%.
    """
    assert (offering.image.width, offering.image.height) == (2400, 1350)
    assert (offering.hero_crop_x, offering.hero_crop_y, offering.hero_crop_w, offering.hero_crop_h) == (0, 0, 1200, 675)
    assert offering.hero_object_position == "25.0% 25.0%"


def describe_the_step_map_matches_the_payload():
    def it_posts_every_teach_form_field():
        # The round trip below only proves what it posts; this keeps the payload honest.
        posted = set(_full_payload(CategoryFactory.build()))
        assert set(TeachClassOfferingForm().fields) - {"image"} <= posted

    def it_posts_every_admin_form_field():
        posted = set(_admin_payload(CategoryFactory.build(), InstructorFactory.build()))
        assert set(ClassOfferingForm().fields) - {"image"} <= posted


def describe_teach_composer_get():
    def it_stamps_every_pane_and_listens_for_the_goto_event(instructor_fixture, client):
        # The guided tour reveals a hidden pane through this contract (static/js/pl_tour.js).
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        for n in range(1, 6):
            assert f'data-composer-step="{n}"' in html, n
        assert html.count("data-composer-step=") == 5
        assert '@composer-goto-step.window="goTo($event.detail.step)"' in html

    def it_announces_every_step_reveal_for_widgets_that_measure_their_pane(instructor_fixture, client):
        # The hero cropper (static/js/hero_cropper.js) can only size itself inside a pane that is
        # on screen, so the root dispatches composer-step-shown after each phase change has
        # painted, and once for the opening step so a ?step=2 load is announced too.
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        x_data = _composer_x_data(html)
        assert (
            "this.$watch('phase', (step) => this.$nextTick(() => this.$dispatch('composer-step-shown', { step })))"
            in x_data
        )
        assert "this.$nextTick(() => this.$dispatch('composer-step-shown', { step: this.phase }))" in x_data

    def it_clears_the_crop_before_remounting_the_cropper_on_a_new_photo_in_both_modes(instructor_fixture, client):
        # Both branches of hero_image_field.html swap a new img into #hero-preview, forget the
        # old crop box, and then hand the img to window.initHeroCropper() (hero_cropper.js).
        client.force_login(instructor_fixture.user)
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        pages = {
            "create": client.get(reverse("classes:teach_class_create")).content.decode(),
            "edit": client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode(),
        }
        for mode, html in pages.items():
            field = html.split("data-hero-image-field")[1].split("</script>")[0]
            assert 'id="hero-preview"' in field, mode
            assert field.index("cropInput.value = ''") < field.index("window.initHeroCropper()"), mode

    def it_puts_the_price_on_the_first_step(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        step_one = html[html.index('data-composer-step="1"') : html.index('data-composer-step="2"')]
        assert "What It Costs" in step_one
        assert 'name="price_cents"' in step_one and 'name="is_free"' not in step_one
        assert 'data-help-key="teach.class-pricing"' in step_one
        step_three = html[html.index('data-composer-step="3"') : html.index('data-composer-step="4"')]
        assert 'name="capacity"' in step_three
        assert 'name="member_discount_pct"' not in step_three  # the admin's to set (#369)
        assert 'id="member-discount-note"' in step_three
        assert 'name="price_cents"' not in step_three

    def it_leaves_validation_to_the_server(instructor_fixture, client):
        # Every step's fields are in the DOM and a hidden step cannot be focused, so the browser's
        # own required check refuses silently from any step but the field's own. novalidate keeps
        # the server path (it_bounces_a_step_one_save_with_no_price_to_step_one and the price floor
        # specs) the one that refuses; the per step check (composer_step_validation_spec.py) reads
        # the same rendered attributes and points at the field, which the browser's bubble cannot.
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert re.search(r'<form[^>]*id="composer-form"[^>]*\bnovalidate\b', html)

    def it_renders_the_five_tabs_and_lands_on_step_one(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        for label in ["1. Basics", "2. Photos", "3. Dates &amp; Price", "4. Details", "5. Review"]:
            assert label in html
        assert "phase: 1," in html
        assert "The Basics" in html and "Photos And Video" in html and "Dates, Seats And Price" in html
        assert "What Students Need To Know" in html and "Review And Submit" in html
        assert "Your Photo, Two Shapes" in html
        assert 'data-help-key="teach.class-basics"' in html
        assert 'data-help-key="teach.class-schedule"' in html
        assert 'data-help-key="teach.class-pricing"' in html
        assert "Save Draft" in html
        assert "Submit for Review" in html
        assert "Submit This Class for Review?" in html

    def it_hides_everything_that_needs_a_saved_row_on_create(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert "Where Your Class Is" not in html
        assert "Ready to Submit?" not in html
        assert "/preview/" not in html
        assert "pl-qr-share" not in html
        assert 'id="gallery-manager"' not in html and 'id="gallery-create"' in html
        assert "Upload a photo and your card shows up here." in html
        assert "Save your draft once" in html
        assert f'href="{reverse("classes:teach_dashboard")}">Cancel</a>' in html
        assert "data-card-focus-input" in html

    def it_mirrors_the_title_into_a_skeleton_card_before_the_first_save(instructor_fixture, client):
        """Create mode has no CatalogGroup, so the mirrored photo sits in a skeleton card."""
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert "Dates, price and spots show up after your first save." in html
        assert (
            html.count('<span class="cls-title" x-text="liveTitle || \'Your class title\'">Your class title</span>')
            == 2
        )
        assert "liveTitle: ''" in _ComposerParser_x_data(html)
        assert 'class="pl-card-focus__sliders" x-show="localSrc" x-cloak' in html
        assert "photo only" not in html
        assert "Add a photo above and the sliders appear." not in html

    def it_says_an_imported_photo_counts_on_the_photos_step(instructor_fixture, client):
        """A legacy only class has its own hero: the note, the preview, and a ticked checklist.

        The crop box cannot position an imported photo, so the preview carries no cropper
        hook, the box hint is hidden, and the note sends the editor to Adjust instead.
        """
        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=Status.DRAFT,
            ready=True,
            image="",
            legacy_image_url=LEGACY_PHOTO,
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert _legacy_note_text(html) == IMPORTED_PHOTO_NOTE
        assert "Everything here works the same." not in html
        hero = _hero_preview_img(html)
        assert "_legacy-image/?url=https%3A%2F%2Fclasses.pastlives.space" in hero
        src = re.search(r'src="([^"]*)"', hero)
        assert src is not None, "no src on the hero preview"
        assert unescape(src.group(1)) == offering.hero_image_url
        assert "data-hero-cropper-preview" not in hero
        assert _crop_hint_tag(html).endswith(" hidden>")
        assert "Replace image" in html
        assert "Add a hero photo." not in html
        assert html.count("pl-phase-tab--done") == 3

    def it_offers_the_crop_box_on_an_uploaded_photo(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "data-hero-cropper-preview" in _hero_preview_img(html)
        assert not _crop_hint_tag(html).endswith(" hidden>")

    def it_shows_the_crop_hint_before_the_first_save(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert not _crop_hint_tag(html).endswith(" hidden>")

    def it_says_nothing_about_the_old_site_for_an_uploaded_photo(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "This photo came over from the old class site." not in html
        assert 'id="hero-legacy-note"' not in html

    def it_shows_the_saved_row_surfaces_on_edit(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        preview = reverse("classes:class_preview", kwargs={"pk": offering.pk})
        assert "Where Your Class Is" in html and "Not submitted yet" in html
        assert "Ready to Submit?" in html
        assert f'src="{preview}?framed=1"' in html
        assert "Refresh Preview" in html and "Open in a New Tab" in html
        assert "Building your preview" in html
        assert "How Your Page Looks" in html and "How Your Card Looks" in html
        assert "pl-qr-share" in html
        assert 'id="gallery-manager"' in html
        assert f'href="{reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})}">Cancel</a>' in html
        assert f'href="{preview}" target="_blank" rel="noopener">Preview' in html
        assert "On a Laptop" in html and "On a Phone" in html
        assert "Match the Banner" in html

    def it_marks_the_three_ready_steps_and_never_the_last_two(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert html.count("pl-phase-tab--done") == 3
        assert html.count("pl-phase-tab__mark") >= 3

    def it_marks_nothing_on_an_unready_draft_and_disables_submit(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, image="", gallery=0)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "pl-phase-tab--done" not in html
        assert "Finish the checklist above first." in html
        assert "disabled" in html.split("Finish the checklist above first.")[0].rsplit("<button", 1)[1]
        # Readiness hints jump to their step instead of linking to a hidden anchor.
        assert "goToField('hero-preview')\">Add a hero photo.</button>" in html
        assert "goToField('gallery-manager')\">Add one gallery photo.</button>" in html
        assert 'href="#hero-preview"' not in html

    def it_enables_submit_on_a_ready_draft(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "Finish the checklist above first." not in html
        assert "confirmSubmit('submit-class')\">Submit for Review</button>" in html

    def it_relabels_submit_on_a_bounced_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.CHANGES_REQUESTED,
            notes="More photos",
            decided_at=timezone.now(),
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "Fix and Resubmit" in html
        assert "Fix the notes below and submit again." in html

    def it_offers_no_submit_on_a_pending_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "Ready to Submit?" not in html
        assert "submit-class" not in html
        assert "You can still edit it until it is approved." in html

    def it_opens_on_the_requested_step(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert "phase: 3," in client.get(f"{url}?step=3").content.decode()
        assert "phase: 5," in client.get(f"{url}?step=9").content.decode()
        assert "phase: 1," in client.get(f"{url}?step=x").content.decode()
        assert "phase: 1," in client.get(url).content.decode()

    def it_carries_the_anchor_map_for_the_readiness_jumps(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        x_data = _composer_x_data(html)
        assert '"hero-preview": 2' in x_data and '"class-dates": 3' in x_data and '"id_description": 1' in x_data

    def it_keeps_the_whole_component_inside_the_x_data_attribute(instructor_fixture, client):
        # A raw double quote inside x-data="..." ends the attribute early and dumps the rest of
        # the component onto the page as text, with no step rendered. Parse the DOM, not the source.
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        x_data = _composer_x_data(html)
        assert x_data.startswith("{")
        assert x_data.rstrip().endswith("}")
        for needle in [
            "phase: 1,",
            "errorSteps: [],",
            "anchorSteps: {",
            "goTo(n) {",
            "goToField(id) {",
            "reloadPreview() {",
        ]:
            assert needle in x_data, needle
        assert "goTo(n)" not in _visible_text(html)


def describe_teach_composer_post():
    def it_round_trips_every_field_on_create(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_create"), _full_payload(cat, step="2"))
        assert resp.status_code == 302
        created = ClassOffering.objects.get(title="Round Trip")
        assert resp["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": created.pk}) + "?step=2"
        assert created.status == Status.DRAFT
        assert created.instructor_id == instructor_fixture.pk
        _assert_round_trip(created, cat)
        assert created.member_discount_pct == 10  # the studio default, not the payload's 15 (#369)
        assert "Draft saved." in _messages(resp)

    def it_round_trips_every_field_on_edit_and_returns_to_the_step(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, title="Before")
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), _full_payload(cat))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}) + "?step=4"
        offering.refresh_from_db()
        _assert_round_trip(offering, cat)
        assert offering.member_discount_pct == 10  # untouched by the payload's 15 (#369)
        assert (offering.hero_crop_x, offering.hero_crop_y, offering.hero_crop_w, offering.hero_crop_h) == (
            10,
            20,
            320,
            180,
        )
        assert "Draft saved." in _messages(resp)

    def it_keeps_an_adjust_focal_point_on_an_imported_photo_through_a_composer_save(instructor_fixture, client):
        """A Save from a composer opened before an Adjust must not write the dead box back."""
        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=Status.DRAFT,
            image="",
            legacy_image_url=LEGACY_PHOTO,
            hero_crop_x=100,
            hero_crop_y=50,
            hero_crop_w=800,
            hero_crop_h=450,
            card_focus_x=None,
            card_focus_y=None,
        )
        # The saved box is ignored on an imported photo: the bug as reported.
        assert offering.card_object_position == "50% 50%"
        client.force_login(instructor_fixture.user)
        edit = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        rendered_crop = _hero_crop_value(client.get(edit).content.decode())

        adjust = client.post(
            reverse("hub_hero_adjust"),
            {
                "content_type_id": ContentType.objects.get_for_model(ClassOffering).pk,
                "object_id": offering.pk,
                "crop": {"x": 30, "y": 70, "w": 0, "h": 0},
            },
            content_type="application/json",
        )
        assert adjust.status_code == 200

        resp = client.post(edit, _full_payload(CategoryFactory(), hero_crop=rendered_crop, card_focus=""))
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.hero_object_position == "30% 70%"
        assert offering.card_object_position == "30% 70%"

    def it_shrinks_a_create_mode_crop_with_the_downsized_upload(instructor_fixture, client, settings):
        # Create mode crops the original file (the FileReader preview); save() then caps the long
        # edge, so the box has to shrink with it or the stored centre points at the wrong part of
        # the stored photo. Edit mode is safe on its own: the instant upload normalises first and
        # the browser crops the stored file.
        settings.IMAGE_MAX_LONG_EDGE_HERO = 2400
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        payload = _full_payload(
            cat, image=_png_upload("phone-shot.png", 4800, 2700), hero_crop=json.dumps(LEFT_HALF_OF_4800)
        )
        resp = client.post(reverse("classes:teach_class_create"), payload)
        assert resp.status_code == 302
        _assert_crop_followed_the_downsize(ClassOffering.objects.get(title="Round Trip"))

    def it_clears_the_card_focus_when_matching_the_banner(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, card_focus_x=1, card_focus_y=2
        )
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, card_focus=""),
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.card_focus_x is None and offering.card_focus_y is None

    def it_redirects_without_a_step_when_the_post_carried_none(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        payload = _full_payload(offering.category)
        payload.pop("step")
        resp = client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), payload)
        assert resp["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})

    def it_says_class_updated_on_a_pending_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), _full_payload(offering.category)
        )
        assert "Class updated." in _messages(resp)

    def it_lands_on_the_first_broken_step_and_lists_the_labels(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="", price_cents="", step="4"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "phase: 1," in html
        assert "errorSteps: [1]," in html
        assert "Some Things Need Fixing" in html
        assert 'goTo(1)">The Basics: Title, Price</button>' in html

    def it_lands_on_step_three_alone_for_a_capacity_error(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, capacity="", step="1"),
        )
        html = resp.content.decode()
        assert "phase: 3," in html
        assert "errorSteps: [3]," in html
        assert 'goTo(3)">Dates, Seats And Price: Capacity</button>' in html

    def it_saves_a_draft_from_step_one_alone(instructor_fixture, client):
        # Title, guild type, description, and the price are the whole of step 1. Steps 2 to 4 are
        # untouched: the POST is exactly what a browser submits from the rendered page (every
        # field with its default, parsed from the GET), plus step 1. capacity, scheduling_model
        # and scheduling_type are required form fields with model defaults, so they ride along
        # as the composer renders them; a literal four field POST is not what a browser sends.
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        untouched = _untouched_form_values(client.get(reverse("classes:teach_class_create")).content.decode())
        assert untouched["capacity"] == "6"
        assert untouched["scheduling_model"] == "fixed"
        assert untouched["scheduling_type"] == "single_session"
        assert "member_discount_pct" not in untouched  # the admin's to set (#369)
        assert untouched["price_cents"] == ""
        resp = client.post(
            reverse("classes:teach_class_create"),
            {
                **untouched,
                **_management(),
                "title": "Step One Draft",
                "category": cat.pk,
                "description": "Just the pitch for now.",
                "price_cents": "45.00",
                "action": "save",
                "step": "1",
            },
        )
        assert resp.status_code == 302, _visible_text(resp.content.decode())[:600]
        created = ClassOffering.objects.get(title="Step One Draft")
        assert created.status == Status.DRAFT
        assert created.price_cents == 4500
        assert created.member_discount_pct == 10
        assert created.capacity == 6
        assert created.scheduling_model == "fixed"
        assert resp["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": created.pk}) + "?step=1"
        assert "Draft saved." in _messages(resp)

    def it_bounces_a_step_one_save_with_no_price_to_step_one(instructor_fixture, client):
        # The price is the one thing a draft cannot be saved without, and it is on step 1.
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        untouched = _untouched_form_values(client.get(reverse("classes:teach_class_create")).content.decode())
        resp = client.post(
            reverse("classes:teach_class_create"),
            {**untouched, **_management(), "title": "No Price", "category": cat.pk, "action": "save", "step": "1"},
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "phase: 1," in html
        assert "errorSteps: [1]," in html
        assert "This field is required." in html
        assert not ClassOffering.objects.filter(title="No Price").exists()

    def it_lands_a_bad_card_focus_on_the_photos_step(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, card_focus=json.dumps({"x": 300, "y": 0})),
        )
        html = resp.content.decode()
        assert "phase: 2," in html
        assert "Focal point must be between 0 and 100." in html

    def it_submits_for_review_from_the_composer(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(
                offering.category, scheduling_model="fixed", scheduling_type="single_session", action="submit"
            ),
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.status == Status.PENDING


def describe_the_submit_check_reads_the_posted_description():
    """Issue #425 named two candidate causes for a typed description being refused as too short.

    Candidate 2, readiness read off something other than what was posted (the row as it stood, or
    an editor that never synced), is ruled out here: the composer saves the POST and then checks
    the saved row, so what was typed is what is measured, in both directions. Candidate 1, the
    count itself dropping typed characters, is the one that reproduced
    (classes/spec/models/class_readiness_spec.py) and is pinned at the view in the last spec.
    """

    def _submit(client, offering: ClassOffering, **extra: object) -> HttpResponse:
        return client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(
                offering.category, scheduling_model="fixed", scheduling_type="single_session", action="submit", **extra
            ),
        )

    def it_submits_a_ready_description_posted_over_a_short_saved_one(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        offering.description = "Short"
        offering.save(update_fields=["description"])
        client.force_login(instructor_fixture.user)

        resp = _submit(client, offering)

        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.status == Status.PENDING
        assert offering.description == READY_DESCRIPTION

    def it_refuses_a_short_description_posted_over_a_ready_saved_one(instructor_fixture, client):
        # The row as it stood would have passed; the POST is what is checked, once it is saved.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)

        resp = _submit(client, offering, description="Short")

        edit = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert resp["Location"] == f"{edit}?step=1&missing=1"
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.description == "Short"
        assert _messages(resp) == [f"Not ready to submit: {READINESS_DESCRIPTION_HINT}"]

    def it_submits_a_description_typed_with_angle_brackets(instructor_fixture, client):
        # Candidate 1 at the view: 63 typed characters, every one shown on the class page, go to review.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)

        resp = _submit(client, offering, description=BRACKETED_DESCRIPTION)

        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.status == Status.PENDING
        assert offering.description == BRACKETED_DESCRIPTION


def describe_admin_composer():
    def it_switches_on_the_admin_only_fields_and_publish(admin_user, client, db):
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_create")).content.decode()
        assert 'name="instructor"' in html
        assert 'name="is_private"' in html and 'name="private_for_name"' in html
        assert 'name="is_free"' not in html
        assert "Publish This Class?" in html
        assert "confirmSubmit('submit-class')\">Publish</button>" in html
        assert "Submit for Review" not in html
        assert "Publish when it is ready." in html
        assert f'href="{reverse("classes:admin_classes")}">Cancel</a>' in html

    def it_leaves_validation_to_the_server(admin_user, client, db):
        # Same shared composer template, same reason as the teach spec of this name.
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_create")).content.decode()
        assert re.search(r'<form[^>]*id="composer-form"[^>]*\bnovalidate\b', html)

    def it_keeps_the_admin_discount_code_urls(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert reverse("classes:admin_discount_code_create") in html
        assert reverse("classes:teach_discount_code_create") not in html
        assert f'href="{reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})}">Cancel</a>' in html

    def it_withholds_the_crop_box_on_an_imported_photo(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, image="", legacy_image_url=LEGACY_PHOTO)
        client.force_login(admin_user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        hero = _hero_preview_img(html)
        src = re.search(r'src="([^"]*)"', hero)
        assert src is not None, "no src on the hero preview"
        assert unescape(src.group(1)) == offering.hero_image_url
        assert "data-hero-cropper-preview" not in hero
        assert _legacy_note_text(html) == IMPORTED_PHOTO_NOTE
        assert _crop_hint_tag(html).endswith(" hidden>")

    def it_says_save_and_offers_no_publish_on_a_live_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        client.force_login(admin_user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert ">Save</button>" in html
        assert "Save Draft" not in html
        assert "submit-class" not in html

    def it_saves_a_draft_on_create_without_publishing(admin_user, client, db):
        cat = CategoryFactory()
        inst = InstructorFactory()
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_create"), _admin_payload(cat, inst, step="2"))
        assert resp.status_code == 302
        created = ClassOffering.objects.get(title="Round Trip")
        assert created.status == Status.DRAFT
        assert resp["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": created.pk}) + "?step=2"
        assert created.instructor_id == inst.pk
        assert created.is_private is True
        assert created.private_for_name == "The Guild"
        _assert_round_trip(created, cat)
        assert created.member_discount_pct == 15
        assert "Draft saved." in _messages(resp)

    def it_shrinks_a_create_mode_crop_with_the_downsized_upload(admin_user, client, db, settings):
        # Same path as the teach composer: the admin form saves through the same model.
        settings.IMAGE_MAX_LONG_EDGE_HERO = 2400
        cat, inst = CategoryFactory(), InstructorFactory()
        client.force_login(admin_user)
        payload = _admin_payload(
            cat, inst, image=_png_upload("phone-shot.png", 4800, 2700), hero_crop=json.dumps(LEFT_HALF_OF_4800)
        )
        resp = client.post(reverse("classes:admin_class_create"), payload)
        assert resp.status_code == 302
        _assert_crop_followed_the_downsize(ClassOffering.objects.get(title="Round Trip"))

    def it_round_trips_every_field_on_edit(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        cat = CategoryFactory()
        inst = InstructorFactory()
        client.force_login(admin_user)
        resp = client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), _admin_payload(cat, inst))
        assert resp["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}) + "?step=4"
        offering.refresh_from_db()
        _assert_round_trip(offering, cat)
        assert offering.member_discount_pct == 15
        assert offering.instructor_id == inst.pk
        assert offering.is_private is True

    def it_returns_to_the_class_page_when_the_post_carried_no_step(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        payload = _admin_payload(offering.category, offering.instructor)
        payload.pop("step")
        resp = client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), payload)
        assert resp["Location"] == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})

    def it_publishes_a_ready_draft_from_the_composer(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, ready=True)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(
                offering.category,
                offering.instructor,
                scheduling_model="fixed",
                scheduling_type="single_session",
                action="publish",
            ),
        )
        assert resp["Location"] == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED
        assert any("is published." in m for m in _messages(resp))

    def it_refuses_to_publish_an_unready_draft_and_says_why(admin_user, client, db):
        # The POST saves a ready description and a flexible note first, so after the save the only
        # gap is the gallery photo on step 2: the refusal lands there, not on the Review step the
        # POST came from.
        offering = ClassOfferingFactory(status=Status.DRAFT, gallery=0)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, action="publish", step="5"),
        )
        edit = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert resp["Location"] == f"{edit}?step=2&missing=1"
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.description == READY_DESCRIPTION
        assert "Not ready to publish: Add one gallery photo." in _messages(resp)
        html = client.get(resp["Location"]).content.decode()
        assert "phase: 2," in html
        notice = _still_missing(html)
        assert "Not ready to publish yet." in notice
        assert "Add one gallery photo." in notice and READINESS_DESCRIPTION_HINT not in notice

    def it_lands_on_the_first_broken_step(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, instructor="999999", capacity="", step="5"),
        )
        html = resp.content.decode()
        assert resp.status_code == 200
        assert "phase: 1," in html
        assert "errorSteps: [1, 3]," in html
        assert "The Basics: Instructor" in html

    def it_refuses_a_crafted_publish_on_a_live_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED, ready=True, published_at=timezone.now())
        stamped = offering.published_at
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(
                offering.category,
                offering.instructor,
                scheduling_model="fixed",
                scheduling_type="single_session",
                action="publish",
                step="5",
            ),
        )
        assert resp["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}) + "?step=5"
        assert "Only a draft can be published from here." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED
        assert offering.published_at == stamped
        assert not CmsActivity.objects.filter(class_offering=offering, kind=CmsActivity.Kind.CLASS_PUBLISHED).exists()

    def it_refuses_a_crafted_publish_on_a_class_still_in_review(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PENDING, ready=True)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(
                offering.category,
                offering.instructor,
                scheduling_model="fixed",
                scheduling_type="single_session",
                action="publish",
                step="5",
            ),
        )
        assert resp.status_code == 302
        assert "Only a draft can be published from here." in _messages(resp)
        offering.refresh_from_db()
        row.refresh_from_db()
        assert offering.status == Status.PENDING
        assert offering.published_at is None
        assert row.decision == ""
        assert not CmsActivity.objects.filter(class_offering=offering, kind=CmsActivity.Kind.CLASS_PUBLISHED).exists()


def describe_the_member_discount_is_the_admins_to_set():
    """#369 item 1: instructors read the discount, admins set it, and a new class starts at the studio default."""

    @pytest.fixture
    def studio_default_fifteen(db):
        settings_obj = ClassSettings.load()
        settings_obj.default_member_discount_pct = 15
        settings_obj.save(update_fields=["default_member_discount_pct"])
        return settings_obj

    def it_shows_the_instructor_a_note_instead_of_an_input(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, price_cents=5000, member_discount_pct=10
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert 'name="member_discount_pct"' not in html
        assert "Members get 10% off. That makes the member price $45." in html

    def it_names_only_the_percentage_before_a_price_is_saved(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert "Members get 10% off.</p>" in html

    def it_creates_at_the_studio_default_whatever_the_instructor_posts(
        instructor_fixture, client, studio_default_fifteen
    ):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_create"), _full_payload(cat, member_discount_pct="0"))
        assert resp.status_code == 302
        assert ClassOffering.objects.get(title="Round Trip").member_discount_pct == 15

    def it_leaves_an_existing_class_alone_whatever_the_instructor_posts(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, member_discount_pct=25)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, member_discount_pct="0"),
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.member_discount_pct == 25

    def it_offers_the_admin_the_studio_default_on_a_new_class(admin_user, client, studio_default_fifteen):
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_create")).content.decode()
        assert re.search(r'<input[^>]*name="member_discount_pct"[^>]*value="15"', html)

    def it_creates_at_the_studio_default_from_the_admin_composer_too(admin_user, client, studio_default_fifteen):
        cat, inst = CategoryFactory(), InstructorFactory()
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_create"), _admin_payload(cat, inst, member_discount_pct="15"))
        assert resp.status_code == 302
        assert ClassOffering.objects.get(title="Round Trip").member_discount_pct == 15

    def it_lists_an_admin_discount_error_on_step_three(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, member_discount_pct=10)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, member_discount_pct="101"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Dates, Seats And Price: Member discount (%)" in html
        assert "Member discount must be between 0 and 100." in html
        offering.refresh_from_db()
        assert offering.member_discount_pct == 10


def describe_live_sale_guard_through_the_composers():
    TOO_LOW = "Turn the sale off or change it from the manage page before setting a price this low."

    def _on_fixed_sale(**kwargs) -> ClassOffering:
        return ClassOfferingFactory(
            status=Status.DRAFT,
            price_cents=10000,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.FIXED,
            sale_amount_cents=8000,
            **kwargs,
        )

    def it_refuses_a_too_low_price_on_the_teach_composer(instructor_fixture, client):
        offering = _on_fixed_sale(instructor=instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, price_cents="50.00", step="3"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "phase: 1," in html and "errorSteps: [1]," in html
        assert "This class is on sale for $80 off." in html and TOO_LOW in html
        offering.refresh_from_db()
        assert offering.price_cents == 10000 and offering.sale_is_active is True

    def it_saves_a_raised_price_on_the_teach_composer(instructor_fixture, client):
        offering = _on_fixed_sale(instructor=instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, price_cents="150.00"),
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.price_cents == 15000
        assert offering.sale_is_active is True and offering.sale_price_cents == 7000

    def it_refuses_a_too_low_price_on_the_admin_composer(admin_user, client, db):
        offering = _on_fixed_sale()
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, price_cents="50.00"),
        )
        assert resp.status_code == 200
        assert TOO_LOW in resp.content.decode()
        offering.refresh_from_db()
        assert offering.price_cents == 10000 and offering.sale_is_active is True

    def it_refuses_a_percent_sale_under_the_floor_on_the_admin_composer(admin_user, client, db):
        offering = ClassOfferingFactory(
            status=Status.PUBLISHED,
            price_cents=10000,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.PERCENT,
            sale_percent=99,
        )
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, price_cents="1.00"),
        )
        assert resp.status_code == 200
        assert "This class is on sale for 99% off." in resp.content.decode()
        offering.refresh_from_db()
        assert offering.price_cents == 10000 and offering.sale_price_cents == 100

    def it_saves_a_raised_price_on_the_admin_composer(admin_user, client, db):
        offering = _on_fixed_sale()
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, price_cents="150.00"),
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.price_cents == 15000 and offering.sale_price_cents == 7000


# ── Issue #368 item 3b: a half filled session row survives the failed save that reports it ──


def describe_a_failed_save_with_a_half_filled_session_row():
    def it_re_renders_the_row_so_the_start_the_user_typed_is_not_lost(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        start = (timezone.now() + timedelta(days=7)).strftime("%Y-%m-%dT10:00")
        resp = client.post(
            reverse("classes:teach_class_create"),
            {
                **_full_payload(cat, title="", step="3"),
                "sessions-TOTAL_FORMS": "1",
                "sessions-0-id": "",
                "sessions-0-starts_at": start,
                "sessions-0-ends_at": "",
                "sessions-0-DELETE": "",
            },
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "errorSteps: [1, 3]," in html
        assert start in html

    def it_keeps_a_row_with_only_an_end(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        end = (timezone.now() + timedelta(days=7)).strftime("%Y-%m-%dT12:00")
        resp = client.post(
            reverse("classes:teach_class_create"),
            {
                **_full_payload(cat, title="", step="3"),
                "sessions-TOTAL_FORMS": "1",
                "sessions-0-id": "",
                "sessions-0-starts_at": "",
                "sessions-0-ends_at": end,
                "sessions-0-DELETE": "",
            },
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "errorSteps: [1, 3]," in html
        assert end in html


# ── Issue #368 item 3a: the price re-renders as typed, and a saved draft never 500s ──


def describe_a_failed_save_with_a_whole_dollar_price():
    def it_re_renders_the_price_the_user_typed_not_a_hundredth_of_it(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="", price_cents="80"),
        )
        assert resp.status_code == 200
        assert _price_input_value(resp.content.decode()) == "80"

    def it_re_renders_a_single_dollar_as_typed(instructor_fixture, client):
        # "1" used to come back as 0.01 and then trip the $1.00 floor on the next save.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="", price_cents="1"),
        )
        assert _price_input_value(resp.content.decode()) == "1"

    def it_re_renders_a_whole_dollar_price_as_typed_on_the_admin_composer(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, title="", price_cents="8000"),
        )
        assert resp.status_code == 200
        assert _price_input_value(resp.content.decode()) == "8000"

    def it_keeps_a_blank_price_blank_on_create(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_create"),
            _full_payload(cat, title="", price_cents=""),
        )
        assert resp.status_code == 200
        assert _price_input_value(resp.content.decode()) == ""


def describe_a_failed_save_with_a_typed_zero_price():
    def it_keeps_the_zero(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="", price_cents="0"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert _price_input_value(html) == "0"
        assert "Classes cost at least $1.00." in html


# ── Issue #368 item 5: no free option; every class costs at least $1.00, from either portal ──


def describe_the_price_floor_through_the_composers():
    FLOOR = "Classes cost at least $1.00."

    @pytest.fixture(params=["teach", "admin"])
    def portal(request):
        return request.param

    @pytest.fixture(params=["create", "edit"])
    def mode(request):
        return request.param

    @pytest.fixture
    def composer(portal, mode, instructor_fixture, admin_user, client):
        """(post, saved): post(price) submits this portal's composer in this mode; saved is the edit target."""
        cat = CategoryFactory()
        if portal == "teach":
            client.force_login(instructor_fixture.user)
            saved = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, price_cents=5000)
            url = (
                reverse("classes:teach_class_create")
                if mode == "create"
                else reverse("classes:teach_class_edit", kwargs={"pk": saved.pk})
            )
        else:
            client.force_login(admin_user)
            saved = ClassOfferingFactory(status=Status.DRAFT, price_cents=5000)
            url = (
                reverse("classes:admin_class_create")
                if mode == "create"
                else reverse("classes:teach_class_edit", kwargs={"pk": saved.pk})
            )

        def post(price: str):
            if portal == "teach":
                return client.post(url, _full_payload(cat, price_cents=price, step="3"))
            return client.post(url, _admin_payload(cat, saved.instructor, price_cents=price, step="3"))

        return post, saved

    def _refused_on_step_one(resp, reason: str, saved: ClassOffering) -> None:
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "phase: 1," in html and "errorSteps: [1]," in html
        assert reason in html
        assert 'goTo(1)">The Basics: Price</button>' in html
        saved.refresh_from_db()
        assert saved.price_cents == 5000
        assert not ClassOffering.objects.filter(title="Round Trip").exists()

    def it_refuses_zero(composer):
        post, saved = composer
        _refused_on_step_one(post("0"), FLOOR, saved)

    def it_refuses_ninety_nine_cents(composer):
        post, saved = composer
        _refused_on_step_one(post("0.99"), FLOOR, saved)

    def it_refuses_a_blank(composer):
        post, saved = composer
        _refused_on_step_one(post(""), "This field is required.", saved)

    def it_accepts_exactly_one_dollar(composer):
        post, _saved = composer
        resp = post("1.00")
        assert resp.status_code == 302, _visible_text(resp.content.decode())[:600]
        assert ClassOffering.objects.get(title="Round Trip").price_cents == 100


def describe_a_failed_save_with_a_blank_price_on_a_saved_draft():
    def it_re_renders_the_teach_composer_instead_of_crashing(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="", price_cents=""),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert _price_input_value(html) == ""
        assert "phase: 1," in html and "errorSteps: [1]," in html

    def it_re_renders_the_admin_composer_instead_of_crashing(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, title="", price_cents=""),
        )
        assert resp.status_code == 200
        assert "phase: 1," in resp.content.decode()

    def it_no_longer_nulls_the_instance_price(db):
        # Mechanism, before #368 item 5: ModelForm._post_clean runs construct_instance even when
        # another field failed, and a blank price (allowed by the free tick) reached it as None, so
        # the in-memory row carried price_cents=None and the card preview frames raised. A required
        # price never reaches cleaned_data when blank, so the row keeps what was saved. The chrome
        # and the card read the saved row regardless (next spec).
        offering = ClassOfferingFactory(status=Status.DRAFT, price_cents=5000)
        form = TeachClassOfferingForm(
            data=_full_payload(offering.category, title="", price_cents=""), instance=offering
        )
        assert form.is_valid() is False
        assert offering.price_cents == 5000

    def it_shows_the_saved_class_in_the_chrome_and_the_card_not_the_rejected_post(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, title="Before", price_cents=5000, member_discount_pct=10
        )
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="", price_cents="", member_discount_pct="50"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        # The heading and the card preview frames read the saved row, not the rejected POST.
        assert "Edit Class: Before" in html
        assert "($45 for Past Lives Members)" in html
        # The fields keep what was typed.
        assert _price_input_value(html) == ""
        # The crafted discount never lands: the note reads the saved row (#369).
        assert 'name="member_discount_pct"' not in html
        assert "Members get 10% off. That makes the member price $45." in html


# ── Issue #368 item 2: a refused submit lands where the gap is, with the reason visible ──


def describe_a_composer_submit_refused_for_readiness():
    def it_lands_on_the_first_unready_step_with_the_still_missing_checklist(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True, gallery=0)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(
                offering.category,
                scheduling_model="fixed",
                scheduling_type="single_session",
                action="submit",
                step="5",
            ),
        )
        assert resp.status_code == 302
        edit = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert resp["Location"] == f"{edit}?step=2&missing=1"
        assert "Not ready to submit: Add one gallery photo." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.title == "Round Trip"  # the save itself went through; only the submit was refused
        html = client.get(resp["Location"]).content.decode()
        assert "phase: 2," in html
        notice = _still_missing(html)
        assert "Still Missing" in notice
        assert "Not ready to submit yet. Tap a line to jump to it." in notice
        assert "goToField('gallery-manager')\">Add one gallery photo.</button>" in notice
        assert "Add a hero photo." not in notice
        # A checklist, not a form error: no error summary, no step marked broken.
        assert "Some Things Need Fixing" not in html
        assert "errorSteps: []," in html

    def it_lands_on_the_dates_step_when_the_only_session_slipped_into_the_past(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        session = offering.sessions.get()
        client.force_login(instructor_fixture.user)
        # Rendered while the session was still ahead: Submit is enabled, the Dates item is ticked.
        before = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "Finish the checklist above first." not in before
        # Time passes with the page open; nothing on screen changes.
        session.starts_at = timezone.now() - timedelta(minutes=1)
        session.ends_at = session.starts_at + timedelta(hours=2)
        session.save()
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(
                offering.category,
                scheduling_model="fixed",
                scheduling_type="single_session",
                action="submit",
                step="5",
            ),
        )
        edit = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert resp["Location"] == f"{edit}?step=3&missing=1"
        assert "Not ready to submit: Add at least one date." in _messages(resp)
        after = client.get(resp["Location"]).content.decode()
        assert "phase: 3," in after
        assert session.starts_at.strftime("%Y-%m-%dT%H:%M") in after
        assert "goToField('class-dates')\">Add at least one date.</button>" in _still_missing(after)

    def it_lands_on_the_photos_step_from_a_first_save_that_submits(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_create"), _full_payload(cat, action="submit", step="5"))
        assert resp.status_code == 302
        created = ClassOffering.objects.get(title="Round Trip")
        assert created.status == Status.DRAFT
        edit = reverse("classes:teach_class_edit", kwargs={"pk": created.pk})
        assert resp["Location"] == f"{edit}?step=2&missing=1"
        assert any(m.startswith("Not ready to submit:") for m in _messages(resp))

    def it_lands_the_admin_publish_on_the_first_unready_step(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, ready=True, gallery=0)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(
                offering.category,
                offering.instructor,
                scheduling_model="fixed",
                scheduling_type="single_session",
                action="publish",
                step="5",
            ),
        )
        edit = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert resp["Location"] == f"{edit}?step=2&missing=1"
        html = client.get(resp["Location"]).content.decode()
        assert "phase: 2," in html
        notice = _still_missing(html)
        assert "Not ready to publish yet." in notice and "Add one gallery photo." in notice

    def it_shows_no_notice_on_a_plain_visit_to_an_unready_draft(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, gallery=0)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert _still_missing(html) == ""

    def it_shows_no_notice_when_the_class_became_ready_before_the_page_loaded(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}) + "?step=5&missing=1"
        html = client.get(url).content.decode()
        assert _still_missing(html) == ""
        assert "phase: 5," in html


def describe_the_admin_create_readiness_preflight():
    def it_lands_on_the_first_unready_step_as_a_checklist_not_a_form_error(admin_user, client, db):
        cat = CategoryFactory()
        inst = InstructorFactory()
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_create"),
            _admin_payload(cat, inst, action="publish", step="5"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "phase: 2," in html
        assert "errorSteps: []," in html
        assert "Some Things Need Fixing" not in html
        assert "Not ready to publish:" not in html
        notice = _still_missing(html)
        assert "Still Missing" in notice
        assert "Not ready to publish yet." in notice
        assert "Add a hero photo." in notice and "Add one gallery photo." in notice
        assert READINESS_DESCRIPTION_HINT not in notice
        assert not ClassOffering.objects.filter(title="Round Trip").exists()

    def it_keeps_every_typed_value_in_the_form(admin_user, client, db):
        cat = CategoryFactory()
        inst = InstructorFactory()
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_create"),
            _admin_payload(cat, inst, action="publish", step="5", price_cents="80"),
        )
        html = resp.content.decode()
        assert 'value="Round Trip"' in html
        assert _price_input_value(html) == "80"
        assert "We will find a time together." in html

    def it_saves_a_draft_for_an_unknown_action(admin_user, client, db):
        # Only action=publish publishes; anything else is a draft save, like the teach composer.
        cat = CategoryFactory()
        inst = InstructorFactory()
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_create"), _admin_payload(cat, inst, action="bogus", step="2"))
        assert resp.status_code == 302
        created = ClassOffering.objects.get(title="Round Trip")
        assert created.status == Status.DRAFT
        assert created.published_at is None
        assert "Draft saved." in _messages(resp)

    def it_saves_a_draft_when_the_action_is_missing(admin_user, client, db):
        cat = CategoryFactory()
        inst = InstructorFactory()
        client.force_login(admin_user)
        payload = _admin_payload(cat, inst, step="2")
        payload.pop("action")
        resp = client.post(reverse("classes:admin_class_create"), payload)
        assert resp.status_code == 302
        assert ClassOffering.objects.get(title="Round Trip").status == Status.DRAFT


def describe_a_composer_post_to_a_class_cancelled_since_the_page_was_rendered():
    def it_keeps_the_typed_work_on_screen_and_saves_nothing(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, title="Before")
        client.force_login(instructor_fixture.user)
        offering.status = Status.CANCELLED
        offering.save(update_fields=["status"])
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="Typed after the render", action="submit", step="5"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'value="Typed after the render"' in html
        assert "Bring patience." in html
        assert (
            "This class was cancelled after you opened this page, so nothing here was saved. "
            "Cancelled and archived classes can only be edited by an admin. "
            "Copy anything you want to keep before you leave." in html
        )
        assert "Edit Class: Before" in html
        assert "phase: 5," in html
        assert "Some Things Need Fixing" not in html
        offering.refresh_from_db()
        assert offering.title == "Before"
        assert offering.status == Status.CANCELLED

    def it_says_archived_for_an_archived_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.ARCHIVED, title="Before")
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="Typed after the render"),
        )
        assert resp.status_code == 200
        assert "This class was archived after you opened this page, so nothing here was saved." in resp.content.decode()
        offering.refresh_from_db()
        assert offering.title == "Before"


_PUBLISHED_NOTICE = (
    "This class was published after you opened this page, so nothing here was saved. "
    "A live class is edited through a shorter form: the description, the photos, and what to bring. "
    "To change the title, dates, price, or capacity, ask an admin. "
    "Copy anything you want to keep, then press Cancel and open Edit again for the shorter form."
)


def _light_payload(**extra) -> dict:
    """What ``classes/teach/class_form_published.html`` actually posts: light fields, no hidden inputs.

    The absence of ``step`` is the point of this helper, so it carries no ``action`` and no
    ``step``; it is what the published branch must keep saving.
    """
    payload = {
        "description": READY_DESCRIPTION,
        "prerequisites": "Bring patience.",
        "materials_included": "All the wood.",
        "materials_to_bring": "Gloves.",
        "safety_requirements": "Eye protection.",
        "age_guardian_note": "Guardians welcome.",
        "flexible_note": "",
        "video_url": "",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    payload.update(extra)
    return payload


def describe_a_composer_post_to_a_class_published_since_the_page_was_rendered():
    """Issue #386: the cancelled race again, landing on the branch that serves the live class.

    That branch takes two kinds of POST and the hidden ``step`` is what tells them apart, so
    both rows are pinned here: the composer's POST saves nothing and comes back on screen, and
    the published light-edit form's own POST saves and redirects exactly as it always has.
    """

    def _publish(offering) -> None:
        offering.status = Status.PUBLISHED
        offering.published_at = timezone.now()
        offering.save(update_fields=["status", "published_at"])

    def it_keeps_the_typed_work_on_screen_and_saves_nothing(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, title="Before")
        description_before = offering.description
        client.force_login(instructor_fixture.user)
        _publish(offering)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="Typed after the render", action="submit", step="5"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'value="Typed after the render"' in html
        assert "Bring patience." in html
        assert _PUBLISHED_NOTICE in html
        assert "Edit Class: Before" in html
        assert "phase: 5," in html
        assert "Some Things Need Fixing" not in html
        assert "Class updated." not in _messages(resp)
        offering.refresh_from_db()
        assert offering.title == "Before"
        # The description is on BOTH forms, so it is the field that proves the light form did
        # not quietly save a composer POST behind the notice.
        assert offering.description == description_before
        assert offering.status == Status.PUBLISHED

    def it_treats_a_step_that_never_got_its_value_as_a_composer_post(instructor_fixture, client):
        # The hidden step is Alpine bound, so a page whose script never ran posts it empty.
        # An empty step is still the composer, and still must not fall through to the light form.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, title="Before")
        client.force_login(instructor_fixture.user)
        _publish(offering)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="Typed after the render", step=""),
        )
        assert resp.status_code == 200
        assert _PUBLISHED_NOTICE in resp.content.decode()
        offering.refresh_from_db()
        assert offering.title == "Before"

    def it_still_saves_the_published_light_edit_form(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, title="Before")
        client.force_login(instructor_fixture.user)
        _publish(offering)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _light_payload(materials_to_bring="An apron"),
        )
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert "Class updated." in _messages(resp)
        offering.refresh_from_db()
        assert offering.materials_to_bring == "An apron"
        assert offering.description == READY_DESCRIPTION
        assert offering.title == "Before"

    def it_still_renders_the_light_form_on_a_get(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, title="Before")
        client.force_login(instructor_fixture.user)
        _publish(offering)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "This class is live." in html
        assert _PUBLISHED_NOTICE not in html
        assert 'name="step"' not in html

    def it_sends_the_member_to_an_exit_that_is_not_another_post(instructor_fixture, client):
        """The notice names Cancel, so Cancel has to reach the shorter form. Proved, not assumed.

        The page is a POST response: reloading it re-sends the POST, which still carries ``step``
        and lands right back on the notice. This walks the route the copy actually promises.
        """
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, title="Before")
        client.force_login(instructor_fixture.user)
        _publish(offering)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        payload = _full_payload(offering.category, title="Typed after the render", step="5")

        first = client.post(url, payload)
        assert _PUBLISHED_NOTICE in first.content.decode()
        # Re-sending the same body is what a reload does, and it loops. This is why the copy
        # cannot say "reload this page".
        again = client.post(url, payload)
        assert again.status_code == 200
        assert _PUBLISHED_NOTICE in again.content.decode()
        assert "This class is live." not in again.content.decode()

        cancel_url = first.context["cancel_url"]
        assert cancel_url == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        landed = client.get(cancel_url)
        assert landed.status_code == 200
        # The class screen offers Edit under that exact label (classes/_components/class_screen_base.html),
        # which is the word the notice uses.
        assert landed.context["can_edit_now"] is True
        assert f'href="{url}">Edit</a>' in landed.content.decode()
        # And that Edit is a GET, which is the shorter form.
        assert "This class is live." in client.get(url).content.decode()

    def it_withholds_the_hero_uploader_that_would_still_write_to_the_live_class(instructor_fixture, client):
        # The hero uploader posts to its own endpoint the instant a file is picked, and
        # _edit_photos_or_404 closes only cancelled and archived classes, so on a published one
        # it would change the public banner from a page headed "nothing here was saved".
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, title="Before")
        client.force_login(instructor_fixture.user)
        _publish(offering)
        html = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="Typed after the render", step="2"),
        ).content.decode()
        hero_upload = reverse("classes:teach_class_hero_upload", kwargs={"pk": offering.pk})
        assert hero_upload not in html
        assert "hero-upload-area" not in html
        assert "The photo cannot be changed from this page." in html
        # The gallery is untouched: the live-edit form offers it too, so it is not this page's to take.
        assert reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk}) in html

    def it_gives_guild_staff_a_cancel_that_is_not_a_dead_end(instructor_fixture, client):
        # Guild staff are the other population that lands here, and the notice tells them to
        # press Cancel, so Cancel has to resolve for them too. It did not: `_guild_access`
        # withholds the Overview on someone else's class, and teach_class_detail 404s without it.
        guild = GuildFactory(name="Race Guild")
        GuildStaffMembershipFactory(guild=guild, member=instructor_fixture)
        offering = ClassOfferingFactory(
            instructor=InstructorFactory(instructor_slug="not-the-staffer"),
            category=CategoryFactory(guild=guild),
            status=Status.DRAFT,
            title="Before",
        )
        client.force_login(instructor_fixture.user)
        _publish(offering)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="Typed after the render", step="5"),
        )
        assert _PUBLISHED_NOTICE in resp.content.decode()
        cancel_url = resp.context["cancel_url"]
        assert cancel_url == reverse("classes:teach_dashboard")
        assert client.get(cancel_url).status_code == 200

    def it_leaves_the_cancelled_render_its_hero_uploader(instructor_fixture, client):
        # The sibling path from #385 is deliberately unchanged; only the published caller withholds.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.CANCELLED, title="Before")
        client.force_login(instructor_fixture.user)
        html = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="Typed after the render", step="2"),
        ).content.decode()
        assert reverse("classes:teach_class_hero_upload", kwargs={"pk": offering.pk}) in html
        assert "hero-upload-area" in html
        assert "The photo cannot be changed from this page." not in html


def describe_the_composers_cancel_link():
    """Cancel has to resolve for every population the composer admits, not just the instructor.

    It is one line in ``_composer_context``, but two different screens behind it: the class
    screen for anyone who has an Overview, the dashboard for guild staff on someone else's
    class, who under Ruling 12 do not. Pinned on the ordinary draft composer, because that is
    where the route has always been reachable, not only on the published race page.
    """

    def it_sends_the_instructor_to_the_class_screen(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        cancel_url = resp.context["cancel_url"]
        assert cancel_url == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert client.get(cancel_url).status_code == 200

    def it_sends_guild_staff_to_the_dashboard_instead_of_an_overview_they_cannot_open(instructor_fixture, client):
        guild = GuildFactory(name="Cancel Guild")
        GuildStaffMembershipFactory(guild=guild, member=instructor_fixture)
        offering = ClassOfferingFactory(
            instructor=InstructorFactory(instructor_slug="someone-else"),
            category=CategoryFactory(guild=guild),
            status=Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        cancel_url = resp.context["cancel_url"]
        assert cancel_url == reverse("classes:teach_dashboard")
        assert client.get(cancel_url).status_code == 200
        # The screen it used to point at is genuinely closed to them; this is not a preference.
        assert client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).status_code == 404

    def it_sends_the_admin_to_the_class_screen(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        cancel_url = resp.context["cancel_url"]
        assert cancel_url == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert client.get(cancel_url).status_code == 200


def describe_the_missing_flag_on_a_class_that_is_no_longer_a_draft():
    # Only a draft can be refused, so only a draft shows the Still Missing notice: a bookmarked or
    # Back navigated landing URL on a class since submitted or published says nothing.
    def it_renders_no_notice_on_a_pending_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING, ready=True, gallery=0)
        client.force_login(instructor_fixture.user)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}) + "?step=2&missing=1"
        html = client.get(url).content.decode()
        assert _still_missing(html) == ""
        assert "Not ready to submit yet." not in html
        assert "phase: 2," in html

    def it_renders_no_notice_on_a_published_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED, gallery=0)
        client.force_login(admin_user)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}) + "?step=2&missing=1"
        html = client.get(url).content.decode()
        assert _still_missing(html) == ""
        assert "Not ready to publish yet." not in html

    def it_still_renders_the_notice_on_a_draft(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True, gallery=0)
        client.force_login(instructor_fixture.user)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}) + "?step=2&missing=1"
        assert "Add one gallery photo." in _still_missing(client.get(url).content.decode())


def describe_a_cancelled_class_post_that_also_has_field_errors():
    def it_leads_with_the_nothing_saved_notice_above_the_error_summary(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.CANCELLED, title="Before")
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, title="", action="submit", step="5"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        notice_at = html.find("so nothing here was saved.")
        errors_at = html.find("Some Things Need Fixing")
        assert notice_at != -1 and errors_at != -1
        assert notice_at < errors_at
        offering.refresh_from_db()
        assert offering.title == "Before"


def describe_the_composer_draft_notice():
    """Draft persistence (issue #368, item 3c): the browser held copy of the in flight typing.

    static/js/composer_draft.js does the keeping and the offering; what the server owes it is a
    key nobody else's draft can collide with, a place to say what is kept, and a one shot signal
    that a save landed so the copy can be dropped. These specs pin those three.
    """

    def it_loads_the_draft_script_on_both_composers(instructor_fixture, admin_user, client):
        client.force_login(instructor_fixture.user)
        teach = client.get(reverse("classes:teach_class_create")).content.decode()
        client.force_login(admin_user)
        admin = client.get(reverse("classes:admin_class_create")).content.decode()
        for html in (teach, admin):
            assert '<script src="/static/js/composer_draft.js" defer></script>' in html
            # In <body> with the other per page boot scripts, never in the head with Alpine's
            # component registrations: it registers no component and needs the fresh DOM.
            assert html.count("js/composer_draft.js") == 1
            assert html.index("js/composer_draft.js") > html.index("js/composer_validation.js")

    def it_renders_the_notice_hidden_with_every_line_and_both_controls(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        notice = _draft_notice(html)
        assert '<div class="pl-composer-draft" data-composer-draft hidden>' in html
        assert 'data-composer-draft-line="offer" hidden>We kept what you typed in this browser' in notice
        assert 'data-composer-draft-line="kept" hidden>Kept in this browser' in notice
        assert 'data-composer-draft-line="blocked" hidden>This browser will not let us keep' in notice
        # The honest half. Files cannot live in localStorage at all, and the session dates,
        # the ticks and any row added after load are widget state this deliberately skips, so
        # the sentence has to cover all of them rather than name photos and stop there.
        assert "Photos, dates, the FAQ and anything you ticked were not." in notice
        assert "Photos, dates, the FAQ and anything you tick are not," in notice
        # The announcement is its own region OUTSIDE the notice: a live region inside a subtree
        # toggled with `hidden` announces nothing when it is revealed.
        assert 'role="status"' not in notice
        assert '<p class="sr-only" role="status" data-composer-draft-live></p>' in html
        assert "data-composer-draft-restore hidden>Restore It</button>" in notice
        assert "data-composer-draft-discard hidden>Discard It</button>" in notice

    def it_sits_above_the_form_so_it_shows_on_every_step(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert html.index("data-composer-draft ") < html.index('id="composer-form"')

    def it_keys_the_copy_by_class_so_two_drafts_never_collide(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        create = _draft_key(client.get(reverse("classes:teach_class_create")).content.decode())
        edit_url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        edit = _draft_key(client.get(edit_url).content.decode())
        assert create.endswith(".new")
        assert edit.endswith(f".{offering.pk}")

    def it_keys_the_copy_by_person_so_a_shared_browser_never_leaks_one(instructor_fixture, client, db):
        other = InstructorFactory(user=UserFactory(username="second-teacher@example.com"))
        url = reverse("classes:teach_class_create")
        client.force_login(instructor_fixture.user)
        mine = _draft_key(client.get(url).content.decode())
        client.force_login(other.user)
        theirs = _draft_key(client.get(url).content.decode())
        assert mine != theirs
        assert str(instructor_fixture.user.pk) in mine and str(other.user.pk) in theirs

    def it_keys_one_copy_per_class_now_that_the_two_composers_are_one_page(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert _draft_key(html).endswith(f".{offering.pk}")
        assert ".admin." not in _draft_key(html)

    def it_stamps_the_pre_merge_keys_so_a_draft_typed_before_the_merge_is_not_stranded(instructor_fixture, client):
        # The composer used to keep a copy per portal. Those keys ride along in a data
        # attribute and composer_draft.js copies the first one still holding something
        # forward — a copy, so reverted code still finds its own.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        legacy = re.search(r'data-composer-draft-legacy-keys="([^"]+)"', html)
        assert legacy is not None, "the composer stamped no legacy keys"
        keys = legacy.group(1).split(" ")
        user_pk = instructor_fixture.user.pk
        assert keys == [
            f"plfog.composer.v1.{user_pk}.admin.{offering.pk}",
            f"plfog.composer.v1.{user_pk}.teach.{offering.pk}",
        ]

    def it_stamps_the_saved_signal_on_the_render_after_a_save_and_takes_it_back_off(instructor_fixture, client):
        # The redirect target is where the browser learns its copy is redundant: the database
        # has the work now. One render only, so typing again on that same page is kept afresh.
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_create"), _full_payload(cat))
        assert resp.status_code == 302
        first = client.get(resp["Location"]).content.decode()
        assert 'data-composer-draft-saved="1"' in first
        second = client.get(resp["Location"]).content.decode()
        assert "data-composer-draft-saved" not in second

    def it_stamps_it_for_the_class_that_was_saved_and_no_other(instructor_fixture, client):
        cat = CategoryFactory()
        other = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        client.post(reverse("classes:teach_class_create"), _full_payload(cat))
        other_url = reverse("classes:teach_class_edit", kwargs={"pk": other.pk})
        assert "data-composer-draft-saved" not in client.get(other_url).content.decode()

    def it_never_stamps_it_on_a_failed_save(instructor_fixture, client):
        # A failed save re-renders from the POST, and what was typed then lives nowhere but this
        # page and the browser's copy. Clearing the copy there would be the loss this feature exists
        # to stop, so the signal is read on a GET only and waits for the next one.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        edit_url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        client.post(edit_url, _full_payload(offering.category))  # leaves the flag on the session
        refused = client.post(edit_url, _full_payload(offering.category, title=""))
        assert refused.status_code == 200
        assert "data-composer-draft-saved" not in refused.content.decode()
        # Still there for the GET that comes next: the save it belongs to really did happen.
        assert 'data-composer-draft-saved="1"' in client.get(edit_url).content.decode()

    def it_stamps_a_submit_that_was_refused_for_readiness_because_the_draft_still_saved(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True, gallery=0)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, action="submit", step="5"),
        )
        assert resp.status_code == 302
        assert 'data-composer-draft-saved="1"' in client.get(resp["Location"]).content.decode()

    def it_marks_a_refused_save_as_unsaved_so_the_browser_trusts_none_of_it(instructor_fixture, client):
        # Every value on a refused save's page came from the POST and none of it is in the
        # database, so composer_draft.js must not read the page as a safe baseline: doing so
        # would shrink the browser copy to whatever was edited after the refusal, and the
        # refresh that followed would bring back that one field and nothing else.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        edit_url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        refused = client.post(edit_url, _full_payload(offering.category, video_url="https://vimeo.com/12345"))
        assert refused.status_code == 200
        assert 'data-composer-draft-unsaved="1"' in refused.content.decode()
        # A GET renders the saved row, which is exactly what a baseline is meant to be.
        assert "data-composer-draft-unsaved" not in client.get(edit_url).content.decode()

    def it_remembers_every_save_that_never_landed_on_a_composer(admin_user, client, db):
        # The admin composer's plain Save with no step goes to the class page, not back here, so
        # the entry waits. One slot per session would let the second save overwrite the first,
        # and that class would then offer text it has already saved for the rest of the session.
        first = ClassOfferingFactory(status=Status.DRAFT)
        second = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        for offering in (first, second):
            resp = client.post(
                reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
                _admin_payload(offering.category, offering.instructor, step=""),
            )
            assert resp["Location"] == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        for offering in (first, second):
            url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
            assert 'data-composer-draft-saved="1"' in client.get(url).content.decode(), offering.pk
            assert "data-composer-draft-saved" not in client.get(url).content.decode(), offering.pk

    def it_caps_how_many_it_remembers_so_a_long_session_cannot_grow_without_bound(rf):
        request = rf.get("/")
        request.session = {}
        for pk in range(1, COMPOSER_SAVED_LIMIT + 6):
            _mark_composer_saved(request, ClassOffering(pk=pk))
        kept = request.session[COMPOSER_SAVED_SESSION_KEY]
        assert kept == list(range(6, COMPOSER_SAVED_LIMIT + 6))
        assert len(kept) == COMPOSER_SAVED_LIMIT

    def it_keeps_one_entry_per_class_however_often_it_is_saved(rf):
        request = rf.get("/")
        request.session = {}
        for _ in range(3):
            _mark_composer_saved(request, ClassOffering(pk=7))
        assert request.session[COMPOSER_SAVED_SESSION_KEY] == [7]

    def it_ships_the_saved_values_exactly_as_a_fresh_page_would_render_them(instructor_fixture, client):
        # The browser compares strings, and `initial` holds Python: a Decimal price, a
        # category's pk, a date. Every one of these that renders differently from the value
        # the GET puts in the DOM is a field that looks edited when it is not, which is the
        # over capture this baseline exists to prevent. So the whole baseline is checked
        # against a real unbound render rather than against a list of expected conversions.
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, ready=True, price_cents=8000
        )
        client.force_login(instructor_fixture.user)
        edit_url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        edit_html = client.get(edit_url).content.decode()
        # Django opens every textarea with a newline the HTML spec tells the browser to drop,
        # so the DOM value, which is what the browser compares, is the text after it.
        rendered = {name: value.removeprefix("\n") for name, value in _untouched_form_values(edit_html).items()}
        refused = client.post(edit_url, _full_payload(offering.category, video_url="https://vimeo.com/12345"))
        baseline = json.loads(_draft_baseline(refused.content.decode()))

        assert refused.status_code == 200
        # Not vacuous: the fields most likely to convert badly are all in here.
        assert {"title", "description", "category", "price_cents", "capacity"} <= set(baseline)
        assert baseline["price_cents"] == rendered["price_cents"]
        assert {name: value for name, value in baseline.items() if name in rendered} == {
            name: value for name, value in rendered.items() if name in baseline
        }

    def it_ships_nothing_on_a_render_that_saved_everything(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "data-composer-draft-baseline" not in html

    def it_ships_an_empty_baseline_in_create_mode_where_nothing_is_saved_yet(instructor_fixture, client):
        # Nothing is in the database, so the whole page is the work and every filled field
        # belongs in the copy. The form's own defaults are still saved values in the sense
        # that matters here: nobody typed them, so they are not what a Restore should put back.
        client.force_login(instructor_fixture.user)
        cat = CategoryFactory()
        refused = client.post(
            reverse("classes:teach_class_create"), _full_payload(cat, video_url="https://vimeo.com/12345")
        )
        baseline = json.loads(_draft_baseline(refused.content.decode()))
        assert baseline["title"] == ""
        assert baseline["description"] == ""
        assert baseline["capacity"] == str(TeachClassOfferingForm().fields["capacity"].initial)
