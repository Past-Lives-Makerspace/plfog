"""The idempotency ledger must not keep a slot for a delivery that never happened.

``emit`` claims the ``(event, user, channel, period)`` slot BEFORE handing the message
to the adapter, so two concurrent emits cannot both send. The cost of that ordering is
that a send which does not happen leaves a claim nobody used, and every later run then
treats the recipient as already delivered.

That is not hypothetical. In September 2026 a results send to the full membership was
rejected for 39 members with an HTTP 429 daily-quota reply, and 4 more were skipped
because they had no address on file at the time. All 43 kept their claimed slots, so no
ordinary re-send could ever reach them; they had to be released by hand against the
production database. These specs pin the behaviour that makes that unnecessary.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from core.events.emit import emit
from core.events.registry import Channel
from core.models import EventDelivery, TransactionalEmailLog
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

# A forced-email event: it emails regardless of preferences, so these specs exercise the
# email path without also having to opt each recipient in.
_EVENT = "member.login_invite"
_PERIOD = "release-spec:1"


def _email_slots(user: User) -> int:
    return EventDelivery.objects.filter(
        event_key=_EVENT, target_ref=f"user:{user.pk}", channel=Channel.EMAIL.value, period=_PERIOD
    ).count()


def _emit_to(user: User) -> None:
    emit(_EVENT, context={"user": user}, title="Sign in", body="b", period=_PERIOD)


def describe_releasing_a_slot_the_send_never_used():
    def describe_when_the_provider_rejects_the_send():
        def it_releases_the_slot_so_a_later_run_retries(db):
            user = User.objects.create_user(username="rejected", email="rejected@example.com")
            with patch("core.email._deliver", side_effect=RuntimeError("429 daily quota")):
                _emit_to(user)
            # The attempt is recorded as failed, and the slot is handed back.
            assert TransactionalEmailLog.objects.filter(trigger_kind=_EVENT, status="failed").count() == 1
            assert _email_slots(user) == 0

        def it_actually_reaches_the_member_on_the_retry(db):
            user = User.objects.create_user(username="retried", email="retried@example.com")
            with patch("core.email._deliver", side_effect=RuntimeError("429 daily quota")):
                _emit_to(user)
            _emit_to(user)  # same event, same period — the ledger must not skip them
            assert (
                TransactionalEmailLog.objects.filter(trigger_kind=_EVENT, to_email=user.email, status="sent").count()
                == 1
            )
            assert _email_slots(user) == 1

    def describe_when_the_recipient_has_no_address_yet():
        def it_releases_the_slot_rather_than_burning_it_silently(db):
            """The failure mode with no log row at all: nothing is sent, nothing is recorded."""
            user = User.objects.create_user(username="addressless", email="placeholder@example.com")
            with patch("core.events.channels.notification_email_for", return_value=""):
                _emit_to(user)
            assert TransactionalEmailLog.objects.filter(trigger_kind=_EVENT).count() == 0
            assert _email_slots(user) == 0

        def it_reaches_them_once_an_address_exists(db):
            user = User.objects.create_user(username="lateaddress", email="late@example.com")
            with patch("core.events.channels.notification_email_for", return_value=""):
                _emit_to(user)
            _emit_to(user)
            assert (
                TransactionalEmailLog.objects.filter(trigger_kind=_EVENT, to_email=user.email, status="sent").count()
                == 1
            )

    def describe_when_the_adapter_raises():
        def it_releases_the_slot_and_lets_the_error_surface(db):
            user = User.objects.create_user(username="boom", email="boom@example.com")
            with patch("core.events.channels.EmailAdapter.deliver", side_effect=RuntimeError("boom")):
                with pytest.raises(RuntimeError):
                    _emit_to(user)
            assert _email_slots(user) == 0


def describe_keeping_a_slot_the_send_did_use():
    def it_does_not_re_email_a_recipient_who_already_received_it(db):
        user = User.objects.create_user(username="delivered", email="delivered@example.com")
        _emit_to(user)
        _emit_to(user)
        assert TransactionalEmailLog.objects.filter(trigger_kind=_EVENT, to_email=user.email).count() == 1
        assert _email_slots(user) == 1

    def it_spends_the_slot_for_a_channel_the_member_never_set_up(linked_member):
        """Push with no registered device is a deliberate non-release.

        Nothing was pushed, but re-pushing on a later run would drop a stale line in the
        member's tray rather than repair anything — unlike an email, the miss is not
        worth fixing late. So the slot stays spent.
        """
        member = linked_member()
        emit("class_cancelled", context={}, title="t", body="b", period=_PERIOD)
        assert (
            EventDelivery.objects.filter(
                event_key="class_cancelled",
                target_ref=f"user:{member.user_id}",
                channel=Channel.PUSH.value,
                period=_PERIOD,
            ).count()
            == 1
        )


def describe_explicit_address_sends():
    """``extra_emails`` claims its own ``email:<addr>`` slot and owes the same release."""

    _GUEST = "guest@example.com"

    def _emit_to_guest() -> None:
        guild = GuildFactory()
        emit("guild_announcement", context={"guild": guild}, title="t", body="b", period=_PERIOD, extra_emails=[_GUEST])

    def it_releases_a_rejected_explicit_address_for_a_retry(db):
        with patch("core.email._deliver", side_effect=RuntimeError("429 daily quota")):
            _emit_to_guest()
        assert not EventDelivery.objects.filter(
            event_key="guild_announcement", target_ref=f"email:{_GUEST}", period=_PERIOD
        ).exists()

    def it_keeps_a_delivered_explicit_address_so_it_is_not_double_sent(db):
        _emit_to_guest()
        _emit_to_guest()
        assert TransactionalEmailLog.objects.filter(to_email=_GUEST).count() == 1
