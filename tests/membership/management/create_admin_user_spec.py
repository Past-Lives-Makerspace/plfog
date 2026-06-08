"""BDD specs for the create_admin_user management command."""

from __future__ import annotations

from django.core.management import call_command

from classes.factories import UserFactory
from core.models import Invite
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory


def describe_create_admin_user_command():
    def it_invites_and_grants_admin_for_a_new_email(db, mailoutbox):
        MembershipPlanFactory()

        call_command("create_admin_user", "newadmin@example.com")

        member = Member.objects.get(_pre_signup_email__iexact="newadmin@example.com")
        assert member.fog_role == Member.FogRole.ADMIN
        assert member.status == Member.Status.INVITED
        assert ["newadmin@example.com"] in [m.to for m in mailoutbox]

    def it_resends_the_invite_for_an_existing_pending_member(db, mailoutbox):
        MembershipPlanFactory()
        call_command("create_admin_user", "pending@example.com")
        mailoutbox.clear()

        call_command("create_admin_user", "pending@example.com")

        member = Member.objects.get(_pre_signup_email__iexact="pending@example.com")
        assert member.fog_role == Member.FogRole.ADMIN
        assert [m.to for m in mailoutbox] == [["pending@example.com"]]

    def it_promotes_an_existing_signed_up_member_found_by_email_address(db):
        # UserFactory's save creates the member (via signal) and a linked
        # EmailAddress. Clearing _pre_signup_email forces the command's lookup
        # to resolve the member through the EmailAddress path instead.
        user = UserFactory(username="signed@example.com", email="signed@example.com")
        member = user.member
        member._pre_signup_email = ""
        member.save(update_fields=["_pre_signup_email"])

        call_command("create_admin_user", "signed@example.com")

        member.refresh_from_db()
        assert member.fog_role == Member.FogRole.ADMIN

    def it_resends_when_a_pending_invite_exists_but_the_member_lookup_misses(db, mailoutbox):
        MembershipPlanFactory()
        member = MemberFactory(_pre_signup_email="other@example.com", status=Member.Status.INVITED)
        Invite.objects.create(email="ghost@example.com", invited_by=None, member=member)

        call_command("create_admin_user", "ghost@example.com")

        member.refresh_from_db()
        assert member.fog_role == Member.FogRole.ADMIN
        assert ["ghost@example.com"] in [m.to for m in mailoutbox]
