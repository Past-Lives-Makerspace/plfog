"""BDD-style tests for membership.forms — InviteMemberForm and AddMemberForm."""

from __future__ import annotations

import pytest

from core.models import Invite
from membership.forms import AddMemberForm, InviteMemberForm
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def describe_AddMemberForm():
    def _valid_data(plan, **overrides):
        data = {
            "full_legal_name": "Jane Maker",
            "email": "jane@example.com",
            "membership_plan": str(plan.pk),
            "preferred_name": "",
            "status": Member.Status.ACTIVE,
        }
        data.update(overrides)
        return data

    def it_accepts_valid_data():
        plan = MembershipPlanFactory()
        form = AddMemberForm(data=_valid_data(plan))
        assert form.is_valid(), form.errors

    def it_defaults_status_to_active():
        assert AddMemberForm().fields["status"].initial == Member.Status.ACTIVE

    def it_requires_a_membership_plan():
        MembershipPlanFactory()
        form = AddMemberForm(data=_valid_data(MembershipPlanFactory(), membership_plan=""))
        assert not form.is_valid()
        assert "membership_plan" in form.errors

    def it_rejects_a_blank_name():
        plan = MembershipPlanFactory()
        form = AddMemberForm(data=_valid_data(plan, full_legal_name="   "))
        assert not form.is_valid()
        assert "Enter the member's full legal name." in form.errors["full_legal_name"]

    def it_strips_whitespace_from_the_name():
        plan = MembershipPlanFactory()
        form = AddMemberForm(data=_valid_data(plan, full_legal_name="  Jane Maker  "))
        assert form.is_valid(), form.errors
        assert form.cleaned_data["full_legal_name"] == "Jane Maker"

    def it_rejects_an_existing_active_member_email():
        plan = MembershipPlanFactory()
        MemberFactory(_pre_signup_email="taken@example.com", status=Member.Status.ACTIVE)
        form = AddMemberForm(data=_valid_data(plan, email="taken@example.com"))
        assert not form.is_valid()
        assert "A member with this email already exists." in form.errors["email"]

    def it_rejects_an_existing_member_email_case_insensitively():
        plan = MembershipPlanFactory()
        MemberFactory(_pre_signup_email="taken@example.com", status=Member.Status.ACTIVE)
        form = AddMemberForm(data=_valid_data(plan, email="TAKEN@example.com"))
        assert not form.is_valid()
        assert "email" in form.errors

    def it_allows_an_email_only_held_by_an_invited_placeholder():
        plan = MembershipPlanFactory()
        MemberFactory(_pre_signup_email="invited@example.com", status=Member.Status.INVITED, user=None)
        form = AddMemberForm(data=_valid_data(plan, email="invited@example.com"))
        assert form.is_valid(), form.errors

    def describe_create_member():
        def it_creates_a_member_from_cleaned_data():
            plan = MembershipPlanFactory()
            form = AddMemberForm(data=_valid_data(plan, preferred_name="Janey"))
            assert form.is_valid(), form.errors
            member = form.create_member()
            assert member.pk is not None
            assert member.full_legal_name == "Jane Maker"
            assert member.preferred_name == "Janey"
            assert member._pre_signup_email == "jane@example.com"
            assert member.membership_plan == plan
            assert member.status == Member.Status.ACTIVE

        def it_provisions_a_login_ready_user_for_an_active_member_without_sending_email(mailoutbox):
            plan = MembershipPlanFactory()
            form = AddMemberForm(data=_valid_data(plan))
            assert form.is_valid(), form.errors
            member = form.create_member()
            member.refresh_from_db()
            assert member.user is not None
            assert len(mailoutbox) == 0

        def it_leaves_a_non_active_member_unlinked():
            plan = MembershipPlanFactory()
            form = AddMemberForm(data=_valid_data(plan, email="later@example.com", status=Member.Status.SUSPENDED))
            assert form.is_valid(), form.errors
            member = form.create_member()
            member.refresh_from_db()
            assert member.user is None


def describe_InviteMemberForm():
    def it_accepts_valid_email():
        form = InviteMemberForm(data={"email": "new@example.com"})
        assert form.is_valid()

    def it_rejects_existing_active_member():
        MemberFactory(_pre_signup_email="taken@example.com", status=Member.Status.ACTIVE)
        form = InviteMemberForm(data={"email": "taken@example.com"})
        assert not form.is_valid()
        assert "A member with this email already exists." in form.errors["email"]

    def it_rejects_existing_active_member_case_insensitive():
        MemberFactory(_pre_signup_email="taken@example.com", status=Member.Status.ACTIVE)
        form = InviteMemberForm(data={"email": "TAKEN@example.com"})
        assert not form.is_valid()

    def it_allows_email_with_invited_status_member():
        MemberFactory(_pre_signup_email="invited@example.com", status=Member.Status.INVITED, user=None)
        form = InviteMemberForm(data={"email": "invited@example.com"})
        # Still blocked by pending invite check if one exists, but not by member check
        assert form.is_valid()

    def it_rejects_pending_invite():
        MembershipPlanFactory()
        Invite.objects.create(email="pending@example.com")
        form = InviteMemberForm(data={"email": "pending@example.com"})
        assert not form.is_valid()
        assert "A pending invite for this email already exists." in form.errors["email"]

    def it_allows_accepted_invite_email():
        MembershipPlanFactory()
        from django.utils import timezone

        Invite.objects.create(email="accepted@example.com", accepted_at=timezone.now())
        form = InviteMemberForm(data={"email": "accepted@example.com"})
        assert form.is_valid()
