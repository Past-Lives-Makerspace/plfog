"""Specs for the portal activity log on Discord (#1330): the seam, the line, and the plain post.

A ``SiteActivity`` row whose kind is in ``ACTIVITY_LOG_KINDS`` queues one post on commit to Site
Settings' Activity log webhook; blank or staging posts nothing; a slow or failing Discord never
reaches the caller (AC 1 and 9).
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, RegistrationFactory
from core.activity_log import (
    MAX_ATTEMPTS,
    POSTED_KEY,
    RETRY_DELAYS,
    activity_headline,
    activity_link,
    activity_message,
    activity_name,
    escape_line_text,
    post_activity,
)
from core.events.discord import PostOutcome, post_text
from core.models import ACTIVITY_LOG_KINDS, SiteActivity, SiteConfiguration, TransactionalEmailLog
from membership.models import Member
from tests.membership.factories import EquipmentReservationFactory, OrientationBookingFactory

pytestmark = pytest.mark.django_db

_WEBHOOK = "https://discord.com/api/webhooks/1330/activity"


def _turn_on(url: str = _WEBHOOK) -> None:
    config = SiteConfiguration.load()
    config.discord_activity_webhook_url = url
    config.save()


def _named(username: str, preferred: str) -> User:
    """A user whose member goes by ``preferred``: the only name the Discord line uses."""
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    Member.objects.filter(user=user).update(preferred_name=preferred)
    return User.objects.get(pk=user.pk)


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
        assert _posted(route)[0] == {"content": "Class published", "allowed_mentions": {"parse": []}}

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


def describe_retries():
    @pytest.fixture
    def queued(monkeypatch):
        calls: list[tuple[object, int, int]] = []

        def record(task, run_after, activity_id, attempt):
            calls.append((run_after, activity_id, attempt))

        monkeypatch.setattr("core.activity_log.enqueue_at_on_commit", record)
        return calls

    @respx.mock
    @pytest.mark.parametrize(
        "answer",
        [httpx.Response(503, text="down"), httpx.Response(429, text="slow down"), httpx.ReadTimeout("slow")],
        ids=["5xx", "429", "timeout"],
    )
    def it_releases_the_claim_and_queues_a_retry_with_backoff(answer, queued):
        _turn_on()
        if isinstance(answer, Exception):
            respx.post(_WEBHOOK).mock(side_effect=answer)
        else:
            respx.post(_WEBHOOK).mock(return_value=answer)
        activity = SiteActivity.objects.create(kind=SiteActivity.Kind.CLASS_APPROVED, payload={"note": "Kept"})
        before = timezone.now()
        post_activity.call(activity.pk)
        activity.refresh_from_db()
        assert activity.payload == {"note": "Kept"}
        [(run_after, activity_id, attempt)] = queued
        assert (activity_id, attempt) == (activity.pk, 2)
        assert run_after >= before + RETRY_DELAYS[0]

    @respx.mock
    def it_waits_longer_on_each_retry(queued):
        _turn_on()
        respx.post(_WEBHOOK).mock(return_value=httpx.Response(502))
        activity = SiteActivity.objects.create(kind=SiteActivity.Kind.CLASS_APPROVED)
        before = timezone.now()
        post_activity.call(activity.pk, 3)
        [(run_after, _activity_id, attempt)] = queued
        assert attempt == 4
        assert run_after >= before + RETRY_DELAYS[2]

    @respx.mock
    def it_gives_up_loudly_after_the_last_attempt_and_keeps_the_claim(queued, caplog):
        _turn_on()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(500))
        activity = SiteActivity.objects.create(kind=SiteActivity.Kind.CLASS_APPROVED)
        with caplog.at_level(logging.ERROR, logger="core.activity_log"):
            post_activity.call(activity.pk, MAX_ATTEMPTS)
        assert route.call_count == 1
        assert queued == []
        activity.refresh_from_db()
        assert activity.payload[POSTED_KEY] is True
        assert [record.levelno for record in caplog.records if record.name == "core.activity_log"] == [logging.ERROR]

    @respx.mock
    def it_never_retries_a_refusal_and_logs_an_error(queued, caplog):
        _turn_on()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(404, text="Unknown Webhook"))
        activity = SiteActivity.objects.create(kind=SiteActivity.Kind.CLASS_APPROVED)
        with caplog.at_level(logging.ERROR, logger="core.activity_log"):
            post_activity.call(activity.pk)
        assert route.call_count == 1
        assert queued == []
        assert "gave up" in caplog.text

    @respx.mock
    def it_posts_on_a_retry_once_discord_is_back(queued):
        _turn_on()
        route = respx.post(_WEBHOOK).mock(side_effect=[httpx.Response(503), httpx.Response(204)])
        activity = SiteActivity.objects.create(kind=SiteActivity.Kind.CLASS_APPROVED)
        post_activity.call(activity.pk)
        [(_run_after, activity_id, attempt)] = queued
        post_activity.call(activity_id, attempt)
        assert route.call_count == 2
        activity.refresh_from_db()
        assert activity.payload[POSTED_KEY] is True


def describe_activity_message():
    def it_reads_the_feed_headline_who_and_the_link_in_angle_brackets(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        booking = OrientationBookingFactory(member=_named("newbie", "Robin").member)
        user = _named("lead_ash", "Ash Lead")
        activity = SiteActivity.log(SiteActivity.Kind.ORIENTATION_CONFIRMED, actor=user, target=booking)
        link = "https://members.example" + reverse("hub_orientation_respond", args=[booking.pk])
        headline = escape_line_text(activity_headline(activity))
        assert headline.startswith("Orientation confirmed: Robin for ")
        assert activity_message(activity) == f"{headline} · Ash Lead · <{link}>"

    def it_escapes_discord_markdown_in_names():
        user = _named("star", "*Star* _Bright_")
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, actor=user)
        assert activity_message(activity) == "Class published · \\*Star\\* \\_Bright\\_"

    def it_shows_a_masked_link_in_member_text_as_typed():
        offering = ClassOfferingFactory(title="[Pay here](https://evil.example)")
        user = _named("sneak", "[Click](https://evil.example)")
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, actor=user, target=offering)
        message = activity_message(activity)
        assert "\\[Pay here\\]\\(https://evil.example\\)" in message
        assert "\\[Click\\]\\(https://evil.example\\)" in message
        assert "[Pay here](" not in message
        assert "[Click](" not in message

    def it_has_no_link_when_the_target_has_none():
        target = TransactionalEmailLog.objects.create(to_email="x@example.com", subject="s", trigger_kind="t")
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, target=target)
        assert activity_link(activity) == ""
        assert activity_message(activity) == "Class published"


def describe_activity_name():
    def it_never_names_an_actor_by_email():
        user = User.objects.create_user(username="nameless@example.com", email="nameless@example.com")
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, actor=user)
        assert activity.who == "nameless@example.com"  # the admin feed is unchanged
        assert activity_name(activity) == f"Member #{user.member.pk}"
        assert "@" not in activity_message(activity)

    def it_names_an_account_with_no_member_row_by_its_id():
        user = User.objects.create_user(username="ghost@example.com", email="ghost@example.com")
        Member.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, actor=user)
        assert activity_name(activity) == f"User #{user.pk}"

    def it_names_a_guest_registrant_without_their_email():
        registration = RegistrationFactory(first_name="", last_name="", email="guest@example.com")
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_REGISTRATION_CANCELLED, target=registration)
        assert activity.who == "guest@example.com"
        assert activity_name(activity) == f"Registration #{registration.pk}"

    def it_names_nobody_for_an_old_row_with_no_actor_rather_than_saying_not_recorded():
        booking = OrientationBookingFactory()
        activity = SiteActivity.log(SiteActivity.Kind.ORIENTATION_DECLINED, target=booking)
        assert activity_name(activity) == ""
        assert "Not recorded" not in activity_message(activity)

    def it_names_nobody_for_a_jobs_doing_rather_than_saying_system():
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, target=ClassOfferingFactory(title="Raku"))
        assert activity_name(activity) == ""
        assert "System" not in activity_message(activity)


def describe_post_text():
    @respx.mock
    def it_posts_plain_content_that_pings_nobody():
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        assert post_text(_WEBHOOK, "@everyone hello") is PostOutcome.SENT
        assert _posted(route) == [{"content": "@everyone hello", "allowed_mentions": {"parse": []}}]

    @respx.mock
    def it_is_off_for_a_blank_webhook():
        route = respx.route().mock(return_value=httpx.Response(204))
        assert post_text("", "hello") is PostOutcome.OFF
        assert not route.called

    @respx.mock
    def it_is_off_on_staging(settings):
        settings.IS_STAGING = True
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        assert post_text(_WEBHOOK, "hello") is PostOutcome.OFF
        assert not route.called

    @respx.mock
    def it_asks_for_a_retry_on_a_network_error():
        respx.post(_WEBHOOK).mock(side_effect=httpx.ConnectError("no route"))
        assert post_text(_WEBHOOK, "hello") is PostOutcome.RETRY

    @respx.mock
    @pytest.mark.parametrize("status", [429, 500, 503])
    def it_asks_for_a_retry_on_a_5xx_or_429(status):
        respx.post(_WEBHOOK).mock(return_value=httpx.Response(status))
        assert post_text(_WEBHOOK, "hello") is PostOutcome.RETRY

    @respx.mock
    @pytest.mark.parametrize("status", [400, 401, 404])
    def it_reports_any_other_status_as_a_refusal(status):
        respx.post(_WEBHOOK).mock(return_value=httpx.Response(status, text="bad"))
        assert post_text(_WEBHOOK, "hello") is PostOutcome.REFUSED


def describe_site_settings():
    def it_defaults_the_activity_log_webhook_to_blank():
        assert SiteConfiguration.load().discord_activity_webhook_url == ""

    def it_edits_the_activity_log_webhook_on_the_site_settings_form():
        from hub.forms import SiteSettingsForm

        assert "discord_activity_webhook_url" in SiteSettingsForm.base_fields
        assert SiteSettingsForm.base_fields["discord_activity_webhook_url"].label == "Activity log webhook"


def describe_preferred_names_only():
    def _sam() -> Member:
        user = User.objects.create_user(
            username="sam", email="sam@example.com", first_name="Samantha", last_name="Rivera"
        )
        Member.objects.filter(user=user).update(preferred_name="Sam Rivera", full_legal_name="Samantha Rivera")
        return Member.objects.get(user=user)

    def _assert_preferred_only(message: str) -> None:
        assert "Sam Rivera" in message
        assert "Samantha" not in message
        assert "sam@example.com" not in message

    def it_names_the_actor_by_preferred_name_only():
        sam = _sam()
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, actor=sam.user)
        assert activity_message(activity) == "Class published · Sam Rivera"

    def it_names_an_orientations_member_by_preferred_name_only_while_the_feed_keeps_the_staff_label():
        sam = _sam()
        booking = OrientationBookingFactory(member=sam)
        activity = SiteActivity.log(SiteActivity.Kind.ORIENTATION_REQUESTED, actor=sam.user, target=booking)
        _assert_preferred_only(activity_message(activity))
        assert "Samantha Rivera" in activity.line.headline

    def it_names_a_reservations_member_by_preferred_name_only_while_the_feed_keeps_the_staff_label():
        sam = _sam()
        reservation = EquipmentReservationFactory(member=sam)
        activity = SiteActivity.log(SiteActivity.Kind.RESERVATION_CONFIRMED, actor=sam.user, target=reservation)
        _assert_preferred_only(activity_message(activity))
        assert "Samantha Rivera" in activity.line.headline

    def it_names_a_member_registrant_by_preferred_name_only():
        sam = _sam()
        registration = RegistrationFactory(member=sam, first_name="Samantha", last_name="Rivera")
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_REGISTRATION_CANCELLED, target=registration)
        _assert_preferred_only(activity_message(activity))

    def it_names_a_member_with_no_preferred_name_by_the_first_word_of_their_legal_name():
        user = User.objects.create_user(username="legal", email="legal@example.com")
        Member.objects.filter(user=user).update(preferred_name="", full_legal_name="  Quinn Ashdown ")
        member = Member.objects.get(user=user)
        activity = SiteActivity.log(SiteActivity.Kind.CLASS_PUBLISHED, actor=member.user)
        assert activity_message(activity) == "Class published · Quinn"

    def it_names_a_member_with_no_name_on_file_by_number():
        user = User.objects.create_user(username="unnamed", email="unnamed@example.com")
        Member.objects.filter(user=user).update(preferred_name="", full_legal_name="")
        member = Member.objects.get(user=user)
        assert member.activity_log_name == f"Member #{member.pk}"

    def it_never_names_a_member_by_an_invites_email_standing_in_as_their_legal_name():
        user = User.objects.create_user(username="invited", email="invited@example.com")
        Member.objects.filter(user=user).update(preferred_name="", full_legal_name="invited@example.com")
        member = Member.objects.get(user=user)
        assert member.activity_log_name == f"Member #{member.pk}"
