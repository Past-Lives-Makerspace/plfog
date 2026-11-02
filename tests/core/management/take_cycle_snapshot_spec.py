"""BDD specs for the take_cycle_snapshot auto-snapshot command and its results-draft phase.

Time is frozen so the "just-closed cycle" is deterministic across the year boundary:
with ``now`` in July 2026 the closed cycle is June 2026 (period voting_close:2026-06).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.models import EventDelivery, Notification, ScheduledTaskRun, TransactionalEmailLog
from core.scheduled_jobs import Trigger, record_run
from membership.models import AnnouncementDraft, FundingSnapshot, Member, VotingSettings
from tests.membership.factories import FundingSnapshotFactory, GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _freeze(monkeypatch, moment):
    monkeypatch.setattr("core.management.commands.take_cycle_snapshot.timezone.now", lambda: moment)


def _linked(member, email):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    return user


def _voter(email):
    member = MemberFactory()
    _linked(member, email)
    g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
    VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
    return member


def _admin(email):
    member = MemberFactory(fog_role=Member.FogRole.ADMIN)
    return _linked(member, email)


def _july():
    return timezone.make_aware(datetime(2026, 7, 5, 9, 0))


def _manual_snapshot(label: str = "June 2026") -> FundingSnapshot:
    """A snapshot an admin took by hand, with per-guild results to announce."""
    return FundingSnapshotFactory(
        cycle_label=label,
        results={"votes_cast": 4, "results": [{"guild_name": "Metal", "funding": "100.00", "share_pct": 100.0}]},
    )


def _claim_the_cycle_slot() -> None:
    EventDelivery.objects.create(
        event_key="voting.auto_snapshot", target_ref="cycle", channel="system", period="voting_close:2026-06"
    )


def describe_take_cycle_snapshot():
    def it_auto_takes_once_per_cycle_flagging_is_auto_and_pinging_admins(monkeypatch):
        _freeze(monkeypatch, _july())
        voter = _voter("voter@x.com")
        admin_user = _admin("admin@x.com")

        call_command("take_cycle_snapshot")
        call_command("take_cycle_snapshot")  # second tick same cycle → no-op

        autos = FundingSnapshot.objects.filter(is_auto=True)
        assert autos.count() == 1
        snap = autos.first()
        assert snap.cycle_label == "June 2026"
        assert EventDelivery.objects.filter(event_key="voting.auto_snapshot", period="voting_close:2026-06").exists()
        # Admins are pinged straight away.
        assert Notification.objects.filter(user=admin_user, trigger="voting.results_ready").exists()

        # Members hear nothing from the auto snapshot. The same tick makes the month's results
        # draft for an admin to check and send; sending here as well would give everyone the old
        # results email first and the admin's announcement second.
        assert snap.results_sent_at is None
        assert snap.results_send_requested_at is None
        assert snap.results_pending is True
        assert not Notification.objects.filter(user=voter.user).exists()
        assert not TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").exists()
        draft = AnnouncementDraft.objects.get()
        assert (draft.funding_snapshot, draft.author, draft.title) == (snap, None, "June 2026 Voting Results")
        assert (draft.sent_at, draft.send_requested_at) == (None, None)

    def it_is_a_noop_when_auto_snapshot_is_disabled(monkeypatch):
        settings = VotingSettings.load()
        settings.auto_snapshot_enabled = False
        settings.save()
        _freeze(monkeypatch, _july())
        _voter("voter@x.com")

        call_command("take_cycle_snapshot")

        assert FundingSnapshot.objects.count() == 0
        assert not EventDelivery.objects.filter(event_key="voting.auto_snapshot").exists()

    def it_is_a_noop_when_there_are_no_votes(monkeypatch):
        _freeze(monkeypatch, _july())

        call_command("take_cycle_snapshot")

        assert FundingSnapshot.objects.count() == 0

    def it_skips_when_a_manual_snapshot_already_covers_the_cycle_even_with_a_custom_title(monkeypatch):
        _freeze(monkeypatch, _july())
        _voter("voter@x.com")
        # An admin already took the month's snapshot under a non-default title.
        FundingSnapshot.take(title="Q2 wrap-up", is_auto=False)

        call_command("take_cycle_snapshot")

        # The window guard (snapshot_at >= cycle_start) suppresses the auto-take.
        assert not FundingSnapshot.objects.filter(is_auto=True).exists()
        assert FundingSnapshot.objects.count() == 1


def describe_the_results_draft_phase():
    def it_makes_the_draft_on_a_tick_after_the_months_slot_is_claimed(monkeypatch):
        _freeze(monkeypatch, _july())
        snapshot = _manual_snapshot()
        _claim_the_cycle_slot()

        call_command("take_cycle_snapshot")

        draft = AnnouncementDraft.objects.get()
        assert (draft.funding_snapshot, draft.author) == (snapshot, None)
        snapshot.refresh_from_db()
        assert snapshot.results_draft_created_at is not None

    def it_makes_the_draft_with_the_auto_snapshot_switched_off(monkeypatch):
        settings = VotingSettings.load()
        settings.auto_snapshot_enabled = False
        settings.save()
        _freeze(monkeypatch, _july())
        snapshot = _manual_snapshot()

        call_command("take_cycle_snapshot")

        assert AnnouncementDraft.objects.get().funding_snapshot == snapshot
        assert not FundingSnapshot.objects.filter(is_auto=True).exists()

    def it_makes_nothing_after_an_admin_opened_the_draft_and_deleted_it(monkeypatch):
        """Click, delete, tick: a draft thrown away never comes back on its own."""
        _freeze(monkeypatch, _july())
        snapshot = _manual_snapshot()
        admin = _admin("admin@x.com")
        snapshot.draft_results_announcement(admin).delete()

        call_command("take_cycle_snapshot")

        assert not AnnouncementDraft.objects.exists()

    def it_makes_it_once_however_many_ticks_run(monkeypatch):
        _freeze(monkeypatch, _july())
        _manual_snapshot()
        call_command("take_cycle_snapshot")
        call_command("take_cycle_snapshot")
        assert AnnouncementDraft.objects.count() == 1

    def it_says_which_draft_it_made(monkeypatch, capsys):
        _freeze(monkeypatch, _july())
        _manual_snapshot()
        call_command("take_cycle_snapshot")
        assert "Made the 'June 2026 Voting Results' draft on the Announcements page." in capsys.readouterr().out

    def it_fails_the_run_when_making_the_draft_raises(monkeypatch):
        _freeze(monkeypatch, _july())
        _manual_snapshot()

        def broken() -> None:
            raise RuntimeError("draft builder broke")

        monkeypatch.setattr(FundingSnapshot, "make_newest_results_draft", broken)
        with pytest.raises(RuntimeError, match="draft builder broke"):
            with record_run("take_cycle_snapshot", trigger=Trigger.SCHEDULED):
                call_command("take_cycle_snapshot")
        assert ScheduledTaskRun.objects.get(task_key="take_cycle_snapshot").status == ScheduledTaskRun.Status.FAILED


def _pacific(y, m, d, h=0, minute=0):
    return timezone.make_aware(datetime(y, m, d, h, minute))


def _snapshot_taken_at(monkeypatch, moment, label):
    """A snapshot an admin took by hand at ``moment`` (``snapshot_at`` follows the frozen clock)."""
    _freeze(monkeypatch, moment)
    return FundingSnapshot.take(title=label)


def describe_the_month_closing_in_local_time():
    """#1078: the cycle closes at local midnight on the 1st, not at 5 PM Pacific when UTC rolls over."""

    def it_does_nothing_at_five_pm_on_the_last_day_of_the_month(monkeypatch):
        _voter("voter@x.com")
        _snapshot_taken_at(monkeypatch, _pacific(2026, 10, 2, 10), "September 2026")

        _freeze(monkeypatch, _pacific(2026, 10, 31, 17, 1))  # already November in UTC
        call_command("take_cycle_snapshot")

        assert FundingSnapshot.objects.count() == 1
        assert not EventDelivery.objects.filter(
            event_key="voting.auto_snapshot", period="voting_close:2026-10"
        ).exists()

    def it_takes_the_closed_month_after_local_midnight_with_last_months_snapshot_taken_on_the_second(monkeypatch):
        _voter("voter@x.com")
        _snapshot_taken_at(monkeypatch, _pacific(2026, 10, 2, 10), "September 2026")

        _freeze(monkeypatch, _pacific(2026, 11, 1, 0, 1))
        call_command("take_cycle_snapshot")

        auto = FundingSnapshot.objects.get(is_auto=True)
        assert auto.cycle_label == "October 2026"
        assert EventDelivery.objects.filter(event_key="voting.auto_snapshot", period="voting_close:2026-10").exists()

    def it_skips_when_an_admin_already_took_the_closed_month_by_hand_on_the_first(monkeypatch):
        _voter("voter@x.com")
        _snapshot_taken_at(monkeypatch, _pacific(2026, 10, 2, 10), "September 2026")
        _snapshot_taken_at(monkeypatch, _pacific(2026, 11, 1, 0, 5), "October wrap-up")

        _freeze(monkeypatch, _pacific(2026, 11, 1, 0, 16))
        call_command("take_cycle_snapshot")

        assert not FundingSnapshot.objects.filter(is_auto=True).exists()
        assert FundingSnapshot.objects.count() == 2

    def it_takes_december_after_local_midnight_on_new_years_day(monkeypatch):
        _voter("voter@x.com")
        _freeze(monkeypatch, _pacific(2026, 12, 31, 17, 30))  # already January in UTC
        call_command("take_cycle_snapshot")
        # Still December locally: the closed cycle is November, never December.
        assert list(FundingSnapshot.objects.filter(is_auto=True).values_list("cycle_label", flat=True)) == [
            "November 2026"
        ]
        assert not EventDelivery.objects.filter(period="voting_close:2026-12").exists()

        _freeze(monkeypatch, _pacific(2027, 1, 1, 0, 1))
        call_command("take_cycle_snapshot")

        assert FundingSnapshot.objects.filter(is_auto=True).first().cycle_label == "December 2026"
        assert EventDelivery.objects.filter(event_key="voting.auto_snapshot", period="voting_close:2026-12").exists()


def describe_a_failed_take():
    def it_leaves_the_months_slot_unclaimed_so_the_next_tick_retries(monkeypatch):
        _voter("voter@x.com")
        _freeze(monkeypatch, _pacific(2026, 11, 1, 0, 1))
        real_take = FundingSnapshot.take.__func__

        def broken(cls, **kwargs):
            raise RuntimeError("take broke")

        monkeypatch.setattr(FundingSnapshot, "take", classmethod(broken))
        with pytest.raises(RuntimeError, match="take broke"):
            call_command("take_cycle_snapshot")

        assert not EventDelivery.objects.filter(event_key="voting.auto_snapshot").exists()

        monkeypatch.setattr(FundingSnapshot, "take", classmethod(real_take))
        call_command("take_cycle_snapshot")
        assert FundingSnapshot.objects.get(is_auto=True).cycle_label == "October 2026"
