from __future__ import annotations

from rest_framework.routers import DefaultRouter

from membership.api_views import (
    CommunityEventViewSet,
    GuildAnnouncementViewSet,
    GuildViewSet,
    MembershipPlanViewSet,
    MemberViewSet,
)

router = DefaultRouter()
router.register("members", MemberViewSet, basename="member")
router.register("guilds", GuildViewSet, basename="guild")
router.register("events", CommunityEventViewSet, basename="event")
router.register("announcements", GuildAnnouncementViewSet, basename="announcement")
router.register("plans", MembershipPlanViewSet, basename="plan")

urlpatterns = router.urls
