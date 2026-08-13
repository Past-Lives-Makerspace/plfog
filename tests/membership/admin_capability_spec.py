"""BDD specs for AdminCapability, Member.has_admin_capability, and sync_admin_capabilities."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member(username: str = "cap") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )
    return user.member


def describe_AdminCapability():
    def it_stringifies_with_the_capability_label():
        member = _member("stringy")
        member.preferred_name = "Robin"
        member.save(update_fields=["preferred_name"])
        cap = AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.CLASS_APPROVER)
        assert "Class Administrator" in str(cap)

    def it_records_the_granting_user():
        member = _member("granted")
        admin = User.objects.create_user(username="granter", email="granter@example.com")
        cap = AdminCapability.objects.create(
            member=member, capability=AdminCapability.Capability.BILLING_APPROVER, granted_by=admin
        )
        assert cap.granted_by == admin

    def describe_unique_constraint():
        def it_forbids_the_same_capability_twice_for_a_member():
            member = _member("dup")
            AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.SPACE_APPROVER)
            with pytest.raises(IntegrityError):
                AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.SPACE_APPROVER)


def describe_has_admin_capability():
    def it_is_true_when_the_member_holds_the_grant():
        member = _member("holder")
        member.admin_capabilities.create(capability=AdminCapability.Capability.DISCOUNT_APPROVER)
        assert member.has_admin_capability(AdminCapability.Capability.DISCOUNT_APPROVER) is True

    def it_is_false_for_a_capability_the_member_lacks():
        member = _member("lacks")
        assert member.has_admin_capability(AdminCapability.Capability.DISCOUNT_APPROVER) is False


def describe_sync_admin_capabilities():
    def it_grants_the_checked_capabilities():
        member = _member("sync1")
        member.sync_admin_capabilities(
            [AdminCapability.Capability.CLASS_APPROVER, AdminCapability.Capability.EVENTS_APPROVER]
        )
        held = set(member.admin_capabilities.values_list("capability", flat=True))
        assert held == {"class_approver", "events_approver"}

    def it_revokes_the_unchecked_capabilities():
        member = _member("sync2")
        member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_APPROVER)
        member.sync_admin_capabilities([AdminCapability.Capability.SPACE_APPROVER])
        held = set(member.admin_capabilities.values_list("capability", flat=True))
        assert held == {"space_approver"}

    def it_leaves_unchanged_grants_intact_and_records_the_granter():
        member = _member("sync3")
        first = member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        granter = User.objects.create_user(username="admin3", email="admin3@example.com")
        member.sync_admin_capabilities(
            [AdminCapability.Capability.CLASS_APPROVER, AdminCapability.Capability.BILLING_APPROVER],
            granted_by=granter,
        )
        # The already-held row is untouched (same pk); the new one records the granter.
        assert member.admin_capabilities.get(capability="class_approver").pk == first.pk
        assert member.admin_capabilities.get(capability="billing_approver").granted_by == granter

    def it_clears_all_when_given_an_empty_list():
        member = _member("sync4")
        member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        member.sync_admin_capabilities([])
        assert member.admin_capabilities.count() == 0
