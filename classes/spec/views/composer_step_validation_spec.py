"""BDD specs for the composer's per step validation contract (issue #368, item 1).

The client (``static/js/composer_validation.js``) reads its rules from the rendered DOM: the
pane stamped ``data-composer-step="N"`` is the step, the controls inside it are the fields,
and the attributes Django put on them are the rules. Three things keep that honest, and
these specs pin them. The ``required`` attribute a pane renders is exactly what the server
form requires of that step, on both composers in both modes, so a form change cannot drift
from what Next enforces. Nothing the client blocks is something the server accepts: the
readiness items are submit gates, not Next gates, and formset rows carry no constraint
attribute at all, so they stay the server's. The one named exception is the gallery's
minimum of one photo (#424), which Next also refuses through a hook on the container rather
than an attribute; ``describe_the_gallery_minimum`` pins it. And no list of fields per step
exists anywhere but ``classes/composer.py``: not in the script, not in the Alpine root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

import pytest
from django.urls import reverse

from classes.composer import COMPOSER_STEPS
from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.forms import (
    DESCRIPTION_HELP_TEXT,
    ClassFaqForm,
    ClassOfferingForm,
    TeachClassOfferingForm,
    build_class_faq_formset,
)
from classes.models import READINESS_MIN_DESCRIPTION_CHARS, ClassOffering

REPO_ROOT = Path(__file__).resolve().parents[3]
JS_PATH = REPO_ROOT / "static" / "js" / "composer_validation.js"
TEMPLATE_PATH = REPO_ROOT / "templates" / "classes" / "_components" / "class_composer.html"
FIELD_NAMES = sorted({name for step in COMPOSER_STEPS for name in step.fields})
# The rule set the instructor's composer renders, by step. Steps 2, 4 and 5 require nothing:
# photos, details and review are readiness or optional, never an attribute driven Next gate
# (the gallery minimum is the one named exception, pinned at the end). scheduling_model is
# a required form field whose <select> has no empty option (a model default, no blank=True), so
# Django omits the attribute: the browser always posts a value and there is nothing to gate.
REQUIRED_BY_STEP = {
    1: {"title", "category", "price_cents"},
    2: set(),
    3: {"capacity", "scheduling_type"},
    4: set(),
    5: set(),
}
# The admin's composer renders one rule more: the member discount is the admin's to set (#369),
# required on their form and a read-only note on the instructor's.
ADMIN_REQUIRED_BY_STEP = {**REQUIRED_BY_STEP, 3: REQUIRED_BY_STEP[3] | {"member_discount_pct"}}
VOID_TAGS = {"input", "img", "br", "hr", "link", "meta", "source", "wbr"}
CONTROL_TAGS = {"input", "select", "textarea"}
# The gallery minimum (#424): the hook the client counts cards inside, the refusal it shows
# (copy lives in the template, never in the script), and the marker the two photo controls carry.
GALLERY_HOOK_ATTR = "data-composer-gallery"
GALLERY_MESSAGE = "Add at least one gallery photo."
REQUIRED_BADGE = '<span class="pl-required">Required</span>'


@dataclass
class _Control:
    tag: str
    name: str | None
    attrs: dict[str, str | None] = field(default_factory=dict)

    @property
    def required(self) -> bool:
        return "required" in self.attrs

    @property
    def kind(self) -> str:
        return self.attrs.get("type") or ("text" if self.tag == "input" else self.tag)


class _PaneParser(HTMLParser):
    """Every control inside each step pane, in DOM order, with ``<template>`` content skipped.

    The client walks the live DOM the same way: ``querySelectorAll`` never descends into a
    ``<template>`` (the scheduler's x-for rows, the FAQ empty form), so neither does this.
    """

    def __init__(self) -> None:
        super().__init__()
        self.controls: dict[int, list[_Control]] = {}
        self.x_data: str | None = None
        self._pane: int | None = None
        self._depth = 0
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self.x_data is None and "pl-composer" in (a.get("class") or "").split():
            self.x_data = a.get("x-data") or ""
        if self._skip:
            if tag == "template":
                self._skip += 1
            return
        if tag == "template":
            self._skip = 1
            return
        if self._pane is None:
            if "data-composer-step" in a:
                self._pane = int(a["data-composer-step"] or "0")
                self._depth = 1
                self.controls.setdefault(self._pane, [])
            return
        if tag not in VOID_TAGS:
            self._depth += 1
        if tag in CONTROL_TAGS:
            self.controls[self._pane].append(_Control(tag=tag, name=a.get("name"), attrs=a))

    def handle_endtag(self, tag: str) -> None:
        if self._skip:
            if tag == "template":
                self._skip -= 1
            return
        if self._pane is None or tag in VOID_TAGS:
            return
        self._depth -= 1
        if self._depth == 0:
            self._pane = None


class _FragmentParser(HTMLParser):
    """Every control in a fragment, template content included (for the FAQ empty form)."""

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[_Control] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in CONTROL_TAGS:
            self.controls.append(_Control(tag=tag, name=a.get("name"), attrs=a))


class _HookParser(HTMLParser):
    """Every element stamped with ``attr`` (``data-composer-gallery`` by default), with all of its attributes."""

    def __init__(self, attr: str = GALLERY_HOOK_ATTR) -> None:
        super().__init__()
        self.attr = attr
        self.hooks: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self.attr in a:
            self.hooks.append(a)


def _hooks(html: str, attr: str = GALLERY_HOOK_ATTR) -> list[dict[str, str | None]]:
    parser = _HookParser(attr)
    parser.feed(html)
    return parser.hooks


def _step_two(html: str) -> str:
    return html[html.index('data-composer-step="2"') : html.index('data-composer-step="3"')]


def _parse(html: str) -> _PaneParser:
    parser = _PaneParser()
    parser.feed(html)
    assert sorted(parser.controls) == [1, 2, 3, 4, 5], sorted(parser.controls)
    assert parser.x_data is not None, "no .pl-composer root found"
    return parser


def _controls_in(fragment: str) -> list[_Control]:
    parser = _FragmentParser()
    parser.feed(fragment)
    return parser.controls


def _by_name(controls: list[_Control], name: str) -> _Control:
    matches = [c for c in controls if c.name == name]
    assert matches, f"no control named {name!r}"
    return matches[0]


def _by_id(controls: list[_Control], control_id: str) -> _Control:
    matches = [c for c in controls if c.attrs.get("id") == control_id]
    assert matches, f"no control with id {control_id!r}"
    return matches[0]


def _server_required(form) -> set[str]:
    """The fields Django renders ``required`` on: required, and a widget that carries the attribute."""
    return {
        name
        for name, form_field in form.fields.items()
        if form_field.required and form_field.widget.use_required_attribute(form[name].initial)
    }


@pytest.fixture
def instructor(db):
    user = UserFactory(username="step-check-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher V", instructor_slug="teacher-v")


@dataclass
class _Composer:
    """One composer (teach or admin): its form class, and its create and edit pages as rendered."""

    form_class: type
    offering: ClassOffering
    pages: dict[str, str]
    required_by_step: dict[int, set[str]]

    def form(self, mode: str):
        return self.form_class(instance=self.offering) if mode == "edit" else self.form_class()


@pytest.fixture(params=["teach", "admin"])
def composer(request, client, instructor, admin_user) -> _Composer:
    if request.param == "teach":
        client.force_login(instructor.user)
        offering = cast(
            ClassOffering, ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT, ready=True)
        )
        create, edit = "classes:teach_class_create", "classes:teach_class_edit"
        form_class: type = TeachClassOfferingForm
        required_by_step = REQUIRED_BY_STEP
    else:
        client.force_login(admin_user)
        offering = cast(ClassOffering, ClassOfferingFactory(status=ClassOffering.Status.DRAFT, ready=True))
        create, edit = "classes:admin_class_create", "classes:teach_class_edit"
        form_class = ClassOfferingForm
        required_by_step = ADMIN_REQUIRED_BY_STEP
    pages = {
        "create": client.get(reverse(create)).content.decode(),
        "edit": client.get(reverse(edit, kwargs={"pk": offering.pk})).content.decode(),
    }
    return _Composer(form_class=form_class, offering=offering, pages=pages, required_by_step=required_by_step)


def describe_required_parity_between_the_panes_and_the_form():
    def it_renders_required_on_exactly_the_fields_the_form_requires_on_every_step(composer):
        for mode, html in composer.pages.items():
            form = composer.form(mode)
            server = _server_required(form)
            parsed = _parse(html)
            rendered_by_step: dict[int, set[str]] = {}
            for step in COMPOSER_STEPS:
                controls = parsed.controls[step.number]
                named = {c.name for c in controls if c.name in form.fields}
                # Nothing of the form renders on a pane its step does not own.
                assert named <= set(step.fields), (mode, step.number, named - set(step.fields))
                rendered_by_step[step.number] = {c.name for c in controls if c.required and c.name in form.fields}
                assert rendered_by_step[step.number] == server & set(step.fields), (mode, step.number)
            # Every required field is rendered somewhere, so the client can see every server rule.
            assert set().union(*rendered_by_step.values()) == server, mode
            assert rendered_by_step == composer.required_by_step, mode

    def it_requires_the_same_things_of_both_composers_but_the_member_discount():
        # The admin only fields instructor, is_private and private_for_name are all optional;
        # member_discount_pct is the one the admin's form requires and the instructor's lacks
        # (#369). Otherwise the client enforces one rule set whichever portal rendered the page.
        admin, teach = _server_required(ClassOfferingForm()), _server_required(TeachClassOfferingForm())
        assert admin - teach == {"member_discount_pct"} and teach <= admin
        assert admin == set().union(*ADMIN_REQUIRED_BY_STEP.values())
        assert teach == set().union(*REQUIRED_BY_STEP.values())

    def it_needs_no_gate_on_a_select_with_no_empty_option(composer):
        # scheduling_model: required on the form, no `required` on the control, and no gap
        # between the two: with no empty <option> the browser cannot post a blank.
        for mode, html in composer.pages.items():
            form = composer.form(mode)
            assert form.fields["scheduling_model"].required is True
            assert form.fields["scheduling_model"].widget.use_required_attribute(None) is False
            select = _by_name(_parse(html).controls[3], "scheduling_model")
            assert not select.required, mode
            assert '<option value="">' not in html.split('name="scheduling_model"')[1].split("</select>")[0], mode

    def it_renders_required_on_the_scheduling_radios_the_hand_written_card_template_draws(composer):
        # scheduling_type_field.html renders {{ radio.tag }} itself instead of form_field.html; the
        # attribute has to survive that path or Next would let an unpicked schedule through.
        for mode, html in composer.pages.items():
            radios = [c for c in _parse(html).controls[3] if c.name == "scheduling_type"]
            assert len(radios) == 2, mode
            assert all(c.kind == "radio" and c.required for c in radios), mode


def describe_what_next_never_blocks_on():
    def it_leaves_every_readiness_item_to_submit(composer):
        # readiness_items (classes/models.py) gates submit on a hero photo, a gallery photo, a
        # description of some length, a date (or a flexible note), and capacity >= 1. None of
        # those is a form rule, so none is a rendered attribute, and Next lets them through, with
        # one exception: the gallery photo, which Next also refuses through the container hook
        # (describe_the_gallery_minimum below, #424). Submit stays the gate for all five.
        for mode, html in composer.pages.items():
            form = composer.form(mode)
            parsed = _parse(html)
            assert form.fields["description"].required is False
            description = _by_name(parsed.controls[1], "description")
            assert not description.required and "minlength" not in description.attrs, mode
            assert form.fields["flexible_note"].required is False
            assert not _by_name(parsed.controls[3], "flexible_note").required, mode
            assert form.fields["image"].required is False
            hero = [c for c in parsed.controls[2] if c.kind == "file"]
            assert hero and not any(c.required for c in hero), mode
            capacity = _by_name(parsed.controls[3], "capacity")
            assert capacity.required and capacity.attrs.get("min") == "0", mode
            for control_id in ("session-add-date", "session-add-time", "session-add-duration"):
                helper = _by_id(parsed.controls[3], control_id)
                assert not helper.required, (mode, control_id)
                # And no name at all: the browser never posts these, they only drive the Alpine
                # scheduler that writes the hidden sessions-N-* inputs. That is the contract the
                # client leans on to skip them, which is what keeps a half typed date (badInput
                # on a type=date control) from holding Next on a step no save could refuse.
                assert "name" not in helper.attrs, (mode, control_id)

    def it_leaves_the_price_floor_and_the_discount_cap_to_the_server(composer):
        # Deliberate: the $1.00 floor and the 100% cap live in _PricingRulesMixin.clean_*, not in
        # the rendered min/max, so the client refuses only what every browser can read from the
        # attributes (a blank) and the server keeps the last word on the amount.
        for mode, html in composer.pages.items():
            controls = _parse(html).controls
            price = _by_name(controls[1], "price_cents")
            assert price.required and price.attrs.get("min") == "0" and price.attrs.get("step") == "0.01", mode
            if composer.form_class is not ClassOfferingForm:
                # The instructor's composer has no discount control to gate (#369).
                assert all(c.name != "member_discount_pct" for c in controls[3]), mode
                continue
            discount = _by_name(controls[3], "member_discount_pct")
            assert discount.required and discount.attrs.get("min") == "0" and "max" not in discount.attrs, mode

    def it_never_refuses_a_youtube_link_typed_without_a_scheme(composer):
        # <input type="url"> demands a scheme; the server does not. forms.URLField normalises
        # "youtube.com/watch?v=…" to https (assume_scheme) and validate_video_url takes what
        # it is handed. The rendered control is the client's whole rule book, so a URL input
        # would make Next refuse a link the very next save accepts. inputmode keeps the URL
        # keyboard on a phone; dropping the type is what stops the browser gating it.
        typed = "youtube.com/watch?v=dQw4w9WgXcQ"
        for mode, html in composer.pages.items():
            video = _by_name(_parse(html).controls[2], "video_url")
            assert video.kind == "text", (mode, video.attrs.get("type"))
            assert video.attrs.get("inputmode") == "url", mode
            assert not video.required, mode
        # The other side of the same rule: the server still takes that exact string.
        form = composer.form_class(data={"video_url": typed})
        form.is_valid()  # the rest of the form is empty on purpose; this field is the subject
        assert "video_url" not in form.errors
        assert form.cleaned_data["video_url"] == f"https://{typed}"

    def it_never_ties_the_private_name_to_the_private_toggle(admin_user, client):
        # The server does not either: private_for_name is blank=True with no clean() rule behind
        # is_private, so a private class with no name saves. The client has nothing to mirror.
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_create")).content.decode()
        controls = _parse(html).controls[3]
        assert not _by_name(controls, "private_for_name").required
        assert not _by_name(controls, "is_private").required
        assert ClassOfferingForm().fields["private_for_name"].required is False


def describe_formset_rows():
    def it_renders_no_constraint_on_a_faq_row_so_the_server_keeps_the_half_filled_ones(instructor, client):
        # ClassFaqForm requires both fields, but Django builds every formset form (and the empty
        # form the + Add button clones) with use_required_attribute=False, so no FAQ control
        # carries `required`. The client therefore cannot and does not gate a FAQ row: an added
        # row left blank never blocks Next, and a question with no answer is refused at save,
        # landing on step 4 (composer_spec.py, it_lands_a_broken_faq_row_on_step_four).
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT)
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        row_fields = ClassFaqForm().fields
        assert row_fields["question"].required and row_fields["answer"].required
        rows = [c for c in _parse(html).controls[4] if c.name and c.name.startswith("faq-") and c.kind != "hidden"]
        assert {c.name for c in rows} == {f"faq-{i}-{name}" for i in range(3) for name in ("question", "answer")}
        assert not any(c.required for c in rows)
        assert not any("minlength" in c.attrs or "pattern" in c.attrs for c in rows)
        empty = html.split('id="faq-empty-template"')[1].split("</template>")[0]
        for name in ("question", "answer"):
            assert not _by_name(_controls_in(empty), f"faq-__prefix__-{name}").required, name

    def it_is_the_server_that_skips_a_blank_extra_row_and_refuses_a_half_filled_one(instructor):
        # Django's empty_permitted rule, which the client leaves alone: an untouched extra form
        # validates, a row with a question and no answer does not.
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT)
        management = {
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "0",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
        }
        blank = build_class_faq_formset({**management, "faq-0-question": "", "faq-0-answer": ""}, offering)
        assert blank.is_valid(), blank.errors
        assert blank.forms[0].empty_permitted is True
        half = build_class_faq_formset({**management, "faq-0-question": "Tools?", "faq-0-answer": ""}, offering)
        assert not half.is_valid()
        assert half.forms[0].errors == {"answer": ["This field is required."]}

    def it_posts_the_session_rows_as_hidden_inputs_outside_constraint_validation(composer):
        # The scheduler writes every session row as type=hidden (session_calendar.html), which the
        # browser never validates, so an empty scheduler cannot block Next; a half filled row is
        # the server's to refuse (ClassSessionForm.clean).
        for mode, html in composer.pages.items():
            sessions = [c for c in _parse(html).controls[3] if c.name and c.name.startswith("sessions-")]
            assert sessions, mode
            assert all(c.kind == "hidden" for c in sessions), mode


def describe_the_gallery_minimum():
    def it_stamps_the_gallery_container_with_the_hook_and_its_message_in_both_modes(composer):
        # "At least one gallery photo" is a rule about how many rows the gallery holds, and no
        # constraint attribute on any control can say that. So the container itself carries the
        # hook the client counts cards inside (the saved class manager and the create mode
        # picker alike), the refusal it shows, and a tabindex so the refusal can focus it the
        # way it focuses a control. The card class it counts is the one the server renders.
        for mode, html in composer.pages.items():
            hooks = _hooks(html)
            assert len(hooks) == 1, (mode, len(hooks))
            assert _hooks(_step_two(html)) == hooks, mode
            hook = hooks[0]
            assert hook["id"] == ("gallery-create" if mode == "create" else "gallery-manager"), mode
            assert hook["data-composer-gallery-message"] == GALLERY_MESSAGE, mode
            assert hook["tabindex"] == "-1", mode
            assert _step_two(html).count('class="cls-image-cell') == (1 if mode == "edit" else 0), mode
            # Both inline scripts signal every add and remove, which is what clears the refusal.
            assert "new CustomEvent('composer-gallery-changed', { bubbles: true })" in _step_two(html), mode

    def it_marks_exactly_the_two_photo_controls_required(composer):
        # The composer had no required marker before #424 (form_field.html renders none), so
        # this one is the hero label's and the Gallery title's alone: nothing else gets it.
        for mode, html in composer.pages.items():
            assert html.count(REQUIRED_BADGE) == 2, mode
            two = _step_two(html)
            assert two.count(REQUIRED_BADGE) == 2, mode
            hero_label = two[two.index("data-hero-image-field") : two.index('id="hero-upload-zone"')]
            assert hero_label.count(REQUIRED_BADGE) == 1 and "Upload image" in hero_label, mode
            gallery_title = re.search(r'<h3 class="pl-compose-section__title">Gallery (.*?)</h3>', two)
            assert gallery_title and gallery_title.group(1) == REQUIRED_BADGE, mode

    def it_reads_the_refusal_from_the_template_and_runs_it_on_next_only():
        js = JS_PATH.read_text(encoding="utf-8")
        # The copy is the template's, so the script carries the attribute names and no message.
        assert GALLERY_MESSAGE not in js
        assert f'"{GALLERY_HOOK_ATTR}"' in js and '"data-composer-gallery-message"' in js
        assert '".cls-image-cell"' in js and '"composer-gallery-changed"' in js
        # Wired into the `only` leg of the walk alone: validateAll (Save Draft, the submit
        # confirm) passes no `only`, so a draft may still be saved without a photo.
        assert "if (!control && only !== undefined) control = emptyGallery(panes[i]);" in js
        assert js.count("emptyGallery(") == 2


def describe_the_description_count():
    def it_stamps_the_box_and_the_minimum_on_one_counter_under_the_description(composer):
        # The live count (#425) is server markup static/js/composer_description_count.js paints into.
        # The textarea's id and the readiness minimum are both stamped by the template from the
        # server's own values, so the script names no field and no number, and the help text under
        # the box names the same minimum the checklist enforces. One counter, on the Basics step.
        for mode, html in composer.pages.items():
            counters = _hooks(html, "data-description-count")
            assert len(counters) == 1, (mode, len(counters))
            step_one = html[html.index('data-composer-step="1"') : html.index('data-composer-step="2"')]
            assert _hooks(step_one, "data-description-count") == counters, mode
            counter = counters[0]
            assert counter["data-description-for"] == _by_name(_parse(html).controls[1], "description").attrs["id"]
            assert counter["data-description-min"] == str(READINESS_MIN_DESCRIPTION_CHARS), mode
            assert counter["aria-live"] == "polite", mode
            assert (counter["class"] or "").split() == ["pl-field-hint", "pl-composer-count-hint"], mode
            assert f'<p class="pl-field-hint">{DESCRIPTION_HELP_TEXT}</p>' in step_one, mode
            assert '<script src="/static/js/composer_description_count.js" defer></script>' in html, mode
        assert composer.form("create").fields["description"].help_text == DESCRIPTION_HELP_TEXT
        assert str(READINESS_MIN_DESCRIPTION_CHARS) in DESCRIPTION_HELP_TEXT


def describe_the_step_to_field_map_stays_in_one_place():
    def it_keeps_every_field_name_out_of_the_script():
        js = JS_PATH.read_text(encoding="utf-8")
        for name in FIELD_NAMES:
            assert not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", js), name
        assert "#id_" not in js and "[name=" not in js and "getElementById" not in js
        # The DOM pane is the map, and the browser's constraint API is the rule book.
        assert '"[data-composer-step]"' in js
        assert "checkValidity()" in js and "validationMessage" in js

    def it_keeps_every_field_name_out_of_the_alpine_root(instructor, client):
        # anchorSteps is the server's own anchor_steps() JSON (id_<field> -> step) for the
        # readiness jumps, rendered by the view; everything else in the root is field agnostic.
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        x_data = _parse(html).x_data or ""
        assert "anchorSteps: {" in x_data
        without_anchors = re.sub(r"anchorSteps: \{[^}]*\}", "anchorSteps: {}", x_data)
        for name in FIELD_NAMES:
            assert not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", without_anchors), name

    def it_keeps_the_body_script_safe_to_re_run_under_hx_boost():
        js = JS_PATH.read_text(encoding="utf-8")
        assert "if (window.plComposerValidation) return;" in js
        # Registering an Alpine component would move it to the head (tests/hub/base_scripts_spec.py).
        assert "Alpine.data(" not in js


def describe_the_wiring():
    def it_checks_the_current_step_on_next_and_every_step_on_save_and_submit(composer):
        html = composer.pages["edit"]
        x_data = _parse(html).x_data or ""
        for needle in (
            "next() {",
            "if (this.validateStep(this.phase)) { this.goTo(this.phase + 1); }",
            "validateStep(n) {",
            "check.firstInvalidStep(this.$root, n)",
            "validateAll() {",
            "check.firstInvalidStep(this.$root)",
            "refuse(step, control) {",
            "this.$nextTick(() => window.plComposerValidation.flag(control));",
            "saveDraft() {",
            "if (this.validateAll()) { this.$refs.composerForm.requestSubmit(); }",
            "confirmSubmit(id) {",
            "if (this.validateAll()) { this.$dispatch('open-confirm', id); }",
        ):
            assert needle in x_data, needle
        # The pane lookup lives in the script; the root names no pane and no modal of its own.
        assert "data-composer-step" not in x_data and "submit-class" not in x_data
        assert '@click="next()">Next &rarr;</button>' in html
        assert '@click.prevent="saveDraft()">Save Draft</button>' in html
        assert "confirmSubmit('submit-class')\">" in html
        assert "$dispatch('open-confirm', 'submit-class')\"" not in html
        # The confirm's own button still submits the form; the check ran before the modal opened.
        assert "document.getElementById('composer-form').requestSubmit();" in html
        assert '<script src="/static/js/composer_validation.js" defer></script>' in html

    def it_leaves_back_the_tabs_and_the_goto_events_free(composer):
        html = composer.pages["edit"]
        assert '@click="goTo(phase - 1)">&larr; Back</button>' in html
        for n in range(1, 6):
            assert f'@click="goTo({n})">' in html, n
        assert '@composer-goto-step.window="goTo($event.detail.step)"' in html
        assert "validateStep" not in html.split('@click="goTo(phase - 1)"')[1].split("Back</button>")[0]

    def it_keeps_novalidate_and_says_why(composer):
        html = composer.pages["create"]
        assert re.search(r'<form[^>]*id="composer-form"[^>]*\bnovalidate\b', html)
        # The reason is a template comment (never rendered), on the line above the form tag.
        source = TEMPLATE_PATH.read_text(encoding="utf-8")
        before_form = source.split('id="composer-form"')[0].splitlines()[-2]
        assert "novalidate stays on purpose" in before_form
        assert "cannot point at a control inside a hidden step" in before_form

    def it_opens_a_collapsed_section_for_a_control_the_check_points_at(composer):
        html = composer.pages["edit"]
        step_four = html[html.index('data-composer-step="4"') : html.index('data-composer-step="5"')]
        assert step_four.count('@composer-reveal-field="open = true"') == 6
