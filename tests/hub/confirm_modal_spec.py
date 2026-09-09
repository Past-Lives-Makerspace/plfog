"""BDD specs for the two `components/confirm_modal.html` fixes (spec D §6.13, D22).

The first is a live bug in shipped code: the note input and the typed-confirmation input
both claim ``.pl-input``, a class that was defined in no CSS file in this repo, and both
sit outside any field scope — so they rendered as browser-default white boxes with
near-invisible text on the Obsidian theme, for every caller.

A rendering test cannot see CSS, so the assertion is on the stylesheet, which is what was
actually missing. The regression guard for a shared-component change is the other half:
all three pre-existing callers still render their confirm inputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string

pytestmark = pytest.mark.django_db


def _components_css() -> str:
    return (Path(settings.BASE_DIR) / "static" / "css" / "components.css").read_text()


def _pl_input_rule() -> str:
    """The body of the ``.pl-input { ... }`` rule."""
    css = _components_css()
    start = css.index(".pl-input {")
    return css[start : css.index("}", start)]


def describe_the_pl_input_rule():
    def it_exists_in_the_stylesheet_the_markup_already_claims():
        assert ".pl-input {" in _components_css()

    def it_reads_the_theme_input_tokens_so_both_themes_come_free():
        rule = _pl_input_rule()
        assert "var(--hub-input-bg)" in rule
        assert "var(--hub-input-border)" in rule
        assert "var(--hub-text)" in rule

    def it_gives_the_field_a_focus_ring():
        assert ".pl-input:focus {" in _components_css()

    def it_never_reaches_for_a_token_that_does_not_exist():
        # --surface and --hub-warn are both undefined in this repo; either would render
        # the box white (and transparent, respectively) in both themes.
        rule = _pl_input_rule()
        assert "var(--surface" not in rule
        assert "var(--hub-warn" not in rule


def describe_the_component_markup():
    def it_styles_the_note_input_with_that_class():
        html = render_to_string(
            "components/confirm_modal.html",
            {"confirm_id": "spec", "confirm_message": "Sure?", "confirm_note_name": "reason"},
        )
        assert 'class="pl-input"' in html

    def it_styles_the_typed_confirmation_input_with_that_class():
        html = render_to_string(
            "components/confirm_modal.html",
            {"confirm_id": "spec", "confirm_message": "Sure?", "confirm_typed_value": "ARCHIVE"},
        )
        assert 'class="pl-input"' in html


def describe_confirm_note_required():
    def it_leaves_the_disabled_expression_untouched_when_not_set():
        # Byte-identical to today for every existing caller, none of which passes it.
        html = render_to_string(
            "components/confirm_modal.html",
            {
                "confirm_id": "spec",
                "confirm_message": "Sure?",
                "confirm_action_url": "/go/",
                "confirm_typed_value": "ARCHIVE",
                "confirm_note_name": "reason",
            },
        )
        assert ":disabled=\"typed !== 'ARCHIVE'\"" in html
        assert "note.trim()" not in html

    def it_adds_the_note_check_when_set():
        html = render_to_string(
            "components/confirm_modal.html",
            {
                "confirm_id": "spec",
                "confirm_message": "Sure?",
                "confirm_action_url": "/go/",
                "confirm_typed_value": "ARCHIVE",
                "confirm_note_name": "reason",
                "confirm_note_required": 1,
            },
        )
        assert ":disabled=\"typed !== 'ARCHIVE' || note.trim() === ''\"" in html

    def it_gates_on_the_note_alone_without_a_typed_value():
        html = render_to_string(
            "components/confirm_modal.html",
            {
                "confirm_id": "spec",
                "confirm_message": "Sure?",
                "confirm_action_url": "/go/",
                "confirm_note_name": "reason",
                "confirm_note_required": 1,
            },
        )
        assert ":disabled=\"note.trim() === ''\"" in html

    def it_drops_the_optional_label_default_when_required():
        required = render_to_string(
            "components/confirm_modal.html",
            {"confirm_id": "spec", "confirm_note_name": "reason", "confirm_note_required": 1},
        )
        plain = render_to_string("components/confirm_modal.html", {"confirm_id": "spec", "confirm_note_name": "reason"})
        assert "Note (optional)" not in required
        assert "Note (optional)" in plain

    def it_still_lets_the_caller_name_the_label():
        html = render_to_string(
            "components/confirm_modal.html",
            {
                "confirm_id": "spec",
                "confirm_note_name": "reason",
                "confirm_note_required": 1,
                "confirm_note_label": "Why is this page being removed?",
            },
        )
        assert "Why is this page being removed?" in html


def describe_the_three_existing_callers():
    """A shared-component change is not finished until the screens it was NOT written for
    still render. All three use the note or typed mode."""

    @pytest.mark.parametrize(
        "template",
        [
            "hub/equipment_manage.html",
            "hub/user_settings.html",
            "classes/partials/roster_modals.html",
        ],
    )
    def it_still_renders_its_confirm_input(template):
        source = (Path(settings.BASE_DIR) / "templates" / template).read_text()
        assert "components/confirm_modal.html" in source

    def it_leaves_the_archive_flow_as_the_only_caller_asking_for_a_required_note():
        root = Path(settings.BASE_DIR) / "templates"
        callers = [
            path.name
            for path in root.rglob("*.html")
            if path.name != "confirm_modal.html" and "confirm_note_required" in path.read_text()
        ]
        assert callers == ["_wiki_moderation_actions.html"]
