"""BDD specs for DiscountCode."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db.utils import IntegrityError

from classes.factories import ClassOfferingFactory, DiscountCodeFactory, UserFactory
from classes.models import DiscountCode


def _active_member_user(username: str, *, is_admin: bool = False, can_self_approve: bool = False):
    """A User linked to an ACTIVE Member with the given role/permission."""
    from membership.models import Member, MembershipPlan

    MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    user = get_user_model().objects.create_user(username=username, email=f"{username}@example.com", password="x")
    Member.objects.update_or_create(
        user=user,
        defaults={
            "full_legal_name": username.title(),
            "fog_role": Member.FogRole.ADMIN if is_admin else Member.FogRole.MEMBER,
            "status": Member.Status.ACTIVE,
            "can_self_approve_discounts": can_self_approve,
        },
    )
    return user


def describe_DiscountCode():
    def it_stringifies_as_code(db):
        code = DiscountCodeFactory(code="HOLIDAY")
        assert str(code) == "HOLIDAY"

    def it_defaults_to_unapproved(db):
        """A freshly created code starts pending — approval is a deliberate act."""
        code = DiscountCode.objects.create(code="BRANDNEW", discount_pct=10)
        assert code.is_approved is False

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

    def describe_approve():
        def it_marks_the_code_approved(db):
            code = DiscountCodeFactory(is_approved=False)
            code.approve()
            code.refresh_from_db()
            assert code.is_approved is True

        def it_is_idempotent_when_already_approved(db):
            code = DiscountCodeFactory(is_approved=True)
            code.approve()
            code.refresh_from_db()
            assert code.is_approved is True

        def it_accepts_an_acting_user(db):
            """The approver is passed for a future audit trail; it must not error today."""
            approver = _active_member_user("dc-approver-model", can_self_approve=True)
            code = DiscountCodeFactory(is_approved=False, created_by=approver)
            code.approve(approver)
            code.refresh_from_db()
            assert code.is_approved is True

    def describe_unapprove():
        def it_returns_the_code_to_pending(db):
            code = DiscountCodeFactory(is_approved=True)
            code.unapprove()
            code.refresh_from_db()
            assert code.is_approved is False

        def it_is_idempotent_when_already_pending(db):
            code = DiscountCodeFactory(is_approved=False)
            code.unapprove()
            code.refresh_from_db()
            assert code.is_approved is False

    def describe_can_be_approved_by():
        def it_denies_anonymous_or_missing_users(db):
            code = DiscountCodeFactory()
            assert code.can_be_approved_by(AnonymousUser()) is False
            assert code.can_be_approved_by(None) is False

        def it_allows_an_admin_member_to_approve_any_code(db):
            admin = _active_member_user("dc-admin", is_admin=True)
            owner = UserFactory(username="dc-owner@example.com")
            code = DiscountCodeFactory(created_by=owner)
            assert code.can_be_approved_by(admin) is True

        def it_allows_a_superuser_without_a_member(db):
            from membership.models import Member

            user = get_user_model().objects.create_superuser(
                username="dc-su@example.com", email="dc-su@example.com", password="x"
            )
            Member.objects.filter(user=user).delete()
            code = DiscountCodeFactory()
            assert code.can_be_approved_by(user) is True

        def it_allows_the_owner_when_they_hold_the_permission(db):
            owner = _active_member_user("dc-perm-owner", can_self_approve=True)
            code = DiscountCodeFactory(created_by=owner)
            assert code.can_be_approved_by(owner) is True

        def it_denies_the_owner_without_the_permission(db):
            owner = _active_member_user("dc-noperm-owner", can_self_approve=False)
            code = DiscountCodeFactory(created_by=owner)
            assert code.can_be_approved_by(owner) is False

        def it_denies_a_permitted_member_on_someone_elses_code(db):
            approver = _active_member_user("dc-permitted", can_self_approve=True)
            other = UserFactory(username="dc-other@example.com")
            code = DiscountCodeFactory(created_by=other)
            assert code.can_be_approved_by(approver) is False

        def it_denies_a_user_with_no_member(db):
            from membership.models import Member

            user = UserFactory(username="dc-nomember@example.com")
            Member.objects.filter(user=user).delete()
            code = DiscountCodeFactory(created_by=user)
            assert code.can_be_approved_by(user) is False
