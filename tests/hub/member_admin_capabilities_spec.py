"""The admin Member edit form assigns and revokes AdminCapability rows on save."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.forms import MemberAdminEditForm
from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _form_data(capabilities: list[str]) -> dict:
    return {
        "role": Member.FogRole.MEMBER,
        "full_legal_name": "Target Member",
        "status": Member.Status.ACTIVE,
        "member_type": Member.MemberType.STANDARD,
        "capabilities": capabilities,
    }


def describe_capabilities_field():
    def it_initializes_from_the_members_existing_grants():
        member = _member_user("init").member
        member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        form = MemberAdminEditForm(instance=member)
        assert "class_approver" in form.fields["capabilities"].initial


def describe_admin_member_edit_save():
    def it_grants_the_checked_capabilities(client: Client):
        _member_user("boss", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target").member
        client.login(username="boss", password="pass")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.pk]),
            _form_data(["class_approver", "space_approver"]),
        )
        assert response.status_code == 302
        held = set(target.admin_capabilities.values_list("capability", flat=True))
        assert held == {"class_approver", "space_approver"}

    def it_renders_the_capability_checkboxes_on_the_edit_page(client: Client):
        _member_user("boss3", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target3").member
        target.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        client.login(username="boss3", password="pass")
        response = client.get(reverse("hub_admin_member_edit", args=[target.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="capabilities"' in content
        assert "Class Administrator" in content
        assert "Billing Administrator" in content

    def it_revokes_unchecked_capabilities(client: Client):
        _member_user("boss2", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target2").member
        target.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        target.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_APPROVER)
        client.login(username="boss2", password="pass")
        client.post(reverse("hub_admin_member_edit", args=[target.pk]), _form_data(["class_approver"]))
        held = set(target.admin_capabilities.values_list("capability", flat=True))
        assert held == {"class_approver"}
