"""Specs for orientations on the portal activity log (#1330, AC 2).

Requested, confirmed, declined, cancelled and completed each post one line, linking to the
booking's respond page. A confirm decides on the row lock, so a second confirm of the same
request (a double POST, a second tab) is refused and posts nothing more.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import SiteActivity, SiteConfiguration
from membership import orientations
from membership.models import Member, OrientationBooking, OrientationError
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_WEBHOOK = "https://discord.com/api/webhooks/1330/activity"
Kind = SiteActivity.Kind


def _member(username: str) -> Member:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com").member


def _slot(lead: Member, **kwargs: object) -> object:
    guild = GuildFactory(guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    orientation_type = OrientationTypeFactory(guild=guild, name="Wheel", duration_minutes=60)
    return OrientationSlotFactory(guild=guild, orientation_type=orientation_type, **kwargs)


def _request() -> tuple[OrientationBooking, Member]:
    lead = _member("lead")
    booking = OrientationBookingFactory(
        slot=_slot(lead), member=_member("newcomer"), status=OrientationBooking.Status.REQUESTED
    )
    return booking, lead


def _turn_on() -> respx.Route:
    config = SiteConfiguration.load()
    config.discord_activity_webhook_url = _WEBHOOK
    config.save()
    return respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))


def _kinds(route: respx.Route) -> list[str]:
    return [json.loads(call.request.content)["content"].split(":")[0] for call in route.calls]


def describe_orientation_posts():
    @respx.mock
    def it_posts_a_request(django_capture_on_commit_callbacks):
        route = _turn_on()
        slot = _slot(_member("lead"), starts_at=timezone.now() + timedelta(days=3))
        with django_capture_on_commit_callbacks(execute=True):
            orientations.request_orientation(slot, _member("asker"))
        assert _kinds(route) == ["Orientation requested"]

    @respx.mock
    def it_posts_a_confirmation_once_and_refuses_a_second_confirm(django_capture_on_commit_callbacks):
        route = _turn_on()
        booking, lead = _request()
        with django_capture_on_commit_callbacks(execute=True):
            orientations.confirm_orientation(booking, oriented_by=lead)
        stale = OrientationBooking.objects.get(pk=booking.pk)
        with pytest.raises(OrientationError, match="already answered"):
            orientations.confirm_orientation(stale, oriented_by=lead)
        assert _kinds(route) == ["Orientation confirmed"]
        assert SiteActivity.objects.filter(kind=Kind.ORIENTATION_CONFIRMED).count() == 1

    @respx.mock
    def it_posts_once_when_a_move_confirms_a_request(django_capture_on_commit_callbacks):
        route = _turn_on()
        booking, lead = _request()
        starts = timezone.localtime(timezone.now() + timedelta(days=5)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )
        with django_capture_on_commit_callbacks(execute=True):
            orientations.move_orientation(booking, starts, actor=lead)
        assert _kinds(route) == ["Orientation confirmed"]

    @respx.mock
    def it_posts_a_decline(django_capture_on_commit_callbacks):
        route = _turn_on()
        booking, lead = _request()
        with django_capture_on_commit_callbacks(execute=True):
            orientations.decline_orientation(booking, note="Full this week.", actor=lead.user)
        assert _kinds(route) == ["Orientation declined"]

    @respx.mock
    def it_posts_a_cancel(django_capture_on_commit_callbacks):
        route = _turn_on()
        booking, lead = _request()
        with django_capture_on_commit_callbacks(execute=True):
            orientations.cancel_orientation(booking, actor_label="Lead", actor=lead.user)
        assert _kinds(route) == ["Orientation cancelled"]

    @respx.mock
    def it_posts_a_completion_from_the_sweep(django_capture_on_commit_callbacks):
        route = _turn_on()
        lead = _member("lead")
        ended = timezone.now() - timedelta(hours=1)
        slot = _slot(lead, starts_at=ended - timedelta(hours=1), ends_at=ended)
        OrientationBookingFactory(slot=slot, member=_member("done"), status=OrientationBooking.Status.CONFIRMED)
        with django_capture_on_commit_callbacks(execute=True):
            orientations.auto_complete()
        with django_capture_on_commit_callbacks(execute=True):
            orientations.auto_complete()
        assert _kinds(route).count("Orientation completed automatically") == 1


def describe_the_link():
    def it_opens_the_bookings_respond_page():
        booking = OrientationBookingFactory()
        assert booking.activity_url == reverse("hub_orientation_respond", args=[booking.pk])
