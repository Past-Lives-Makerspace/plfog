"""BDD specs for the catalog card focal point (card_object_position) and turn_sale_off."""

from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering


def _real_png(size: tuple[int, int] = (1000, 500)) -> SimpleUploadedFile:
    img = Image.new("RGB", size, (50, 50, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return SimpleUploadedFile("hero.png", buf.getvalue(), content_type="image/png")


def describe_card_object_position():
    def it_uses_the_card_focus_when_both_are_set(db):
        offering = ClassOfferingFactory(card_focus_x=20, card_focus_y=80)
        assert offering.card_object_position == "20% 80%"

    def it_falls_back_to_centre_when_nothing_is_stored(db):
        offering = ClassOfferingFactory()
        assert offering.card_object_position == "50% 50%"

    def it_follows_the_banner_focal_point_when_neither_is_set(db):
        # The detail page Adjust sliders store w=0/h=0: "focal point mode". The card follows it.
        offering = ClassOfferingFactory(hero_crop_x=10, hero_crop_y=90)
        assert offering.card_object_position == "10% 90%"

    def it_follows_the_banner_crop_box_when_neither_is_set(db):
        offering = ClassOfferingFactory(
            image=_real_png(), hero_crop_x=200, hero_crop_y=100, hero_crop_w=400, hero_crop_h=200
        )
        assert offering.card_object_position == "40.0% 40.0%"

    def it_follows_the_banner_when_only_x_is_set(db):
        offering = ClassOfferingFactory(card_focus_x=20, hero_crop_x=10, hero_crop_y=90)
        assert offering.card_object_position == "10% 90%"

    def it_follows_the_banner_when_only_y_is_set(db):
        offering = ClassOfferingFactory(card_focus_y=20, hero_crop_x=10, hero_crop_y=90)
        assert offering.card_object_position == "10% 90%"

    def it_prefers_the_card_focus_over_a_banner_crop_box(db):
        offering = ClassOfferingFactory(
            image=_real_png(),
            hero_crop_x=200,
            hero_crop_y=100,
            hero_crop_w=400,
            hero_crop_h=200,
            card_focus_x=5,
            card_focus_y=95,
        )
        assert offering.hero_object_position == "40.0% 40.0%"
        assert offering.card_object_position == "5% 95%"

    def it_treats_zero_as_a_real_focal_point(db):
        offering = ClassOfferingFactory(card_focus_x=0, card_focus_y=0)
        assert offering.card_object_position == "0% 0%"


def describe_turn_sale_off():
    def it_switches_the_sale_off_and_keeps_the_dormant_amounts(db):
        offering = ClassOfferingFactory(
            sale_enabled=True, sale_kind=ClassOffering.SaleKind.PERCENT, sale_percent=20, sale_banner_text="Go"
        )
        assert offering.sale_is_active is True
        offering.turn_sale_off()
        offering.refresh_from_db()
        assert offering.sale_enabled is False
        assert offering.sale_is_active is False
        assert offering.sale_percent == 20
        assert offering.sale_banner_text == "Go"

    def it_does_not_validate_stale_amounts(db):
        # A fixed amount at or above the price would fail the form; ending the sale never asks.
        offering = ClassOfferingFactory(
            price_cents=1000, sale_enabled=True, sale_kind=ClassOffering.SaleKind.FIXED, sale_amount_cents=5000
        )
        offering.turn_sale_off()
        offering.refresh_from_db()
        assert offering.sale_enabled is False
        assert offering.sale_amount_cents == 5000
