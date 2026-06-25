"""emit() — log activity, resolve recipients, fan out per channel, idempotent."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from core.events.emit import _record_delivery, emit
from core.events.registry import Channel
from classes.factories import RegistrationFactory
from core.models import EventDelivery, Notification, NotificationPreference, SiteActivity, TransactionalEmailLog
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db


def describe_emit():
    def describe_activity_logging():
        def it_writes_an_activity_row_when_the_event_declares_a_kind(linked_member):
            actor = User.objects.create_user(username="actor", email="actor@example.com")
            linked_member()
            emit("class_published", actor=actor, context={}, title="t", body="b")
            assert SiteActivity.objects.filter(kind="class_published", actor=actor).exists()

        def it_writes_no_activity_row_when_none_declared(linked_member):
            member = linked_member()
            emit("class_reminder", context={"member": member}, title="t", body="b")
            assert not SiteActivity.objects.exists()

    def describe_fan_out():
        def it_creates_in_app_rows_for_resolved_recipients(linked_member):
            member = linked_member()
            result = emit("class_published", context={}, title="New class", body="b", url="/c/")
            assert Notification.objects.filter(user=member.user, trigger="class_published").exists()
            assert (member.user_id, Channel.IN_APP) in result.delivered

        def it_emails_only_opted_in_recipients(linked_member):
            opted = linked_member()
            NotificationPreference.objects.create(user=opted.user, trigger="class_published", email_enabled=True)
            linked_member()  # not opted in
            emit("class_published", context={}, title="t", body="b")
            logs = list(TransactionalEmailLog.objects.filter(trigger_kind="class_published"))
            assert len(logs) == 1
            assert logs[0].to_email == opted.user.email

        def it_force_emails_ignoring_preferences():
            user = User.objects.create_user(username="sec", email="sec@example.com")
            emit("new_login", context={"user": user}, title="New login", body="b")
            assert TransactionalEmailLog.objects.filter(trigger_kind="new_login", to_email=user.email).exists()

        def it_does_not_deliver_channels_the_event_does_not_declare(linked_member):
            # class_published declares in_app + email + push (no discord), so emit must
            # not post to discord — only declared channels fan out.
            linked_member()
            result = emit("class_published", context={}, title="t", body="b")
            assert all(channel is not Channel.DISCORD for _pk, channel in result.delivered)
            assert result.broadcast_channels == []

    def describe_scoped_routing():
        def it_routes_class_review_to_guild_leadership_only(linked_member):
            lead = linked_member()
            staff = linked_member()
            other = linked_member()  # unrelated member, must not be notified
            guild = GuildFactory(guild_lead=lead)
            GuildStaffMembershipFactory(guild=guild, member=staff)
            emit("class_review_requested", context={"guild": guild}, title="Review", body="b")
            notified = set(Notification.objects.values_list("user_id", flat=True))
            assert notified == {lead.user_id, staff.user_id}
            assert other.user_id not in notified

    def describe_idempotency():
        def it_does_not_redeliver_on_re_emit(linked_member):
            member = linked_member()
            NotificationPreference.objects.create(user=member.user, trigger="class_published", email_enabled=True)
            emit("class_published", context={}, title="t", body="b")
            emit("class_published", context={}, title="t", body="b")
            # Exactly one in-app row and one email despite two emits.
            assert Notification.objects.filter(user=member.user, trigger="class_published").count() == 1
            assert TransactionalEmailLog.objects.filter(trigger_kind="class_published").count() == 1

        def it_reports_skipped_duplicates_on_re_emit(linked_member):
            member = linked_member()
            emit("class_published", context={}, title="t", body="b")
            second = emit("class_published", context={}, title="t", body="b")
            assert (member.user_id, Channel.IN_APP) in second.skipped_duplicates
            assert second.delivered == []

        def it_separates_deliveries_by_period(linked_member):
            member = linked_member()
            emit("class_published", context={}, title="t", body="b", period="2026-06")
            emit("class_published", context={}, title="t", body="b", period="2026-07")
            # Different period buckets → two in-app rows.
            assert Notification.objects.filter(user=member.user, trigger="class_published").count() == 2

        def it_records_one_eventdelivery_row_per_channel(linked_member):
            member = linked_member()
            emit("class_published", context={}, title="t", body="b")
            row = EventDelivery.objects.get(event_key="class_published", target_ref=f"user:{member.user_id}")
            assert row.channel == Channel.IN_APP.value
            assert row.period == ""

    def describe_result():
        def it_reports_recipient_and_delivery_counts(linked_member):
            linked_member()
            result = emit("class_published", context={}, title="t", body="b")
            assert result.recipient_count == 1
            assert result.delivery_count == 1
            assert "class_published" in repr(result)

    def describe_unknown_event():
        def it_raises_keyerror_for_an_unregistered_key():
            with pytest.raises(KeyError):
                emit("not_an_event", context={}, title="t", body="b")


def describe_per_channel_overrides():
    """Phase-4 structural senders pass a pre-rendered Message + attachments per channel."""

    def it_uses_the_email_channel_message_override_verbatim(linked_member):
        from core.events.channels import Message

        member = linked_member()
        NotificationPreference.objects.create(user=member.user, trigger="registration_confirmed", email_enabled=True)
        override = Message(
            title="Structural subject",
            body="Line one\nLine two\nSessions: Jul 12, Jul 19",
            html_body="<p>rich</p>",
            trigger_kind="registration_confirmed",
        )
        emit(
            "registration_confirmed",
            context={"member": member},
            title="Plain in-app title",
            body="plain",
            messages={Channel.EMAIL: override},
        )
        log = TransactionalEmailLog.objects.get(trigger_kind="registration_confirmed")
        assert log.subject == "Structural subject"
        # The in-app row still uses the non-override title (copy/explicit path).
        note = Notification.objects.get(user=member.user, trigger="registration_confirmed")
        assert note.title == "Plain in-app title"

    def it_passes_email_attachments_through_to_the_choke_point(linked_member):
        member = linked_member()
        NotificationPreference.objects.create(user=member.user, trigger="orientation_update", email_enabled=True)
        ics = ("orientation.ics", b"BEGIN:VCALENDAR", "text/calendar")
        with patch("core.events.channels.send_email") as mock_send:
            emit(
                "orientation_update",
                context={"member": member},
                title="t",
                body="b",
                attachments={Channel.EMAIL: [ics]},
            )
        _args, kwargs = mock_send.call_args
        assert kwargs["attachments"] == [ics]


def describe_explicit_email_recipient():
    """A dedicated email can address a raw address while in-app routes to the user."""

    def it_emails_the_explicit_address_not_the_resolved_users_email(linked_member):
        member = linked_member(email="account@example.com")
        emit(
            "registration_confirmed",
            context={"member": member},
            title="Registration confirmed",
            body="Class",
            email_to="form-entered@example.com",
        )
        logs = list(TransactionalEmailLog.objects.filter(trigger_kind="registration_confirmed"))
        assert len(logs) == 1
        assert logs[0].to_email == "form-entered@example.com"
        # In-app still goes to the linked user.
        assert Notification.objects.filter(user=member.user, trigger="registration_confirmed").exists()

    def it_still_emails_when_no_user_resolves():
        # A guest registration (member is None) — the explicit email must still go
        # out, with no in-app recipient. Use a registration whose member is unset.
        registration = RegistrationFactory(email="guest@example.com")
        assert registration.member is None
        emit(
            "registration_confirmed",
            context={"registration": registration},
            title="t",
            body="b",
            email_to="guest@example.com",
        )
        logs = list(TransactionalEmailLog.objects.filter(trigger_kind="registration_confirmed"))
        assert len(logs) == 1
        assert logs[0].to_email == "guest@example.com"
        assert Notification.objects.count() == 0

    def it_dedupes_the_explicit_email_across_re_emits():
        registration = RegistrationFactory(email="g@example.com")
        ctx = {"registration": registration}
        emit("registration_confirmed", context=ctx, title="t", body="b", email_to="g@example.com")
        emit("registration_confirmed", context=ctx, title="t", body="b", email_to="g@example.com")
        assert TransactionalEmailLog.objects.filter(trigger_kind="registration_confirmed").count() == 1


def describe_record_delivery():
    def it_returns_true_on_first_claim_and_false_after():
        user = User.objects.create_user(username="d", email="d@example.com")
        first = _record_delivery("class_published", user, Channel.IN_APP, "")
        second = _record_delivery("class_published", user, Channel.IN_APP, "")
        assert first is True
        assert second is False


def _with_discord(event):
    """Return a copy of ``event`` with the Discord broadcast channel appended."""
    import dataclasses

    from core.events.registry import Channel as Ch
    from core.events.registry import ChannelDefault, ChannelSpec

    extra = ChannelSpec(Ch.DISCORD, ChannelDefault.ON)
    return dataclasses.replace(event, channels=(*event.channels, extra))


def describe_broadcast_fan_out():
    """Discord is a per-event broadcast: posted ONCE per emit, not per recipient."""

    def it_posts_discord_once_regardless_of_recipient_count(monkeypatch, linked_member):
        from unittest.mock import patch

        from core.events import registry

        linked_member()
        linked_member()  # two recipients — discord must still post exactly once
        event = _with_discord(registry.get_event("site_announcement"))
        monkeypatch.setitem(registry._BY_KEY, "site_announcement", event)
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            result = emit("site_announcement", context={}, title="Hi all", body="b")
        assert mock_post.call_count == 1
        assert Channel.DISCORD in result.broadcast_channels

    def it_dedupes_the_broadcast_across_re_emits(monkeypatch, linked_member):
        from unittest.mock import patch

        from core.events import registry

        linked_member()
        event = _with_discord(registry.get_event("site_announcement"))
        monkeypatch.setitem(registry._BY_KEY, "site_announcement", event)
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit("site_announcement", context={}, title="t", body="b")
            second = emit("site_announcement", context={}, title="t", body="b")
        assert mock_post.call_count == 1  # second emit deduped via EventDelivery
        assert second.broadcast_channels == []

    def it_records_a_broadcast_eventdelivery_row(monkeypatch, linked_member):
        from unittest.mock import patch

        from core.events import registry

        linked_member()
        event = _with_discord(registry.get_event("site_announcement"))
        monkeypatch.setitem(registry._BY_KEY, "site_announcement", event)
        with patch("core.events.discord.post_embed", return_value=True):
            emit("site_announcement", context={}, title="t", body="b")
        assert EventDelivery.objects.filter(
            event_key="site_announcement", target_ref="broadcast", channel="discord"
        ).exists()
