"""``send_queued_announcements``: the scheduler job that sends what the composer queued.

A site-wide send reaches every active member and outlives a web request, so the composer only
queues it. This job sends each queued draft, oldest first, keeps going past one that fails,
and then fails loudly so the run record is red.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from membership.models import AnnouncementDraft, FundingSnapshot
from tests.membership.factories import FundingSnapshotFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _author(username: str = "felix") -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@x.com", password="pw")


def _queued(author: User, *, minutes_ago: int = 0, **fields) -> AnnouncementDraft:
    defaults = {"title": "Makerspace Announcement", "body": "<p>Hello</p>"}
    defaults.update(fields)
    draft = AnnouncementDraft.objects.create(author=author, **defaults)
    draft.queue_send()
    AnnouncementDraft.objects.filter(pk=draft.pk).update(
        send_requested_at=timezone.now() - timedelta(minutes=minutes_ago)
    )
    draft.refresh_from_db()
    return draft


def _results_snapshot() -> FundingSnapshot:
    return FundingSnapshotFactory(
        cycle_label="September 2026",
        funding_pool=Decimal("1000.00"),
        results={
            "votes_cast": 3,
            "results": [{"guild_name": "Metal Guild", "funding": "1000.00", "share_pct": 100.0}],
        },
    )


def describe_send_queued_announcements():
    def it_says_so_when_nothing_is_queued():
        out = StringIO()
        call_command("send_queued_announcements", stdout=out)
        assert out.getvalue().strip() == "No queued announcements."

    def it_sends_a_queued_draft_and_reports_it():
        draft = _queued(_author())
        out = StringIO()
        call_command("send_queued_announcements", stdout=out)
        draft.refresh_from_db()
        assert draft.sent_at is not None
        assert draft.send_requested_at is None
        assert f"Sent announcement #{draft.pk} (Makerspace Announcement) to 0 recipient(s)." in out.getvalue()

    def it_sends_the_oldest_request_first():
        author = _author()
        newer = _queued(author, minutes_ago=1)
        older = _queued(author, minutes_ago=10)
        order: list[int] = []
        real_send = AnnouncementDraft.send

        def recording(self):
            order.append(self.pk)
            return real_send(self)

        with patch.object(AnnouncementDraft, "send", recording):
            call_command("send_queued_announcements")
        assert order == [older.pk, newer.pk]

    def it_leaves_unqueued_and_sent_drafts_alone():
        author = _author()
        unqueued = AnnouncementDraft.objects.create(author=author, title="Makerspace Announcement", body="<p>x</p>")
        sent_at = timezone.now() - timedelta(days=1)
        sent = AnnouncementDraft.objects.create(
            author=author, title="Makerspace Announcement", body="<p>x</p>", sent_at=sent_at
        )
        out = StringIO()
        call_command("send_queued_announcements", stdout=out)
        unqueued.refresh_from_db()
        sent.refresh_from_db()
        assert unqueued.sent_at is None
        assert sent.sent_at == sent_at
        assert out.getvalue().strip() == "No queued announcements."

    def it_marks_a_results_announcements_snapshot_sent():
        snapshot = _results_snapshot()
        draft = snapshot.draft_results_announcement(_author())
        draft.queue_send()
        call_command("send_queued_announcements")
        snapshot.refresh_from_db()
        assert snapshot.results_sent_at is not None
        assert snapshot.results_send_count == 1

    def describe_when_a_send_raises():
        def it_keeps_that_draft_queued_sends_the_rest_and_fails_naming_it():
            author = _author()
            broken = _queued(author, minutes_ago=10)
            fine = _queued(author, minutes_ago=1)
            real_send = AnnouncementDraft.send

            def flaky(self):
                if self.pk == broken.pk:
                    raise RuntimeError("provider down")
                return real_send(self)

            with patch.object(AnnouncementDraft, "send", flaky):
                with pytest.raises(CommandError) as raised:
                    call_command("send_queued_announcements")
            assert str(raised.value) == (
                f"Could not send announcement #{broken.pk} (Makerspace Announcement): provider down"
            )
            broken.refresh_from_db()
            fine.refresh_from_db()
            assert broken.sent_at is None
            assert broken.send_requested_at is not None
            assert fine.sent_at is not None

        def it_names_every_draft_that_failed():
            author = _author()
            first = _queued(author, minutes_ago=10)
            second = _queued(author, minutes_ago=1)
            with patch.object(AnnouncementDraft, "send", side_effect=RuntimeError("boom")):
                with pytest.raises(CommandError) as raised:
                    call_command("send_queued_announcements")
            assert str(raised.value) == (
                f"Could not send announcement #{first.pk} (Makerspace Announcement): boom; "
                f"announcement #{second.pk} (Makerspace Announcement): boom"
            )

    def describe_when_the_results_already_went_out():
        def it_takes_the_draft_off_the_queue_and_fails_naming_it():
            snapshot = _results_snapshot()
            draft = snapshot.draft_results_announcement(_author())
            draft.queue_send()
            FundingSnapshot.objects.filter(pk=snapshot.pk).update(results_sent_at=timezone.now())

            with pytest.raises(CommandError) as raised:
                call_command("send_queued_announcements")

            assert str(raised.value) == (
                f"Could not send announcement #{draft.pk} (September 2026 Voting Results): "
                "These results were already sent. It was taken off the queue."
            )
            draft.refresh_from_db()
            assert draft.sent_at is None
            assert draft.send_requested_at is None
            # Off the queue, so the next tick is quiet instead of red forever.
            out = StringIO()
            call_command("send_queued_announcements", stdout=out)
            assert out.getvalue().strip() == "No queued announcements."


def describe_the_retry_cap():
    def it_keeps_retrying_and_keeps_the_error_until_the_third_failed_run():
        draft = _queued(_author())
        with patch.object(AnnouncementDraft, "send", side_effect=RuntimeError("provider down")):
            for attempt in (1, 2):
                with pytest.raises(CommandError) as raised:
                    call_command("send_queued_announcements")
                assert (
                    str(raised.value)
                    == f"Could not send announcement #{draft.pk} (Makerspace Announcement): provider down"
                )
                draft.refresh_from_db()
                assert draft.send_attempts == attempt
                assert draft.send_error == "provider down"
                assert draft.send_requested_at is not None

    def it_takes_the_draft_off_the_queue_after_the_third_failed_run():
        draft = _queued(_author())
        with patch.object(AnnouncementDraft, "send", side_effect=RuntimeError("provider down")):
            for _attempt in range(2):
                with pytest.raises(CommandError):
                    call_command("send_queued_announcements")
            with pytest.raises(CommandError) as raised:
                call_command("send_queued_announcements")
        assert str(raised.value) == (
            f"Could not send announcement #{draft.pk} (Makerspace Announcement): provider down "
            "It was taken off the queue after 3 attempt(s)."
        )
        draft.refresh_from_db()
        assert draft.send_requested_at is None
        assert draft.sent_at is None
        assert draft.send_attempts == 3
        assert draft.send_error == "provider down"
        assert draft.send_given_up is True
        # Off the queue: the next tick is quiet.
        out = StringIO()
        call_command("send_queued_announcements", stdout=out)
        assert out.getvalue().strip() == "No queued announcements."

    def it_gives_up_before_another_try_when_every_run_died_without_a_word():
        """A worker killed mid-send never reaches the handler; the count alone must bound the loop."""
        draft = _queued(_author())
        AnnouncementDraft.objects.filter(pk=draft.pk).update(send_attempts=3)
        with patch.object(AnnouncementDraft, "send") as send:
            with pytest.raises(CommandError) as raised:
                call_command("send_queued_announcements")
        send.assert_not_called()
        assert str(raised.value) == (
            f"Could not send announcement #{draft.pk} (Makerspace Announcement): Every attempt stopped before it "
            "finished. It was taken off the queue after 3 attempt(s)."
        )
        draft.refresh_from_db()
        assert draft.send_requested_at is None
        assert draft.send_error == "Every attempt stopped before it finished."

    def it_counts_an_attempt_even_when_the_run_is_killed():
        draft = _queued(_author())
        with patch.object(AnnouncementDraft, "send", side_effect=KeyboardInterrupt("worker killed")):
            with pytest.raises(KeyboardInterrupt):
                call_command("send_queued_announcements")
        draft.refresh_from_db()
        assert draft.send_attempts == 1
        assert draft.send_error == ""
        assert draft.send_requested_at is not None

    def it_keeps_only_the_start_of_a_long_error():
        draft = _queued(_author())
        with patch.object(AnnouncementDraft, "send", side_effect=RuntimeError("x" * 2000)):
            with pytest.raises(CommandError):
                call_command("send_queued_announcements")
        draft.refresh_from_db()
        assert draft.send_error == "x" * 500

    def it_clears_the_error_once_a_later_run_sends():
        draft = _queued(_author())
        with patch.object(AnnouncementDraft, "send", side_effect=RuntimeError("provider down")):
            with pytest.raises(CommandError):
                call_command("send_queued_announcements")
        call_command("send_queued_announcements")
        draft.refresh_from_db()
        assert draft.sent_at is not None
        assert draft.send_error == ""
        assert draft.send_attempts == 2

    def it_counts_two_overlapping_failed_runs_as_two_attempts():
        draft = _queued(_author())
        first_run = AnnouncementDraft.objects.get(pk=draft.pk)
        second_run = AnnouncementDraft.objects.get(pk=draft.pk)
        with patch.object(AnnouncementDraft, "send", side_effect=RuntimeError("provider down")):
            for run in (first_run, second_run):
                with pytest.raises(RuntimeError):
                    run.send_from_queue()
        draft.refresh_from_db()
        assert draft.send_attempts == 2
        assert second_run.send_attempts == 2
