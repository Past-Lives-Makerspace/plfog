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
