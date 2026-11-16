"""Form hints carry the id Django's aria-describedby names (#1028).

Django 6 adds ``aria-describedby="<id>_helptext"`` to the widget of any field with help text.
``components/form_field.html`` (and ``components/toggle.html`` for a checkbox) give the hint
that id, so a screen reader reads the hint with the field instead of skipping it.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django import forms
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from tests.membership.factories import MembershipPlanFactory

HELP = "Spec help text for the hint."


class _HintForm(forms.Form):
    name = forms.CharField(help_text=HELP)
    agree = forms.BooleanField(required=False, help_text=HELP)
    colour = forms.CharField(help_text=HELP, widget=forms.TextInput(attrs={"type": "color"}))


class _StartTags(HTMLParser):
    """The attributes of every start tag in the document, in order.

    A ``<template>``'s content is inert and outside the document's ids (the contact formset's
    empty row is stamped into three of them, ``__prefix__`` swapped on clone), so it is skipped.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[dict[str, str | None]] = []
        self._template_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "template":
            self._template_depth += 1
        elif not self._template_depth:
            self.tags.append(dict(attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag == "template":
            self._template_depth -= 1


def _tags(html: str) -> list[dict[str, str | None]]:
    parser = _StartTags()
    parser.feed(html)
    return parser.tags


def _described_by(html: str, name: str) -> str | None:
    (control,) = [tag for tag in _tags(html) if tag.get("name") == name]
    return control.get("aria-describedby")


def _ids_of(html: str, css_class: str) -> list[str | None]:
    return [tag.get("id") for tag in _tags(html) if css_class in (tag.get("class") or "").split()]


def _render(field: forms.BoundField, **context: object) -> str:
    return render_to_string("components/form_field.html", {"field": field, **context})


def describe_form_field_hints():
    def it_gives_the_help_text_hint_the_id_the_widget_names():
        html = _render(_HintForm()["name"])
        assert _described_by(html, "name") == "id_name_helptext"
        assert _ids_of(html, "pl-field-hint") == ["id_name_helptext"]

    def it_gives_a_field_hint_override_the_same_id():
        html = _render(_HintForm()["name"], field_hint="Spec override hint.")
        assert _described_by(html, "name") == "id_name_helptext"
        assert '<p class="pl-field-hint" id="id_name_helptext">Spec override hint.</p>' in html

    def it_gives_the_color_picker_hint_the_id_as_well():
        # The picker draws its own inputs, so no control carries Django's reference here.
        assert _ids_of(_render(_HintForm()["colour"]), "pl-field-hint") == ["id_colour_helptext"]

    def describe_for_a_checkbox():
        def it_gives_the_toggle_description_the_id_the_checkbox_names():
            html = _render(_HintForm()["agree"])
            assert _described_by(html, "agree") == "id_agree_helptext"
            assert _ids_of(html, "pl-toggle-desc") == ["id_agree_helptext"]

        def it_renders_no_description_when_hints_are_off():
            assert _ids_of(_render(_HintForm()["agree"], field_hint_off=True), "pl-toggle-desc") == []

    def describe_when_hints_are_off():
        def it_renders_no_hint_and_leaves_djangos_reference_pointing_at_nothing():
            # The chosen behaviour (#1028): the rows that switch hints off explain themselves and
            # their help text is model help text written for developers, so nothing is rendered,
            # not even an sr-only copy. Browsers skip an aria-describedby id that is not on the page.
            html = _render(_HintForm()["name"], field_hint_off=True)
            assert _described_by(html, "name") == "id_name_helptext"
            assert _ids_of(html, "pl-field-hint") == []
            assert 'id="id_name_helptext"' not in html

    def describe_when_the_form_renders_no_ids():
        def it_renders_the_hint_without_an_id():
            html = _render(_HintForm(auto_id=False)["name"])
            assert _described_by(html, "name") is None
            assert _ids_of(html, "pl-field-hint") == [None]

        def it_renders_the_toggle_description_without_an_id():
            html = _render(_HintForm(auto_id=False)["agree"])
            assert _described_by(html, "agree") is None
            assert _ids_of(html, "pl-toggle-desc") == [None]


@pytest.mark.django_db
def describe_the_member_settings_page():
    def it_points_each_hinted_control_at_its_one_hint(client: Client):
        MembershipPlanFactory()
        client.force_login(User.objects.create_user(username="hintids"))
        tags = _tags(client.get(reverse("hub_user_settings")).content.decode())
        references = {ref for tag in tags for ref in (tag.get("aria-describedby") or "").split()}
        # The commission note is a form_field.html hint; open for commissions a toggle.html description.
        for ref, hint_class in (
            ("id_commission_note_helptext", "pl-field-hint"),
            ("id_open_for_commissions_helptext", "pl-toggle-desc"),
        ):
            assert ref in references
            assert [tag.get("class") for tag in tags if tag.get("id") == ref] == [hint_class]

    def it_never_repeats_a_hint_id(client: Client):
        MembershipPlanFactory()
        client.force_login(User.objects.create_user(username="hintonce"))
        tags = _tags(client.get(reverse("hub_user_settings")).content.decode())
        hint_ids = [tag_id for tag in tags if (tag_id := tag.get("id")) and tag_id.endswith("_helptext")]
        assert hint_ids
        assert len(hint_ids) == len(set(hint_ids))
