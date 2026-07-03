"""Tests for membership models."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from membership.models import DEFAULT_PRICE_PER_SQFT, Lease, Member, Space
from tests.membership.factories import (
    GuildFactory,
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

    def it_defaults_to_standard_member_type():
        member = MemberFactory()
        assert member.member_type == Member.MemberType.STANDARD

    def it_defaults_to_member_fog_role():
        member = MemberFactory()
        assert member.fog_role == Member.FogRole.MEMBER

    def it_is_listed_in_the_directory_by_default():
        # New members are opted in to the directory; they can opt out in settings.
        member = MemberFactory()
        assert member.show_in_directory is True

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
def describe_member_initials():
    def it_returns_two_initials_from_full_name():
        user = User.objects.create_user(username="jd", first_name="Jane", last_name="Doe")
        assert user.member.initials == "JD"

    def it_returns_one_initial_from_first_name_only():
        user = User.objects.create_user(username="cher", first_name="Cher", last_name="")
        assert user.member.initials == "C"

    def it_falls_back_to_email_initial_when_no_name():
        user = User.objects.create_user(username="noname", email="zara@example.com")
        assert user.member.initials == "Z"

    def it_returns_empty_string_when_no_name_and_no_email():
        user = User.objects.create_user(username="ghost", email="")
        assert user.member.initials == ""

    def it_falls_back_to_email_when_name_is_whitespace_only():
        user = User.objects.create_user(username="ws", email="test@example.com")
        with patch.object(User, "get_full_name", return_value="  "):
            assert user.member.initials == "T"

    def it_returns_empty_string_when_user_is_none():
        member = MemberFactory(user=None)
        assert member.initials == ""


@pytest.mark.django_db
def describe_member_optional_fields():
    def it_allows_blank_email():
        member = MemberFactory(_pre_signup_email="")
        assert member._pre_signup_email == ""

    def it_allows_null_join_date():
        member = MemberFactory(join_date=None)
        assert member.join_date is None

    def it_stores_billing_name():
        member = MemberFactory(billing_name="Test Billing")
        assert member.billing_name == "Test Billing"


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
            tenant_obj=member,
            space=space_a,
            monthly_rent=Decimal("300.00"),
            start_date=today - timedelta(days=10),
        )
        LeaseFactory(
            tenant_obj=member,
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
            tenant_obj=member,
            space=space,
            monthly_rent=Decimal("100.00"),
            start_date=today - timedelta(days=10),
        )

        assert member.total_monthly_spend == Decimal("300.00")


@pytest.mark.django_db
def describe_member_has_started_profile():
    def it_is_false_for_a_brand_new_member():
        member = MemberFactory()
        assert member.has_started_profile is False

    def it_is_true_when_about_me_is_set():
        member = MemberFactory(about_me="Potter, welder, tea enthusiast.")
        assert member.has_started_profile is True

    def it_is_true_when_pronouns_are_set():
        member = MemberFactory(pronouns=Member.Pronouns.HE_HIM)
        assert member.has_started_profile is True

    def it_is_true_when_a_discord_handle_is_typed():
        member = MemberFactory(discord_handle="@maker")
        assert member.has_started_profile is True

    def it_is_true_when_a_discord_account_is_linked():
        member = MemberFactory(discord_user_id="123456789012345678")
        assert member.has_started_profile is True

    def it_is_true_when_a_profile_photo_is_set():
        member = MemberFactory(profile_photo="members/profile/avatar.png")
        assert member.has_started_profile is True


def _fully_completed_member() -> Member:
    """A member with every profile-completeness field filled in."""
    return MemberFactory(
        profile_photo="members/profile/avatar.png",
        about_me="Potter and welder.",
        pronouns=Member.Pronouns.SHE_HER,
        discord_user_id="123456789012345678",
        show_in_directory=True,
    )


@pytest.mark.django_db
def describe_member_profile_completeness():
    def it_reports_missing_fields_for_a_brand_new_member():
        # A fresh member has no photo/bio/pronouns/Discord, but show_in_directory
        # defaults True — so the directory listing is the one satisfied field.
        member = MemberFactory()
        result = member.profile_completeness
        assert result.missing == ["Profile photo", "Short bio", "Pronouns", "Discord link"]
        assert result.complete is False

    def it_is_complete_when_all_fields_are_set():
        member = _fully_completed_member()
        result = member.profile_completeness
        assert result.missing == []
        assert result.complete is True
        assert result.percent == 100

    def it_computes_percent_from_filled_fields():
        # Brand-new member: only the directory listing is filled → 1 of 5 → 20%.
        member = MemberFactory()
        assert member.profile_completeness.percent == 20

    def it_counts_a_typed_discord_handle_as_the_discord_field():
        member = MemberFactory(discord_handle="@maker")
        assert "Discord link" not in member.profile_completeness.missing

    def it_flags_directory_listing_when_the_member_opts_out():
        member = MemberFactory(show_in_directory=False)
        assert "Directory listing" in member.profile_completeness.missing


@pytest.mark.django_db
def describe_member_dismiss_welcome():
    def it_stamps_welcome_dismissed_at():
        member = MemberFactory()
        assert member.welcome_dismissed_at is None

        member.dismiss_welcome()

        member.refresh_from_db()
        assert member.welcome_dismissed_at is not None


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
                tenant_obj=member,
                space=space_active,
                start_date=today - timedelta(days=30),
            )
            LeaseFactory(
                tenant_obj=member,
                space=space_ended,
                start_date=today - timedelta(days=60),
                end_date=today - timedelta(days=1),
            )
            LeaseFactory(
                tenant_obj=member,
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
                tenant_obj=member,
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
                tenant_obj=member,
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

        def it_uses_default_rate_when_rate_per_sqft_is_none():
            space = SpaceFactory(size_sqft=Decimal("100"), rate_per_sqft=None)
            assert space.full_price == Decimal("375.00")  # 100 * 3.75

        def it_uses_custom_rate_per_sqft_when_set():
            space = SpaceFactory(size_sqft=Decimal("100"), rate_per_sqft=Decimal("4.00"))
            assert space.full_price == Decimal("400.00")  # 100 * 4.00

        def it_prefers_manual_price_over_rate_per_sqft():
            space = SpaceFactory(
                size_sqft=Decimal("100"),
                rate_per_sqft=Decimal("4.00"),
                manual_price=Decimal("500.00"),
            )
            assert space.full_price == Decimal("500.00")


@pytest.mark.django_db
def describe_space_is_rentable():
    def it_defaults_to_true():
        space = SpaceFactory()
        assert space.is_rentable is True

    def it_can_be_set_to_false():
        space = SpaceFactory(is_rentable=False)
        assert space.is_rentable is False


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
                tenant_obj=member,
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
                tenant_obj=member,
                space=space,
                start_date=today - timedelta(days=60),
                end_date=today - timedelta(days=1),
            )

            occupants = list(space.current_occupants)
            assert len(occupants) == 0

    def describe_revenue():
        def it_calculates_actual_revenue_from_active_leases():
            plan = MembershipPlanFactory(name="Rev Plan")
            member_a = MemberFactory(
                membership_plan=plan, full_legal_name="Alice", _pre_signup_email="alice@example.com"
            )
            member_b = MemberFactory(membership_plan=plan, full_legal_name="Bob", _pre_signup_email="bob@example.com")
            today = timezone.now().date()
            space = SpaceFactory(space_id="S-REV", manual_price=Decimal("600.00"))

            LeaseFactory(
                tenant_obj=member_a,
                space=space,
                monthly_rent=Decimal("300.00"),
                start_date=today - timedelta(days=10),
            )
            LeaseFactory(
                tenant_obj=member_b,
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
            member = MemberFactory(membership_plan=plan, _pre_signup_email="rl@example.com")
            today = timezone.now().date()

            LeaseFactory(
                tenant_obj=member,
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
            _pre_signup_email="tu@example.com",
        )
        space = SpaceFactory(space_id="L-100", name="Main")
        lease = LeaseFactory(
            tenant_obj=member,
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

    def describe_new_fields():
        def it_stores_discount_reason():
            lease = LeaseFactory(discount_reason="Annual discount 10%")
            assert lease.discount_reason == "Annual discount 10%"

        def it_stores_is_split():
            lease = LeaseFactory(is_split=True)
            assert lease.is_split is True

        def it_defaults_is_split_to_false():
            lease = LeaseFactory()
            assert lease.is_split is False

        def it_stores_prepaid_through():
            lease = LeaseFactory(prepaid_through=date(2025, 12, 31))
            assert lease.prepaid_through == date(2025, 12, 31)

        def it_allows_null_prepaid_through():
            lease = LeaseFactory()
            assert lease.prepaid_through is None


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


# ---------------------------------------------------------------------------
# Space.sublet_guild
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_space_sublet():
    def it_defaults_to_null_sublet_guild():
        space = SpaceFactory()
        assert space.sublet_guild is None

    def it_assigns_and_persists_sublet_guild():
        guild = GuildFactory(name="Ceramics Guild")
        space = SpaceFactory(sublet_guild=guild)
        space.refresh_from_db()
        assert space.sublet_guild == guild

    def it_sets_null_on_guild_deletion():
        guild = GuildFactory(name="Temp Guild")
        space = SpaceFactory(sublet_guild=guild)
        guild.delete()
        space.refresh_from_db()
        assert space.sublet_guild is None


# ---------------------------------------------------------------------------
# Guild.sublet_revenue
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_guild_sublet_revenue():
    def it_returns_zero_when_guild_has_no_sublets():
        guild = GuildFactory(name="No Sublets Guild")
        assert guild.sublet_revenue == Decimal("0.00")

    def it_returns_zero_when_guild_has_sublets_but_no_leases():
        guild = GuildFactory(name="Empty Sublet Guild")
        SpaceFactory(sublet_guild=guild)
        assert guild.sublet_revenue == Decimal("0.00")

    def it_calculates_revenue_from_single_active_lease():
        guild = GuildFactory(name="Single Lease Guild")
        space = SpaceFactory(sublet_guild=guild)
        today = timezone.now().date()
        LeaseFactory(
            space=space,
            monthly_rent=Decimal("350.00"),
            start_date=today - timedelta(days=10),
        )
        assert guild.sublet_revenue == Decimal("350.00")

    def it_sums_revenue_from_multiple_active_leases_on_multiple_sublets():
        guild = GuildFactory(name="Multi Lease Guild")
        space_a = SpaceFactory(sublet_guild=guild)
        space_b = SpaceFactory(sublet_guild=guild)
        today = timezone.now().date()
        LeaseFactory(
            space=space_a,
            monthly_rent=Decimal("200.00"),
            start_date=today - timedelta(days=10),
        )
        LeaseFactory(
            space=space_b,
            monthly_rent=Decimal("300.00"),
            start_date=today - timedelta(days=5),
        )
        assert guild.sublet_revenue == Decimal("500.00")

    def it_excludes_expired_leases():
        guild = GuildFactory(name="Expired Lease Guild")
        space = SpaceFactory(sublet_guild=guild)
        today = timezone.now().date()
        LeaseFactory(
            space=space,
            monthly_rent=Decimal("400.00"),
            start_date=today - timedelta(days=60),
            end_date=today - timedelta(days=1),
        )
        assert guild.sublet_revenue == Decimal("0.00")

    def it_excludes_leases_on_non_sublet_spaces():
        guild = GuildFactory(name="Non-Sublet Guild")
        sublet_space = SpaceFactory(sublet_guild=guild)
        non_sublet_space = SpaceFactory()  # no sublet_guild
        today = timezone.now().date()
        LeaseFactory(
            space=sublet_space,
            monthly_rent=Decimal("250.00"),
            start_date=today - timedelta(days=10),
        )
        LeaseFactory(
            space=non_sublet_space,
            monthly_rent=Decimal("999.00"),
            start_date=today - timedelta(days=10),
        )
        assert guild.sublet_revenue == Decimal("250.00")


# ---------------------------------------------------------------------------
# Member paying status
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_member_is_paying():
    def it_is_true_for_standard():
        member = MemberFactory(member_type=Member.MemberType.STANDARD)
        assert member.is_paying is True

    def it_is_false_for_work_trade():
        member = MemberFactory(member_type=Member.MemberType.WORK_TRADE)
        assert member.is_paying is False

    def it_is_false_for_employee():
        member = MemberFactory(member_type=Member.MemberType.EMPLOYEE)
        assert member.is_paying is False

    def it_is_false_for_volunteer():
        member = MemberFactory(member_type=Member.MemberType.VOLUNTEER)
        assert member.is_paying is False

    def it_is_false_for_guild_lead():
        member = MemberFactory(member_type=Member.MemberType.GUILD_LEAD)
        assert member.is_paying is False


@pytest.mark.django_db
def describe_member_queryset_paying():
    def it_returns_only_standard_members():
        standard = MemberFactory(member_type=Member.MemberType.STANDARD)
        MemberFactory(member_type=Member.MemberType.WORK_TRADE)
        MemberFactory(member_type=Member.MemberType.EMPLOYEE)

        paying = Member.objects.paying()
        assert list(paying) == [standard]


# ---------------------------------------------------------------------------
# Member FOG roles
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_member_fog_roles():
    def describe_is_fog_admin():
        def it_is_true_for_admin_fog_role():
            member = MemberFactory(fog_role=Member.FogRole.ADMIN)
            assert member.is_fog_admin is True

        def it_is_false_for_guild_officer():
            member = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
            assert member.is_fog_admin is False

        def it_is_false_for_member():
            member = MemberFactory(fog_role=Member.FogRole.MEMBER)
            assert member.is_fog_admin is False

    def describe_is_guild_officer():
        def it_is_true_for_guild_officer():
            member = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
            assert member.is_guild_officer is True

        def it_is_false_for_admin():
            member = MemberFactory(fog_role=Member.FogRole.ADMIN)
            assert member.is_guild_officer is False

        def it_is_false_for_member():
            member = MemberFactory(fog_role=Member.FogRole.MEMBER)
            assert member.is_guild_officer is False

    def describe_can_edit_guild():
        def it_is_true_for_admin_on_any_guild():
            member = MemberFactory(fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory()
            assert member.can_edit_guild(guild) is True

        def it_is_true_for_guild_officer_on_any_guild():
            member = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
            guild = GuildFactory()
            assert member.can_edit_guild(guild) is True

        def it_is_true_for_the_guilds_own_lead():
            lead = MemberFactory(fog_role=Member.FogRole.MEMBER)
            guild = GuildFactory(guild_lead=lead)
            assert lead.can_edit_guild(guild) is True

        def it_is_false_for_regular_members_other_guilds():
            member = MemberFactory(fog_role=Member.FogRole.MEMBER)
            other_lead = MemberFactory(fog_role=Member.FogRole.MEMBER)
            guild = GuildFactory(guild_lead=other_lead)
            assert member.can_edit_guild(guild) is False

    def describe_sync_user_permissions():
        def it_grants_full_admin_for_admin_fog_role():
            user = User.objects.create_user(username="sync_adm", email="sync_adm@example.com", password="pass")
            member = user.member
            member.fog_role = Member.FogRole.ADMIN
            member.save(update_fields=["fog_role"])

            member.sync_user_permissions()
            user.refresh_from_db()

            assert user.is_staff is True
            assert user.is_superuser is True

        def it_grants_staff_only_for_guild_officer():
            user = User.objects.create_user(username="sync_go", email="sync_go@example.com", password="pass")
            member = user.member
            member.fog_role = Member.FogRole.GUILD_OFFICER
            member.save(update_fields=["fog_role"])

            member.sync_user_permissions()
            user.refresh_from_db()

            assert user.is_staff is True
            assert user.is_superuser is False

        def it_removes_privileges_for_member():
            user = User.objects.create_user(username="sync_mem", email="sync_mem@example.com", password="pass")
            user.is_staff = True
            user.is_superuser = True
            user.save()

            member = user.member
            # fog_role defaults to MEMBER, already correct

            member.sync_user_permissions()
            user.refresh_from_db()

            assert user.is_staff is False
            assert user.is_superuser is False

        def it_skips_save_when_already_correct():
            user = User.objects.create_user(username="sync_noop", email="sync_noop@example.com", password="pass")
            # fog_role=member, is_staff=False, is_superuser=False — already correct
            member = user.member

            with patch.object(User, "save") as mock_save:
                member.sync_user_permissions()
                mock_save.assert_not_called()

        def it_does_nothing_when_user_is_none():
            member = MemberFactory(user=None, fog_role=Member.FogRole.ADMIN)
            member.sync_user_permissions()  # Should not raise

    def describe_set_fog_role():
        def it_allows_admin_to_set_any_role():
            admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
            target = MemberFactory(fog_role=Member.FogRole.MEMBER)

            target.set_fog_role(Member.FogRole.ADMIN, changed_by=admin)

            target.refresh_from_db()
            assert target.fog_role == Member.FogRole.ADMIN

        def it_allows_guild_officer_to_set_member():
            officer = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
            target = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)

            target.set_fog_role(Member.FogRole.MEMBER, changed_by=officer)

            target.refresh_from_db()
            assert target.fog_role == Member.FogRole.MEMBER

        def it_allows_guild_officer_to_set_guild_officer():
            officer = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
            target = MemberFactory(fog_role=Member.FogRole.MEMBER)

            target.set_fog_role(Member.FogRole.GUILD_OFFICER, changed_by=officer)

            target.refresh_from_db()
            assert target.fog_role == Member.FogRole.GUILD_OFFICER

        def it_prevents_guild_officer_from_granting_admin():
            officer = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
            target = MemberFactory(fog_role=Member.FogRole.MEMBER)

            with pytest.raises(PermissionError, match="cannot grant admin"):
                target.set_fog_role(Member.FogRole.ADMIN, changed_by=officer)

            target.refresh_from_db()
            assert target.fog_role == Member.FogRole.MEMBER

        def it_prevents_regular_member_from_changing_roles():
            member = MemberFactory(fog_role=Member.FogRole.MEMBER)
            target = MemberFactory(fog_role=Member.FogRole.MEMBER)

            with pytest.raises(PermissionError, match="Only admins and guild officers"):
                target.set_fog_role(Member.FogRole.GUILD_OFFICER, changed_by=member)

        def it_raises_on_invalid_role():
            admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
            target = MemberFactory()

            with pytest.raises(ValueError, match="Invalid role"):
                target.set_fog_role("superadmin", changed_by=admin)

        def it_syncs_user_permissions_after_change():
            user = User.objects.create_user(username="sync_target", email="sync@example.com", password="pass")
            target = user.member
            admin = MemberFactory(fog_role=Member.FogRole.ADMIN)

            target.set_fog_role(Member.FogRole.GUILD_OFFICER, changed_by=admin)

            user.refresh_from_db()
            assert user.is_staff is True
            assert user.is_superuser is False
