"""BDD specs for guest role in view_as."""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from hub.view_as import ROLE_GUEST, ViewAs, compute_actual_roles


def describe_guest_role():
    def it_is_assigned_to_anonymous_users(db):
        roles = compute_actual_roles(AnonymousUser())
        assert roles == frozenset({ROLE_GUEST})

    def it_exposes_is_guest_on_view_as(db):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.session = {}
        view_as = ViewAs.for_request(request)
        assert view_as.is_guest is True
        assert view_as.is_admin is False
        assert view_as.is_member is False
