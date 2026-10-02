"""BDD specs for the booking line box on the three class forms.

The composer forms show the box under Flexible with the standard line already in it, so the
instructor edits the page's own words. What comes back unchanged is stored blank, which keeps
the page building the line live (a renamed instructor, a window added later); what they changed
is stored as typed. The published class form carries the box only for a flexible class.
"""

from __future__ import annotations

from datetime import date

import pytest

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db

OWN_LINE = "Email me and we will pick a Saturday.\nMornings are best."


def _flexible(**traits) -> ClassOffering:
    traits.setdefault("instructor", InstructorFactory(full_legal_name="Billy Anvil"))
    return ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, **traits)


def _post(form_class, offering: ClassOffering, text: str, rendered: str | None = None):
    """A composer POST with ``text`` in the box; ``rendered`` is the line the box was filled with (today's unless given)."""
    data = {
        "flexible_booking_text_default": offering.default_flexible_booking_line if rendered is None else rendered,
        "title": offering.title,
        "slug": offering.slug,
        "category": str(offering.category_id),
        "instructor": str(offering.instructor_id),
        "description": "Hands on intro.",
        "price_cents": "100.00",
        "capacity": "6",
        "scheduling_model": "flexible",
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "flexible_note": "",
        "flexible_booking_text": text,
        "flexible_starts_on": "",
        "flexible_ends_on": "",
        "sale_kind": "percent",
    }
    return form_class(data=data, instance=offering)


@pytest.fixture(params=[TeachClassOfferingForm, ClassOfferingForm], ids=["teach", "admin"])
def form_class(request):
    return request.param


def describe_the_booking_line_box_on_the_composer():
    def it_is_labelled_and_optional(form_class):
        field = form_class().fields["flexible_booking_text"]
        assert field.label == "How booking works"
        assert field.required is False
        assert field.help_text.startswith("Shown on the class page under Flexible Scheduling.")
        assert field.widget.attrs["rows"] == 3

    def it_is_pre_filled_with_the_standard_line_when_nothing_was_written(form_class):
        offering = _flexible(flexible_ends_on=date(2026, 12, 1))
        form = form_class(instance=offering)
        assert form["flexible_booking_text"].value() == offering.default_flexible_booking_line
        assert "Billy Anvil" in form["flexible_booking_text"].value()
        assert "inside this window" in form["flexible_booking_text"].value()

    def it_is_pre_filled_with_the_teaching_member_on_a_new_class():
        # The teach form sets the instructor on save; the box has to name them before that, or an
        # edited line would carry "your instructor" onto the live page for good.
        teacher = InstructorFactory(full_legal_name="Riley Harrison")
        value = TeachClassOfferingForm(teaching_member=teacher)["flexible_booking_text"].value()
        assert "directly with Riley Harrison and pick a day" in value

    def it_is_pre_filled_with_your_instructor_on_a_new_admin_class():
        value = ClassOfferingForm()["flexible_booking_text"].value()
        assert value.startswith("After you register, you'll book your session directly with your instructor")

    def it_carries_the_rendered_line_in_a_hidden_field(form_class):
        offering = _flexible()
        html = str(form_class(instance=offering)["flexible_booking_text_default"])
        assert 'type="hidden"' in html
        assert 'name="flexible_booking_text_default"' in html
        assert "Billy Anvil" in html

    def it_counts_the_line_the_box_was_filled_with_as_unchanged_after_a_rename(form_class):
        # The instructor was renamed while the composer sat open: the box and the hidden field
        # both still say Billy, and today's line does not.
        offering = _flexible()
        stale = offering.default_flexible_booking_line
        offering.instructor.full_legal_name = "William E. Ottaviani"
        offering.instructor.save(update_fields=["full_legal_name"])
        offering.refresh_from_db()
        assert stale != offering.default_flexible_booking_line
        form = _post(form_class, offering, stale, rendered=stale)
        assert form.is_valid(), form.errors
        assert form.save().flexible_booking_text == ""

    def it_counts_todays_line_as_unchanged_when_a_post_carries_no_hidden_field(form_class):
        offering = _flexible()
        form = _post(form_class, offering, offering.default_flexible_booking_line)
        form.data.pop("flexible_booking_text_default")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["flexible_booking_text"] == ""

    def it_stores_a_standard_line_with_one_word_changed(form_class):
        offering = _flexible()
        changed = offering.default_flexible_booking_line.replace("pick a day", "pick a Saturday")
        form = _post(form_class, offering, changed)
        assert form.is_valid(), form.errors
        assert form.save().flexible_booking_text == changed

    def it_stores_a_standard_line_with_contact_details_after_the_name(form_class):
        offering = _flexible()
        with_email = offering.default_flexible_booking_line.replace(
            "Billy Anvil", "Billy Anvil (email billy@example.com)"
        )
        form = _post(form_class, offering, with_email)
        assert form.is_valid(), form.errors
        assert form.save().flexible_booking_text == with_email

    def it_shows_the_instructors_own_text_once_written(form_class):
        offering = _flexible(flexible_booking_text=OWN_LINE)
        assert form_class(instance=offering)["flexible_booking_text"].value() == OWN_LINE

    def it_stores_blank_when_the_standard_line_comes_back_unchanged(form_class):
        offering = _flexible()
        form = _post(form_class, offering, offering.default_flexible_booking_line)
        assert form.is_valid(), form.errors
        assert form.save().flexible_booking_text == ""

    def it_counts_a_rewrapped_standard_line_as_unchanged(form_class):
        offering = _flexible()
        wrapped = offering.default_flexible_booking_line.replace(" and pick", "\n  and pick") + "  "
        form = _post(form_class, offering, wrapped)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["flexible_booking_text"] == ""

    def it_stores_the_instructors_text_as_typed(form_class):
        offering = _flexible()
        form = _post(form_class, offering, OWN_LINE)
        assert form.is_valid(), form.errors
        assert form.save().flexible_booking_text == OWN_LINE

    def it_stores_blank_when_the_box_is_emptied(form_class):
        offering = _flexible(flexible_booking_text=OWN_LINE)
        form = _post(form_class, offering, "  ")
        assert form.is_valid(), form.errors
        assert form.save().flexible_booking_text == ""


def describe_the_booking_line_box_on_a_published_class():
    def _post_published(offering: ClassOffering, **extra):
        data = {
            "subtitle": "",
            "description": "Hands on intro.",
            "prerequisites": "",
            "materials_included": "",
            "materials_to_bring": "",
            "safety_requirements": "",
            "age_guardian_note": "",
            "flexible_note": "",
            "flexible_booking_text_default": offering.default_flexible_booking_line,
            "video_url": "",
            **extra,
        }
        return TeachPublishedClassForm(data=data, instance=offering)

    def it_carries_the_box_pre_filled_for_a_flexible_class():
        offering = _flexible(status=ClassOffering.Status.PUBLISHED)
        form = TeachPublishedClassForm(instance=offering)
        assert form["flexible_booking_text"].value() == offering.default_flexible_booking_line
        assert form.fields["flexible_booking_text"].label == "How booking works"

    def it_has_no_box_and_no_hidden_field_for_a_fixed_class():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, category=CategoryFactory())
        fields = TeachPublishedClassForm(instance=offering).fields
        assert "flexible_booking_text" not in fields
        assert "flexible_booking_text_default" not in fields

    def it_stores_the_instructors_text_and_blank_for_the_standard_line():
        offering = _flexible(status=ClassOffering.Status.PUBLISHED)
        form = _post_published(offering, flexible_booking_text=OWN_LINE)
        assert form.is_valid(), form.errors
        assert form.save().flexible_booking_text == OWN_LINE
        form = _post_published(offering, flexible_booking_text=offering.default_flexible_booking_line)
        assert form.is_valid(), form.errors
        assert form.save().flexible_booking_text == ""
