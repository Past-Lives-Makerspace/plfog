"""Shared fixtures for classes specs."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model


@pytest.fixture(autouse=True)
def _instructor_discount_codes_on(db):
    """Every existing spec under classes/spec/ was written when instructors could always
    manage their own discount codes. Keep that the default test posture now that it's a
    flag (default OFF in production) — the OFF/gated behavior gets its own explicit specs
    in instructor_discount_codes_flag_spec.py."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = True
    config.save(update_fields=["instructor_discount_codes_enabled"])


@pytest.fixture
def admin_user(db):
    from membership.models import Member, MembershipPlan

    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="admin@example.com", defaults={"email": "admin@example.com"})
    # The ensure_user_has_member signal auto-creates a Member with fog_role=MEMBER,
    # so get_or_create always finds the existing record. Flip to ADMIN unconditionally.
    member, _ = Member.objects.get_or_create(
        user=user,
        defaults={"full_legal_name": "Admin User", "fog_role": Member.FogRole.ADMIN, "membership_plan": plan},
    )
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


@pytest.fixture
def member_user(db):
    from django.utils import timezone

    from membership.models import Member, MembershipPlan

    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="member@example.com", defaults={"email": "member@example.com"})
    member, _ = Member.objects.get_or_create(
        user=user,
        defaults={"full_legal_name": "Plain Member", "fog_role": Member.FogRole.MEMBER, "membership_plan": plan},
    )
    # Teach-portal specs need the instructor-orientation unlock (Spec D) — set it
    # unconditionally (the signal-created Member ignores the defaults above).
    # Locked-member specs build their own member instead of using this fixture.
    member.instructor_oriented_at = timezone.now()
    member.save(update_fields=["instructor_oriented_at"])
    return user


@pytest.fixture
def free_offering(db):
    from datetime import timedelta

    from django.utils import timezone

    from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
    from classes.models import ClassOffering

    offering = ClassOfferingFactory(
        title="Free Demo",
        slug="free-demo",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=0,
        member_discount_pct=0,
        capacity=4,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=3),
        ends_at=timezone.now() + timedelta(days=3, hours=2),
    )
    return offering
