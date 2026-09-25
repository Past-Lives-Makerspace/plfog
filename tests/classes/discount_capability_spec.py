"""Discount codes: a new code notifies the Discount Administrators, who can also approve it."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
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


def describe_discount_code_requested_notification():
    def it_notifies_a_discount_approver_on_creation():
        approver = _member("dapprover")
        approver.admin_capabilities.create(capability=AdminCapability.Capability.DISCOUNT_APPROVER)
        DiscountCode.objects.create(code="spring20", discount_pct=20)
        row = Notification.objects.get(user=approver.user, trigger="discount_code.requested")
        # Absolute, because the email channel uses the url verbatim and a bare path is dead in mail.
        assert row.url == f"{settings.MEMBER_BASE_URL}{reverse('classes:admin_discount_codes')}"

    def it_does_not_notify_a_plain_member():
        bystander = _member("bystander")
        DiscountCode.objects.create(code="none10", discount_pct=10)
        assert not Notification.objects.filter(user=bystander.user, trigger="discount_code.requested").exists()

    def it_sends_no_approval_ping_for_a_code_born_approved():
        # DiscountCodeRequest.approve creates the code already approved; a "needs approval" ping
        # right after the approver approved it would be false.
        approver = _member("dapprover2")
        approver.admin_capabilities.create(capability=AdminCapability.Capability.DISCOUNT_APPROVER)
        DiscountCode.objects.create(code="born20", discount_pct=20, is_approved=True)
        assert not Notification.objects.filter(user=approver.user, trigger="discount_code.requested").exists()


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
