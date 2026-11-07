"""BDD specs for the unpaid orientation bill reminders (#1120).

The daily ``send_orientation_bill_reminders`` job reminds the member 7, 14 and 30 local days
after a bill was sent, the latest step due only, decided under the booking row lock with the
step saved first, so a rerun sends nothing more. The 30 day reminder also tells the orientor
who ran it, in the bell and by email. Paid and cancelled bills get nothing, Resend Bill does
not reset the schedule, and the job can be switched off on Automations. Every run freezes
``timezone.now`` (6 AM Pacific unless a spec says otherwise), so nothing depends on the day or
hour the suite runs.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from billing import orientation_bills
from core.factories import ScheduledJobStateFactory
from core.models import Notification
from core.scheduled_jobs import JOBS_BY_KEY, Cadence
from membership.models import Member, OrientationBooking, OrientationBookingQuerySet
from tests.membership.factories import (
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

JOB_KEY = "send_orientation_bill_reminders"


def _local(year: int, month: int, day: int, hour: int = 6) -> datetime:
    """An aware moment at ``hour`` local time: the job's 6 AM Pacific slot by default."""
    return timezone.make_aware(datetime(year, month, day, hour))


def _account(username: str, name: str) -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=username, email=f"{username}@example.com").member
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    return member


def _bill(billed_at: datetime, *, oriented_by: Member | None = None, **fields: Any) -> OrientationBooking:
    """A $40 Reminder Lathe bill for Rina Reminded, sent at ``billed_at``, with the send's own email cleared."""
    member = _account(f"rem_member_{OrientationBooking.objects.count()}", "Rina Reminded")
    sender = User.objects.create_user(username=f"rem_sender_{OrientationBooking.objects.count()}")
    booking = orientation_bills.send_bill(
        member,
        OrientationTypeFactory(name="Reminder Lathe"),
        completed_on=timezone.localdate() - timedelta(days=1),
        oriented_by=oriented_by,
        amount_cents=4000,
        sent_by=sender,
    )
    OrientationBooking.objects.filter(pk=booking.pk).update(billed_at=billed_at, **fields)
    booking.refresh_from_db()
    mail.outbox.clear()
    return booking


def _run_on(moment: datetime) -> int:
    return _pass_on(moment).sent


def _pass_on(moment: datetime) -> orientation_bills.ReminderRun:
    with patch("django.utils.timezone.now", return_value=moment):
        return orientation_bills.send_due_reminders()


def _orientor_notices() -> int:
    return Notification.objects.filter(trigger="billing.orientation_bill_unpaid").count()


def _step(booking: OrientationBooking) -> int:
    booking.refresh_from_db(fields=["bill_reminder_step"])
    return booking.bill_reminder_step


def _member_mail(booking: OrientationBooking) -> list[Any]:
    address = booking.member.user.email
    return [message for message in mail.outbox if address in message.to]


def describe_bill_reminder_due():
    def it_is_nothing_on_the_sixth_day():
        booking = _bill(_local(2026, 1, 5, 9))
        assert booking.bill_reminder_due(_local(2026, 1, 11).date()) == 0

    def it_is_the_first_step_on_the_seventh_local_day_whatever_hour_it_was_sent():
        # Sent late in the evening: the 6 AM run on the seventh day is short of 7 x 24 hours.
        booking = _bill(_local(2026, 1, 5, 23))
        assert booking.bill_reminder_due(_local(2026, 1, 12).date()) == 1

    def it_counts_local_days_across_the_change_back_from_daylight_time():
        # Sent Monday Oct 26 at 3 PM PDT; Nov 1 2026 falls back; due Mon Nov 2 at 6 AM PST.
        booking = _bill(_local(2026, 10, 26, 15))
        assert booking.bill_reminder_due(_local(2026, 11, 1).date()) == 0
        assert booking.bill_reminder_due(_local(2026, 11, 2).date()) == 1

    def it_is_the_second_step_from_the_fourteenth_day_and_the_third_from_the_thirtieth():
        booking = _bill(_local(2026, 1, 1, 12))
        assert booking.bill_reminder_due(_local(2026, 1, 14).date()) == 1
        assert booking.bill_reminder_due(_local(2026, 1, 15).date()) == 2
        assert booking.bill_reminder_due(_local(2026, 1, 30).date()) == 2
        assert booking.bill_reminder_due(_local(2026, 1, 31).date()) == 3

    def it_is_only_the_latest_step_when_earlier_ones_were_missed():
        booking = _bill(_local(2026, 1, 1, 12))
        assert booking.bill_reminder_due(_local(2026, 1, 21).date()) == 2

    def it_is_nothing_once_that_step_was_had():
        booking = _bill(_local(2026, 1, 1, 12), bill_reminder_step=2)
        assert booking.bill_reminder_due(_local(2026, 1, 21).date()) == 0

    def it_never_comes_to_a_fourth():
        booking = _bill(_local(2026, 1, 1, 12), bill_reminder_step=3)
        assert booking.bill_reminder_due(_local(2026, 6, 1).date()) == 0

    def it_is_nothing_for_a_paid_or_cancelled_bill():
        paid = _bill(_local(2026, 1, 1, 12), paid_at=_local(2026, 1, 2, 12))
        cancelled = _bill(_local(2026, 1, 1, 12), bill_cancelled_at=_local(2026, 1, 2, 12))
        today = _local(2026, 1, 9).date()
        assert (paid.bill_reminder_due(today), cancelled.bill_reminder_due(today)) == (0, 0)

    def it_is_nothing_for_an_ordinary_booking():
        assert OrientationBookingFactory().bill_reminder_due(_local(2026, 6, 1).date()) == 0

    def it_names_the_thirtieth_day_as_the_last_step():
        assert [OrientationBooking.is_last_bill_reminder(step) for step in (1, 2, 3)] == [False, False, True]


def describe_bills_awaiting_reminder():
    def it_holds_unpaid_bills_sent_seven_or_more_local_days_ago_with_a_step_left():
        due = _bill(_local(2026, 1, 5, 23))
        _bill(_local(2026, 1, 6, 0))  # the sixth day on Jan 12
        _bill(_local(2026, 1, 1, 12), paid_at=_local(2026, 1, 2, 12))
        _bill(_local(2026, 1, 1, 12), bill_cancelled_at=_local(2026, 1, 2, 12))
        _bill(_local(2025, 12, 1, 12), bill_reminder_step=3)
        OrientationBookingFactory()
        awaiting = OrientationBooking.objects.bills_awaiting_reminder(_local(2026, 1, 12).date())
        assert list(awaiting) == [due]


def describe_send_due_reminders():
    def it_sends_one_reminder_at_seven_days_and_nothing_more_on_a_second_run_that_day():
        booking = _bill(_local(2026, 1, 5, 10))
        assert _run_on(_local(2026, 1, 12)) == 1
        assert _run_on(_local(2026, 1, 12, 7)) == 0
        assert len(mail.outbox) == 1
        assert _step(booking) == 1

    def it_says_it_is_a_reminder_with_the_bills_details_and_pay_link():
        booking = _bill(_local(2026, 1, 5, 10), oriented_by=MemberFactory(full_legal_name="Otto Orientor"))
        _run_on(_local(2026, 1, 12))
        [email] = mail.outbox
        assert email.to == [booking.member.user.email]
        assert email.subject == "Reminder: your Reminder Lathe orientation bill"
        for expected in (
            "is still unpaid. We sent it on Monday, January 5.",
            f"Orientation: Reminder Lathe, {booking.orientation_type.owner_name}",
            "Run by: Otto Orientor",
            "Amount: $40",
            f"Pay it online: {orientation_bills.bill_url(booking)}",
        ):
            assert expected in email.body
        html = email.alternatives[0][0]
        assert f'href="{orientation_bills.bill_url(booking)}"' in html
        assert "Pay $40</a>" in html

    def it_puts_the_reminder_in_the_members_bell_too():
        booking = _bill(_local(2026, 1, 5, 10))
        _run_on(_local(2026, 1, 12))
        bell = Notification.objects.get(user=booking.member.user, trigger="billing.orientation_bill_reminder")
        assert bell.title == "Reminder: your Reminder Lathe orientation bill"

    def it_reminds_at_seven_fourteen_and_thirty_days_and_never_a_fourth_time():
        booking = _bill(_local(2026, 1, 1, 12))
        sent = [_run_on(_local(2026, 1, day)) for day in range(2, 32)]
        sent += [_run_on(_local(2026, 2, day)) for day in range(1, 29)]
        assert sum(sent) == 3
        assert [index + 2 for index, count in enumerate(sent[:30]) if count] == [8, 15, 31]
        assert len(_member_mail(booking)) == 3
        assert _step(booking) == 3

    def it_catches_up_with_one_reminder_counted_as_the_fourteen_day_one():
        booking = _bill(_local(2026, 1, 1, 12))
        assert _run_on(_local(2026, 1, 20)) == 1
        assert len(mail.outbox) == 1
        assert _step(booking) == 2
        assert _run_on(_local(2026, 1, 21)) == 0
        assert _run_on(_local(2026, 1, 31)) == 1
        assert _step(booking) == 3

    def it_never_reminds_a_paid_bill():
        booking = _bill(_local(2026, 1, 1, 12), paid_at=_local(2026, 1, 3, 12))
        assert _run_on(_local(2026, 1, 8)) == 0
        assert mail.outbox == []
        assert _step(booking) == 0

    def it_never_reminds_a_cancelled_bill():
        booking = _bill(_local(2026, 1, 1, 12), bill_cancelled_at=_local(2026, 1, 3, 12))
        assert _run_on(_local(2026, 1, 31)) == 0
        assert mail.outbox == []
        assert _step(booking) == 0

    def it_stops_once_a_reminded_bill_is_paid():
        booking = _bill(_local(2026, 1, 1, 12))
        _run_on(_local(2026, 1, 8))
        OrientationBooking.objects.filter(pk=booking.pk).update(paid_at=_local(2026, 1, 9, 12))
        mail.outbox.clear()
        assert _run_on(_local(2026, 1, 15)) == 0
        assert mail.outbox == []

    def it_skips_a_bill_settled_after_it_was_listed_by_reading_it_again_under_the_lock():
        booking = _bill(_local(2026, 1, 1, 12), paid_at=_local(2026, 1, 3, 12))

        def listed_anyway(self: OrientationBookingQuerySet, today: Any) -> OrientationBookingQuerySet:
            return self.filter(pk=booking.pk)

        with patch.object(OrientationBookingQuerySet, "bills_awaiting_reminder", listed_anyway):
            assert _run_on(_local(2026, 1, 8)) == 0
        assert mail.outbox == []
        assert _step(booking) == 0

    def it_keeps_the_schedule_when_the_orientor_resends_the_bill():
        booking = _bill(_local(2026, 1, 1, 12))
        _run_on(_local(2026, 1, 8))
        with patch("django.utils.timezone.now", return_value=_local(2026, 1, 10, 9)):
            orientation_bills.resend_bill(booking)
        assert _step(booking) == 1
        assert _run_on(_local(2026, 1, 14)) == 0
        assert _run_on(_local(2026, 1, 15)) == 1
        assert _step(booking) == 2

    def it_sends_each_bill_its_own_step():
        first = _bill(_local(2026, 1, 1, 12))
        second = _bill(_local(2026, 1, 10, 12))
        assert _run_on(_local(2026, 1, 17)) == 2
        assert (_step(first), _step(second)) == (2, 1)
        assert len(_member_mail(first)) == len(_member_mail(second)) == 1


def describe_the_orientor_notice():
    def _orientor(username: str = "rem_orientor") -> Member:
        return _account(username, "Otto Orientor")

    def it_tells_the_orientor_in_the_bell_and_by_email_on_the_thirty_day_reminder():
        orientor = _orientor()
        booking = _bill(_local(2026, 1, 1, 12), oriented_by=orientor)
        _run_on(_local(2026, 1, 31))
        [email] = [message for message in mail.outbox if orientor.user.email in message.to]
        assert email.subject == "Rina Reminded has not paid their Reminder Lathe orientation bill"
        for expected in (
            "Hi Otto Orientor,",
            "Member: Rina Reminded",
            f"Orientation: Reminder Lathe, {booking.orientation_type.owner_name}",
            "Amount: $40",
            "Sent on: Thursday, January 1",
            f"Bookings tab: {orientation_bills._absolute_url(reverse('hub_orientations'))}?view=bookings&show=past",
        ):
            assert expected in email.body
        bell = Notification.objects.get(user=orientor.user, trigger="billing.orientation_bill_unpaid")
        assert bell.title == "Rina Reminded has not paid their orientation bill"
        assert bell.url == f"{reverse('hub_orientations')}?view=bookings&show=past"
        assert len(_member_mail(booking)) == 1

    def it_tells_the_orientor_once_even_on_a_rerun():
        orientor = _orientor()
        _bill(_local(2026, 1, 1, 12), oriented_by=orientor)
        _run_on(_local(2026, 1, 31))
        _run_on(_local(2026, 2, 1))
        assert Notification.objects.filter(user=orientor.user, trigger="billing.orientation_bill_unpaid").count() == 1

    def it_tells_the_orientor_when_a_catch_up_lands_on_the_thirty_day_step():
        orientor = _orientor()
        _bill(_local(2026, 1, 1, 12), oriented_by=orientor)
        assert _run_on(_local(2026, 2, 20)) == 1
        assert Notification.objects.filter(user=orientor.user, trigger="billing.orientation_bill_unpaid").exists()

    def it_leaves_the_orientor_out_of_the_earlier_reminders():
        orientor = _orientor()
        _bill(_local(2026, 1, 1, 12), oriented_by=orientor)
        _run_on(_local(2026, 1, 8))
        _run_on(_local(2026, 1, 15))
        assert [message for message in mail.outbox if orientor.user.email in message.to] == []
        assert not Notification.objects.filter(user=orientor.user, trigger="billing.orientation_bill_unpaid").exists()

    def it_skips_an_orientor_with_no_account_silently():
        booking = _bill(_local(2026, 1, 1, 12), oriented_by=MemberFactory(full_legal_name="Nell Nologin"))
        assert _run_on(_local(2026, 1, 31)) == 1
        assert mail.outbox == _member_mail(booking)
        assert not Notification.objects.filter(trigger="billing.orientation_bill_unpaid").exists()

    def it_skips_a_bill_with_nobody_recorded_as_orientor():
        booking = _bill(_local(2026, 1, 1, 12))
        assert _run_on(_local(2026, 1, 31)) == 1
        assert mail.outbox == _member_mail(booking)
        assert not Notification.objects.filter(trigger="billing.orientation_bill_unpaid").exists()


def describe_the_job():
    def it_is_a_switchable_daily_job_named_as_its_command():
        job = JOBS_BY_KEY[JOB_KEY]
        assert (job.command, job.cadence, job.toggleable, job.default_enabled) == (JOB_KEY, Cadence.DAILY, True, True)

    def it_sends_the_due_reminders_and_says_how_many():
        _bill(_local(2026, 1, 1, 12))
        with patch("django.utils.timezone.now", return_value=_local(2026, 1, 8)):
            out = io.StringIO()
            call_command(JOB_KEY, stdout=out)
        assert out.getvalue().strip() == "Sent 1 orientation bill reminder(s); skipped 0."
        assert len(mail.outbox) == 1

    def _dispatched_in_the_daily_hour() -> list[str]:
        # The dispatcher's daily gate is the 13:00 UTC hour.
        with (
            patch("core.management.commands.run_scheduled_tasks.call_command") as dispatched,
            patch(
                "core.management.commands.run_scheduled_tasks.timezone.now",
                return_value=datetime(2026, 1, 8, 13, tzinfo=UTC),
            ),
        ):
            call_command("run_scheduled_tasks")
        return [call.args[0] for call in dispatched.call_args_list]

    def it_runs_in_the_daily_slot():
        assert JOB_KEY in _dispatched_in_the_daily_hour()

    def it_does_not_run_when_switched_off_on_automations():
        ScheduledJobStateFactory(task_key=JOB_KEY, enabled=False)
        assert JOB_KEY not in _dispatched_in_the_daily_hour()


def describe_a_reminder_that_does_not_go_out():
    def it_puts_the_step_back_when_the_email_channel_refuses_the_send_and_retries_next_run():
        booking = _bill(_local(2026, 1, 1, 12))
        with patch("core.events.channels.EmailAdapter.deliver", return_value=False):
            run = _pass_on(_local(2026, 1, 8))
        assert (run.sent, run.failed) == (0, 1)
        assert _step(booking) == 0
        assert _run_on(_local(2026, 1, 9)) == 1
        assert len(_member_mail(booking)) == 1
        assert _step(booking) == 1

    def it_puts_the_step_back_when_the_send_raises_and_carries_on_with_the_other_bills(caplog):
        broken = _bill(_local(2026, 1, 1, 12))
        fine = _bill(_local(2026, 1, 1, 12))
        real_send = orientation_bills._send_reminder_email

        def send_or_raise(booking: OrientationBooking, step: int) -> bool:
            if booking.pk == broken.pk:
                raise RuntimeError("smtp down")
            return real_send(booking, step)

        with patch("billing.orientation_bills._send_reminder_email", side_effect=send_or_raise):
            run = _pass_on(_local(2026, 1, 8))
        assert (run.sent, run.failed) == (1, 1)
        assert (_step(broken), _step(fine)) == (0, 1)
        assert f"Orientation bill {broken.pk}: the reminder failed" in caplog.text
        assert _member_mail(broken) == []
        assert len(_member_mail(fine)) == 1

    def it_tells_the_orientor_only_once_the_members_last_reminder_went_out():
        orientor = _account("rem_orientor_fail", "Otto Orientor")
        booking = _bill(_local(2026, 1, 1, 12), oriented_by=orientor)
        with patch("core.events.channels.EmailAdapter.deliver", return_value=False):
            _pass_on(_local(2026, 1, 31))
        assert _orientor_notices() == 0
        assert _step(booking) == 0
        assert _run_on(_local(2026, 2, 1)) == 1
        assert _orientor_notices() == 1
        assert _step(booking) == 3

    def it_counts_an_email_the_ledger_already_holds_as_sent():
        booking = _bill(_local(2026, 1, 1, 12))
        _run_on(_local(2026, 1, 8))
        OrientationBooking.objects.filter(pk=booking.pk).update(bill_reminder_step=0)
        assert _run_on(_local(2026, 1, 8, 7)) == 1
        assert len(_member_mail(booking)) == 1
        assert _step(booking) == 1

    def it_never_winds_back_a_step_a_later_run_moved_on():
        booking = _bill(_local(2026, 1, 1, 12), bill_reminder_step=2)
        OrientationBooking.undo_bill_reminder(booking.pk, step=1, previous=0)
        assert _step(booking) == 2

    def it_fails_the_command_so_the_webmasters_hear_after_trying_every_bill():
        booking = _bill(_local(2026, 1, 1, 12))
        out = io.StringIO()
        with (
            patch("core.events.channels.EmailAdapter.deliver", return_value=False),
            patch("django.utils.timezone.now", return_value=_local(2026, 1, 8)),
            pytest.raises(CommandError, match="1 orientation bill reminder"),
        ):
            call_command(JOB_KEY, stdout=out)
        assert out.getvalue().strip() == "Sent 0 orientation bill reminder(s); skipped 0."
        assert _step(booking) == 0


def describe_a_bill_settled_while_its_reminder_was_being_sent():
    def it_skips_the_email_and_leaves_the_step_recorded():
        booking = _bill(_local(2026, 1, 1, 12))
        record = OrientationBooking.record_bill_reminder

        def record_then_paid(self: OrientationBooking, step: int) -> None:
            record(self, step)
            OrientationBooking.objects.filter(pk=self.pk).update(paid_at=_local(2026, 1, 8, 5))

        with patch.object(OrientationBooking, "record_bill_reminder", record_then_paid):
            run = _pass_on(_local(2026, 1, 8))
        assert (run.sent, run.failed, run.skipped) == (0, 0, 1)
        assert mail.outbox == []
        assert _step(booking) == 1


def describe_the_orientor_notices_day_count():
    def it_is_the_last_reminder_day_not_a_written_in_thirty():
        orientor = _account("rem_orientor_days", "Otto Orientor")
        _bill(_local(2026, 1, 1, 12), oriented_by=orientor)
        with patch.object(OrientationBooking, "BILL_REMINDER_DAYS", (7, 14, 45)):
            assert _run_on(_local(2026, 2, 15)) == 1
        [email] = [message for message in mail.outbox if orientor.user.email in message.to]
        assert "still unpaid after 45 days." in email.body
        assert "still unpaid after 45 days." in email.alternatives[0][0]


def describe_a_bill_that_can_never_be_emailed():
    def it_skips_a_member_with_a_blank_email_keeps_the_step_and_never_fails_the_run(caplog):
        booking = _bill(_local(2026, 1, 1, 12))
        User.objects.filter(pk=booking.member.user_id).update(email="")
        run = _pass_on(_local(2026, 1, 8))
        assert (run.sent, run.failed, run.skipped) == (0, 0, 1)
        assert _step(booking) == 1
        assert f"Orientation bill {booking.pk}: the member has no email address" in caplog.text
        next_day = _pass_on(_local(2026, 1, 9))
        assert (next_day.sent, next_day.failed, next_day.skipped) == (0, 0, 0)
        assert mail.outbox == []

    def it_skips_a_member_whose_account_is_gone():
        booking = _bill(_local(2026, 1, 1, 12))
        Member.objects.filter(pk=booking.member_id).update(user=None)
        run = _pass_on(_local(2026, 1, 8))
        assert (run.sent, run.failed, run.skipped) == (0, 0, 1)
        assert _step(booking) == 1

    def it_lets_the_command_pass_and_says_what_it_skipped():
        booking = _bill(_local(2026, 1, 1, 12))
        User.objects.filter(pk=booking.member.user_id).update(email="")
        out = io.StringIO()
        with patch("django.utils.timezone.now", return_value=_local(2026, 1, 8)):
            call_command(JOB_KEY, stdout=out)
        assert out.getvalue().strip() == "Sent 0 orientation bill reminder(s); skipped 1."

    def it_fails_and_retries_a_refused_send_on_its_due_day():
        booking = _bill(_local(2026, 1, 1, 12))
        with patch("core.events.channels.EmailAdapter.deliver", return_value=False):
            run = _pass_on(_local(2026, 1, 8))
        assert (run.sent, run.failed, run.skipped) == (0, 1, 0)
        assert _step(booking) == 0

    def it_still_retries_two_days_after_the_due_day():
        booking = _bill(_local(2026, 1, 1, 12))
        with patch("core.events.channels.EmailAdapter.deliver", return_value=False):
            run = _pass_on(_local(2026, 1, 10))
        assert run.failed == 1
        assert _step(booking) == 0

    def it_keeps_the_step_of_a_send_refused_three_days_past_due_and_lets_the_run_pass(caplog):
        booking = _bill(_local(2026, 1, 1, 12))
        with patch("core.events.channels.EmailAdapter.deliver", return_value=False):
            run = _pass_on(_local(2026, 1, 11))
        assert (run.sent, run.failed, run.skipped) == (0, 0, 1)
        assert _step(booking) == 1
        assert "giving up on it" in caplog.text
        assert _pass_on(_local(2026, 1, 12)).failed == 0

    def it_keeps_the_step_of_a_send_that_raises_three_days_past_due(caplog):
        booking = _bill(_local(2026, 1, 1, 12))
        with patch("billing.orientation_bills._send_reminder_email", side_effect=RuntimeError("smtp down")):
            run = _pass_on(_local(2026, 1, 11))
        assert (run.sent, run.failed, run.skipped) == (0, 0, 1)
        assert _step(booking) == 1
        assert f"Orientation bill {booking.pk}: reminder 1 raised past its retry days" in caplog.text

    def it_dates_each_step_from_the_local_day_the_bill_was_sent():
        booking = _bill(_local(2026, 1, 1, 23))
        due = [booking.bill_reminder_due_on(step) for step in (1, 2, 3)]
        assert due == [_local(2026, 1, 8).date(), _local(2026, 1, 15).date(), _local(2026, 1, 31).date()]


def describe_an_orientor_notice_that_raises():
    def it_is_logged_and_the_bill_still_counts_as_reminded(caplog):
        orientor = _account("rem_orientor_raise", "Otto Orientor")
        booking = _bill(_local(2026, 1, 1, 12), oriented_by=orientor)
        with patch("billing.orientation_bills._tell_orientor_unpaid", side_effect=RuntimeError("bell down")):
            run = _pass_on(_local(2026, 1, 31))
        assert (run.sent, run.failed, run.skipped) == (1, 0, 0)
        assert _step(booking) == 3
        assert len(_member_mail(booking)) == 1
        assert f"Orientation bill {booking.pk}: could not tell the orientor" in caplog.text
