"""Tests for membership models."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from membership.models import DEFAULT_PRICE_PER_SQFT, Lease, Member, Space
from tests.membership.factories import (
    LeaseFactory,
    MemberFactory,
    MembershipPlanFactory,
    SpaceFactory,
)

# ---------------------------------------------------------------------------
# MembershipPlan
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_membership_plan():
    def it_has_str_representation():
        plan = MembershipPlanFactory(name="Premium Studio")
        assert str(plan) == "Premium Studio"

    def it_stores_monthly_price():
        plan = MembershipPlanFactory(monthly_price=Decimal("275.50"))
        plan.refresh_from_db()
        assert plan.monthly_price == Decimal("275.50")

    def it_allows_nullable_deposit():
        plan_no_deposit = MembershipPlanFactory(name="No Deposit Plan")
        assert plan_no_deposit.deposit_required is None

        plan_with_deposit = MembershipPlanFactory(
            name="With Deposit Plan",
            deposit_required=Decimal("500.00"),
        )
        plan_with_deposit.refresh_from_db()
        assert plan_with_deposit.deposit_required == Decimal("500.00")


# ---------------------------------------------------------------------------
# Member
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_member():
    def it_has_str_representation():
        member = MemberFactory(preferred_name="JD")
        assert str(member) == "JD"

    def describe_display_name():
        def it_returns_preferred_name_when_set():
            member = MemberFactory(
                full_legal_name="Jane Doe",
                preferred_name="JD",
            )
            assert member.display_name == "JD"

        def it_returns_full_legal_name_when_no_preferred_name():
            member = MemberFactory(
                full_legal_name="Jane Doe",
                preferred_name="",
            )
            assert member.display_name == "Jane Doe"

    def it_defaults_to_active_status():
        member = MemberFactory()
        assert member.status == Member.Status.ACTIVE

    def it_defaults_to_standard_role():
        member = MemberFactory()
        assert member.role == Member.Role.STANDARD

    def it_allows_null_user():
        member = MemberFactory(user=None)
        assert member.user is None

    def it_stores_committed_until():
        member = MemberFactory(committed_until=date(2025, 6, 30))
        member.refresh_from_db()
        assert member.committed_until == date(2025, 6, 30)

    def it_allows_null_committed_until():
        member = MemberFactory()
        assert member.committed_until is None


@pytest.mark.django_db
def describe_member_computed_properties():
    def it_calculates_membership_monthly_dues():
        plan = MembershipPlanFactory(monthly_price=Decimal("250.00"))
        member = MemberFactory(membership_plan=plan)
        assert member.membership_monthly_dues == Decimal("250.00")

    def it_calculates_studio_storage_total_with_active_leases():
        plan = MembershipPlanFactory(monthly_price=Decimal("150.00"))
        member = MemberFactory(membership_plan=plan)
        today = timezone.now().date()

        space_a = SpaceFactory(space_id="S-A")
        space_b = SpaceFactory(space_id="S-B")

        LeaseFactory(
            member=member,
            space=space_a,
            monthly_rent=Decimal("300.00"),
            start_date=today - timedelta(days=10),
        )
        LeaseFactory(
            member=member,
            space=space_b,
            monthly_rent=Decimal("150.00"),
            start_date=today - timedelta(days=5),
        )

        assert member.studio_storage_total == Decimal("450.00")

    def it_returns_zero_studio_storage_with_no_leases():
        member = MemberFactory()
        assert member.studio_storage_total == Decimal("0.00")

    def it_calculates_total_monthly_spend():
        plan = MembershipPlanFactory(monthly_price=Decimal("200.00"))
        member = MemberFactory(membership_plan=plan)
        today = timezone.now().date()

        space = SpaceFactory(space_id="S-TMS")
        LeaseFactory(
            member=member,
            space=space,
            monthly_rent=Decimal("100.00"),
            start_date=today - timedelta(days=10),
        )

        assert member.total_monthly_spend == Decimal("300.00")


@pytest.mark.django_db
def describe_member_leases_and_spaces():
    def describe_active_leases():
        def it_returns_active_leases():
            member = MemberFactory()
            today = timezone.now().date()

            space_active = SpaceFactory(space_id="S-ACT")
            space_ended = SpaceFactory(space_id="S-END")
            space_future = SpaceFactory(space_id="S-FUT")

            active_lease = LeaseFactory(
                member=member,
                space=space_active,
                start_date=today - timedelta(days=30),
            )
            LeaseFactory(
                member=member,
                space=space_ended,
                start_date=today - timedelta(days=60),
                end_date=today - timedelta(days=1),
            )
            LeaseFactory(
                member=member,
                space=space_future,
                start_date=today + timedelta(days=30),
            )

            active = list(member.active_leases)
            assert len(active) == 1
            assert active[0].pk == active_lease.pk

        def it_includes_ongoing_lease_with_no_end_date():
            member = MemberFactory()
            today = timezone.now().date()
            space = SpaceFactory(space_id="S-ONG")
            ongoing = LeaseFactory(
                member=member,
                space=space,
                start_date=today - timedelta(days=10),
                end_date=None,
            )
            active = list(member.active_leases)
            assert len(active) == 1
            assert active[0].pk == ongoing.pk

    def describe_current_spaces():
        def it_returns_current_spaces():
            member = MemberFactory()
            today = timezone.now().date()

            space = SpaceFactory(space_id="S-CUR")
            LeaseFactory(
                member=member,
                space=space,
                start_date=today - timedelta(days=10),
            )

            spaces = list(member.current_spaces)
            assert len(spaces) == 1
            assert spaces[0].pk == space.pk


# ---------------------------------------------------------------------------
# Space
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_space():
    def it_has_str_representation():
        space = SpaceFactory(space_id="A-101", name="Corner Studio")
        assert str(space) == "A-101 - Corner Studio"

    def it_has_str_representation_without_name():
        space = SpaceFactory(space_id="A-102", name="")
        assert str(space) == "A-102"

    def describe_full_price():
        def it_uses_manual_price_when_set():
            space = SpaceFactory(
                space_id="S-MP",
                manual_price=Decimal("500.00"),
                size_sqft=Decimal("100.00"),
            )
            assert space.full_price == Decimal("500.00")

        def it_calculates_from_sqft_when_no_manual_price():
            space = SpaceFactory(
                space_id="S-SQ",
                manual_price=None,
                size_sqft=Decimal("100.00"),
            )
            expected = Decimal("100.00") * DEFAULT_PRICE_PER_SQFT
            assert space.full_price == expected

        def it_returns_none_when_no_size_or_manual_price():
            space = SpaceFactory(
                space_id="S-NO",
                manual_price=None,
                size_sqft=None,
            )
            assert space.full_price is None


@pytest.mark.django_db
def describe_space_vacancy_value():
    def it_returns_full_price_when_available():
        space = SpaceFactory(
            space_id="S-VA",
            status=Space.Status.AVAILABLE,
            manual_price=Decimal("400.00"),
        )
        assert space.vacancy_value == Decimal("400.00")

    def it_returns_zero_when_occupied():
        space = SpaceFactory(
            space_id="S-OC",
            status=Space.Status.OCCUPIED,
            manual_price=Decimal("400.00"),
        )
        assert space.vacancy_value == Decimal("0.00")

    def it_returns_zero_when_available_but_no_price():
        space = SpaceFactory(
            space_id="S-NP",
            status=Space.Status.AVAILABLE,
            manual_price=None,
            size_sqft=None,
        )
        assert space.vacancy_value == Decimal("0.00")


@pytest.mark.django_db
def describe_space_occupants_and_revenue():
    def describe_current_occupants():
        def it_returns_current_occupants():
            plan = MembershipPlanFactory(name="Occ Plan")
            member = MemberFactory(membership_plan=plan)
            today = timezone.now().date()
            space = SpaceFactory(space_id="S-OCC")

            LeaseFactory(
                member=member,
                space=space,
                start_date=today - timedelta(days=10),
            )

            occupants = list(space.current_occupants)
            assert len(occupants) == 1
            assert occupants[0].pk == member.pk

        def it_excludes_ended_leases():
            plan = MembershipPlanFactory(name="Occ Plan 2")
            member = MemberFactory(membership_plan=plan)
            today = timezone.now().date()
            space = SpaceFactory(space_id="S-OC2")

            LeaseFactory(
                member=member,
                space=space,
                start_date=today - timedelta(days=60),
                end_date=today - timedelta(days=1),
            )

            occupants = list(space.current_occupants)
            assert len(occupants) == 0

    def describe_revenue():
        def it_calculates_actual_revenue_from_active_leases():
            plan = MembershipPlanFactory(name="Rev Plan")
            member_a = MemberFactory(membership_plan=plan, full_legal_name="Alice", email="alice@example.com")
            member_b = MemberFactory(membership_plan=plan, full_legal_name="Bob", email="bob@example.com")
            today = timezone.now().date()
            space = SpaceFactory(space_id="S-REV", manual_price=Decimal("600.00"))

            LeaseFactory(
                member=member_a,
                space=space,
                monthly_rent=Decimal("300.00"),
                start_date=today - timedelta(days=10),
            )
            LeaseFactory(
                member=member_b,
                space=space,
                monthly_rent=Decimal("200.00"),
                start_date=today - timedelta(days=5),
            )

            assert space.actual_revenue == Decimal("500.00")

        def it_returns_zero_revenue_with_no_active_leases():
            space = SpaceFactory(space_id="S-NR")
            assert space.actual_revenue == Decimal("0.00")

        def it_calculates_revenue_loss():
            space = SpaceFactory(
                space_id="S-RL",
                manual_price=Decimal("600.00"),
            )
            plan = MembershipPlanFactory(name="RL Plan")
            member = MemberFactory(membership_plan=plan, email="rl@example.com")
            today = timezone.now().date()

            LeaseFactory(
                member=member,
                space=space,
                monthly_rent=Decimal("400.00"),
                start_date=today - timedelta(days=5),
            )

            assert space.revenue_loss == Decimal("200.00")

        def it_returns_none_revenue_loss_when_no_full_price():
            space = SpaceFactory(
                space_id="S-NRL",
                manual_price=None,
                size_sqft=None,
            )
            assert space.revenue_loss is None


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_lease():
    def it_has_str_representation():
        plan = MembershipPlanFactory(name="Lease Plan")
        member = MemberFactory(
            membership_plan=plan,
            full_legal_name="Test User",
            preferred_name="TU",
            email="tu@example.com",
        )
        space = SpaceFactory(space_id="L-100", name="Main")
        lease = LeaseFactory(
            member=member,
            space=space,
            start_date=date(2024, 3, 1),
        )
        assert str(lease) == "TU @ L-100 - Main (2024-03-01)"

    def it_stores_committed_until():
        lease = LeaseFactory(committed_until=date(2025, 12, 31))
        lease.refresh_from_db()
        assert lease.committed_until == date(2025, 12, 31)

    def it_allows_null_committed_until():
        lease = LeaseFactory()
        assert lease.committed_until is None


@pytest.mark.django_db
def describe_lease_is_active():
    def it_is_active_when_ongoing():
        """Ongoing lease: started in the past, no end_date."""
        today = timezone.now().date()
        lease = LeaseFactory(
            start_date=today - timedelta(days=30),
            end_date=None,
        )
        assert lease.is_active is True

    def it_is_active_when_within_date_range():
        today = timezone.now().date()
        lease = LeaseFactory(
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=30),
        )
        assert lease.is_active is True

    def it_is_active_when_end_date_is_today():
        today = timezone.now().date()
        lease = LeaseFactory(
            start_date=today - timedelta(days=30),
            end_date=today,
        )
        assert lease.is_active is True

    def it_is_active_when_start_date_is_today():
        today = timezone.now().date()
        lease = LeaseFactory(
            start_date=today,
            end_date=None,
        )
        assert lease.is_active is True

    def it_is_not_active_when_ended():
        today = timezone.now().date()
        lease = LeaseFactory(
            start_date=today - timedelta(days=60),
            end_date=today - timedelta(days=1),
        )
        assert lease.is_active is False

    def it_is_not_active_when_not_started():
        today = timezone.now().date()
        lease = LeaseFactory(
            start_date=today + timedelta(days=1),
        )
        assert lease.is_active is False


def describe_lease_is_active_start_date_boundary():
    """Boundary-value tests for the start_date > today comparison.

    These use in-memory Lease instances (no ORM create) to ensure
    the mutated class's is_active property is exercised directly.
    This kills mutants that change ``>`` to ``>=`` or ``<=``.
    """

    def it_is_active_when_start_date_equals_today():
        """start_date == today means the lease has started; is_active is True.

        Kills ``> → >=``: with ``>=``, ``today >= today`` is True so the
        guard fires and is_active incorrectly returns False.
        """
        today = timezone.now().date()
        lease = Lease(start_date=today, end_date=None)
        assert lease.is_active is True

    def it_is_not_active_when_start_date_is_tomorrow():
        """start_date == tomorrow means the lease hasn't started; is_active is False.

        Kills ``> → <=``: with ``<=``, ``tomorrow <= today`` is False so the
        guard does NOT fire and is_active incorrectly returns True.
        """
        today = timezone.now().date()
        lease = Lease(start_date=today + timedelta(days=1), end_date=None)
        assert lease.is_active is False


def describe_lease_is_active_end_date_boundary():
    """Boundary-value tests for the end_date < today comparison.

    These use in-memory Lease instances (no ORM create) to ensure
    the mutated class's is_active property is exercised directly.
    This kills mutants that change ``<`` to ``<=`` or ``>=``.
    """

    def it_is_active_when_end_date_equals_today():
        """end_date == today means the lease is still active on its last day.

        Kills ``< → <=``: with ``<=``, ``today <= today`` is True so the
        guard fires and is_active incorrectly returns False.
        """
        today = timezone.now().date()
        lease = Lease(start_date=today - timedelta(days=30), end_date=today)
        assert lease.is_active is True

    def it_is_not_active_when_end_date_is_yesterday():
        """end_date == yesterday means the lease has expired; is_active is False.

        Kills ``< → >=``: with ``>=``, ``yesterday >= today`` is False so the
        guard does NOT fire and is_active incorrectly returns True.
        """
        today = timezone.now().date()
        lease = Lease(
            start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=1),
        )
        assert lease.is_active is False
