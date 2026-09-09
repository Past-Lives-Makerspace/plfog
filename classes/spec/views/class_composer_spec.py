"""BDD specs for the five step class composer (teach and admin twins of one shared template)."""

from __future__ import annotations

import json
from html.parser import HTMLParser

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    READY_DESCRIPTION,
    CategoryFactory,
    ClassOfferingFactory,
    InstructorFactory,
    UserFactory,
)
from classes.forms import ClassOfferingForm, TeachClassOfferingForm
from classes.models import ClassApproval, ClassOffering, CmsActivity

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
        "is_free": "",
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
    assert offering.member_discount_pct == 15
    assert offering.capacity == 8
    assert offering.scheduling_model == "flexible"
    assert offering.scheduling_type == "series_package"
    assert offering.flexible_note == "We will find a time together."
    assert offering.video_url == VIDEO
    assert (offering.card_focus_x, offering.card_focus_y) == (30, 70)


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

    def it_puts_the_price_on_the_first_step(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        step_one = html[html.index('data-composer-step="1"') : html.index('data-composer-step="2"')]
        assert "Free Or Paid" in step_one
        assert 'name="price_cents"' in step_one and 'name="is_free"' in step_one
        assert 'data-help-key="teach.class-pricing"' in step_one
        step_three = html[html.index('data-composer-step="3"') : html.index('data-composer-step="4"')]
        assert 'name="member_discount_pct"' in step_three and 'name="capacity"' in step_three
        assert 'name="price_cents"' not in step_three

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
        """A legacy only class has its own hero: the note, the preview, and a ticked checklist."""
        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=Status.DRAFT,
            ready=True,
            image="",
            legacy_image_url="https://classes.pastlives.space/sites/default/files/glen.jpg",
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert (
            "This photo came over from the old class site. Everything here works the same. "
            "Upload a new one to replace it." in html
        )
        hero = html.split('id="hero-preview"')[1].split("</div>")[0]
        assert "_legacy-image/?url=https%3A%2F%2Fclasses.pastlives.space" in hero
        assert "data-hero-cropper-preview" in hero
        assert "Replace image" in html
        assert "Add a hero photo." not in html
        assert html.count("pl-phase-tab--done") == 3

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
        assert "open-confirm', 'submit-class')\">Submit for Review</button>" in html

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
        assert (offering.hero_crop_x, offering.hero_crop_y, offering.hero_crop_w, offering.hero_crop_h) == (
            10,
            20,
            320,
            180,
        )
        assert "Draft saved." in _messages(resp)

    def it_round_trips_the_free_toggle(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, is_free="on", price_cents=""),
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.price_cents == 0
        assert offering.member_discount_pct == 0

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
        # Title, guild type, description, and the free tick are the whole of step 1. Steps 2 to 4
        # are untouched: the POST is exactly what a browser submits from the rendered page (every
        # field with its default, parsed from the GET), plus step 1. capacity, scheduling_model,
        # and scheduling_type are required form fields with model defaults, so they ride along as
        # the composer renders them; a literal four field POST is not what a browser sends.
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        untouched = _untouched_form_values(client.get(reverse("classes:teach_class_create")).content.decode())
        assert untouched["capacity"] == "6"
        assert untouched["scheduling_model"] == "fixed"
        assert untouched["scheduling_type"] == "single_session"
        assert untouched["price_cents"] == ""
        assert "is_free" not in untouched
        resp = client.post(
            reverse("classes:teach_class_create"),
            {
                **untouched,
                **_management(),
                "title": "Step One Draft",
                "category": cat.pk,
                "description": "Just the pitch for now.",
                "is_free": "on",
                "action": "save",
                "step": "1",
            },
        )
        assert resp.status_code == 302, _visible_text(resp.content.decode())[:600]
        created = ClassOffering.objects.get(title="Step One Draft")
        assert created.status == Status.DRAFT
        assert created.price_cents == 0
        assert created.capacity == 6
        assert created.scheduling_model == "fixed"
        assert resp["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": created.pk}) + "?step=1"
        assert "Draft saved." in _messages(resp)

    def it_saves_a_paid_draft_from_step_one_alone(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        untouched = _untouched_form_values(client.get(reverse("classes:teach_class_create")).content.decode())
        resp = client.post(
            reverse("classes:teach_class_create"),
            {
                **untouched,
                **_management(),
                "title": "Paid Step One Draft",
                "category": cat.pk,
                "description": "Just the pitch for now.",
                "price_cents": "45.00",
                "action": "save",
                "step": "1",
            },
        )
        assert resp.status_code == 302, _visible_text(resp.content.decode())[:600]
        created = ClassOffering.objects.get(title="Paid Step One Draft")
        assert created.status == Status.DRAFT
        assert created.price_cents == 4500
        assert created.capacity == 6

    def it_bounces_a_step_one_save_with_no_price_and_no_free_tick_to_step_one(instructor_fixture, client):
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


def describe_admin_composer():
    def it_switches_on_the_admin_only_fields_and_publish(admin_user, client, db):
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_create")).content.decode()
        assert 'name="instructor"' in html
        assert 'name="is_private"' in html and 'name="private_for_name"' in html
        assert "Publish This Class?" in html
        assert "open-confirm', 'submit-class')\">Publish</button>" in html
        assert "Submit for Review" not in html
        assert "Publish when it is ready." in html
        assert f'href="{reverse("classes:admin_classes")}">Cancel</a>' in html

    def it_keeps_the_admin_discount_code_urls(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert reverse("classes:admin_discount_code_create") in html
        assert reverse("classes:teach_discount_code_create") not in html
        assert f'href="{reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})}">Cancel</a>' in html

    def it_says_save_and_offers_no_publish_on_a_live_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})).content.decode()
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
        assert resp["Location"] == reverse("classes:admin_class_edit", kwargs={"pk": created.pk}) + "?step=2"
        assert created.instructor_id == inst.pk
        assert created.is_private is True
        assert created.private_for_name == "The Guild"
        _assert_round_trip(created, cat)
        assert "Draft saved." in _messages(resp)

    def it_round_trips_every_field_on_edit(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        cat = CategoryFactory()
        inst = InstructorFactory()
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}), _admin_payload(cat, inst))
        assert resp["Location"] == reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}) + "?step=4"
        offering.refresh_from_db()
        _assert_round_trip(offering, cat)
        assert offering.instructor_id == inst.pk
        assert offering.is_private is True

    def it_returns_to_the_class_page_when_the_post_carried_no_step(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        payload = _admin_payload(offering.category, offering.instructor)
        payload.pop("step")
        resp = client.post(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}), payload)
        assert resp["Location"] == reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})

    def it_publishes_a_ready_draft_from_the_composer(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, ready=True)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(
                offering.category,
                offering.instructor,
                scheduling_model="fixed",
                scheduling_type="single_session",
                action="publish",
            ),
        )
        assert resp["Location"] == reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED
        assert any("is published." in m for m in _messages(resp))

    def it_refuses_to_publish_an_unready_draft_and_says_why(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, gallery=0)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, action="publish", step="5"),
        )
        assert resp["Location"] == reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}) + "?step=5"
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert any(m.startswith("Not ready to publish:") for m in _messages(resp))

    def it_lands_on_the_first_broken_step(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
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
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(
                offering.category,
                offering.instructor,
                scheduling_model="fixed",
                scheduling_type="single_session",
                action="publish",
                step="5",
            ),
        )
        assert resp["Location"] == reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}) + "?step=5"
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
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
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

    def it_refuses_the_free_tick_on_the_teach_composer(instructor_fixture, client):
        offering = _on_fixed_sale(instructor=instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _full_payload(offering.category, is_free="on", price_cents=""),
        )
        assert resp.status_code == 200
        assert "before making it free." in resp.content.decode()
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
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
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
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
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
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            _admin_payload(offering.category, offering.instructor, price_cents="150.00"),
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.price_cents == 15000 and offering.sale_price_cents == 7000
