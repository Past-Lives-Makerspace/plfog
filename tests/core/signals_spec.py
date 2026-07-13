"""SiteActivity is written on auth events."""

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory

from core.models import SiteActivity

pytestmark = pytest.mark.django_db


def describe_auth_activity():
    def it_logs_login():
        user = User.objects.create_user(username="u", email="u@example.com", password="pw12345!")
        from allauth.account.signals import user_logged_in

        request = RequestFactory().get("/")
        user_logged_in.send(sender=User, request=request, user=user)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.LOGIN, actor=user).exists()

    def it_logs_logout():
        user = User.objects.create_user(username="o", email="o@example.com", password="pw12345!")
        from allauth.account.signals import user_logged_out

        request = RequestFactory().get("/")
        user_logged_out.send(sender=User, request=request, user=user)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.LOGOUT, actor=user).exists()

    def it_logs_signup():
        user = User.objects.create_user(username="s", email="s@example.com")
        from allauth.account.signals import user_signed_up

        request = RequestFactory().get("/")
        user_signed_up.send(sender=User, request=request, user=user)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.MEMBER_SIGNUP, actor=user).exists()


def describe_new_member_admin_notification():
    def it_notifies_admins_on_every_signup_not_just_the_first():
        # Each signup carries a unique idempotency period (signup:<pk>), so the second
        # signup is NOT deduped against the first — admins still get alerted. With the
        # default one-shot period ("") the delivery ledger would record the event once
        # and silently drop every later signup.
        from allauth.account.signals import user_signed_up
        from django.db.models.signals import post_save
        from factory.django import mute_signals

        from core.models import Notification
        from membership.models import Member
        from tests.membership.factories import MemberFactory

        admin_member = MemberFactory(fog_role=Member.FogRole.ADMIN)
        with mute_signals(post_save):  # don't auto-mint a Member for the admin user
            admin_user = User.objects.create_user(username="fogadmin", email="fogadmin@example.com")
        admin_member.user = admin_user
        admin_member.save(update_fields=["user"])

        request = RequestFactory().get("/")
        first = User.objects.create_user(username="join1", email="join1@example.com")
        user_signed_up.send(sender=User, request=request, user=first)
        second = User.objects.create_user(username="join2", email="join2@example.com")
        user_signed_up.send(sender=User, request=request, user=second)

        assert Notification.objects.filter(user=admin_user, trigger="new_member_joined").count() == 2
