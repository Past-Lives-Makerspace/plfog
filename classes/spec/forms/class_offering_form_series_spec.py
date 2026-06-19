"""BDD specs for the scheduling_type (series vs single) field on ClassOffering forms."""

from __future__ import annotations

import pytest

from classes.factories import CategoryFactory, InstructorFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


def _admin_post_data(**overrides) -> dict:
    data = {
        "title": "Forge Basics",
        "slug": "forge-basics",
        "category": str(CategoryFactory().pk),
        "instructor": str(InstructorFactory().pk),
        "description": "Hands-on intro.",
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_minimum": "",
        "age_guardian_note": "",
        "price_cents": "100.00",
        "member_discount_pct": "10",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "flexible_note": "",
        "is_private": "",
        "private_for_name": "",
        "recurring_pattern": "",
        "image": "",
        "requires_model_release": "",
    }
    data.update(overrides)
    return data


def describe_ClassOfferingForm_scheduling_type():
    def it_exposes_the_scheduling_type_field(db):
        assert "scheduling_type" in ClassOfferingForm().fields

    def it_saves_a_single_session_offering_by_default(db):
        form = ClassOfferingForm(data=_admin_post_data())
        assert form.is_valid(), form.errors
        offering = form.save()
        assert offering.scheduling_type == ClassOffering.SchedulingType.SINGLE_SESSION

    def it_saves_a_series_offering(db):
        form = ClassOfferingForm(data=_admin_post_data(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE))
        assert form.is_valid(), form.errors
        offering = form.save()
        assert offering.scheduling_type == ClassOffering.SchedulingType.SERIES_PACKAGE

    def it_assigns_the_series_a_grouping_key(db):
        # Series now group like singles, so multiple runs of the same class
        # collapse into one catalog card instead of standing alone.
        form = ClassOfferingForm(data=_admin_post_data(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE))
        assert form.is_valid(), form.errors
        offering = form.save()
        assert offering.grouping_key != ""


def describe_TeachClassOfferingForm_scheduling_type():
    def it_exposes_the_scheduling_type_field(db):
        assert "scheduling_type" in TeachClassOfferingForm().fields
