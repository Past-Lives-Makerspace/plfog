"""Notification dispatch at billing events — tab_charged, tab_charge_failed,
tab_entry_added, tab_approaching_limit."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from factory.django import mute_signals

from billing.notifications import notify_admin_charge_failed, send_receipt
from core.models import Notification
from tests.billing.factories import BillingSettingsFactory, TabChargeFactory, TabFactory, ProductFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _user_for_member(member):
    """Create and link a User to an existing Member."""
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"m{member.pk}", email=f"m{member.pk}@example.com")
    member.user = user
    member.save(update_fields=["user"])
    return user


def describe_tab_charged_dispatch():
    def it_creates_in_app_notification_on_send_receipt():
        member = MemberFactory(_pre_signup_email="charged@example.com")
        user = _user_for_member(member)
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status="succeeded", amount=Decimal("50.00"))

        send_receipt(charge)

        assert Notification.objects.filter(user=user, trigger="tab_charged").exists()

    def it_does_not_create_notification_when_member_has_no_user():
        member = MemberFactory(_pre_signup_email="nousertab@example.com")
        # No user linked
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status="succeeded", amount=Decimal("50.00"))

        send_receipt(charge)

        assert not Notification.objects.filter(trigger="tab_charged").exists()


def describe_tab_charge_failed_dispatch():
    def it_creates_in_app_notification_on_notify_admin_charge_failed():
        member = MemberFactory(_pre_signup_email="failed@example.com")
        user = _user_for_member(member)
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status="failed", amount=Decimal("75.00"))

        notify_admin_charge_failed(charge)

        assert Notification.objects.filter(user=user, trigger="tab_charge_failed").exists()

    def it_does_not_create_notification_when_member_has_no_user():
        member = MemberFactory(_pre_signup_email="nouserfail@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(tab=tab, status="failed", amount=Decimal("75.00"))

        notify_admin_charge_failed(charge)

        assert not Notification.objects.filter(trigger="tab_charge_failed").exists()


def describe_tab_entry_added_dispatch():
    def it_creates_notification_when_admin_adds_entry():
        BillingSettingsFactory()
        member = MemberFactory(_pre_signup_email="entry@example.com")
        user = _user_for_member(member)
        tab = TabFactory(member=member)
        admin = User.objects.create_user(username="admin_entry", email="admin_entry@example.com")
        product = ProductFactory()

        tab.add_entry(
            description="Laser time", amount=Decimal("20.00"), added_by=admin, is_self_service=False, product=product
        )

        assert Notification.objects.filter(user=user, trigger="tab_entry_added").exists()

    def it_does_not_create_notification_for_self_service_entry():
        BillingSettingsFactory()
        member = MemberFactory(_pre_signup_email="self@example.com")
        user = _user_for_member(member)
        tab = TabFactory(member=member)
        product = ProductFactory()

        tab.add_entry(description="Self service", amount=Decimal("5.00"), is_self_service=True, product=product)

        assert not Notification.objects.filter(user=user, trigger="tab_entry_added").exists()

    def it_does_not_create_notification_when_no_added_by():
        BillingSettingsFactory()
        member = MemberFactory(_pre_signup_email="noadmin@example.com")
        user = _user_for_member(member)
        tab = TabFactory(member=member)
        product = ProductFactory()

        tab.add_entry(
            description="Auto entry", amount=Decimal("5.00"), added_by=None, is_self_service=False, product=product
        )

        assert not Notification.objects.filter(user=user, trigger="tab_entry_added").exists()


def describe_tab_approaching_limit_dispatch():
    def it_creates_notification_when_balance_reaches_80_percent_of_limit():
        BillingSettingsFactory(default_tab_limit=Decimal("100.00"))
        member = MemberFactory(_pre_signup_email="limit@example.com")
        user = _user_for_member(member)
        # Set a custom limit for predictability
        tab = TabFactory(member=member, tab_limit=Decimal("100.00"))
        product = ProductFactory()

        # Add entry that brings balance to exactly 80.00 (80% of 100)
        tab.add_entry(description="Limit test", amount=Decimal("80.00"), product=product)

        assert Notification.objects.filter(user=user, trigger="tab_approaching_limit").exists()

    def it_does_not_create_notification_when_balance_below_80_percent():
        BillingSettingsFactory(default_tab_limit=Decimal("100.00"))
        member = MemberFactory(_pre_signup_email="belowlimit@example.com")
        user = _user_for_member(member)
        tab = TabFactory(member=member, tab_limit=Decimal("100.00"))
        product = ProductFactory()

        # 79% — should not trigger
        tab.add_entry(description="Below limit", amount=Decimal("79.00"), product=product)

        assert not Notification.objects.filter(user=user, trigger="tab_approaching_limit").exists()
