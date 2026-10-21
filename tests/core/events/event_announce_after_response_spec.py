"""Specs for #845: publishing an event never waits on its member announcement.

``CommunityEvent.publish`` stamps ``announcement_owed_since`` and hands the announcement to
``core.deferred``; a background thread sends it after the response, and the
``finish_event_announcements`` cron completes a send the process did not live to finish.
Every runner goes through the ``event:{pk}:published`` period, so each member is told once.
Lives under ``tests/core/events`` for the ``linked_member`` and ``held_deferred`` fixtures.
"""

from __future__ import annotations

import threading
from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import deferred
from core.events import channels
from core.models import Notification
from membership.models import CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

if TYPE_CHECKING:
    from collections.abc import Callable

State = CommunityEvent.ModerationState
_KEY = "event.community_published"


def _admin(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _payload(title: str) -> dict:
    starts = timezone.localtime() + timedelta(days=30)
    return {
        "title": title,
        "starts_at": starts.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": (starts + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        "location": "Main Studio",
        "description": "Come help.",
        "recurrence": "none",
    }


def _bells(members: list) -> Counter:
    """How many launch bell rows each member holds."""
    rows = Notification.objects.filter(trigger=_KEY, user__in=[m.user for m in members])
    return Counter(rows.values_list("user_id", flat=True))


def _emails(members: list, mailoutbox: list) -> Counter:
    """How many emails each member received (the proposer's own approval email is not theirs)."""
    addresses = {m.user.email for m in members}
    return Counter(address for message in mailoutbox for address in message.to if address in addresses)


def _told_once(members: list, mailoutbox: list) -> None:
    assert _bells(members) == Counter({m.user.pk: 1 for m in members})
    assert _emails(members, mailoutbox) == Counter({m.user.email: 1 for m in members})


def _run(held: list) -> None:
    """Run the held background tasks the way the thread would, then forget them."""
    for _name, task in held:
        task()
    held.clear()


def _overdue(event: CommunityEvent) -> None:
    """Age the stamp past the grace, as if the process died longer ago than that."""
    CommunityEvent.objects.filter(pk=event.pk).update(
        announcement_owed_since=timezone.now() - CommunityEvent.ANNOUNCEMENT_FINISH_GRACE - timedelta(seconds=1)
    )


def _join_deferred_threads() -> None:
    for thread in threading.enumerate():
        if thread.name.startswith(deferred.THREAD_NAME_PREFIX):
            thread.join(timeout=10)
            assert not thread.is_alive()


def describe_publishing_a_site_wide_event():
    @pytest.mark.django_db(transaction=True)
    def it_responds_before_a_slow_fan_out_finishes(client: Client, linked_member, mailoutbox, monkeypatch):
        # The real after-commit thread, not the inline test schedule: the send blocks until
        # released, and the save has already answered by then. The thread waits for the
        # response before touching the database, as production's "right after the response"
        # timing does, because SQLite's shared-cache test database fails a read that overlaps
        # another connection's write ("database table is locked") instead of waiting.
        responded = threading.Event()

        def after_the_response(task: Callable[[], None], name: str) -> None:
            def waits_for_the_response() -> None:
                assert responded.wait(timeout=10)
                task()

            deferred.after_commit_thread(waits_for_the_response, name)

        monkeypatch.setattr(deferred, "_schedule", after_the_response)
        members = [linked_member() for _ in range(3)]
        _admin("slow_admin")
        client.login(username="slow_admin", password="pass")
        sending = threading.Event()
        release = threading.Event()
        real_send = channels.send_email

        def slow_send(**kwargs: object) -> object:
            sending.set()
            assert release.wait(timeout=10)
            return real_send(**kwargs)

        with patch("core.events.channels.send_email", side_effect=slow_send):
            # Were the fan-out still inline, this post would block in slow_send, since nothing
            # releases it until after the post returns.
            resp = client.post(reverse("hub_event_add"), data=_payload("Winterlights Workday"))
            assert resp.status_code == 302
            responded.set()
            assert sending.wait(timeout=10)
            event = CommunityEvent.objects.get(title="Winterlights Workday")
            assert event.moderation_state == State.PUBLISHED
            assert event.announcement_owed_since is not None
            assert mailoutbox == []
            release.set()
            _join_deferred_threads()

        event.refresh_from_db()
        assert event.announcement_owed_since is None
        _told_once(members, mailoutbox)

    @pytest.mark.django_db
    def it_owes_the_announcement_and_sends_none_of_it_in_the_request(
        client: Client, linked_member, mailoutbox, held_deferred
    ):
        members = [linked_member() for _ in range(2)]
        _admin("add_admin")
        client.login(username="add_admin", password="pass")

        resp = client.post(reverse("hub_event_add"), data=_payload("Potluck"))

        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Potluck")
        assert event.announcement_owed_since is not None
        assert [name for name, _task in held_deferred] == [f"event:{event.pk}:announce"]
        assert _bells(members) == Counter()
        assert mailoutbox == []
        _run(held_deferred)
        event.refresh_from_db()
        assert event.announcement_owed_since is None
        _told_once(members, mailoutbox)


def describe_every_publishing_route():
    @pytest.mark.django_db
    def it_defers_the_announcement_on_approve(client: Client, linked_member, mailoutbox, held_deferred):
        members = [linked_member() for _ in range(2)]
        event = CommunityEventFactory(community=True, pending=True)
        _admin("approver")
        client.login(username="approver", password="pass")

        client.post(reverse("hub_event_review_decision", args=[event.pk]) + "?decision=approve")

        event.refresh_from_db()
        assert event.moderation_state == State.PUBLISHED
        assert event.announcement_owed_since is not None
        assert _bells(members) == Counter()
        _run(held_deferred)
        _told_once(members, mailoutbox)

    @pytest.mark.django_db
    def it_defers_the_announcement_on_scheduled_publish(linked_member, mailoutbox, held_deferred):
        members = [linked_member() for _ in range(2)]
        event = CommunityEventFactory(
            community=True, moderation_state=State.SCHEDULED, publish_at=timezone.now() - timedelta(minutes=1)
        )

        call_command("publish_due_events")

        event.refresh_from_db()
        assert event.moderation_state == State.PUBLISHED
        assert event.announcement_owed_since is not None
        assert _bells(members) == Counter()
        _run(held_deferred)
        _told_once(members, mailoutbox)

    @pytest.mark.django_db
    def it_never_owes_studio_hours(held_deferred):
        event = CommunityEventFactory(studio_hours=True)

        event.publish()

        event.refresh_from_db()
        assert event.announcement_owed_since is None
        assert held_deferred == []


def describe_an_interrupted_send():
    @pytest.mark.django_db
    def it_is_finished_by_the_cron_and_tells_each_member_once(linked_member, mailoutbox, held_deferred):
        members = [linked_member() for _ in range(4)]
        event = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        event.schedule_or_go_live()
        real_send = channels.send_email
        sends = {"n": 0}

        def dies_on_the_third(**kwargs: object) -> object:
            sends["n"] += 1
            if sends["n"] == 3:
                raise SystemExit("worker shut down mid send")
            return real_send(**kwargs)

        with patch("core.events.channels.send_email", side_effect=dies_on_the_third), pytest.raises(SystemExit):
            _run(held_deferred)

        event.refresh_from_db()
        assert event.announcement_owed_since is not None
        assert len(mailoutbox) == 2
        _overdue(event)

        call_command("finish_event_announcements")
        call_command("finish_event_announcements")

        event.refresh_from_db()
        assert event.announcement_owed_since is None
        _told_once(members, mailoutbox)

    @pytest.mark.django_db
    def it_leaves_a_send_still_inside_its_grace_to_the_thread(linked_member, held_deferred):
        members = [linked_member()]
        event = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        event.schedule_or_go_live()

        call_command("finish_event_announcements")

        event.refresh_from_db()
        assert event.announcement_owed_since is not None
        assert _bells(members) == Counter()

    @pytest.mark.django_db
    def it_announces_nothing_for_an_event_taken_down_before_the_send(linked_member, mailoutbox, held_deferred):
        members = [linked_member()]
        event = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        event.schedule_or_go_live()
        CommunityEvent.objects.filter(pk=event.pk).update(moderation_state=State.DRAFT)

        _run(held_deferred)

        event.refresh_from_db()
        assert event.announcement_owed_since is None
        assert _bells(members) == Counter()
        assert mailoutbox == []

    @pytest.mark.django_db
    def it_skips_an_event_deleted_before_the_send(held_deferred):
        event = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        event.schedule_or_go_live()
        CommunityEvent.objects.filter(pk=event.pk).delete()

        _run(held_deferred)

        assert not CommunityEvent.objects.filter(pk=event.pk).exists()

    @pytest.mark.django_db
    def it_keeps_the_stamp_of_an_event_published_again_meanwhile(held_deferred):
        event = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        event.schedule_or_go_live()
        stale = CommunityEvent.objects.get(pk=event.pk)
        newer = timezone.now() + timedelta(seconds=5)
        CommunityEvent.objects.filter(pk=event.pk).update(announcement_owed_since=newer)

        assert stale.finish_announcement() is True

        event.refresh_from_db()
        assert event.announcement_owed_since == newer

    @pytest.mark.django_db
    def it_reports_nothing_owed_for_an_unstamped_event():
        event = CommunityEventFactory(community=True)
        assert event.finish_announcement() is False


def describe_the_finish_command():
    @pytest.mark.django_db
    def it_says_so_when_nothing_is_unfinished(capsys):
        call_command("finish_event_announcements")
        assert "No event announcement left unfinished." in capsys.readouterr().out

    @pytest.mark.django_db
    def it_counts_what_it_finished(capsys, held_deferred):
        event = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        event.schedule_or_go_live()
        _overdue(event)

        call_command("finish_event_announcements")

        assert "Finished 1 event announcement(s)." in capsys.readouterr().out

    @pytest.mark.django_db
    def it_does_not_count_an_unpublished_event_it_only_cleared(capsys, held_deferred):
        event = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        event.schedule_or_go_live()
        CommunityEvent.objects.filter(pk=event.pk).update(moderation_state=State.DRAFT)
        _overdue(event)

        call_command("finish_event_announcements")

        assert "No event announcement left unfinished." in capsys.readouterr().out
        event.refresh_from_db()
        assert event.announcement_owed_since is None

    @pytest.mark.django_db
    def it_fails_the_run_and_keeps_the_stamp_when_a_send_raises(capsys, held_deferred):
        event = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        event.schedule_or_go_live()
        _overdue(event)

        with (
            patch.object(CommunityEvent, "announce", side_effect=RuntimeError("resend down")),
            pytest.raises(RuntimeError, match=rf"event\(s\) \[{event.pk}\]"),
        ):
            call_command("finish_event_announcements")

        assert f"finish announcement for event {event.pk}: resend down" in capsys.readouterr().err
        event.refresh_from_db()
        assert event.announcement_owed_since is not None


def describe_duplicate():
    @pytest.mark.django_db
    def it_starts_the_copy_owing_nothing(held_deferred):
        source = CommunityEventFactory(community=True, moderation_state=State.DRAFT)
        source.schedule_or_go_live()

        copy = source.duplicate(by=User.objects.create_user(username="dup_by"))

        assert copy.announcement_owed_since is None
