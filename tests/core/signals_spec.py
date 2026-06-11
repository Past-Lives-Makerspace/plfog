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


def describe_new_login_detection():
    def it_notifies_on_a_first_time_signature():
        from allauth.account.signals import user_logged_in

        from core.models import Notification

        user = User.objects.create_user(username="nl", email="nl@example.com")
        request = RequestFactory().get("/", HTTP_USER_AGENT="Firefox", REMOTE_ADDR="1.2.3.4")
        user_logged_in.send(sender=User, request=request, user=user)
        assert Notification.objects.filter(trigger="new_login", user=user).count() == 1

    def it_does_not_notify_on_a_known_signature():
        from allauth.account.signals import user_logged_in

        from core.models import Notification

        user = User.objects.create_user(username="nl2", email="nl2@example.com")
        request = RequestFactory().get("/", HTTP_USER_AGENT="Firefox", REMOTE_ADDR="1.2.3.4")
        user_logged_in.send(sender=User, request=request, user=user)
        user_logged_in.send(sender=User, request=request, user=user)  # same signature
        assert Notification.objects.filter(trigger="new_login", user=user).count() == 1
