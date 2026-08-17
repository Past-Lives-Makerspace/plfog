"""Channel adapters — wrap existing mechanisms; shells defer to Phase 2."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from core.events import channels
from core.events.channels import (
    DigestAdapter,
    DiscordAdapter,
    EmailAdapter,
    InAppAdapter,
    Message,
    PushAdapter,
    ScheduledEmailAdapter,
    get_adapter,
    is_implemented,
)
from core.events.registry import Channel
from core.models import EventDelivery, FcmDevice, Notification, PushSubscription, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def _user():
    return User.objects.create_user(username="chan", email="chan@example.com")


def _message(**kw):
    base = dict(title="T", body="B", url="/x/", trigger_kind="class_published")
    base.update(kw)
    return Message(**base)


def describe_in_app_adapter():
    def it_creates_a_bell_row_matching_dispatch():
        user = _user()
        InAppAdapter().deliver(user, _message())
        note = Notification.objects.get(user=user)
        assert note.trigger == "class_published"
        assert note.title == "T"
        assert note.body == "B"
        assert note.url == "/x/"

    def it_clips_an_over_length_title_and_body_to_the_column_limits():
        # A long sitewide announcement must not overflow Notification.title (200) /
        # body (500) — that would crash the whole emit() mid-fan-out.
        user = _user()
        InAppAdapter().deliver(user, _message(title="T" * 300, body="B" * 900))
        note = Notification.objects.get(user=user)
        assert len(note.title) == 200
        assert len(note.body) == 500
        assert note.body.endswith("…")


def describe_email_adapter():
    def it_sends_through_the_choke_point_and_logs():
        user = _user()
        EmailAdapter().deliver(user, _message())
        assert TransactionalEmailLog.objects.filter(trigger_kind="class_published").exists()

    def it_skips_users_without_an_email():
        user = User.objects.create_user(username="noemail", email="")
        EmailAdapter().deliver(user, _message())
        assert not TransactionalEmailLog.objects.exists()

    def it_passes_best_effort_and_html_to_the_choke_point():
        user = _user()
        with patch("core.events.channels.send_email") as mock_send:
            EmailAdapter().deliver(user, _message(html_body="<b>hi</b>"))
        _args, kwargs = mock_send.call_args
        assert kwargs["best_effort"] is True
        assert kwargs["html_body"] == "<b>hi</b>"
        assert kwargs["trigger_kind"] == "class_published"


def describe_push_adapter():
    def it_pushes_to_each_subscription():
        user = _user()
        PushSubscription.objects.create(user=user, endpoint="https://p/1", p256dh="k", auth="a")
        PushSubscription.objects.create(user=user, endpoint="https://p/2", p256dh="k", auth="a")
        with patch("core.events.channels.send_web_push") as mock_push:
            PushAdapter().deliver(user, _message())
        assert mock_push.call_count == 2

    def it_does_nothing_without_subscriptions():
        user = _user()
        with patch("core.events.channels.send_web_push") as mock_push:
            PushAdapter().deliver(user, _message())
        mock_push.assert_not_called()

    def it_pushes_to_each_fcm_device():
        user = _user()
        FcmDevice.objects.create(user=user, token="d1", platform=FcmDevice.Platform.ANDROID)
        FcmDevice.objects.create(user=user, token="d2", platform=FcmDevice.Platform.IOS)
        with patch("core.events.channels.send_fcm") as mock_fcm:
            PushAdapter().deliver(user, _message())
        assert mock_fcm.call_count == 2

    def it_does_nothing_without_fcm_devices():
        user = _user()
        with patch("core.events.channels.send_fcm") as mock_fcm:
            PushAdapter().deliver(user, _message())
        mock_fcm.assert_not_called()

    def it_fans_out_to_both_web_push_and_fcm():
        user = _user()
        PushSubscription.objects.create(user=user, endpoint="https://p/1", p256dh="k", auth="a")
        FcmDevice.objects.create(user=user, token="d1", platform=FcmDevice.Platform.ANDROID)
        with (
            patch("core.events.channels.send_web_push") as mock_push,
            patch("core.events.channels.send_fcm") as mock_fcm,
        ):
            PushAdapter().deliver(user, _message())
        mock_push.assert_called_once()
        mock_fcm.assert_called_once()

    def it_flattens_html_and_newlines_to_one_tray_line():
        # An announcement body is sanitized HTML; a push must never carry tags or
        # multiple paragraphs, so the adapter flattens it to a single clean line.
        user = _user()
        FcmDevice.objects.create(user=user, token="d1", platform=FcmDevice.Platform.ANDROID)
        with patch("core.events.channels.send_fcm") as mock_fcm:
            PushAdapter().deliver(user, _message(body="<p>Snow day.</p><p>Closed till <b>Thu</b>.</p>"))
        assert mock_fcm.call_args.kwargs["body"] == "Snow day. Closed till Thu."

    def it_caps_an_over_length_title_and_body_for_the_tray():
        user = _user()
        FcmDevice.objects.create(user=user, token="d1", platform=FcmDevice.Platform.ANDROID)
        with patch("core.events.channels.send_fcm") as mock_fcm:
            PushAdapter().deliver(user, _message(title="T" * 200, body="B" * 400))
        kwargs = mock_fcm.call_args.kwargs
        assert len(kwargs["title"]) == 80 and kwargs["title"].endswith("…")
        assert len(kwargs["body"]) == 200 and kwargs["body"].endswith("…")


def describe_scheduled_email_adapter():
    def it_sends_through_the_choke_point_like_email():
        user = _user()
        ScheduledEmailAdapter().deliver(user, _message())
        assert TransactionalEmailLog.objects.filter(trigger_kind="class_published").exists()

    def it_skips_users_without_an_email():
        user = User.objects.create_user(username="noemail2", email="")
        ScheduledEmailAdapter().deliver(user, _message())
        assert not TransactionalEmailLog.objects.exists()

    def it_is_not_a_broadcast_channel():
        assert ScheduledEmailAdapter().is_broadcast is False

    def describe_is_due():
        def it_is_due_when_fire_time_is_in_the_window():
            from datetime import timedelta

            from django.utils import timezone

            now = timezone.now()
            # anchor 48h out, offset -48h → fire_at == now → due this tick.
            anchor = now + timedelta(hours=48)
            assert ScheduledEmailAdapter.is_due(anchor, timedelta(hours=-48), now=now) is True

        def it_is_not_due_when_fire_time_is_outside_the_window():
            from datetime import timedelta

            from django.utils import timezone

            now = timezone.now()
            anchor = now + timedelta(hours=72)
            assert ScheduledEmailAdapter.is_due(anchor, timedelta(hours=-48), now=now) is False


def describe_digest_adapter():
    def it_buffers_a_pending_row_instead_of_sending():
        user = _user()
        DigestAdapter().deliver(user, _message(title="Hi", body="Body", url="/u/"))
        assert not TransactionalEmailLog.objects.exists()
        row = EventDelivery.objects.get(target_ref=f"user:{user.pk}", channel="digest")
        assert row.status == EventDelivery.Status.PENDING
        assert row.title == "Hi"
        assert row.body == "Body"
        assert row.url == "/u/"

    def it_lists_pending_rows_for_a_user_oldest_first():
        user = _user()
        DigestAdapter().deliver(user, _message(trigger_kind="class_published"))
        DigestAdapter().deliver(user, _message(trigger_kind="class_cancelled"))
        pending = DigestAdapter.pending_for(user)
        assert [r.event_key for r in pending] == ["class_published", "class_cancelled"]

    def it_flush_due_groups_per_recipient_and_marks_sent():
        user = _user()
        DigestAdapter().deliver(user, _message(trigger_kind="class_published"))
        DigestAdapter().deliver(user, _message(trigger_kind="class_cancelled"))
        grouped = DigestAdapter.flush_due()
        assert set(grouped) == {f"user:{user.pk}"}
        assert len(grouped[f"user:{user.pk}"]) == 2
        # All flipped to SENT — nothing pending remains.
        assert DigestAdapter.pending_for(user) == []

    def it_flush_due_returns_empty_when_nothing_pending():
        assert DigestAdapter.flush_due() == {}

    def it_is_not_a_broadcast_channel():
        assert DigestAdapter().is_broadcast is False


def describe_discord_adapter():
    def it_is_a_broadcast_channel():
        assert DiscordAdapter().is_broadcast is True

    def it_broadcasts_by_posting_an_embed(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = "https://discord/webhook/abc"
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            result = DiscordAdapter().broadcast(_message())
        assert result is True
        mock_post.assert_called_once()

    def it_deliver_defers_to_broadcast():
        user = _user()
        with patch.object(DiscordAdapter, "broadcast", return_value=True) as mock_bcast:
            DiscordAdapter().deliver(user, _message())
        mock_bcast.assert_called_once()


def describe_registry():
    def it_registers_an_adapter_for_every_channel():
        for channel in Channel:
            assert get_adapter(channel).channel is channel

    def it_marks_every_channel_implemented():
        for channel in Channel:
            assert is_implemented(channel)

    def it_marks_only_discord_a_broadcast_channel():
        broadcast = [c for c in Channel if get_adapter(c).is_broadcast]
        assert broadcast == [Channel.DISCORD]

    def it_routes_broadcast_helper_to_the_adapter():
        with patch.object(DiscordAdapter, "broadcast") as mock_bcast:
            channels.broadcast(get_adapter(Channel.DISCORD), _message())
        mock_bcast.assert_called_once()

    def it_raises_for_an_unregistered_channel_lookup():
        with pytest.raises(KeyError):
            channels._ADAPTERS["nope"]  # noqa: B018 — asserting the dict has no such key
