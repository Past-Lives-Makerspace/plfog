"""BDD specs for the self-service account-deletion view (hub.views.account_delete).

Login-gated and POST-only. On the correct typed confirmation it anonymizes the member,
locks the User, and signs them out; on a wrong confirmation it deletes nothing and keeps
the member signed in.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member_user(username: str) -> User:
    """A logged-in-able User whose Member is auto-provisioned (a plan must exist first)."""
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")


def _unlinked_user(username: str) -> User:
    """A User whose auto-created Member row has been removed, to reach the no-member guard."""
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    Member.objects.filter(user=user).delete()
    return user


def describe_account_delete():
    def it_redirects_an_anonymous_visitor_to_login():
        response = Client().post(reverse("hub_account_delete"), {"confirm_text": "DELETE"})

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_rejects_a_get_request():
        client = Client()
        client.force_login(_member_user("del_get"))

        response = client.get(reverse("hub_account_delete"))

        assert response.status_code == 405

    def it_errors_when_the_account_has_no_linked_member():
        client = Client()
        client.force_login(_unlinked_user("del_nomember"))

        response = client.post(reverse("hub_account_delete"), {"confirm_text": "DELETE"})

        assert response.status_code == 302
        assert response["Location"] == reverse("hub_user_settings")

    def describe_wrong_confirmation_text():
        def it_deletes_nothing_and_keeps_the_member_signed_in():
            user = _member_user("del_wrong")
            member = user.member
            client = Client()
            client.force_login(user)

            response = client.post(reverse("hub_account_delete"), {"confirm_text": "nope"})

            assert response.status_code == 302
            assert response["Location"] == f"{reverse('hub_user_settings')}?tab=account"
            member.refresh_from_db()
            assert member.deleted_at is None
            assert member.status == Member.Status.ACTIVE
            assert "_auth_user_id" in client.session

    def describe_correct_confirmation():
        def it_anonymizes_the_member_and_locks_the_user():
            user = _member_user("del_ok")
            member = user.member
            client = Client()
            client.force_login(user)

            response = client.post(reverse("hub_account_delete"), {"confirm_text": "DELETE"})

            assert response.status_code == 302
            assert response["Location"] == reverse("account_login")
            member.refresh_from_db()
            user.refresh_from_db()
            assert member.status == Member.Status.FORMER
            assert member.deleted_at is not None
            assert member.full_legal_name == "Deleted Member"
            assert user.is_active is False

        def it_signs_the_member_out():
            user = _member_user("del_out")
            client = Client()
            client.force_login(user)

            client.post(reverse("hub_account_delete"), {"confirm_text": "DELETE"})

            assert "_auth_user_id" not in client.session

        def it_reports_success_to_the_member():
            user = _member_user("del_msg")
            client = Client()
            client.force_login(user)

            response = client.post(reverse("hub_account_delete"), {"confirm_text": "DELETE"})

            messages = [m.message for m in get_messages(response.wsgi_request)]
            assert any("has been deleted" in m for m in messages)
