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
