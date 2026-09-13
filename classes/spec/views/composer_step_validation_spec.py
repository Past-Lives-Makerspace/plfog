"""BDD specs for the composer's per step validation contract (issue #368, item 1).

The client (``static/js/composer_validation.js``) reads its rules from the rendered DOM: the
pane stamped ``data-composer-step="N"`` is the step, the controls inside it are the fields,
and the attributes Django put on them are the rules. Three things keep that honest, and
these specs pin them. The ``required`` attribute a pane renders is exactly what the server
form requires of that step, on both composers in both modes, so a form change cannot drift
from what Next enforces. Nothing the client blocks is something the server accepts: the
readiness items are submit gates, not Next gates, and formset rows carry no constraint
attribute at all, so they stay the server's. And no list of fields per step exists anywhere
but ``classes/composer.py``: not in the script, not in the Alpine root.
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
from classes.forms import ClassFaqForm, ClassOfferingForm, TeachClassOfferingForm, build_class_faq_formset
from classes.models import ClassOffering

REPO_ROOT = Path(__file__).resolve().parents[3]
JS_PATH = REPO_ROOT / "static" / "js" / "composer_validation.js"
TEMPLATE_PATH = REPO_ROOT / "templates" / "classes" / "_components" / "class_composer.html"
FIELD_NAMES = sorted({name for step in COMPOSER_STEPS for name in step.fields})
# The one rule set both composers render, by step. Steps 2, 4 and 5 require nothing: photos,
# details and review are readiness or optional, never a Next gate. scheduling_model is a
# required form field whose <select> has no empty option (a model default, no blank=True), so
# Django omits the attribute: the browser always posts a value and there is nothing to gate.
REQUIRED_BY_STEP = {
    1: {"title", "category", "price_cents"},
    2: set(),
    3: {"capacity", "member_discount_pct", "scheduling_type"},
    4: set(),
    5: set(),
}
VOID_TAGS = {"input", "img", "br", "hr", "link", "meta", "source", "wbr"}
CONTROL_TAGS = {"input", "select", "textarea"}


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
    else:
        client.force_login(admin_user)
        offering = cast(ClassOffering, ClassOfferingFactory(status=ClassOffering.Status.DRAFT, ready=True))
        create, edit = "classes:admin_class_create", "classes:admin_class_edit"
        form_class = ClassOfferingForm
    pages = {
        "create": client.get(reverse(create)).content.decode(),
        "edit": client.get(reverse(edit, kwargs={"pk": offering.pk})).content.decode(),
    }
    return _Composer(form_class=form_class, offering=offering, pages=pages)


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
            assert rendered_by_step == REQUIRED_BY_STEP, mode

    def it_requires_the_same_things_of_both_composers():
        # The admin only fields (instructor, is_private, private_for_name) are all optional, so
        # the client enforces one rule set whichever portal rendered the page.
        assert _server_required(ClassOfferingForm()) == _server_required(TeachClassOfferingForm())
        assert _server_required(ClassOfferingForm()) == set().union(*REQUIRED_BY_STEP.values())

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
        # those is a form rule, so none is a rendered attribute, so Next lets them all through.
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
                assert not _by_id(parsed.controls[3], control_id).required, (mode, control_id)

    def it_leaves_the_price_floor_and_the_discount_cap_to_the_server(composer):
        # Deliberate: the $1.00 floor and the 100% cap live in _PricingRulesMixin.clean_*, not in
        # the rendered min/max, so the client refuses only what every browser can read from the
        # attributes (a blank) and the server keeps the last word on the amount.
        for mode, html in composer.pages.items():
            controls = _parse(html).controls
            price = _by_name(controls[1], "price_cents")
            assert price.required and price.attrs.get("min") == "0" and price.attrs.get("step") == "0.01", mode
            discount = _by_name(controls[3], "member_discount_pct")
            assert discount.required and discount.attrs.get("min") == "0" and "max" not in discount.attrs, mode

    def it_never_refuses_a_youtube_link_typed_without_a_scheme(composer):
        # <input type="url"> demands a scheme; the server does not. forms.URLField normalises
        # "youtube.com/watch?v=…" to https (assume_scheme) and _validate_youtube_url takes what
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
