"""BDD specs for queueing a results send and draining it on the scheduler.

Emailing the whole membership is a fan-out of roughly twenty queries per member and
runs longer than a web request is allowed to live. In September 2026 that killed the
worker mid-loop: most of the emails had gone out, ``results_sent_at`` was never
stamped, and the admin was shown a failure for a send that had largely succeeded.
So the admin's click now only records the request, and this job performs it.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.db.models.signals import post_save
from factory.django import mute_signals

from core.models import TransactionalEmailLog
from membership.models import (
    MAX_RESULTS_SEND_ATTEMPTS,
    FundingSnapshot,
    Member,
    ResultsAlreadySentError,
)
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _voter(email):
    """An active paying member with a linked, email-bearing user and a vote."""
    member = MemberFactory(member_type=Member.MemberType.STANDARD, status=Member.Status.ACTIVE)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    VotePreferenceFactory(
        member=member, guild_1st=GuildFactory(), guild_2nd=GuildFactory(), guild_3rd=GuildFactory(), signed_up=False
    )
    return member


def _sent_addresses() -> set[str]:
    return set(
        TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published", status="sent").values_list(
            "to_email", flat=True
        )
    )


def _snapshot_with(*emails):
    for email in emails:
        _voter(email)
    snap = FundingSnapshot.take()
    assert snap is not None
    mail.outbox.clear()
    return snap


def describe_queue_results_send():
    def it_records_the_request_without_sending_anything():
        snap = _snapshot_with("a@x.com")
        snap.queue_results_send()
        snap.refresh_from_db()
        assert snap.results_send_requested_at is not None
        assert snap.results_sent_at is None
        assert len(mail.outbox) == 0

    def it_drops_the_snapshot_off_the_review_banner():
        """A queued cycle is no longer waiting on the admin, so it must stop being offered."""
        snap = _snapshot_with("a@x.com")
        snap.queue_results_send()
        snap.refresh_from_db()
        assert snap.results_pending is False
        assert FundingSnapshot.most_recent_pending() is None

    def describe_when_results_were_already_sent():
        def it_refuses_a_plain_queue():
            snap = _snapshot_with("a@x.com")
            snap.queue_results_send()
            call_command("send_pending_funding_results")
            snap.refresh_from_db()
            with pytest.raises(ResultsAlreadySentError):
                snap.queue_results_send()

        def it_allows_a_deliberate_resend():
            snap = _snapshot_with("a@x.com")
            snap.queue_results_send()
            call_command("send_pending_funding_results")
            snap.refresh_from_db()
            snap.queue_results_send(resend=True)
            snap.refresh_from_db()
            assert snap.results_send_resend is True


def describe_a_send_that_was_never_queued():
    """The headless and auto paths must always stamp, even when members were missed.

    Nothing retries an unqueued send, so leaving it unstamped would hang the cycle in the
    admin UI forever. This pins the ``still_queued`` term in ``_finish_results_send``:
    without it, a direct send with one rejected address never stamps.
    """

    def it_stamps_even_though_a_member_was_missed():
        from core.email import _deliver as real_send

        snap = _snapshot_with("reached@x.com", "missed@x.com")

        def flaky(*args, **kwargs):
            if "missed@x.com" in kwargs.get("recipients", []):
                raise RuntimeError("rejected")
            return real_send(*args, **kwargs)

        with patch("core.email._deliver", side_effect=flaky):
            snap.send_results()

        snap.refresh_from_db()
        assert snap.results_sent_at is not None
        assert snap.results_send_requested_at is None
        assert _sent_addresses() == {"reached@x.com"}

    def it_reports_only_the_members_actually_emailed():
        """A written bell row is not a delivered results email."""
        from core.email import _deliver as real_send

        snap = _snapshot_with("reached@x.com", "missed@x.com")

        def flaky(*args, **kwargs):
            if "missed@x.com" in kwargs.get("recipients", []):
                raise RuntimeError("rejected")
            return real_send(*args, **kwargs)

        with patch("core.email._deliver", side_effect=flaky):
            sent = snap.send_results()
        assert sent == 1


def describe_picking_up_a_headless_run_that_died():
    """The admin clicks Send after a Render one-off run was killed partway.

    This is the documented incident-response path, and it is the one that most easily
    re-emails the whole membership: the click must continue the dead run's generation
    rather than opening a new one, AND it must actually send. Both halves ride on
    ``results_send_attempts``, which doubles as the retry signal and the attempt budget.
    """

    def _crash_a_headless_run(snap, victim="missed@x.com"):
        from core.email import _deliver as real_send

        def flaky(*args, **kwargs):
            if victim in kwargs.get("recipients", []):
                raise KeyboardInterrupt("worker killed")
            return real_send(*args, **kwargs)

        with patch("core.email._deliver", side_effect=flaky):
            with pytest.raises(KeyboardInterrupt):
                snap.send_results()
        snap.refresh_from_db()

    def it_continues_the_dead_generation_instead_of_re_emailing_everyone():
        snap = _snapshot_with("reached@x.com", "missed@x.com")
        _crash_a_headless_run(snap)
        assert snap.results_send_count == 1

        snap.queue_results_send()
        call_command("send_pending_funding_results")

        snap.refresh_from_db()
        assert snap.results_send_count == 1, "the click must not open a new generation"
        assert _sent_addresses() == {"reached@x.com", "missed@x.com"}
        assert (
            TransactionalEmailLog.objects.filter(
                trigger_kind="voting.results_published", status="sent", to_email="reached@x.com"
            ).count()
            == 1
        ), "nobody emailed twice"

    def it_still_sends_after_the_budget_was_spent_by_crashed_runs():
        """An inherited, already-spent budget must not turn the click into a no-op.

        Enough crashed headless runs would otherwise leave attempts at the cap, so the
        next tick abandoned before sending anything and stamped the cycle as sent.
        """
        snap = _snapshot_with("reached@x.com", "missed@x.com")
        for _ in range(MAX_RESULTS_SEND_ATTEMPTS + 1):
            _crash_a_headless_run(snap)
        assert snap.results_send_attempts >= MAX_RESULTS_SEND_ATTEMPTS

        snap.queue_results_send()
        call_command("send_pending_funding_results")

        snap.refresh_from_db()
        assert _sent_addresses() == {"reached@x.com", "missed@x.com"}
        assert snap.results_sent_at is not None


def describe_send_pending_funding_results():
    def it_does_nothing_when_no_send_is_queued():
        _snapshot_with("a@x.com")
        call_command("send_pending_funding_results")
        assert len(mail.outbox) == 0

    def it_sends_a_queued_snapshot_and_clears_the_flag():
        snap = _snapshot_with("a@x.com")
        snap.queue_results_send()
        call_command("send_pending_funding_results")
        snap.refresh_from_db()
        assert snap.results_sent_at is not None
        assert snap.results_send_requested_at is None
        assert snap.results_send_resend is False
        assert len(mail.outbox) == 1

    def it_re_emails_everyone_for_a_queued_resend():
        snap = _snapshot_with("a@x.com")
        snap.queue_results_send()
        call_command("send_pending_funding_results")
        snap.refresh_from_db()
        snap.queue_results_send(resend=True)
        call_command("send_pending_funding_results")
        snap.refresh_from_db()
        assert snap.results_send_count == 2
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 2

    def describe_when_a_run_dies_partway():
        def it_keeps_the_request_queued_so_the_next_tick_finishes_it():
            snap = _snapshot_with("a@x.com")
            snap.queue_results_send()
            with patch("core.events.channels.EmailAdapter.deliver", side_effect=RuntimeError("worker killed")):
                with pytest.raises(RuntimeError):
                    call_command("send_pending_funding_results")
            snap.refresh_from_db()
            assert snap.results_sent_at is None
            assert snap.results_send_requested_at is not None

        def it_emails_only_the_members_the_failed_run_missed():
            """The self-healing property: a retry is not a resend.

            The first attempt reaches one member and is rejected for the other. The
            rejected slot is released and the request stays queued, so the next tick
            runs on the SAME delivery generation — skipping the member already emailed
            and reaching only the one who was missed. This is the case that had to be
            repaired by hand against production in September 2026.
            """
            from core.email import _deliver as real_send

            snap = _snapshot_with("reached@x.com", "missed@x.com")
            snap.queue_results_send()

            def flaky(*args, **kwargs):
                if "missed@x.com" in kwargs.get("recipients", []):
                    raise RuntimeError("429 daily quota")
                return real_send(*args, **kwargs)

            with patch("core.email._deliver", side_effect=flaky):
                call_command("send_pending_funding_results")

            snap.refresh_from_db()
            assert _sent_addresses() == {"reached@x.com"}
            # Still queued and unstamped, because somebody was missed.
            assert snap.results_sent_at is None
            assert snap.results_send_requested_at is not None

            call_command("send_pending_funding_results")

            snap.refresh_from_db()
            assert _sent_addresses() == {"reached@x.com", "missed@x.com"}
            assert snap.results_sent_at is not None
            assert snap.results_send_requested_at is None
            # The generation never moved, so nobody was emailed twice.
            assert snap.results_send_count == 1
            assert (
                TransactionalEmailLog.objects.filter(
                    trigger_kind="voting.results_published", status="sent", to_email="reached@x.com"
                ).count()
                == 1
            )

        def it_says_out_loud_that_the_send_was_only_partial():
            """The job's output is the run record, and a silent partial send is the whole bug."""
            from core.email import _deliver as real_send

            snap = _snapshot_with("reached@x.com", "missed@x.com")
            snap.queue_results_send()

            def flaky(*args, **kwargs):
                if "missed@x.com" in kwargs.get("recipients", []):
                    raise RuntimeError("rejected")
                return real_send(*args, **kwargs)

            out = StringIO()
            with patch("core.email._deliver", side_effect=flaky):
                call_command("send_pending_funding_results", stdout=out)
            printed = out.getvalue()
            assert "some members were missed" in printed
            assert "attempt 1 of" in printed

        def it_gives_up_after_a_bounded_number_of_attempts():
            """An undeliverable address must not keep the request queued forever."""
            snap = _snapshot_with("a@x.com")
            snap.queue_results_send()
            out = StringIO()
            with patch("core.email._deliver", side_effect=RuntimeError("permanently rejected")):
                for _ in range(4):
                    call_command("send_pending_funding_results", stdout=out)
            snap.refresh_from_db()
            assert snap.results_send_attempts == 0
            assert snap.results_sent_at is not None
            assert snap.results_send_requested_at is None
            # Giving up must be loud. Stamping a snapshot "sent" while members were never
            # emailed, and saying nothing, is the original bug wearing a different hat.
            assert "Gave up" in out.getvalue()

        def it_stops_a_run_that_crashes_every_tick():
            """The budget has to bound a CRASHING send, not just a partly failing one.

            A run that raises never reaches its own finaliser, so a budget checked only at
            the end would let a deterministic crash retry every 15 minutes forever.
            """
            snap = _snapshot_with("a@x.com")
            snap.queue_results_send()
            with patch("core.events.channels.EmailAdapter.deliver", side_effect=RuntimeError("boom")):
                for _ in range(MAX_RESULTS_SEND_ATTEMPTS):
                    with pytest.raises(RuntimeError):
                        call_command("send_pending_funding_results")
                # Budget spent: this tick abandons instead of crashing again.
                call_command("send_pending_funding_results")
            snap.refresh_from_db()
            assert snap.results_send_requested_at is None
            assert snap.results_sent_at is not None
