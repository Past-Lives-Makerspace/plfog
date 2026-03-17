"""Tests for membership signals."""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth import get_user_model

from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

User = get_user_model()


@pytest.mark.django_db
def describe_ensure_user_has_member():
    def it_auto_creates_member_for_regular_user():
        MembershipPlanFactory()
        user = User.objects.create_user(
            username="regular",
            email="regular@example.com",
            password="password",
        )
        assert Member.objects.filter(user=user).exists()

    def it_auto_creates_member_for_staff_user():
        MembershipPlanFactory()
        user = User.objects.create_user(
            username="staffuser",
            email="staff@example.com",
            password="password",
            is_staff=True,
        )
        assert Member.objects.filter(user=user).exists()

    def it_does_not_create_duplicate_member():
        plan = MembershipPlanFactory()
        user = User.objects.create_user(
            username="existing",
            email="existing@example.com",
            password="password",
        )
        # Signal already created one; saving again shouldn't duplicate
        user.save()
        assert Member.objects.filter(user=user).count() == 1

    def it_logs_warning_and_skips_when_no_membership_plan_exists(caplog):
        with caplog.at_level(logging.WARNING, logger="membership.signals"):
            user = User.objects.create_user(
                username="noplan",
                email="noplan@example.com",
                password="password",
            )

        assert not Member.objects.filter(user=user).exists()
        assert "no MembershipPlan exists" in caplog.text

    def it_uses_full_name_when_available():
        MembershipPlanFactory()
        user = User.objects.create_user(
            username="fullname",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            password="password",
        )
        member = Member.objects.get(user=user)
        assert member.full_legal_name == "Jane Doe"

    def it_falls_back_to_username_when_full_name_is_blank():
        MembershipPlanFactory()
        user = User.objects.create_user(
            username="nofullname",
            first_name="",
            last_name="",
            email="nofullname@example.com",
            password="password",
        )
        member = Member.objects.get(user=user)
        assert member.full_legal_name == "nofullname"
