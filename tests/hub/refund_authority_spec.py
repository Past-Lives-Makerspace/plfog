"""BDD specs for the new capability gates in hub.view_as.

``refund_authority_required`` has no wired URL until the refund UI phase, so it
is exercised directly at the decorator level here; ``billing_admin_access_required``
gets its member-less edge covered the same way (the client-level dashboard specs
live in tests/billing/dashboard_gating_spec.py).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.db.models.signals import post_save
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from factory.django import mute_signals

from hub.view_as import (
    ROLE_MEMBER,
    ViewAs,
    billing_admin_access_required,
    has_billing_admin_access,
    refund_authority_required,
)
from membership.models import AdminCapability

pytestmark = pytest.mark.django_db


@refund_authority_required
def _refund_probe(request: HttpRequest) -> HttpResponse:
    return HttpResponse("refund ok")


@billing_admin_access_required
def _billing_probe(request: HttpRequest) -> HttpResponse:
    return HttpResponse("billing ok")


def _request_as(user) -> HttpRequest:
    request = RequestFactory().get("/probe/")
    request.user = user
    request.view_as = ViewAs.for_request(request)  # type: ignore[attr-defined]
    return request


def _plain_user(username: str) -> User:
    return User.objects.create_user(username=username, email=f"{username}@example.com")


def describe_refund_authority_required():
    def it_passes_a_fog_admin():
        admin = User.objects.create_superuser(username="boss", email="boss@example.com", password="x")
        response = _refund_probe(_request_as(admin))
        assert response.status_code == 200

    def it_passes_a_refunds_holder():
        user = _plain_user("refunder")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)  # type: ignore[attr-defined]
        response = _refund_probe(_request_as(user))
        assert response.status_code == 200

    def it_forbids_a_member_without_the_grant():
        response = _refund_probe(_request_as(_plain_user("bystander")))
        assert response.status_code == 403
        assert b"Refunds require" in response.content

    def it_forbids_a_billing_administrator_without_the_refunds_grant():
        # Panel access and refund authority are separate grants by design.
        user = _plain_user("billeronly")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)  # type: ignore[attr-defined]
        response = _refund_probe(_request_as(user))
        assert response.status_code == 403

    def it_redirects_anonymous_users_to_login():
        response = _refund_probe(_request_as(AnonymousUser()))
        assert response.status_code == 302


def describe_billing_admin_access_required():
    def it_forbids_a_user_with_no_linked_member():
        with mute_signals(post_save):
            user = User.objects.create_user(username="memberless", email="memberless@example.com")
        response = _billing_probe(_request_as(user))
        assert response.status_code == 403

    def it_passes_a_billing_administrator():
        user = _plain_user("billerok")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)  # type: ignore[attr-defined]
        response = _billing_probe(_request_as(user))
        assert response.status_code == 200


def describe_has_billing_admin_access():
    def it_is_true_for_a_fog_admin():
        admin = User.objects.create_superuser(username="hba_admin", email="hba_admin@example.com", password="x")
        assert has_billing_admin_access(_request_as(admin)) is True

    def it_stays_true_for_a_fog_admin_previewing_as_a_member():
        # It reads has_actual, so a view-as preview neither grants nor revokes it.
        admin = User.objects.create_superuser(username="hba_admin2", email="hba_admin2@example.com", password="x")
        request = _request_as(admin)
        request.view_as = ViewAs(actual=request.view_as.actual, picked=ROLE_MEMBER)  # type: ignore[attr-defined]
        assert has_billing_admin_access(request) is True

    def it_is_true_for_a_billing_administrator():
        user = _plain_user("hba_biller")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)  # type: ignore[attr-defined]
        assert has_billing_admin_access(_request_as(user)) is True

    def it_is_false_for_a_plain_member():
        assert has_billing_admin_access(_request_as(_plain_user("hba_plain"))) is False

    def it_is_false_for_a_memberless_user():
        with mute_signals(post_save):
            user = User.objects.create_user(username="hba_memberless", email="hba_memberless@example.com")
        assert has_billing_admin_access(_request_as(user)) is False
