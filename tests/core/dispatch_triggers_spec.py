"""Notification dispatch at core events — invite_accepted, new_member_joined."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory

from core.models import Invite, Notification
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_invite_accepted_dispatch():
    def it_dispatches_to_invited_by_user_on_mark_accepted():
        invited_by = User.objects.create_user(username="inviter", email="inviter@example.com")
        member = MemberFactory()
        invite = Invite.objects.create(email="invitee@example.com", invited_by=invited_by, member=member)

        invite.mark_accepted()

        assert Notification.objects.filter(user=invited_by, trigger="invite_accepted").exists()

    def it_does_not_dispatch_when_invited_by_is_none():
        member = MemberFactory()
        invite = Invite.objects.create(email="noinviter@example.com", invited_by=None, member=member)

        invite.mark_accepted()

        assert not Notification.objects.filter(trigger="invite_accepted").exists()


def describe_new_member_joined_dispatch():
    def it_dispatches_to_staff_users_on_signup():
        staff = User.objects.create_user(username="staff1", email="staff1@example.com", is_staff=True)
        new_user = User.objects.create_user(username="newbie", email="newbie@example.com")

        from allauth.account.signals import user_signed_up

        request = RequestFactory().get("/")
        user_signed_up.send(sender=User, request=request, user=new_user)

        assert Notification.objects.filter(user=staff, trigger="new_member_joined").exists()

    def it_does_not_dispatch_to_non_staff_users_on_signup():
        regular = User.objects.create_user(username="regular1", email="regular1@example.com", is_staff=False)
        new_user = User.objects.create_user(username="newbie2", email="newbie2@example.com")

        from allauth.account.signals import user_signed_up

        request = RequestFactory().get("/")
        user_signed_up.send(sender=User, request=request, user=new_user)

        assert not Notification.objects.filter(user=regular, trigger="new_member_joined").exists()
