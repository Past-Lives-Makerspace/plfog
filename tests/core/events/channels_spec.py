"""Channel adapters — wrap existing mechanisms; shells defer to Phase 2."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from core.events import channels
from core.events.channels import (
    ChannelNotImplemented,
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
from core.models import Notification, PushSubscription, TransactionalEmailLog

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


def describe_shell_adapters():
    @pytest.mark.parametrize("adapter", [ScheduledEmailAdapter(), DigestAdapter(), DiscordAdapter()])
    def it_raises_when_invoked_before_phase_2(adapter):
        user = _user()
        with pytest.raises(ChannelNotImplemented):
            adapter.deliver(user, _message())


def describe_registry():
    def it_registers_an_adapter_for_every_channel():
        for channel in Channel:
            assert get_adapter(channel).channel is channel

    def it_marks_live_channels_implemented():
        assert is_implemented(Channel.IN_APP)
        assert is_implemented(Channel.EMAIL)
        assert is_implemented(Channel.PUSH)

    def it_marks_phase_2_channels_unimplemented():
        assert not is_implemented(Channel.SCHEDULED_EMAIL)
        assert not is_implemented(Channel.DIGEST)
        assert not is_implemented(Channel.DISCORD)

    def it_raises_for_an_unregistered_channel_lookup():
        with pytest.raises(KeyError):
            channels._ADAPTERS["nope"]  # noqa: B018 — asserting the dict has no such key
