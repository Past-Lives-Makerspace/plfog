"""Specs for the portal activity log on Discord (#1330): the seam, the line, and the plain post.

A ``SiteActivity`` row whose kind is in ``ACTIVITY_LOG_KINDS`` queues one post on commit to Site
Settings' Activity log webhook; blank or staging posts nothing; a slow or failing Discord never
reaches the caller (AC 1 and 9).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.urls import reverse

from core.activity_log import POSTED_KEY, activity_link, activity_message, post_activity
from core.events.discord import post_text
from core.models import ACTIVITY_LOG_KINDS, SiteActivity, SiteConfiguration, TransactionalEmailLog
from tests.membership.factories import OrientationBookingFactory

pytestmark = pytest.mark.django_db

_WEBHOOK = "https://discord.com/api/webhooks/1330/activity"


def _turn_on(url: str = _WEBHOOK) -> None:
    config = SiteConfiguration.load()
    config.discord_activity_webhook_url = url
    config.save()


def _posted(route: respx.Route) -> list[dict[str, object]]:
    return [json.loads(call.request.content) for call in route.calls]


def describe_ACTIVITY_LOG_KINDS():
    def it_lists_the_part_one_happenings_and_leaves_the_quiet_ones_out():
        kinds = SiteActivity.Kind
        assert {
            kinds.ORIENTATION_REQUESTED,
            kinds.ORIENTATION_CONFIRMED,
            kinds.ORIENTATION_DECLINED,
            kinds.ORIENTATION_CANCELLED,
            kinds.ORIENTATION_COMPLETED,
            kinds.RESERVATION_REQUESTED,
            kinds.RESERVATION_CONFIRMED,
            kinds.RESERVATION_DECLINED,
            kinds.RESERVATION_CANCELLED,
            kinds.RESERVATION_HAPPENED,
            kinds.CLASS_SUBMITTED,
            kinds.CLASS_APPROVED,
            kinds.CLASS_PUBLISHED,
            kinds.CLASS_CANCELLED,
            kinds.CLASS_REGISTERED,
            kinds.CLASS_REGISTRATION_CANCELLED,
        } == ACTIVITY_LOG_KINDS
        assert kinds.LOGIN not in ACTIVITY_LOG_KINDS


def describe_the_seam():
    @respx.mock
    def it_posts_an_allowlisted_row_once_its_transaction_commits(django_capture_on_commit_callbacks):
        _turn_on()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED)
            assert not route.called
        for callback in callbacks:
            callback()
        assert route.call_count == 1
        assert _posted(route)[0] == {"content": "Class published · System", "allowed_mentions": {"parse": []}}

    @respx.mock
    def it_posts_nothing_for_a_kind_off_the_allowlist(django_capture_on_commit_callbacks):
        _turn_on()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            SiteActivity.log(SiteActivity.Kind.LOGIN, actor=User.objects.create_user(username="quiet"))
        assert callbacks == []
        assert not route.called

    @respx.mock
    def it_posts_nothing_when_the_webhook_is_blank(django_capture_on_commit_callbacks):
        route = respx.route().mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=True):
            SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED)
        assert not route.called

    @respx.mock
    def it_never_posts_on_staging(settings, django_capture_on_commit_callbacks):
        settings.IS_STAGING = True
        _turn_on()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=True):
            SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED)
        assert not route.called

    @respx.mock
    def it_never_reaches_the_caller_when_discord_times_out(django_capture_on_commit_callbacks):
        _turn_on()
        route = respx.post(_WEBHOOK).mock(side_effect=httpx.ReadTimeout("slow"))
        with django_capture_on_commit_callbacks(execute=True):
            activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED)
        assert route.called
        assert SiteActivity.objects.filter(pk=activity.pk).exists()

    @respx.mock
    def it_never_reaches_the_caller_when_discord_refuses(django_capture_on_commit_callbacks):
        _turn_on()
        respx.post(_WEBHOOK).mock(return_value=httpx.Response(500, text="down"))
        with django_capture_on_commit_callbacks(execute=True):
            activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED)
        assert activity.pk is not None


def describe_post_activity():
    @respx.mock
    def it_posts_a_row_once_however_often_it_runs():
        _turn_on()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        activity = SiteActivity.objects.create(kind=SiteActivity.Kind.CLASS_APPROVED)
        post_activity.call(activity.pk)
        post_activity.call(activity.pk)
        assert route.call_count == 1
        activity.refresh_from_db()
        assert activity.payload[POSTED_KEY] is True

    @respx.mock
    def it_keeps_the_rows_note_when_it_marks_it_posted():
        _turn_on()
        respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        activity = SiteActivity.objects.create(kind=SiteActivity.Kind.CLASS_APPROVED, payload={"note": "Lovely"})
        post_activity.call(activity.pk)
        activity.refresh_from_db()
        assert activity.payload == {"note": "Lovely", POSTED_KEY: True}

    @respx.mock
    def it_posts_nothing_for_a_row_deleted_before_it_ran():
        _turn_on()
        route = respx.route().mock(return_value=httpx.Response(204))
        post_activity.call(987654)
        assert not route.called


def describe_activity_message():
    def it_reads_the_feed_headline_who_and_the_link_in_angle_brackets(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        booking = OrientationBookingFactory()
        user = User.objects.create_user(username="lead_ash", first_name="Ash", last_name="Lead")
        activity = SiteActivity.log(SiteActivity.Kind.ORIENTATION_CONFIRMED, actor=user, target=booking)
        link = "https://members.example" + reverse("hub_orientation_respond", args=[booking.pk])
        assert activity_message(activity) == f"{activity.line.headline} · Ash Lead · <{link}>"
        assert activity.line.headline.startswith("Orientation confirmed: ")

    def it_escapes_discord_markdown_in_names():
        user = User.objects.create_user(username="star", first_name="*Star*", last_name="_Bright_")
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, actor=user)
        assert activity_message(activity) == "Class published · \\*Star\\* \\_Bright\\_"

    def it_has_no_link_when_the_target_has_none():
        target = TransactionalEmailLog.objects.create(to_email="x@example.com", subject="s", trigger_kind="t")
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, target=target)
        assert activity_link(activity) == ""
        assert activity_message(activity) == "Class published · System"


def describe_post_text():
    @respx.mock
    def it_posts_plain_content_that_pings_nobody():
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        assert post_text(_WEBHOOK, "@everyone hello") is True
        assert _posted(route) == [{"content": "@everyone hello", "allowed_mentions": {"parse": []}}]

    @respx.mock
    def it_is_a_no_op_for_a_blank_webhook():
        route = respx.route().mock(return_value=httpx.Response(204))
        assert post_text("", "hello") is False
        assert not route.called

    @respx.mock
    def it_refuses_on_staging(settings):
        settings.IS_STAGING = True
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        assert post_text(_WEBHOOK, "hello") is False
        assert not route.called

    @respx.mock
    def it_returns_false_on_a_network_error():
        respx.post(_WEBHOOK).mock(side_effect=httpx.ConnectError("no route"))
        assert post_text(_WEBHOOK, "hello") is False

    @respx.mock
    def it_returns_false_on_a_refusal():
        respx.post(_WEBHOOK).mock(return_value=httpx.Response(400, text="bad"))
        assert post_text(_WEBHOOK, "hello") is False


def describe_site_settings():
    def it_defaults_the_activity_log_webhook_to_blank():
        assert SiteConfiguration.load().discord_activity_webhook_url == ""

    def it_edits_the_activity_log_webhook_on_the_site_settings_form():
        from hub.forms import SiteSettingsForm

        assert "discord_activity_webhook_url" in SiteSettingsForm.base_fields
        assert SiteSettingsForm.base_fields["discord_activity_webhook_url"].label == "Activity log webhook"
