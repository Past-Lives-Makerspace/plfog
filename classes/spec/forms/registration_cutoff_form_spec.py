"""BDD specs for the registration cutoff on both composer forms: a toggle and an hours box."""

from __future__ import annotations

import pytest

from classes.composer import step_for_field
from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.forms import (
    REGISTRATION_CUTOFF_REQUIRED_MESSAGE,
    ClassOfferingForm,
    TeachClassOfferingForm,
    TeachPublishedClassForm,
)
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


@pytest.fixture(params=[TeachClassOfferingForm, ClassOfferingForm], ids=["teach", "admin"])
def form_class(request):
    return request.param


def _data(**overrides) -> dict:
    data = {
        "title": "Forge Basics",
        "category": str(CategoryFactory().pk),
        "description": "Hands-on intro.",
        "price_cents": "100.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
    }
    data.update(overrides)
    return data


def describe_the_fields():
    def it_starts_on_at_48_hours_for_a_new_class(form_class):
        form = form_class()
        assert form.fields["registration_cutoff_enabled"].initial is True
        assert form.fields["registration_cutoff_hours"].initial == 48

    def it_starts_on_at_the_saved_hours(form_class):
        form = form_class(instance=ClassOfferingFactory(registration_cutoff_hours=12))
        assert form.fields["registration_cutoff_enabled"].initial is True
        assert form.fields["registration_cutoff_hours"].initial == 12

    def it_starts_off_at_48_hours_when_the_cutoff_is_off(form_class):
        form = form_class(instance=ClassOfferingFactory(registration_cutoff_hours=None))
        assert form.fields["registration_cutoff_enabled"].initial is False
        assert form.fields["registration_cutoff_hours"].initial == 48

    def it_binds_the_toggle_to_the_composer_state_and_bounds_the_box(form_class):
        form = form_class()
        assert form.fields["registration_cutoff_enabled"].widget.attrs["x-model"] == "registrationCutoff"
        attrs = form.fields["registration_cutoff_hours"].widget.attrs
        assert (attrs["min"], attrs["max"]) == (1, 720)
        assert form.fields["registration_cutoff_hours"].required is False

    def it_sits_on_step_3():
        assert step_for_field("registration_cutoff_enabled") == 3
        assert step_for_field("registration_cutoff_hours") == 3

    def it_is_not_on_the_published_class_light_edit():
        fields = set(TeachPublishedClassForm().fields)
        assert {"registration_cutoff_enabled", "registration_cutoff_hours"}.isdisjoint(fields)


def describe_clean():
    def it_saves_null_when_the_toggle_is_off(form_class):
        form = form_class(_data(registration_cutoff_hours="24"))
        assert form.is_valid(), form.errors
        assert form.cleaned_data["registration_cutoff_hours"] is None
        assert form.save().registration_cutoff_hours is None

    def it_saves_the_hours_when_the_toggle_is_on(form_class):
        form = form_class(_data(registration_cutoff_enabled="on", registration_cutoff_hours="24"))
        assert form.is_valid(), form.errors
        assert form.save().registration_cutoff_hours == 24

    def it_refuses_an_empty_box_when_the_toggle_is_on(form_class):
        form = form_class(_data(registration_cutoff_enabled="on", registration_cutoff_hours=""))
        assert not form.is_valid()
        assert form.errors["registration_cutoff_hours"] == [REGISTRATION_CUTOFF_REQUIRED_MESSAGE]

    def it_refuses_zero_and_more_than_720_hours(form_class):
        for hours in ("0", "721"):
            form = form_class(_data(registration_cutoff_enabled="on", registration_cutoff_hours=hours))
            assert not form.is_valid(), hours
            assert list(form.errors) == ["registration_cutoff_hours"], hours

    def it_stores_no_cutoff_for_a_flexible_class(form_class):
        flexible = ClassOffering.SchedulingModel.FLEXIBLE
        form = form_class(
            _data(scheduling_model=flexible, registration_cutoff_enabled="on", registration_cutoff_hours="24")
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["registration_cutoff_hours"] is None

    def it_does_not_refuse_a_hidden_empty_box_on_a_flexible_class(form_class):
        # Hours cleared, then switched to Flexible: the box is hidden, so no error may land on it.
        flexible = ClassOffering.SchedulingModel.FLEXIBLE
        form = form_class(
            _data(scheduling_model=flexible, registration_cutoff_enabled="on", registration_cutoff_hours="")
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["registration_cutoff_hours"] is None

    def it_accepts_the_bounds(form_class):
        for hours in ("1", "720"):
            form = form_class(_data(registration_cutoff_enabled="on", registration_cutoff_hours=hours))
            assert form.is_valid(), form.errors
            assert form.cleaned_data["registration_cutoff_hours"] == int(hours)
