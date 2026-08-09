from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from membership.models import Member


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
