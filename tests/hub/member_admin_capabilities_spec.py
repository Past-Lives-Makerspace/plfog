"""The admin Member Permissions tab assigns/revokes AdminCapability rows via toggles,
and lets an admin edit a member's notification preferences for them."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import NotificationPreference
from hub.forms import MemberCapabilitiesForm
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


def _cap_post(**checked: bool) -> dict:
    """A capabilities-form POST: the form_id plus each checked capability toggle ('on')."""
    data: dict[str, str] = {"form_id": "capabilities"}
    for name, on in checked.items():
        if on:
            data[name] = "on"
    return data


def describe_capabilities_form():
    def it_initializes_from_the_members_existing_grants():
        member = _member_user("init").member
        member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        initial = MemberCapabilitiesForm.initial_for(member)
        assert initial["cap_class_approver"] is True
        assert initial["cap_space_approver"] is False

    def it_reports_selected_capabilities_from_the_checked_toggles():
        form = MemberCapabilitiesForm({"cap_class_approver": "on", "cap_billing_approver": "on"})
        assert form.is_valid()
        assert set(form.selected()) == {"class_approver", "billing_approver"}

    def it_offers_the_refunds_toggle():
        form = MemberCapabilitiesForm({"cap_refunds": "on"})
        assert form.is_valid()
        assert form.selected() == ["refunds"]
        assert form.fields["cap_refunds"].label == "Refunds"

    def it_initializes_the_refunds_toggle_from_an_existing_grant():
        member = _member_user("refinit").member
        member.admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)
        assert MemberCapabilitiesForm.initial_for(member)["cap_refunds"] is True

    def it_offers_the_equipment_and_space_manager_toggles():
        # #502: EQUIPMENT was never on the form; SPACE_MANAGER is new. Both after Refunds.
        form = MemberCapabilitiesForm({"cap_equipment": "on", "cap_space_manager": "on"})
        assert form.is_valid()
        assert form.selected() == ["equipment", "space_manager"]
        fields = list(form.fields)
        after_refunds = fields.index("cap_refunds") + 1
        assert fields[after_refunds : after_refunds + 2] == ["cap_equipment", "cap_space_manager"]
        assert form.fields["cap_equipment"].label == "Equipment Administrator"
        assert form.fields["cap_space_manager"].label == "Space Manager"
        assert (
            form.fields["cap_space_manager"].help_text
            == (AdminCapability.DESCRIPTIONS[AdminCapability.Capability.SPACE_MANAGER])
        )

    def it_initializes_the_new_toggles_from_existing_grants():
        member = _member_user("spaceinit").member
        member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
        initial = MemberCapabilitiesForm.initial_for(member)
        assert initial["cap_space_manager"] is True
        assert initial["cap_equipment"] is False

    def it_offers_the_webmaster_toggle_last():
        form = MemberCapabilitiesForm({"cap_webmaster": "on"})
        assert form.is_valid()
        assert form.selected() == ["webmaster"]
        assert list(form.fields)[-1] == "cap_webmaster"
        assert form.fields["cap_webmaster"].label == "Webmaster"
        assert form.fields["cap_webmaster"].help_text == (
            "Gets an alert when an automation fails, such as the reminder emails, with what broke and a link "
            "to the Automations page."
        )

    def it_initializes_the_webmaster_toggle_from_an_existing_grant():
        member = _member_user("webinit").member
        member.admin_capabilities.create(capability=AdminCapability.Capability.WEBMASTER)
        assert MemberCapabilitiesForm.initial_for(member)["cap_webmaster"] is True


def describe_admin_member_edit_permissions():
    def it_grants_the_checked_capabilities(client: Client):
        _member_user("boss", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target").member
        client.login(username="boss", password="pass")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.pk]),
            _cap_post(cap_class_approver=True, cap_space_approver=True),
        )
        assert response.status_code == 302
        held = set(target.admin_capabilities.values_list("capability", flat=True))
        assert held == {"class_approver", "space_approver"}

    def it_grants_and_revokes_the_refunds_capability(client: Client):
        _member_user("refboss", fog_role=Member.FogRole.ADMIN)
        target = _member_user("reftarget").member
        client.login(username="refboss", password="pass")
        url = reverse("hub_admin_member_edit", args=[target.pk])

        client.post(url, _cap_post(cap_refunds=True))
        assert set(target.admin_capabilities.values_list("capability", flat=True)) == {"refunds"}

        client.post(url, _cap_post())
        assert target.admin_capabilities.count() == 0

    def it_renders_the_capability_toggles_on_the_permissions_tab(client: Client):
        _member_user("boss3", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target3").member
        target.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        client.login(username="boss3", password="pass")
        response = client.get(reverse("hub_admin_member_edit", args=[target.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="cap_class_approver"' in content
        assert "CMS Administrator" in content
        assert "Billing Administrator" in content
        # Notifications live on their own tab (not under Permissions), rendering the matrix.
        assert "section === 'notifications'" in content
        assert "pl-notif-matrix" in content

    def it_renders_the_equipment_and_space_manager_toggles_on_the_permissions_tab(client: Client):
        _member_user("boss5", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target5").member
        target.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
        client.login(username="boss5", password="pass")
        content = client.get(reverse("hub_admin_member_edit", args=[target.pk])).content.decode()
        assert 'name="cap_equipment"' in content
        assert 'name="cap_space_manager"' in content
        assert "Equipment Administrator" in content
        assert "Space Manager" in content
        assert "Cannot add tools." in content

    def it_grants_and_revokes_the_equipment_and_space_manager_capabilities(client: Client):
        _member_user("boss6", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target6").member
        client.login(username="boss6", password="pass")
        url = reverse("hub_admin_member_edit", args=[target.pk])

        client.post(url, _cap_post(cap_equipment=True, cap_space_manager=True))
        assert set(target.admin_capabilities.values_list("capability", flat=True)) == {"equipment", "space_manager"}

        client.post(url, _cap_post(cap_space_manager=True))
        assert set(target.admin_capabilities.values_list("capability", flat=True)) == {"space_manager"}

        client.post(url, _cap_post())
        assert target.admin_capabilities.count() == 0

    def it_renders_and_grants_the_webmaster_toggle_on_the_permissions_tab(client: Client):
        _member_user("boss7", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target7").member
        client.login(username="boss7", password="pass")
        url = reverse("hub_admin_member_edit", args=[target.pk])

        content = client.get(url).content.decode()
        assert 'name="cap_webmaster"' in content
        # Anchored on the toggle's markup: a changelog entry quoting the copy cannot satisfy it.
        description = AdminCapability.DESCRIPTIONS[AdminCapability.Capability.WEBMASTER]
        assert f'<div class="pl-toggle-desc">{description}</div>' in content

        client.post(url, _cap_post(cap_webmaster=True))
        assert set(target.admin_capabilities.values_list("capability", flat=True)) == {"webmaster"}

        client.post(url, _cap_post())
        assert target.admin_capabilities.count() == 0

    def it_revokes_unchecked_capabilities(client: Client):
        _member_user("boss2", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target2").member
        target.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        target.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_APPROVER)
        client.login(username="boss2", password="pass")
        client.post(
            reverse("hub_admin_member_edit", args=[target.pk]),
            _cap_post(cap_class_approver=True),
        )
        held = set(target.admin_capabilities.values_list("capability", flat=True))
        assert held == {"class_approver"}

    def it_saves_the_members_notification_preferences_not_the_admins(client: Client):
        _member_user("boss4", fog_role=Member.FogRole.ADMIN)
        target = _member_user("target4").member
        client.login(username="boss4", password="pass")
        # A notifications POST with no boxes checked turns every opt-out-able channel off
        # for the target — writing preference rows against the MEMBER, not the acting admin.
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.pk]),
            {"form_id": "notifications"},
        )
        assert response.status_code == 302
        assert NotificationPreference.objects.filter(user=target.user).exists()
        assert not NotificationPreference.objects.filter(user__username="boss4").exists()
