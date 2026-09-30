"""The class description is written in the rich-text editor and stored as sanitized editor HTML.

Both composer forms and the live-class edit form share one rule (``_RichDescriptionMixin``):
editor HTML is sanitized to the allowlist, an empty editor stores blank, and plain text from a
client without the editor is kept exactly as typed, angle brackets and all (issue #425).
"""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm
from core.widgets import RichBodyEditorWidget

pytestmark = pytest.mark.django_db

EDITOR_HTML = '<p onclick="x()">Make a <strong>coat hook</strong> from one bar.</p><ol><li data-list="bullet">Bring gloves</li></ol><iframe src="x"></iframe>'
CLEAN_HTML = "<p>Make a <strong>coat hook</strong> from one bar.</p><ul><li>Bring gloves</li></ul>"
PLAIN = "Wear <closed toe shoes> and bring <safety glasses> to the shop."


def _cleaned(form_class, instance, description: str) -> str:
    form = form_class(data={"description": description}, instance=instance)
    form.full_clean()
    return form.cleaned_data["description"]


@pytest.mark.parametrize("form_class", [ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm])
def describe_the_description_field(form_class):
    def it_is_the_rich_text_editor():
        assert isinstance(form_class().fields["description"].widget, RichBodyEditorWidget)

    def it_stores_editor_html_sanitized():
        offering = ClassOfferingFactory()
        assert _cleaned(form_class, offering, EDITOR_HTML) == CLEAN_HTML

    def it_stores_an_empty_editor_as_blank():
        offering = ClassOfferingFactory()
        assert _cleaned(form_class, offering, "<p><br></p>") == ""

    def it_keeps_plain_text_exactly_as_typed():
        offering = ClassOfferingFactory()
        assert _cleaned(form_class, offering, PLAIN) == PLAIN


def describe_saving_through_the_live_class_form():
    def it_writes_the_sanitized_html_to_the_class():
        offering = ClassOfferingFactory()
        form = TeachPublishedClassForm(data={"description": EDITOR_HTML}, instance=offering)
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert offering.description == CLEAN_HTML
