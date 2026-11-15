"""Discount codes: the Discount Administrators approve any code; a new code asks nobody (#1185)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from classes.models import DiscountCode
from core.models import Notification
from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", last_login=timezone.now())
    member = user.member
    member.status = Member.Status.ACTIVE
    member.fog_role = fog_role
    member.save(update_fields=["status", "fog_role"])
    return member


def describe_a_new_code():
    def it_asks_nobody_to_approve_it():
        # #1185 retired discount_code.requested: no new code rings anyone's bell to be approved.
        approver = _member("dapprover")
        approver.admin_capabilities.create(capability=AdminCapability.Capability.DISCOUNT_APPROVER)
        DiscountCode.objects.create(code="spring20", discount_pct=20)
        assert not Notification.objects.filter(user=approver.user).exists()


def describe_approver_for_capability():
    def it_lets_a_discount_approver_approve_any_code():
        approver = _member("dcap")
        approver.admin_capabilities.create(capability=AdminCapability.Capability.DISCOUNT_APPROVER)
        code = DiscountCode.objects.create(code="cap5", discount_fixed_cents=500)
        assert code.can_be_approved_by(approver.user) is True

    def it_refuses_a_plain_member_on_someone_elses_code():
        plain = _member("plain2")
        code = DiscountCode.objects.create(code="deny5", discount_fixed_cents=500)
        assert code.can_be_approved_by(plain.user) is False
