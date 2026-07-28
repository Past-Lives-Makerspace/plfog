"""BDD specs for the hub-native admin pages: voting dashboard, members, member edit, site settings."""

from __future__ import annotations

from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Invite, SiteConfiguration
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _create_superuser(client: Client, *, username: str = "admin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _create_member_user(*, username: str, fog_role: str = Member.FogRole.MEMBER) -> User:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    member.fog_role = fog_role
    if not member.full_legal_name:
        member.full_legal_name = username.title()
    member.save()
    return user


def _create_nonmember_user(*, username: str, email: str) -> User:
    """A genuine "Non-member user" — a User with no Member but a primary EmailAddress.

    The user signal may auto-create a Member (when a plan exists), so we drop it and
    re-seed a clean primary EmailAddress so the email column reads the real address.
    """
    user = User.objects.create_user(username=username, email=email)
    Member.objects.filter(user=user).delete()
    EmailAddress.objects.filter(user=user).delete()
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return user


def _create_bare_nonmember_user(*, username: str, email: str) -> User:
    """A non-member User with NO allauth EmailAddress (the no-primary-email edge)."""
    user = User.objects.create_user(username=username, email=email)
    Member.objects.filter(user=user).delete()
    EmailAddress.objects.filter(user=user).delete()
    return user


# The admin Voting surface (overview/history/snapshots/settings + gating) now has
# its own dedicated spec: tests/hub/voting_admin_spec.py.


def describe_admin_members():
    def it_requires_login(client):
        response = client.get(reverse("hub_admin_members"))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="m1")
        client.login(username=user.username, password="p")
        response = client.get(reverse("hub_admin_members"))
        assert response.status_code == 403

    def it_renders_for_admin_with_default_active_filter(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_members"))
        assert response.status_code == 200
        assert b"Manage Members" in response.content
        assert response.context["status_filter"] == "active"

    def it_renders_the_invites_card(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_members"))
        assert b"Members &amp; invites" in response.content
        assert b'id="invites-list"' in response.content
        assert "invites" in response.context
        assert "invite_form" in response.context

    def it_lists_outstanding_invites(client):
        admin = _create_superuser(client, username="memadmin")
        Invite.objects.create(email="outstanding@x.com", invited_by=admin)
        response = client.get(reverse("hub_admin_members"))
        assert b"outstanding@x.com" in response.content

    def it_shows_the_invites_empty_state_when_none(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_members"))
        assert b"No outstanding invites" in response.content

    def it_filters_by_all_status(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_members") + "?status=all")
        assert response.status_code == 200
        assert response.context["status_filter"] == "all"

    def it_filters_by_search_role_and_type(client):
        _create_superuser(client)
        target = _create_member_user(username="searchtarget", fog_role=Member.FogRole.ADMIN)
        target.member.full_legal_name = "Findable Person"
        target.member.member_type = Member.MemberType.STANDARD
        target.member.save()
        response = client.get(reverse("hub_admin_members") + "?status=all&q=Findable&role=admin&type=standard")
        assert response.status_code == 200
        assert response.context["search"] == "Findable"
        assert response.context["role_filter"] == "admin"
        assert response.context["type_filter"] == "standard"
        assert b"Findable Person" in response.content

    def it_shows_the_primary_email_for_an_unlinked_airtable_member(client):
        _create_superuser(client)
        MemberFactory(user=None, _pre_signup_email="airtable@example.com", full_legal_name="Airtable Person")
        response = client.get(reverse("hub_admin_members"))
        # Previously this rendered the (empty) user.email mirror as "—".
        assert b"airtable@example.com" in response.content

    def it_shows_the_emailaddress_not_the_stale_user_mirror(client):
        _create_superuser(client)
        MembershipPlanFactory()
        user = User.objects.create_user(username="linkedreal", email="mirror@example.com")
        EmailAddress.objects.filter(user=user).update(email="real@example.com")
        user.email = "stale@example.com"
        user.save(update_fields=["email"])
        response = client.get(reverse("hub_admin_members"))
        content = response.content.decode()
        assert "real@example.com" in content
        assert "stale@example.com" not in content

    def it_filters_to_only_emailless_members(client):
        _create_superuser(client)
        MemberFactory(user=None, _pre_signup_email="", full_legal_name="Emailless Person")
        MemberFactory(user=None, _pre_signup_email="has@example.com", full_legal_name="Has Email Person")
        response = client.get(reverse("hub_admin_members") + "?email=missing&status=all")
        assert response.status_code == 200
        assert response.context["email_filter"] == "missing"
        content = response.content.decode()
        assert "Emailless Person" in content
        assert "Has Email Person" not in content

    def it_labels_why_each_emailless_member_has_no_email(client):
        _create_superuser(client)
        MembershipPlanFactory()
        MemberFactory(user=None, _pre_signup_email="")  # no_airtable_email bucket
        User.objects.create_user(username="noacct", email="")  # no_account_email bucket
        response = client.get(reverse("hub_admin_members") + "?email=missing&status=all")
        content = response.content.decode()
        assert "Never signed up — no email on file from Airtable" in content
        assert "Signed up, but has no email on their account" in content

    def it_counts_emailless_members_within_the_current_filters(client):
        _create_superuser(client)
        MemberFactory(user=None, _pre_signup_email="")
        MemberFactory(user=None, _pre_signup_email="")
        MemberFactory(user=None, _pre_signup_email="has@example.com")
        response = client.get(reverse("hub_admin_members") + "?status=all")
        assert response.context["missing_count"] == 2
        assert '<span class="hub-badge">2</span>' in response.content.decode()

    def it_shows_a_friendly_empty_state_when_nobody_is_missing_email(client):
        _create_superuser(client)
        MemberFactory(user=None, _pre_signup_email="has@example.com")
        response = client.get(reverse("hub_admin_members") + "?email=missing&status=all")
        assert response.context["missing_count"] == 0
        assert "No members are missing an email — nice." in response.content.decode()

    def it_keeps_the_email_filter_in_the_form_when_active(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_members") + "?email=missing")
        assert b'name="email" value="missing"' in response.content

    def it_carries_the_email_filter_through_pagination(client):
        plan = MembershipPlanFactory()
        _create_superuser(client)
        MemberFactory.create_batch(51, user=None, _pre_signup_email="", membership_plan=plan)
        MemberFactory(
            user=None,
            _pre_signup_email="present@example.com",
            full_legal_name="Has Email Control",
            membership_plan=plan,
        )

        page1 = client.get(reverse("hub_admin_members") + "?email=missing&status=all")
        assert b"&email=missing" in page1.content  # pagination links carry the filter

        page2 = client.get(reverse("hub_admin_members") + "?email=missing&status=all&page=2")
        assert page2.status_code == 200
        assert page2.context["page"].number == 2
        assert page2.context["page"].paginator.count == 51  # only the emailless set is paginated
        assert b"Has Email Control" not in page2.content

    def it_lists_non_member_users_badged_and_linked_to_the_user_edit_route(client):
        _create_superuser(client)
        MembershipPlanFactory()
        _create_member_user(username="realmember")
        nonmember = _create_nonmember_user(username="registrant", email="student@pastlives.demo")
        response = client.get(reverse("hub_admin_members") + "?status=all")
        content = response.content.decode()
        assert "student@pastlives.demo" in content
        assert "Non-member user" in content
        assert reverse("hub_admin_user_edit", args=[nonmember.pk]) in content

    def it_excludes_superusers_from_the_non_member_rows(client):
        # The owner's admin login (a superuser with no Member) must NOT appear as a
        # class-registrant-style "Non-member user" row (Review fix #7).
        _create_superuser(client, username="owner")
        response = client.get(reverse("hub_admin_members") + "?status=all")
        assert b"Non-member user" not in response.content

    def it_shows_non_member_users_by_default(client):
        _create_superuser(client)
        MembershipPlanFactory()
        _create_nonmember_user(username="reg_default", email="reg-default@pastlives.demo")
        response = client.get(reverse("hub_admin_members"))
        assert response.context["member_only_filter_active"] is False
        assert b"reg-default@pastlives.demo" in response.content

    def it_hides_non_member_users_when_a_member_only_filter_is_active(client):
        _create_superuser(client)
        MembershipPlanFactory()
        _create_nonmember_user(username="reg_hidden", email="reg-hidden@pastlives.demo")
        response = client.get(reverse("hub_admin_members") + "?status=all&role=admin")
        assert response.context["member_only_filter_active"] is True
        assert b"reg-hidden@pastlives.demo" not in response.content

    def it_paginates_over_the_member_and_user_union(client):
        admin = _create_superuser(client)
        # Drop the admin's auto-created Member (and it's a superuser) so the count is
        # exactly the rows we seed below.
        Member.objects.filter(user=admin).delete()
        plan = MembershipPlanFactory()
        MemberFactory.create_batch(49, membership_plan=plan, status=Member.Status.ACTIVE)
        _create_nonmember_user(username="nmu1", email="aaa-nmu1@x.com")
        _create_nonmember_user(username="nmu2", email="zzz-nmu2@x.com")
        page1 = client.get(reverse("hub_admin_members") + "?status=all")
        assert page1.context["page"].paginator.count == 51  # 49 members + 2 non-member users
        page2 = client.get(reverse("hub_admin_members") + "?status=all&page=2")
        assert page2.context["page"].number == 2
        # Members fill page 1; the last non-member user (by email) lands on page 2.
        assert b"zzz-nmu2@x.com" in page2.content

    def it_links_a_member_with_classes_to_the_class_admin(client):
        from classes.factories import ClassOfferingFactory

        _create_superuser(client)
        target = _create_member_user(username="teacher")
        ClassOfferingFactory(instructor=target.member)
        response = client.get(reverse("hub_admin_members") + "?status=all")
        assert f"?instructor={target.member.pk}" in response.content.decode()

    def it_lists_a_non_member_user_with_no_primary_email(client):
        _create_superuser(client)
        user = _create_bare_nonmember_user(username="bare", email="bare@x.com")
        response = client.get(reverse("hub_admin_members") + "?status=all")
        # Listed even without a primary EmailAddress; the email column never reads the mirror.
        assert reverse("hub_admin_user_edit", args=[user.pk]).encode() in response.content

    def it_avoids_n_plus_one_queries_for_the_email_column(client, django_assert_max_num_queries):
        _create_superuser(client)
        plan = MembershipPlanFactory()
        User.objects.create_user(username="nqsmall", email="nqsmall@example.com")
        MemberFactory(user=None, membership_plan=plan)
        with django_assert_max_num_queries(50) as captured:
            client.get(reverse("hub_admin_members") + "?status=all")
        budget = len(captured.captured_queries)

        for i in range(8):
            User.objects.create_user(username=f"nqbig{i}", email=f"nqbig{i}@example.com")
        MemberFactory.create_batch(8, user=None, membership_plan=plan)
        # The prefetch keeps the query count flat — an N+1 here would blow past the small-set budget.
        with django_assert_max_num_queries(budget):
            client.get(reverse("hub_admin_members") + "?status=all")

    def it_shows_the_add_member_affordance(client):
        _create_superuser(client, username="addaff")
        response = client.get(reverse("hub_admin_members"))
        assert b"+ Add member" in response.content
        assert b'name="full_legal_name"' in response.content

    def it_collapses_expired_invites_behind_a_count(client):
        admin = _create_superuser(client, username="expcol")
        expired = Invite.objects.create(email="stale@x.com", invited_by=admin)
        Invite.objects.filter(pk=expired.pk).update(created_at=timezone.now() - timedelta(days=30))
        response = client.get(reverse("hub_admin_members"))
        assert b"Show 1 expired invite" in response.content
        assert b"Clear expired" in response.content


def describe_admin_member_create():
    def _valid_post(plan, **overrides):
        data = {
            "full_legal_name": "New Member",
            "email": "created@x.com",
            "membership_plan": str(plan.pk),
            "preferred_name": "",
            "status": Member.Status.ACTIVE,
        }
        data.update(overrides)
        return data

    def it_requires_login(client):
        response = client.post(reverse("hub_admin_member_create"), {})
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="mc1")
        client.login(username=user.username, password="p")
        response = client.post(reverse("hub_admin_member_create"), {})
        assert response.status_code == 403

    def it_rejects_get(client):
        _create_superuser(client, username="createadmin")
        response = client.get(reverse("hub_admin_member_create"))
        assert response.status_code == 405

    def it_creates_a_member_and_redirects_to_the_list(client):
        plan = MembershipPlanFactory()
        _create_superuser(client, username="createadmin")
        response = client.post(reverse("hub_admin_member_create"), _valid_post(plan))
        assert response.status_code == 204
        assert response["HX-Redirect"] == reverse("hub_admin_members")
        member = Member.objects.get(_pre_signup_email="created@x.com")
        assert member.full_legal_name == "New Member"
        assert member.status == Member.Status.ACTIVE

    def it_does_not_send_any_email(client, mailoutbox):
        plan = MembershipPlanFactory()
        _create_superuser(client, username="createadmin")
        client.post(reverse("hub_admin_member_create"), _valid_post(plan))
        assert len(mailoutbox) == 0

    def it_re_renders_the_form_with_errors_for_a_duplicate_email(client):
        plan = MembershipPlanFactory()
        _create_superuser(client, username="createadmin")
        MemberFactory(_pre_signup_email="dupe@x.com", status=Member.Status.ACTIVE)
        response = client.post(reverse("hub_admin_member_create"), _valid_post(plan, email="dupe@x.com"))
        assert response.status_code == 200
        assert b"A member with this email already exists." in response.content
        assert not Member.objects.filter(_pre_signup_email="dupe@x.com", full_legal_name="New Member").exists()

    def it_re_renders_the_form_with_errors_for_a_blank_name(client):
        plan = MembershipPlanFactory()
        _create_superuser(client, username="createadmin")
        response = client.post(reverse("hub_admin_member_create"), _valid_post(plan, full_legal_name="   "))
        assert response.status_code == 200
        assert b"Enter the member" in response.content


def describe_admin_member_invite():
    def it_requires_login(client):
        response = client.post(reverse("hub_admin_member_invite"), {"email": "new@x.com"})
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="mi1")
        client.login(username=user.username, password="p")
        response = client.post(reverse("hub_admin_member_invite"), {"email": "new@x.com"})
        assert response.status_code == 403

    def it_rejects_get(client):
        _create_superuser(client, username="invadmin")
        response = client.get(reverse("hub_admin_member_invite"))
        assert response.status_code == 405

    def it_sends_an_invite_and_returns_the_panel(client):
        MembershipPlanFactory()
        _create_superuser(client, username="invadmin")
        response = client.post(reverse("hub_admin_member_invite"), {"email": "newbie@x.com"})
        assert response.status_code == 200
        assert b"newbie@x.com" in response.content
        assert "Invite sent" in response["HX-Trigger"]
        assert Invite.objects.filter(email="newbie@x.com").exists()

    def it_returns_204_and_error_toast_for_an_existing_member(client):
        _create_superuser(client, username="invadmin")
        _create_member_user(username="taken")  # member with email taken@x.com
        response = client.post(reverse("hub_admin_member_invite"), {"email": "taken@x.com"})
        assert response.status_code == 204
        assert "already exists" in response["HX-Trigger"]
        assert not Invite.objects.filter(email="taken@x.com").exists()

    def it_returns_204_and_error_toast_for_an_invalid_email(client):
        _create_superuser(client, username="invadmin")
        response = client.post(reverse("hub_admin_member_invite"), {"email": "not-an-email"})
        assert response.status_code == 204
        assert "HX-Trigger" in response
        assert Invite.objects.count() == 0


def describe_admin_invite_resend():
    def it_requires_login(client):
        admin = User.objects.create_superuser(username="ra", email="ra@x.com", password="p")
        invite = Invite.objects.create(email="r@x.com", invited_by=admin)
        response = client.post(reverse("hub_admin_invite_resend", args=[invite.pk]))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        admin = User.objects.create_superuser(username="ra", email="ra@x.com", password="p")
        invite = Invite.objects.create(email="r@x.com", invited_by=admin)
        user = _create_member_user(username="mr1")
        client.login(username=user.username, password="p")
        response = client.post(reverse("hub_admin_invite_resend", args=[invite.pk]))
        assert response.status_code == 403

    def it_resends_a_pending_invite_and_moves_last_sent_at_forward(client):
        admin = _create_superuser(client, username="resadmin")
        invite = Invite.objects.create(email="resend@x.com", invited_by=admin)
        five_days_ago = timezone.now() - timedelta(days=5)
        Invite.objects.filter(pk=invite.pk).update(last_sent_at=five_days_ago)
        response = client.post(reverse("hub_admin_invite_resend", args=[invite.pk]))
        assert response.status_code == 200
        assert b"resend@x.com" in response.content
        assert "Invite resent" in response["HX-Trigger"]
        invite.refresh_from_db()
        assert invite.last_sent_at > five_days_ago

    def it_returns_204_for_an_already_accepted_invite(client):
        admin = _create_superuser(client, username="resadmin")
        invite = Invite.objects.create(email="acc@x.com", invited_by=admin)
        invite.mark_accepted()
        response = client.post(reverse("hub_admin_invite_resend", args=[invite.pk]))
        assert response.status_code == 204
        assert "already accepted" in response["HX-Trigger"]


def describe_admin_invite_revoke():
    def it_requires_login(client):
        admin = User.objects.create_superuser(username="rva", email="rva@x.com", password="p")
        invite = Invite.objects.create(email="rv@x.com", invited_by=admin)
        response = client.post(reverse("hub_admin_invite_revoke", args=[invite.pk]))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        admin = User.objects.create_superuser(username="rva", email="rva@x.com", password="p")
        invite = Invite.objects.create(email="rv@x.com", invited_by=admin)
        user = _create_member_user(username="mrv1")
        client.login(username=user.username, password="p")
        response = client.post(reverse("hub_admin_invite_revoke", args=[invite.pk]))
        assert response.status_code == 403

    def it_revokes_a_pending_invite(client):
        admin = _create_superuser(client, username="rvadmin")
        invite = Invite.objects.create(email="rev@x.com", invited_by=admin)
        response = client.post(reverse("hub_admin_invite_revoke", args=[invite.pk]), follow=True)
        assert not Invite.objects.filter(pk=invite.pk).exists()
        assert b"Revoked the invite" in response.content

    def it_shows_an_error_for_an_accepted_invite(client):
        admin = _create_superuser(client, username="rvadmin")
        invite = Invite.objects.create(email="acc@x.com", invited_by=admin)
        invite.mark_accepted()
        response = client.post(reverse("hub_admin_invite_revoke", args=[invite.pk]), follow=True)
        assert Invite.objects.filter(pk=invite.pk).exists()
        assert b"already been accepted" in response.content


def describe_admin_invite_clear_expired():
    def it_requires_login(client):
        response = client.post(reverse("hub_admin_invite_clear_expired"))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="ce1")
        client.login(username=user.username, password="p")
        response = client.post(reverse("hub_admin_invite_clear_expired"))
        assert response.status_code == 403

    def it_rejects_get(client):
        _create_superuser(client, username="clearadmin")
        response = client.get(reverse("hub_admin_invite_clear_expired"))
        assert response.status_code == 405

    def it_clears_expired_invites_and_returns_the_panel(client):
        admin = _create_superuser(client, username="clearadmin")
        expired = Invite.objects.create(email="old@x.com", invited_by=admin)
        Invite.objects.filter(pk=expired.pk).update(created_at=timezone.now() - timedelta(days=30))
        fresh = Invite.objects.create(email="fresh@x.com", invited_by=admin)
        response = client.post(reverse("hub_admin_invite_clear_expired"))
        assert response.status_code == 200
        assert "Cleared 1 expired invite" in response["HX-Trigger"]
        assert not Invite.objects.filter(pk=expired.pk).exists()
        assert Invite.objects.filter(pk=fresh.pk).exists()
        assert b"fresh@x.com" in response.content

    def it_pluralizes_the_toast_for_multiple_expired(client):
        admin = _create_superuser(client, username="clearadmin")
        a = Invite.objects.create(email="a@x.com", invited_by=admin)
        b = Invite.objects.create(email="b@x.com", invited_by=admin)
        Invite.objects.filter(pk__in=[a.pk, b.pk]).update(created_at=timezone.now() - timedelta(days=30))
        response = client.post(reverse("hub_admin_invite_clear_expired"))
        assert "Cleared 2 expired invites" in response["HX-Trigger"]


def describe_admin_member_edit_role_dispatch():
    def it_promotes_to_instructor(client):
        _create_superuser(client)
        target = _create_member_user(username="becomeinst")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={
                "full_legal_name": target.member.full_legal_name,
                "preferred_name": "",
                "pronouns": "",
                "discord_handle": "",
                "about_me": "",
                "status": Member.Status.ACTIVE,
                "member_type": Member.MemberType.STANDARD,
                "role": "instructor",
                "show_in_directory": "on",
            },
        )
        assert response.status_code == 302
        target.member.refresh_from_db()
        assert target.member.fog_role == Member.FogRole.MEMBER
        assert target.member.status == Member.Status.ACTIVE
        assert target.member.instructor_slug != ""

    def it_demotes_to_guest_by_setting_status_former(client):
        _create_superuser(client)
        target = _create_member_user(username="becomeguest")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={
                "full_legal_name": target.member.full_legal_name,
                "preferred_name": "",
                "pronouns": "",
                "discord_handle": "",
                "about_me": "",
                "status": Member.Status.ACTIVE,
                "member_type": Member.MemberType.STANDARD,
                "role": "guest",
                "show_in_directory": "on",
            },
        )
        assert response.status_code == 302
        target.member.refresh_from_db()
        assert target.member.status == Member.Status.FORMER
        assert target.member.fog_role == Member.FogRole.MEMBER

    def it_initial_role_reflects_existing_instructor_record(client):
        from classes.factories import InstructorFactory

        _create_superuser(client)
        target = _create_member_user(username="alreadyinst")
        InstructorFactory(user=target)
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert response.context["form"]["role"].initial == "instructor"

    def it_initial_role_reflects_inactive_status_as_guest(client):
        _create_superuser(client)
        target = _create_member_user(username="formerguy")
        target.member.status = Member.Status.FORMER
        target.member.save(update_fields=["status"])
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert response.context["form"]["role"].initial == "guest"


def describe_admin_member_edit():
    def it_requires_login(client):
        m = _create_member_user(username="target")
        response = client.get(reverse("hub_admin_member_edit", args=[m.member.pk]))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        target = _create_member_user(username="target2")
        plain = _create_member_user(username="plain2")
        client.login(username=plain.username, password="p")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 403

    def it_renders_edit_form_for_admin(client):
        _create_superuser(client)
        target = _create_member_user(username="target3")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert b"Save member" in response.content

    def it_saves_changes_and_redirects(client):
        _create_superuser(client)
        target = _create_member_user(username="target4")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={
                "full_legal_name": "Updated Name",
                "preferred_name": "",
                "pronouns": "",
                "discord_handle": "",
                "about_me": "",
                "status": Member.Status.ACTIVE,
                "member_type": Member.MemberType.STANDARD,
                "role": Member.FogRole.MEMBER,
                "show_in_directory": "on",
            },
        )
        assert response.status_code == 302
        target.member.refresh_from_db()
        assert target.member.full_legal_name == "Updated Name"

    def it_shows_the_self_approve_discounts_toggle(client):
        _create_superuser(client)
        target = _create_member_user(username="dctoggle")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert b"Can approve their own discount codes" in response.content

    def it_grants_the_self_approve_discounts_permission(client):
        _create_superuser(client)
        target = _create_member_user(username="dcgrant")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={
                "full_legal_name": target.member.full_legal_name,
                "preferred_name": "",
                "pronouns": "",
                "discord_handle": "",
                "about_me": "",
                "status": Member.Status.ACTIVE,
                "member_type": Member.MemberType.STANDARD,
                "role": Member.FogRole.MEMBER,
                "show_in_directory": "on",
                "can_self_approve_discounts": "on",
            },
        )
        assert response.status_code == 302
        target.member.refresh_from_db()
        assert target.member.can_self_approve_discounts is True

    def it_clears_the_self_approve_discounts_permission_when_unchecked(client):
        _create_superuser(client)
        target = _create_member_user(username="dcrevoke")
        target.member.can_self_approve_discounts = True
        target.member.save(update_fields=["can_self_approve_discounts"])
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={
                "full_legal_name": target.member.full_legal_name,
                "preferred_name": "",
                "pronouns": "",
                "discord_handle": "",
                "about_me": "",
                "status": Member.Status.ACTIVE,
                "member_type": Member.MemberType.STANDARD,
                "role": Member.FogRole.MEMBER,
                "show_in_directory": "on",
            },
        )
        assert response.status_code == 302
        target.member.refresh_from_db()
        assert target.member.can_self_approve_discounts is False

    def it_re_renders_on_invalid_post(client):
        _create_superuser(client)
        target = _create_member_user(username="target5")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={"full_legal_name": ""},
        )
        assert response.status_code == 200

    def it_404s_for_unknown_member(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_member_edit", args=[99999]))
        assert response.status_code == 404


def describe_admin_site_settings():
    def it_requires_login(client):
        response = client.get(reverse("hub_admin_site_settings"))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="plain3")
        client.login(username=user.username, password="p")
        response = client.get(reverse("hub_admin_site_settings"))
        assert response.status_code == 403

    def it_renders_settings_form_for_admin(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings"))
        assert response.status_code == 200
        assert b"Site Settings" in response.content

    def it_saves_changes_and_redirects(client):
        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "signage_default_slide_seconds": "12",
                "signage_event_days_ahead": "30",
                "feeds-TOTAL_FORMS": "0",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        config = SiteConfiguration.load()
        assert config.registration_mode == SiteConfiguration.RegistrationMode.OPEN

    def it_keeps_the_save_button_inside_the_settings_form(client):
        # Regression: the Sync Now control used to be its own nested <form>, which is invalid
        # HTML — the browser closed the main settings form at the nested </form>, orphaning the
        # Save button so every save silently did nothing. (Test-client POSTs don't parse HTML, so
        # the bug was invisible to the other specs.) Assert no nested form and the Save button +
        # Sync Now control live inside the settings form.
        _create_superuser(client)
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        start = html.index('<form method="post" id="site-settings-form"')
        main_form = html[start : html.index("</form>", start)]
        assert "<form" not in main_form[1:], "no nested <form> inside the settings form"
        assert "Save settings" in main_form, "Save button must be inside the settings form"
        assert 'name="action" value="sync_now"' in main_form, "Sync Now submits the main form"
        assert 'id="sync-now-form"' not in html, "the old nested Sync Now form must be gone"

    def it_re_renders_with_errors_on_invalid_settings(client):
        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": "not-a-real-mode",  # invalid choice → form invalid
                "classes_calendar_color": "#abcdef",
                "feeds-TOTAL_FORMS": "0",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 200  # invalid → re-render with errors, not a redirect

    def it_creates_calendar_feed_from_formset(client):
        from core.models import CalendarFeed

        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "signage_default_slide_seconds": "12",
                "signage_event_days_ahead": "30",
                "feeds-TOTAL_FORMS": "1",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
                "feeds-0-name": "Workshops",
                "feeds-0-ical_url": "https://example.com/workshops.ics",
                "feeds-0-color": "#FF8800",
            },
        )
        assert response.status_code == 302
        assert CalendarFeed.objects.filter(name="Workshops").exists()

    def it_discards_blank_calendar_feed_rows(client):
        from core.models import CalendarFeed

        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "signage_default_slide_seconds": "12",
                "signage_event_days_ahead": "30",
                "feeds-TOTAL_FORMS": "1",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
                "feeds-0-name": "",
                "feeds-0-ical_url": "",
                "feeds-0-color": "#EEB44B",
            },
        )
        assert response.status_code == 302
        assert CalendarFeed.objects.count() == 0

    def it_deletes_calendar_feed_via_formset(client):
        from core.models import CalendarFeed

        _create_superuser(client)
        feed = CalendarFeed.objects.create(name="Old", ical_url="https://example.com/old.ics", color="#EEB44B")
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "signage_default_slide_seconds": "12",
                "signage_event_days_ahead": "30",
                "feeds-TOTAL_FORMS": "1",
                "feeds-INITIAL_FORMS": "1",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
                "feeds-0-id": str(feed.pk),
                "feeds-0-name": feed.name,
                "feeds-0-ical_url": feed.ical_url,
                "feeds-0-color": feed.color,
                "feeds-0-DELETE": "on",
            },
        )
        assert response.status_code == 302
        assert not CalendarFeed.objects.filter(pk=feed.pk).exists()

    def it_renders_calendar_tab_when_requested(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=calendar")
        assert response.status_code == 200
        assert response.context["active_tab"] == "calendar"

    def it_re_renders_on_invalid_post(client):
        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={"registration_mode": "not-a-real-mode"},
        )
        assert response.status_code == 200


def describe_admin_site_settings_legacy_cms():
    def it_renders_legacy_cms_tab_when_active(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=legacy-cms")
        assert response.status_code == 200
        assert b"Legacy CMS" in response.content
        assert response.context["active_tab"] == "legacy-cms"

    def it_syncs_now_on_post_with_sync_now_action(client):
        from unittest.mock import patch

        _create_superuser(client)
        with patch("classes.import_service.sync_legacy_cms", return_value=5) as mock_sync:
            response = client.post(
                reverse("hub_admin_site_settings"),
                data={"action": "sync_now"},
            )
        assert response.status_code == 302
        assert "tab=legacy-cms" in response["Location"]
        mock_sync.assert_called_once()

    def it_handles_sync_now_failure_gracefully(client):
        from unittest.mock import patch

        _create_superuser(client)
        with patch("classes.import_service.sync_legacy_cms", side_effect=RuntimeError("connection refused")):
            response = client.post(
                reverse("hub_admin_site_settings"),
                data={"action": "sync_now"},
            )
        assert response.status_code == 302
        assert "tab=legacy-cms" in response["Location"]

    def it_includes_instructor_sync_rows_in_context(client):
        from classes.factories import InstructorFactory

        _create_superuser(client)
        InstructorFactory(full_legal_name="Test Instructor")
        response = client.get(reverse("hub_admin_site_settings") + "?tab=legacy-cms")
        assert response.status_code == 200
        rows = response.context["instructor_sync_rows"]
        assert any(row["instructor"].display_name == "Test Instructor" for row in rows)


def describe_admin_site_settings_features():
    def it_renders_the_features_tab_when_requested(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=features")
        assert response.status_code == 200
        assert response.context["active_tab"] == "features"
        assert b"Enable My Tab &amp; Payments" in response.content
        assert b"Allow class registration" in response.content

    def it_renders_the_help_and_wiki_sidebar_toggles(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=features")
        assert response.status_code == 200
        assert b"Show Help in the sidebar" in response.content
        assert b"Show Wiki link in the sidebar" in response.content

    def it_renders_the_feature_fields_only_once(client):
        # Excluded from the General loop — each control renders only in the Features panel.
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings"))
        assert response.content.count(b'id="id_tab_payments_enabled"') == 1
        assert response.content.count(b'id="id_class_registration_enabled"') == 1
        assert response.content.count(b'id="id_class_registration_disabled_note"') == 1
        assert response.content.count(b'id="id_help_page_enabled"') == 1
        assert response.content.count(b'id="id_wiki_link_enabled"') == 1

    def it_saves_the_feature_switches(client):
        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "signage_default_slide_seconds": "12",
                "signage_event_days_ahead": "30",
                # Both switches omitted → unchecked → False.
                "class_registration_disabled_note": "We'll be back soon.",
                "submitted_tab": "features",
                "feeds-TOTAL_FORMS": "0",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        assert "tab=features" in response["Location"]
        config = SiteConfiguration.load()
        assert config.tab_payments_enabled is False
        assert config.class_registration_enabled is False
        assert config.class_registration_disabled_note == "We'll be back soon."

    def it_keeps_the_switches_on_when_checked(client):
        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "signage_default_slide_seconds": "12",
                "signage_event_days_ahead": "30",
                "tab_payments_enabled": "on",
                "class_registration_enabled": "on",
                "class_registration_disabled_note": "",
                "feeds-TOTAL_FORMS": "0",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        config = SiteConfiguration.load()
        assert config.tab_payments_enabled is True
        assert config.class_registration_enabled is True


def describe_admin_site_settings_announcements():
    # The plain sitewide composer moved to the /announcements/compose/ wizard (covered by
    # tests/hub/announcement_compose_spec.py + tests/membership/announcement_draft_spec.py).
    # The Announcements tab now links to it and still hosts the sectioned Release composer.
    def it_renders_the_announcements_tab_linking_to_the_wizard(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=announcements")
        assert response.status_code == 200
        assert reverse("hub_compose").encode() in response.content
        assert b"Compose an announcement" in response.content

    def it_prefills_a_release_draft(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=announcements&draft=release")
        assert response.status_code == 200
        # "Draft from latest release" enters the sectioned Release composer with a
        # prefilled subject from the current release line's changelog.
        assert response.context["release_mode"] is True
        assert b"What&#x27;s new at Past Lives:" in response.content


def describe_fog_admin_required():
    def it_redirects_anonymous_users_to_login(rf):
        from django.contrib.auth.models import AnonymousUser

        from hub.view_as import fog_admin_required

        @fog_admin_required
        def view(request):
            return "ok"

        request = rf.get("/")
        request.user = AnonymousUser()
        response = view(request)
        # @login_required is the outermost wrapper, so anonymous users redirect
        # before the view_as admin check ever runs.
        assert response.status_code == 302

    def it_returns_403_for_authenticated_non_admin(rf):
        from hub.view_as import fog_admin_required

        @fog_admin_required
        def view(request):
            return "ok"

        user = _create_member_user(username="nonadmin_decorator")
        request = rf.get("/")
        request.user = user
        # No view_as attribute attached — simulates the inner check rejecting
        # a user who doesn't actually hold admin.
        response = view(request)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Hub-native email-address management on the member edit page
# ---------------------------------------------------------------------------


def _target_with_email(*, username: str = "edittarget") -> User:
    """An editable member with a single primary, verified EmailAddress."""
    from allauth.account.models import EmailAddress

    user = _create_member_user(username=username)
    EmailAddress.objects.filter(user=user).delete()
    EmailAddress.objects.create(user=user, email=f"{username}@x.com", verified=True, primary=True)
    return user


def describe_admin_member_email_panel():
    def it_renders_emails_and_the_add_form_for_a_linked_member(client):
        _create_superuser(client)
        target = _target_with_email()
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert b"Email addresses" in response.content
        assert b"edittarget@x.com" in response.content
        assert reverse("hub_admin_member_email_add", args=[target.member.pk]).encode() in response.content

    def it_shows_the_add_email_prompt_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="airtable-only@x.com")
        response = client.get(reverse("hub_admin_member_edit", args=[member.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        # The dead-end "No linked user yet" copy is gone (every member is now a user).
        assert "No linked user yet" not in content
        assert "Add an email to enable sign-in" in content


def describe_admin_member_email_add():
    def it_adds_a_verified_non_primary_alias(client):
        from allauth.account.models import EmailAddress

        _create_superuser(client)
        target = _target_with_email()
        response = client.post(
            reverse("hub_admin_member_email_add", args=[target.member.pk]),
            data={"email": "alt@example.com"},
        )
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[target.member.pk])
        created = EmailAddress.objects.get(user=target, email="alt@example.com")
        assert created.verified is True
        assert created.primary is False

    def it_rejects_a_duplicate_without_creating(client):
        from allauth.account.models import EmailAddress

        _create_superuser(client)
        target = _target_with_email()
        response = client.post(
            reverse("hub_admin_member_email_add", args=[target.member.pk]),
            data={"email": "edittarget@x.com"},
        )
        assert response.status_code == 302
        assert EmailAddress.objects.filter(user=target).count() == 1

    def it_forbids_a_plain_member(client):
        target = _target_with_email(username="add_forbid")
        plain = _create_member_user(username="add_plain")
        client.login(username=plain.username, password="p")
        response = client.post(
            reverse("hub_admin_member_email_add", args=[target.member.pk]),
            data={"email": "nope@example.com"},
        )
        assert response.status_code == 403

    def it_redirects_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="airtable@x.com")
        response = client.post(
            reverse("hub_admin_member_email_add", args=[member.pk]),
            data={"email": "new@example.com"},
        )
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member.pk])

    def it_rejects_get(client):
        _create_superuser(client)
        target = _target_with_email(username="add_get")
        response = client.get(reverse("hub_admin_member_email_add", args=[target.member.pk]))
        assert response.status_code == 405


def describe_admin_member_email_actions():
    def _alias(user, email, *, verified=True, primary=False):
        from allauth.account.models import EmailAddress

        return EmailAddress.objects.create(user=user, email=email, verified=verified, primary=primary)

    def it_removes_a_non_primary_alias(client):
        from allauth.account.models import EmailAddress

        _create_superuser(client)
        target = _target_with_email(username="rm")
        alias = _alias(target, "gone@example.com")
        response = client.post(
            reverse("hub_admin_member_email_remove", args=[target.member.pk, alias.pk]),
        )
        assert response.status_code == 302
        assert not EmailAddress.objects.filter(pk=alias.pk).exists()

    def it_promotes_a_verified_alias_to_primary(client):
        _create_superuser(client)
        target = _target_with_email(username="sp")
        alias = _alias(target, "next@example.com", verified=True, primary=False)
        response = client.post(
            reverse("hub_admin_member_email_set_primary", args=[target.member.pk, alias.pk]),
        )
        assert response.status_code == 302
        alias.refresh_from_db()
        assert alias.primary is True

    def it_toggles_verified(client):
        _create_superuser(client)
        target = _target_with_email(username="tv")
        alias = _alias(target, "unv@example.com", verified=False, primary=False)
        response = client.post(
            reverse("hub_admin_member_email_toggle_verified", args=[target.member.pk, alias.pk]),
        )
        assert response.status_code == 302
        alias.refresh_from_db()
        assert alias.verified is True

    def it_redirects_remove_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="a@x.com")
        response = client.post(reverse("hub_admin_member_email_remove", args=[member.pk, 1]))
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member.pk])

    def it_redirects_set_primary_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="b@x.com")
        response = client.post(reverse("hub_admin_member_email_set_primary", args=[member.pk, 1]))
        assert response.status_code == 302

    def it_redirects_toggle_verified_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="c@x.com")
        response = client.post(reverse("hub_admin_member_email_toggle_verified", args=[member.pk, 1]))
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# Tabbed member edit page (Details + Emails) and the "Send login invite" path
# ---------------------------------------------------------------------------


def describe_admin_member_edit_page():
    def it_renders_details_and_emails_tabs(client):
        _create_superuser(client)
        target = _create_member_user(username="tabs")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert ">Details<" in content
        assert ">Emails<" in content

    def it_header_shows_primary_email_and_not_signed_in_badge(client):
        _create_superuser(client)
        target = _target_with_email(username="hdr")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        content = response.content.decode()
        assert "hdr@x.com" in content
        # The apostrophe in "Hasn't" is HTML-escaped in the rendered page.
        assert "signed in yet" in content
        assert response.context["status_label"] == "Hasn't signed in yet"
        assert response.context["primary_email"] == "hdr@x.com"

    def it_header_shows_signed_in_badge_and_hides_invite_when_logged_in(client):
        _create_superuser(client)
        target = _target_with_email(username="seen")
        target.last_login = timezone.now()
        target.save(update_fields=["last_login"])
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        content = response.content.decode()
        assert "Signed in" in content
        assert "Send login invite" not in content

    def it_offers_send_login_invite_when_not_signed_in(client):
        _create_superuser(client)
        target = _target_with_email(username="invitee")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        content = response.content.decode()
        assert "Send login invite" in content
        assert reverse("hub_admin_member_send_login_invite", args=[target.member.pk]) in content

    def it_no_longer_shows_the_no_linked_user_string(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="airtable-only@x.com")
        response = client.get(reverse("hub_admin_member_edit", args=[member.pk]))
        assert "No linked user yet" not in response.content.decode()

    def it_renders_a_confirm_modal_for_email_removal(client):
        _create_superuser(client)
        target = _target_with_email(username="confmodal")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        content = response.content.decode()
        # Remove opens a confirm modal rather than posting directly (repo standard).
        assert "open-confirm" in content
        assert "remove-email-" in content


def describe_admin_member_send_login_invite():
    def it_requires_login(client):
        m = _create_member_user(username="sli1")
        response = client.post(reverse("hub_admin_member_send_login_invite", args=[m.member.pk]))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        target = _create_member_user(username="sli_t")
        plain = _create_member_user(username="sli_p")
        client.login(username=plain.username, password="p")
        response = client.post(reverse("hub_admin_member_send_login_invite", args=[target.member.pk]))
        assert response.status_code == 403

    def it_rejects_get(client):
        _create_superuser(client)
        target = _create_member_user(username="sli_get")
        response = client.get(reverse("hub_admin_member_send_login_invite", args=[target.member.pk]))
        assert response.status_code == 405

    def it_sends_a_login_invite_and_returns_a_toast(client, mailoutbox):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(_pre_signup_email="firsttime@example.com")
        response = client.post(reverse("hub_admin_member_send_login_invite", args=[member.pk]))
        assert response.status_code == 204
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["firsttime@example.com"]
        assert "Login invite sent" in response["HX-Trigger"]

    def it_returns_an_error_toast_when_no_email_on_file(client, mailoutbox):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(_pre_signup_email="")
        response = client.post(reverse("hub_admin_member_send_login_invite", args=[member.pk]))
        assert response.status_code == 204
        assert "no email on file" in response["HX-Trigger"]
        assert mailoutbox == []


def describe_admin_user_edit():
    def it_requires_login(client):
        user = _create_nonmember_user(username="ue1", email="ue1@x.com")
        response = client.get(reverse("hub_admin_user_edit", args=[user.pk]))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_nonmember_user(username="ue2", email="ue2@x.com")
        plain = _create_member_user(username="ue_plain")
        client.login(username=plain.username, password="p")
        response = client.get(reverse("hub_admin_user_edit", args=[user.pk]))
        assert response.status_code == 403

    def it_renders_in_non_member_user_mode(client):
        _create_superuser(client)
        user = _create_nonmember_user(username="ue3", email="ue3@x.com")
        response = client.get(reverse("hub_admin_user_edit", args=[user.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Non-member user" in content
        assert "ue3@x.com" in content
        assert response.context["is_member"] is False
        assert "Save member" not in content

    def it_redirects_to_member_edit_when_the_user_has_a_member(client):
        _create_superuser(client)
        member_user = _create_member_user(username="ue4")
        response = client.get(reverse("hub_admin_user_edit", args=[member_user.pk]))
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member_user.member.pk])

    def it_renders_a_non_member_user_with_no_email(client):
        _create_superuser(client)
        user = _create_bare_nonmember_user(username="bareedit", email="bareedit@x.com")
        response = client.get(reverse("hub_admin_user_edit", args=[user.pk]))
        assert response.status_code == 200
        assert response.context["primary_email"] == ""


def describe_admin_user_email_actions():
    def it_adds_an_alias_to_a_non_member_user(client):
        _create_superuser(client)
        user = _create_nonmember_user(username="uea", email="uea@x.com")
        response = client.post(
            reverse("hub_admin_user_email_add", args=[user.pk]),
            data={"email": "alt-uea@x.com"},
        )
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_user_edit", args=[user.pk])
        assert EmailAddress.objects.filter(user=user, email="alt-uea@x.com").exists()

    def it_removes_an_alias_from_a_non_member_user(client):
        _create_superuser(client)
        user = _create_nonmember_user(username="uer", email="uer@x.com")
        alias = EmailAddress.objects.create(user=user, email="gone-uer@x.com", verified=True, primary=False)
        response = client.post(reverse("hub_admin_user_email_remove", args=[user.pk, alias.pk]))
        assert response.status_code == 302
        assert not EmailAddress.objects.filter(pk=alias.pk).exists()

    def it_sets_a_non_member_users_primary(client):
        _create_superuser(client)
        user = _create_nonmember_user(username="ues", email="ues@x.com")
        alias = EmailAddress.objects.create(user=user, email="next-ues@x.com", verified=True, primary=False)
        response = client.post(reverse("hub_admin_user_email_set_primary", args=[user.pk, alias.pk]))
        assert response.status_code == 302
        alias.refresh_from_db()
        assert alias.primary is True

    def it_toggles_verified_for_a_non_member_user(client):
        _create_superuser(client)
        user = _create_nonmember_user(username="uet", email="uet@x.com")
        alias = EmailAddress.objects.create(user=user, email="unv-uet@x.com", verified=False, primary=False)
        response = client.post(reverse("hub_admin_user_email_toggle_verified", args=[user.pk, alias.pk]))
        assert response.status_code == 302
        alias.refresh_from_db()
        assert alias.verified is True

    def it_redirects_add_to_member_edit_when_the_user_has_a_member(client):
        _create_superuser(client)
        member_user = _create_member_user(username="uem")
        response = client.post(
            reverse("hub_admin_user_email_add", args=[member_user.pk]),
            data={"email": "x@x.com"},
        )
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member_user.member.pk])

    def it_redirects_remove_to_member_edit_when_the_user_has_a_member(client):
        _create_superuser(client)
        member_user = _create_member_user(username="uem_r")
        response = client.post(reverse("hub_admin_user_email_remove", args=[member_user.pk, 1]))
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member_user.member.pk])

    def it_redirects_set_primary_to_member_edit_when_the_user_has_a_member(client):
        _create_superuser(client)
        member_user = _create_member_user(username="uem_s")
        response = client.post(reverse("hub_admin_user_email_set_primary", args=[member_user.pk, 1]))
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member_user.member.pk])

    def it_redirects_toggle_verified_to_member_edit_when_the_user_has_a_member(client):
        _create_superuser(client)
        member_user = _create_member_user(username="uem_t")
        response = client.post(reverse("hub_admin_user_email_toggle_verified", args=[member_user.pk, 1]))
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member_user.member.pk])


def describe_member_edit_inline_style_lint():
    def it_is_removed_from_the_inline_style_baseline():
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        script = (repo_root / "scripts" / "check_no_inline_style_in_extra_head.py").read_text(encoding="utf-8")
        assert '"templates/hub/admin/member_edit.html"' not in script

    def it_has_no_inline_style_and_links_external_css():
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        template = (repo_root / "templates" / "hub" / "admin" / "member_edit.html").read_text(encoding="utf-8")
        assert "<style" not in template
        assert "css/member-edit.css" in template
