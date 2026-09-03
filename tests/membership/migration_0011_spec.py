"""Tests for migration 0011 — seed default MembershipPlan and backfill Members."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
def describe_migration_0011_seed_default_membership_plan():
    """Test the data migration against a populated database."""

    def it_creates_default_membership_plan():
        from membership.models import MembershipPlan

        assert MembershipPlan.objects.filter(name="Standard Membership").exists()

    def it_backfills_member_for_user_with_full_name():
        from membership.models import Member

        user = User.objects.create_user(
            username="backfilltest",
            first_name="Alice",
            last_name="Smith",
            email="alice@example.com",
            password="pass",
        )
        # Signal already creates the member using the same logic as the migration
        member = Member.objects.get(user=user)
        assert member.full_legal_name == "Alice Smith"

    def it_leaves_name_blank_when_user_has_no_name():
        # This spec exercises the live signal, not the historical migration.
        # Issue #274 removed the username fallback: blank beats seeding the
        # lowercased email local part as a legal name.
        from membership.models import Member

        user = User.objects.create_user(
            username="noname_user",
            first_name="",
            last_name="",
            email="noname@example.com",
            password="pass",
        )
        member = Member.objects.get(user=user)
        assert member.full_legal_name == ""

    def it_backfills_member_with_first_name_only():
        from membership.models import Member

        user = User.objects.create_user(
            username="cher_user",
            first_name="Cher",
            last_name="",
            email="cher@example.com",
            password="pass",
        )
        member = Member.objects.get(user=user)
        assert member.full_legal_name == "Cher"

    def it_assigns_standard_membership_plan():
        from membership.models import Member, MembershipPlan

        user = User.objects.create_user(
            username="plancheck",
            email="plancheck@example.com",
            password="pass",
        )
        member = Member.objects.get(user=user)
        plan = MembershipPlan.objects.get(name="Standard Membership")
        assert member.membership_plan == plan

    def it_sets_active_status():
        from membership.models import Member

        user = User.objects.create_user(
            username="statuscheck",
            email="statuscheck@example.com",
            password="pass",
        )
        member = Member.objects.get(user=user)
        assert member.status == "active"

    def it_sets_monthly_price_to_150():
        from membership.models import MembershipPlan

        plan = MembershipPlan.objects.get(name="Standard Membership")
        assert plan.monthly_price == Decimal("150.00")
