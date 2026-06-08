"""BDD specs for DiscountCode."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db.utils import IntegrityError

from classes.factories import ClassOfferingFactory, DiscountCodeFactory
from classes.models import DiscountCode


def describe_DiscountCode():
    def it_stringifies_as_code(db):
        code = DiscountCodeFactory(code="HOLIDAY")
        assert str(code) == "HOLIDAY"

    def describe_apply_to():
        def it_applies_percent_discount(db):
            code = DiscountCodeFactory(discount_pct=25, discount_fixed_cents=None)
            assert code.apply_to(10_000) == 7_500

        def it_applies_fixed_cents_discount(db):
            code = DiscountCodeFactory(discount_pct=None, discount_fixed_cents=2_000)
            assert code.apply_to(10_000) == 8_000

        def it_clamps_fixed_to_zero_minimum(db):
            code = DiscountCodeFactory(discount_pct=None, discount_fixed_cents=20_000)
            assert code.apply_to(10_000) == 0

        def it_returns_price_unchanged_when_no_discount_set():
            code = DiscountCode(code="NOOP", discount_pct=None, discount_fixed_cents=None)
            assert code.apply_to(10_000) == 10_000

    def describe_is_currently_valid():
        def it_is_valid_when_no_window_and_active_and_under_limit(db):
            code = DiscountCodeFactory(is_active=True, valid_from=None, valid_until=None, max_uses=None)
            assert code.is_currently_valid() is True

        def it_is_invalid_when_inactive(db):
            code = DiscountCodeFactory(is_active=False)
            assert code.is_currently_valid() is False

        def it_is_invalid_before_valid_from(db):
            code = DiscountCodeFactory(valid_from=date.today() + timedelta(days=1))
            assert code.is_currently_valid() is False

        def it_is_invalid_after_valid_until(db):
            code = DiscountCodeFactory(valid_until=date.today() - timedelta(days=1))
            assert code.is_currently_valid() is False

        def it_is_invalid_when_unapproved(db):
            code = DiscountCodeFactory(is_active=True, is_approved=False)
            assert code.is_currently_valid() is False

        def it_is_valid_when_approved(db):
            code = DiscountCodeFactory(is_active=True, is_approved=True)
            assert code.is_currently_valid() is True

        def it_is_invalid_when_at_max_uses(db):
            code = DiscountCodeFactory(max_uses=1, use_count=1)
            assert code.is_currently_valid() is False

    def it_rejects_code_with_no_value(db):
        with pytest.raises(IntegrityError):
            DiscountCode.objects.create(code="EMPTY", discount_pct=None, discount_fixed_cents=None)

    def describe_best_auto_apply_for():
        def it_returns_none_when_no_auto_apply_codes(db):
            offering = ClassOfferingFactory()
            assert DiscountCode.objects.best_auto_apply_for(offering, 10_000) is None

        def it_picks_the_code_that_drops_price_furthest(db):
            offering = ClassOfferingFactory()
            DiscountCodeFactory(class_offering=offering, auto_apply=True, discount_pct=10, discount_fixed_cents=None)
            deepest = DiscountCodeFactory(
                class_offering=offering, auto_apply=True, discount_pct=40, discount_fixed_cents=None
            )
            assert DiscountCode.objects.best_auto_apply_for(offering, 10_000) == deepest

        def it_ignores_codes_scoped_to_other_offerings(db):
            offering = ClassOfferingFactory()
            other = ClassOfferingFactory()
            DiscountCodeFactory(class_offering=other, auto_apply=True)
            assert DiscountCode.objects.best_auto_apply_for(offering, 10_000) is None

        def it_skips_currently_invalid_codes(db):
            offering = ClassOfferingFactory()
            DiscountCodeFactory(class_offering=offering, auto_apply=True, is_active=False)
            assert DiscountCode.objects.best_auto_apply_for(offering, 10_000) is None

        def it_ignores_non_auto_apply_codes(db):
            offering = ClassOfferingFactory()
            DiscountCodeFactory(class_offering=offering, auto_apply=False)
            assert DiscountCode.objects.best_auto_apply_for(offering, 10_000) is None
