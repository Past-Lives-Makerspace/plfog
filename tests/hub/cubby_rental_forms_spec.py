"""BDD specs for the pay what you want form (#978): hub/cubby_rental_forms.py.

The chips (Minimum, Suggested, Extra Help, then Other to rent or Current to change), never two at
the same price; the starting pick; and the floor rule on the field the member sees.
"""

from __future__ import annotations

import pytest

from hub.cubby_rental_forms import CubbyAmountForm, amount_chips
from tests.membership.factories import CubbyFactory

pytestmark = pytest.mark.django_db


def _cubby(floor: int = 1500, suggested: int = 2000):
    return CubbyFactory(label="B3", floor_cents=floor, suggested_cents=suggested)


def _chips(chips) -> list[tuple[str, str, str]]:
    return [(chip.key, chip.value_display, chip.sub) for chip in chips]


def describe_amount_chips():
    def it_offers_minimum_suggested_extra_help_and_other_to_rent():
        assert _chips(amount_chips(_cubby())) == [
            ("floor", "$15", "Minimum"),
            ("suggested", "$20", "Suggested"),
            ("extra", "$25", "Extra Help"),
            ("other", "Other", "You Choose"),
        ]

    def it_offers_current_instead_of_other_to_change_and_never_two_chips_at_one_price():
        assert _chips(amount_chips(_cubby(), current_cents=2200))[-1] == ("current", "$22", "Current")
        assert [chip.key for chip in amount_chips(_cubby(), current_cents=2000)] == ["floor", "suggested", "extra"]
        assert [chip.key for chip in amount_chips(_cubby(2000, 2000))] == ["floor", "extra", "other"]

    def it_caps_extra_help_at_five_hundred_dollars():
        assert [chip.key for chip in amount_chips(_cubby(50000, 50000))] == ["floor", "other"]
        assert [chip.cents for chip in amount_chips(_cubby(49800, 49800))] == [49800, 50000, None]


def describe_CubbyAmountForm():
    def it_opens_renting_on_suggested_and_changing_on_what_the_member_pays():
        assert CubbyAmountForm(cubby=_cubby()).picked == "suggested"
        assert CubbyAmountForm(cubby=_cubby(), current_cents=2500).picked == "extra"
        assert CubbyAmountForm(cubby=_cubby(), current_cents=2200).picked == "current"

    def it_turns_a_chip_or_an_other_amount_into_cents():
        form = CubbyAmountForm({"choice": "floor"}, cubby=_cubby())
        assert form.is_valid() and form.cleaned_data["amount_cents"] == 1500
        form = CubbyAmountForm({"choice": "other", "other": "31.50"}, cubby=_cubby())
        assert form.is_valid() and form.cleaned_data["amount_cents"] == 3150
        assert form.picked == "other"

    def it_puts_the_floor_on_the_other_box():
        form = CubbyAmountForm({"choice": "other", "other": "8"}, cubby=_cubby())
        assert not form.is_valid()
        assert form.errors["other"] == ["The lowest you can pay for B3 is $15 a month. Pick $15 or more."]
        assert form.error_text == "The lowest you can pay for B3 is $15 a month. Pick $15 or more."

    def it_asks_for_an_other_amount_and_says_why_one_is_unreadable():
        assert CubbyAmountForm({"choice": "other"}, cubby=_cubby()).errors == {"other": ["Enter your amount."]}
        assert CubbyAmountForm({"choice": "other", "other": "lots"}, cubby=_cubby()).errors == {
            "other": ["Enter dollars, like 15 or 15.50."]
        }
        assert CubbyAmountForm({}, cubby=_cubby()).errors == {"choice": ["Pick an amount."]}

    def it_keeps_the_current_amount_allowed_below_a_raised_floor():
        cubby = _cubby(2000, 2500)
        form = CubbyAmountForm({"choice": "current"}, cubby=cubby, current_cents=1500)
        assert form.is_valid() and form.cleaned_data["amount_cents"] == 1500

    def it_refuses_a_chip_price_the_floor_moved_past_on_the_chips():
        cubby = _cubby()
        form = CubbyAmountForm({"choice": "floor"}, cubby=cubby, current_cents=2200)
        cubby.floor_cents = 1800  # the floor rose after the chips were drawn
        assert not form.is_valid()
        assert "choice" in form.errors

    def it_wires_the_other_box_to_the_sheet():
        attrs = CubbyAmountForm(cubby=_cubby()).fields["other"].widget.attrs
        assert attrs["x-model"] == "other"
        assert attrs["class"] == "pl-cubby-other__input"
