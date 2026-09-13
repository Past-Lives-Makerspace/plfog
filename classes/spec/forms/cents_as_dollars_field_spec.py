"""BDD specs for CentsAsDollarsField."""

from __future__ import annotations

from decimal import Decimal

from classes.forms import CentsAsDollarsField


def describe_CentsAsDollarsField():
    def describe_prepare_value():
        def it_converts_cents_to_dollars():
            field = CentsAsDollarsField()
            assert field.prepare_value(8000) == Decimal("80.00")

        def it_handles_zero():
            field = CentsAsDollarsField()
            assert field.prepare_value(0) == Decimal("0.00")

        def it_handles_none():
            field = CentsAsDollarsField()
            assert field.prepare_value(None) is None

        def it_handles_empty_string():
            field = CentsAsDollarsField()
            assert field.prepare_value("") == ""

        def it_handles_fractional_cents():
            field = CentsAsDollarsField()
            assert field.prepare_value(1250) == Decimal("12.50")

        def it_leaves_a_whole_dollar_string_alone():
            # A bound field hands the raw POST string back through here on a failed save, and that
            # string is already dollars: "80" used to re-render as 0.8.
            field = CentsAsDollarsField()
            assert field.prepare_value("80") == "80"

        def it_leaves_a_single_dollar_string_alone():
            # "1" used to come back as 0.01 and then trip the $1.00 floor on the next save.
            field = CentsAsDollarsField()
            assert field.prepare_value("1") == "1"

        def it_leaves_a_decimal_string_alone():
            field = CentsAsDollarsField()
            assert field.prepare_value("80.00") == "80.00"

        def it_leaves_a_typed_zero_string_alone():
            field = CentsAsDollarsField()
            assert field.prepare_value("0") == "0"

    def describe_clean():
        def it_converts_dollars_to_cents():
            field = CentsAsDollarsField(required=True)
            assert field.clean("80.00") == 8000

        def it_handles_whole_dollars():
            field = CentsAsDollarsField(required=True)
            assert field.clean("25") == 2500

        def it_handles_fractional_dollars():
            field = CentsAsDollarsField(required=True)
            assert field.clean("12.50") == 1250

        def it_returns_zero_for_zero():
            field = CentsAsDollarsField(required=False)
            assert field.clean("0") == 0

        def it_returns_none_for_empty_when_not_required():
            field = CentsAsDollarsField(required=False)
            assert field.clean("") is None
