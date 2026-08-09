from __future__ import annotations

from typing import Any, ClassVar

from allauth.account.models import EmailAddress
from django.db.models import Prefetch
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from api.permissions import IsFogAdmin, IsFogAdminOrReadOnly
from membership.models import CommunityEvent, Guild, GuildAnnouncement, Member, MembershipPlan
from membership.serializers import (
    CommunityEventSerializer,
    GuildAnnouncementSerializer,
    GuildSerializer,
    MemberSerializer,
    MembershipPlanSerializer,
    MemberWriteSerializer,
)

_VALID_ROLES = set(Member.FogRole.values) | {"instructor", "guest"}


class MemberViewSet(viewsets.ModelViewSet):
    permission_classes: ClassVar = [IsFogAdmin]
    queryset = Member.objects.active().select_related("membership_plan", "user")
    http_method_names: ClassVar = ["get", "patch", "post", "head", "options"]

    def get_serializer_class(self) -> type[serializers.BaseSerializer]:
        if self.request.method == "PATCH":
            return MemberWriteSerializer
        return MemberSerializer

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()
        serializer = MemberWriteSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MemberSerializer(instance).data)

    @action(detail=True, methods=["post"], url_path="set-role")
    def set_role(self, request: Request, pk: str | None = None) -> Response:
        instance = self.get_object()
        role = request.data.get("role", "")
        if role not in _VALID_ROLES:
            raise serializers.ValidationError({"role": f"Invalid role. Choose from: {sorted(_VALID_ROLES)}"})
        instance.apply_admin_role(role)
        return Response(MemberSerializer(instance).data)


class GuildViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes: ClassVar = [IsFogAdminOrReadOnly]
    queryset = (
        Guild.objects.filter(is_active=True, deleted_at__isnull=True)
        .select_related("guild_lead__user")
        .prefetch_related(
            Prefetch(
                "guild_lead__user__emailaddress_set",
                queryset=EmailAddress.objects.filter(primary=True),
                to_attr="_primary_emailaddresses",
            )
        )
    )
    serializer_class = GuildSerializer


class CommunityEventViewSet(viewsets.ModelViewSet):
    permission_classes: ClassVar = [IsFogAdminOrReadOnly]
    serializer_class = CommunityEventSerializer
    queryset = CommunityEvent.objects.select_related("guild", "created_by").order_by("-starts_at")

    def perform_create(self, serializer: serializers.BaseSerializer) -> None:
        event = serializer.save(created_by=self.request.user)
        event.schedule_or_go_live(actor=self.request.user)

    def perform_update(self, serializer: serializers.BaseSerializer) -> None:
        serializer.save()


class GuildAnnouncementViewSet(viewsets.ModelViewSet):
    permission_classes: ClassVar = [IsFogAdminOrReadOnly]
    serializer_class = GuildAnnouncementSerializer
    queryset = GuildAnnouncement.objects.select_related("guild", "author").order_by("-published_at")

    def perform_create(self, serializer: serializers.BaseSerializer) -> None:
        announcement = serializer.save(
            author=self.request.user,
            moderation_state=GuildAnnouncement.ModerationState.PUBLISHED,
        )
        announcement.notify_members()


class MembershipPlanViewSet(viewsets.ModelViewSet):
    permission_classes: ClassVar = [IsFogAdmin]
    serializer_class = MembershipPlanSerializer
    queryset = MembershipPlan.objects.all().order_by("monthly_price")
