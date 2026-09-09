"""BDD specs for the hidden card_focus field on both class forms (_CardFocusMixin)."""

from __future__ import annotations

import json

import pytest

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


def _base_data(**overrides) -> dict:
    data = {
        "title": "Forge Basics",
        "category": str(CategoryFactory().pk),
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
        "image": "",
        "hero_crop": "",
        "card_focus": "",
    }
    data.update(overrides)
    return data


def _form(form_class, instance=None, **overrides):
    data = _base_data(**overrides)
    if form_class is ClassOfferingForm:
        data.update(instructor=str(InstructorFactory().pk), is_private="", private_for_name="")
    return form_class(data=data, instance=instance)


@pytest.fixture(params=[TeachClassOfferingForm, ClassOfferingForm], ids=["teach", "admin"])
def form_class(request):
    return request.param


def describe_card_focus_field():
    def it_is_a_hidden_input_the_tool_can_find(form_class):
        form = form_class()
        widget = form.fields["card_focus"].widget
        assert widget.input_type == "hidden"
        assert "data-card-focus-input" in widget.attrs

    def it_starts_empty_on_a_class_that_follows_the_banner(form_class):
        offering = ClassOfferingFactory()
        assert form_class(instance=offering)["card_focus"].value() == ""

    def it_starts_from_the_saved_focal_point(form_class):
        offering = ClassOfferingFactory(card_focus_x=15, card_focus_y=85)
        assert json.loads(form_class(instance=offering)["card_focus"].value()) == {"x": 15, "y": 85}

    def it_is_optional(form_class):
        form = _form(form_class, card_focus="")
        assert form.is_valid(), form.errors

    def it_saves_a_focal_point(form_class):
        form = _form(form_class, card_focus=json.dumps({"x": 25, "y": 75}))
        assert form.is_valid(), form.errors
        offering = form.save()
        offering.refresh_from_db()
        assert (offering.card_focus_x, offering.card_focus_y) == (25, 75)
        assert offering.card_object_position == "25% 75%"

    def it_accepts_the_edges(form_class):
        form = _form(form_class, card_focus=json.dumps({"x": 0, "y": 100}))
        assert form.is_valid(), form.errors
        offering = form.save()
        assert (offering.card_focus_x, offering.card_focus_y) == (0, 100)

    def it_clears_both_columns_when_emptied(form_class):
        offering = ClassOfferingFactory(card_focus_x=15, card_focus_y=85)
        form = _form(form_class, instance=offering, card_focus="")
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert offering.card_focus_x is None
        assert offering.card_focus_y is None

    def it_rejects_a_value_above_one_hundred(form_class):
        form = _form(form_class, card_focus=json.dumps({"x": 101, "y": 50}))
        assert not form.is_valid()
        assert form.errors["card_focus"] == ["Focal point must be between 0 and 100."]

    def it_rejects_a_negative_value(form_class):
        form = _form(form_class, card_focus=json.dumps({"x": 50, "y": -1}))
        assert not form.is_valid()
        assert form.errors["card_focus"] == ["Focal point must be between 0 and 100."]

    def it_rejects_a_non_integer(form_class):
        form = _form(form_class, card_focus=json.dumps({"x": "left", "y": 50}))
        assert not form.is_valid()
        assert form.errors["card_focus"] == ["Focal point is malformed; clear it and try again."]

    def it_rejects_a_missing_axis(form_class):
        form = _form(form_class, card_focus=json.dumps({"x": 50}))
        assert not form.is_valid()
        assert "malformed" in form.errors["card_focus"][0]

    def it_rejects_broken_json(form_class):
        form = _form(form_class, card_focus="{not json")
        assert not form.is_valid()
        assert "malformed" in form.errors["card_focus"][0]

    def it_leaves_the_banner_crop_alone(form_class):
        offering = ClassOfferingFactory(hero_crop_x=10, hero_crop_y=90)
        form = _form(form_class, instance=offering, card_focus=json.dumps({"x": 30, "y": 40}))
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert (offering.hero_crop_x, offering.hero_crop_y) == (10, 90)
        assert offering.card_object_position == "30% 40%"


def describe_sale_fields_left_the_class_forms():
    def it_has_no_sale_field(form_class):
        names = set(form_class().fields)
        assert not {n for n in names if n.startswith("sale_")}

    def it_ignores_a_crafted_sale_post(form_class):
        offering = ClassOfferingFactory(sale_enabled=False, price_cents=10000)
        form = _form(form_class, instance=offering, sale_enabled="on", sale_kind="percent", sale_percent="20")
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert offering.sale_enabled is False
        assert offering.sale_percent is None
