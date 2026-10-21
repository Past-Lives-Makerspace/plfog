from __future__ import annotations

from collections.abc import Hashable, Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from rest_framework.fields import BooleanField
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from membership.models import Member

if TYPE_CHECKING:
    from django.http import HttpRequest


class IsFogAdmin(BasePermission):
    """Grants access only to members with the admin fog role or Django superusers."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            return request.user.member.fog_role == Member.FogRole.ADMIN
        except Member.DoesNotExist:
            return False


class IsFogAdminOrReadOnly(BasePermission):
    """Authentication required for all methods. Reads for any member; writes for fog admins only."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        if request.user.is_superuser:
            return True
        try:
            return request.user.member.fog_role == Member.FogRole.ADMIN
        except Member.DoesNotExist:
            return False


class MayPublishWithoutAnnouncing(BasePermission):
    """A create that sends ``announce: false`` needs whoever may administer events.

    The same capability as the composer's "Publish without announcing to members" switch
    (:func:`membership.permissions.can_administer_events`): an admin or a Calendar
    Administrator, and a superuser through the admin role it always carries. Every other
    request, and a create that announces, passes untouched; a value the serializer would
    refuse passes too, and gets its 400 there.
    """

    message = "Only an events administrator can publish an event without announcing it."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if getattr(view, "action", None) != "create" or not _asks_not_to_announce(request):
            return True
        return _administers_events(request)


def _asks_not_to_announce(request: Request) -> bool:
    """Whether the body's ``announce`` reads as false the way the serializer's BooleanField does."""
    data = request.data
    if not isinstance(data, Mapping):
        return False
    value = data.get("announce", True)
    return isinstance(value, Hashable) and value in BooleanField.FALSE_VALUES


def _administers_events(request: Request) -> bool:
    """:func:`can_administer_events` for an API caller, read off the user DRF authenticated.

    ``ViewAsMiddleware`` runs before DRF's token authentication, so ``request.view_as``
    describes an anonymous visitor on a token call. The roles are recomputed from the
    authenticated user, with no preview: an API call has none to honour.
    """
    from hub.view_as import ViewAs, compute_actual_roles
    from membership.permissions import can_administer_events

    caller = SimpleNamespace(user=request.user, view_as=ViewAs(compute_actual_roles(request.user), None))
    return can_administer_events(cast("HttpRequest", caller))
