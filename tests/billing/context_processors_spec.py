"""BDD-style tests for billing context processor."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory

from billing.context_processors import tab_context
from billing.models import Tab
from core.models import SiteConfiguration
from membership.models import Member
from tests.billing.factories import TabEntryFactory, TabFactory

pytestmark = pytest.mark.django_db


def describe_tab_context():
    def it_returns_empty_dict_for_anonymous_user(rf: RequestFactory):
        request = rf.get("/")
        request.user = AnonymousUser()

        result = tab_context(request)

        assert result == {}

    def it_returns_empty_dict_when_no_member_linked(rf: RequestFactory):
        user = User.objects.create_user(username="no_member", password="pass")
        Member.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)
        request = rf.get("/")
        request.user = user

        result = tab_context(request)

        assert result == {}

    def it_creates_tab_lazily_for_member(rf: RequestFactory):
        user = User.objects.create_user(username="with_member", password="pass")
        request = rf.get("/")
        request.user = user

        assert not Tab.objects.filter(member=user.member).exists()

        result = tab_context(request)

        assert Tab.objects.filter(member=user.member).exists()
        assert result["tab_balance"] == Decimal("0.00")
        assert result["tab_is_locked"] is False
        assert result["tab_has_payment_method"] is False

    def it_returns_empty_dict_when_tab_payments_disabled(rf: RequestFactory):
        config = SiteConfiguration.load()
        config.tab_payments_enabled = False
        config.save()
        user = User.objects.create_user(username="payments_off", password="pass")
        TabFactory(member=user.member)
        request = rf.get("/")
        request.user = user

        result = tab_context(request)

        assert result == {}

    def it_returns_correct_balance(rf: RequestFactory):
        user = User.objects.create_user(username="has_tab", password="pass")
        tab = TabFactory(member=user.member)
        TabEntryFactory(tab=tab, amount=Decimal("42.50"))
        request = rf.get("/")
        request.user = user

        result = tab_context(request)

        assert result["tab_balance"] == Decimal("42.50")

    def it_returns_locked_status(rf: RequestFactory):
        user = User.objects.create_user(username="locked", password="pass")
        TabFactory(member=user.member, is_locked=True)
        request = rf.get("/")
        request.user = user

        result = tab_context(request)

        assert result["tab_is_locked"] is True

    def it_returns_payment_method_status(rf: RequestFactory):
        user = User.objects.create_user(username="has_pm", password="pass")
        TabFactory(member=user.member, stripe_payment_method_id="pm_test_123")
        request = rf.get("/")
        request.user = user

        result = tab_context(request)

        assert result["tab_has_payment_method"] is True
