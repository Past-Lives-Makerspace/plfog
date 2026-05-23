"""BDD specs for custom-question injection in RegistrationForm."""

from __future__ import annotations

import pytest

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import RegistrationForm
from classes.models import ClassOffering, ClassSettings, RegistrationAnswer, RegistrationQuestion

pytestmark = pytest.mark.django_db


@pytest.fixture
def offering():
    return ClassOfferingFactory(
        title="Forge Basics",
        slug="forge-basics",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=0,  # free class — simpler test path
        member_discount_pct=0,
        capacity=4,
    )


@pytest.fixture
def settings_obj():
    return ClassSettings.load()


def _post_data(**overrides):
    data = {
        "first_name": "Sam",
        "last_name": "Smith",
        "pronouns": "",
        "email": "sam@example.com",
        "phone": "",
        "prior_experience": "",
        "looking_for": "",
        "discount_code": "",
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }
    data.update(overrides)
    return data


def describe_RegistrationForm_custom_questions():
    def it_injects_one_field_per_active_question(offering, settings_obj):
        q1 = RegistrationQuestion.objects.create(prompt="Pronouns?", sort_order=1)
        q2 = RegistrationQuestion.objects.create(prompt="Allergies?", sort_order=2)
        form = RegistrationForm(offering=offering, settings_obj=settings_obj)
        assert f"custom_q_{q1.pk}" in form.fields
        assert f"custom_q_{q2.pk}" in form.fields

    def it_skips_inactive_questions(offering, settings_obj):
        active = RegistrationQuestion.objects.create(prompt="Active?", is_active=True)
        inactive = RegistrationQuestion.objects.create(prompt="Retired?", is_active=False)
        form = RegistrationForm(offering=offering, settings_obj=settings_obj)
        assert f"custom_q_{active.pk}" in form.fields
        assert f"custom_q_{inactive.pk}" not in form.fields

    def it_validates_required_fields(offering, settings_obj):
        q = RegistrationQuestion.objects.create(prompt="Required?", is_required=True)
        form = RegistrationForm(
            data=_post_data(),  # no custom_q_<pk>
            offering=offering,
            settings_obj=settings_obj,
        )
        assert not form.is_valid()
        assert f"custom_q_{q.pk}" in form.errors

    def it_persists_answers_on_save(offering, settings_obj):
        q = RegistrationQuestion.objects.create(prompt="Pronouns?")
        form = RegistrationForm(
            data=_post_data(**{f"custom_q_{q.pk}": "they/them"}),
            offering=offering,
            settings_obj=settings_obj,
        )
        assert form.is_valid(), form.errors
        registration = form.save()
        answer = RegistrationAnswer.objects.get(registration=registration, question=q)
        assert answer.answer_text == "they/them"

    def it_routes_long_text_to_textarea(offering, settings_obj):
        from django import forms as django_forms

        RegistrationQuestion.objects.create(
            prompt="Tell us a story",
            question_type=RegistrationQuestion.QuestionType.LONG_TEXT,
        )
        form = RegistrationForm(offering=offering, settings_obj=settings_obj)
        field = next(f for name, f in form.fields.items() if name.startswith("custom_q_"))
        assert isinstance(field.widget, django_forms.Textarea)

    def it_routes_single_choice_to_choicefield_with_options(offering, settings_obj):
        q = RegistrationQuestion.objects.create(
            prompt="Pick one",
            question_type=RegistrationQuestion.QuestionType.SINGLE_CHOICE,
            choices_json=["Red", "Blue", "Green"],
        )
        form = RegistrationForm(offering=offering, settings_obj=settings_obj)
        field = form.fields[f"custom_q_{q.pk}"]
        # Choices include the blank placeholder plus the three options.
        labels = [label for _, label in field.choices]
        assert "Red" in labels and "Blue" in labels and "Green" in labels

    def it_routes_yes_no_to_typed_choice_field(offering, settings_obj):
        from django import forms as django_forms

        q = RegistrationQuestion.objects.create(
            prompt="Have you taken a class before?",
            question_type=RegistrationQuestion.QuestionType.YES_NO,
        )
        form = RegistrationForm(offering=offering, settings_obj=settings_obj)
        field = form.fields[f"custom_q_{q.pk}"]
        assert isinstance(field, django_forms.TypedChoiceField)
        values = [v for v, _ in field.choices]
        assert "yes" in values and "no" in values

    def it_skips_empty_answers_in_create_custom_answers(offering, settings_obj):
        q = RegistrationQuestion.objects.create(prompt="Optional?", is_required=False)
        # Submit with no value for the optional question.
        form = RegistrationForm(
            data=_post_data(**{f"custom_q_{q.pk}": ""}),
            offering=offering,
            settings_obj=settings_obj,
        )
        assert form.is_valid(), form.errors
        registration = form.save()
        # No RegistrationAnswer should be created for the blank optional field.
        assert not RegistrationAnswer.objects.filter(registration=registration, question=q).exists()

    def it_exposes_custom_question_fields_as_bound_fields(offering, settings_obj):
        from django.forms import BoundField

        q = RegistrationQuestion.objects.create(prompt="What brings you here?")
        form = RegistrationForm(offering=offering, settings_obj=settings_obj)
        bound_fields = form.custom_question_fields
        assert len(bound_fields) == 1
        assert isinstance(bound_fields[0], BoundField)
        assert bound_fields[0].name == f"custom_q_{q.pk}"
