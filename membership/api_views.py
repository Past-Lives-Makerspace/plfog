from __future__ import annotations

from typing import Any, ClassVar

from allauth.account.models import EmailAddress
from django.db.models import Prefetch
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from api.permissions import IsFogAdmin, IsFogAdminOrReadOnly, MayPublishWithoutAnnouncing
from membership.models import CommunityEvent, Guild, GuildAnnouncement, InvalidEventTransition, Member, MembershipPlan
from membership.serializers import (
    CommunityEventSerializer,
    GuildAnnouncementSerializer,
    GuildSerializer,
    MemberSerializer,
    MembershipPlanSerializer,
    MemberWriteSerializer,
)

# "instructor" is a permission now (granted via the member-edit Permissions tab), NOT a
# role — it must not be set through set-role, whose "instructor" branch also reset
# fog_role/status and so would silently demote an admin/officer or reactivate a former member.
_VALID_ROLES = set(Member.FogRole.values) | {"guest"}


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
    """``/api/v1/events/``: read for any member, write for admins.

    ``POST`` publishes (or schedules, for a future ``publish_at``) and announces exactly as
    the app's composer does. It takes one optional boolean beyond the stored fields,
    ``announce`` (default ``true``): ``false`` puts the event on the calendars without the
    member announcement, to be sent later with Announce now on Manage Events or the
    ``announce`` action. Only a caller who may administer events (``MayPublishWithoutAnnouncing``:
    an admin, a Calendar Administrator or a superuser) may send ``false``; anyone else gets a
    403 and nothing is created. The field is ignored on updates and never appears in a response.

    Before rolling back to a release without migration 0241, unpublish every held event
    (``announcement_held``): the older code has no hold, and its 15 minute #calendar
    announcer and reminders would announce them all.
    """

    permission_classes: ClassVar = [IsFogAdminOrReadOnly, MayPublishWithoutAnnouncing]
    serializer_class = CommunityEventSerializer
    # A draft (a copy, or an event taken down on Manage Events, #831) is nobody's to read here;
    # the other unpublished states keep the behaviour this API always had.
    queryset = (
        CommunityEvent.objects.exclude(moderation_state=CommunityEvent.ModerationState.DRAFT)
        .select_related("guild", "created_by")
        .order_by("-starts_at")
    )

    def perform_create(self, serializer: serializers.BaseSerializer) -> None:
        announce = serializer.validated_data.pop("announce")
        user = self.request.user
        event = serializer.save(created_by=user)
        event.schedule_or_go_live(actor=user, announce=announce)

    def perform_update(self, serializer: serializers.BaseSerializer) -> None:
        serializer.validated_data.pop("announce", None)
        serializer.save()

    @action(detail=True, methods=["post"], permission_classes=[IsFogAdmin])
    def sync(self, request: Request, pk: str | None = None) -> Response:
        """Re-push this event to Google Calendar and Discord. Idempotent."""
        event = self.get_object()
        event.schedule_or_go_live(actor=request.user)
        return Response(CommunityEventSerializer(event).data)

    @action(detail=True, methods=["post"], permission_classes=[IsFogAdmin])
    def announce(self, request: Request, pk: str | None = None) -> Response:
        """Fire the in-app, email, and Discord announcement without re-pushing to Google Calendar.

        A held event goes through ``announce_now``, so the hold is released with the send and
        a later Announce now on Manage Events sends nothing more. A held event that is not live
        yet is refused with a 400, as Announce now refuses it.
        """
        event = self.get_object()
        if not event.announcement_held:
            event.announce(actor=request.user)
        else:
            try:
                event.announce_now(actor=request.user)
            except InvalidEventTransition as exc:
                raise ValidationError({"detail": "Only a published event can be announced."}) from exc
        return Response(CommunityEventSerializer(event).data)


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
