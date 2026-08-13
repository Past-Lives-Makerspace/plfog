"""A Space & Cubby Administrator (capability, not the admin role) can reach the space review queue."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["fog_role", "status"])
    member.sync_user_permissions()
    return user


def describe_space_review_authorization():
    def it_lets_a_space_approver_holder_open_the_queue(client: Client):
        user = _member_user("spaceapprover")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_APPROVER)
        client.login(username="spaceapprover", password="pass")
        response = client.get(reverse("hub_space_request_review_queue"))
        assert response.status_code == 200

    def it_forbids_a_plain_member_without_the_capability(client: Client):
        _member_user("plainmember")
        client.login(username="plainmember", password="pass")
        response = client.get(reverse("hub_space_request_review_queue"))
        assert response.status_code == 403
