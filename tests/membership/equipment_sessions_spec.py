"""BDD specs for Sessions (#1096) on the model: Start Session and Stop Session on a member's own reservation.

Start records the time once; Stop records the time and credits the rest through End Early's own
code (the last half hour mark, the time left rounded up, one ENDED_EARLY line, the once per
reservation guard), whether or not End Early is on; under half an hour left, or credit already
given, it records the time and credits nothing. A session never stopped reads the booked end as
its stop. Every gate refuses and changes nothing. Names are factory strings no changelog could
contain (STANDARDS.md §8).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models import QuerySet
from django.utils import timezone

from billing.models import TabEntry
from membership.models import (
    Equipment,
    EquipmentCredit,
    EquipmentError,
    EquipmentReservation,
    Member,
    SessionStop,
)
from tests.membership.factories import EquipmentFactory, EquipmentReservationFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _at(hour: int, minute: int = 0, day=TODAY) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _kiln(**kwargs: Any) -> Equipment:
    kwargs.setdefault("sessions_enabled", True)
    kwargs.setdefault("name", "Quillfern Laser")
    return EquipmentFactory(
        pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, max_duration_minutes=480, **kwargs
    )


def _member(username: str = "bramblewick") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Thistledown"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _booking(item: Equipment, member: Member, **kwargs: Any) -> EquipmentReservation:
    """A 9 to 4 booking today, paid in full."""
    kwargs.setdefault("starts_at", _at(9))
    kwargs.setdefault("ends_at", _at(16))
    return EquipmentReservationFactory(equipment=item, member=member, amount_paid_cents=17500, **kwargs)


def _credit_lines(member: Member, item: Equipment) -> list[tuple[str, int]]:
    return list(EquipmentCredit.objects.filter(member=member, equipment=item).values_list("kind", "minutes"))


def describe_start_session():
    def it_records_the_start_time_once():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)

        first = booking.start_session(member, now=_at(9, 5))
        again = booking.start_session(member, now=_at(9, 40))

        assert (first.started_at, first.already_started) == (_at(9, 5), False)
        assert (again.started_at, again.already_started) == (_at(9, 5), True)
        booking.refresh_from_db()
        assert booking.session_started_at == _at(9, 5)
        # Starting never touches the booking's time or the ledger.
        assert (booking.starts_at, booking.ends_at) == (_at(9), _at(16))
        assert not EquipmentCredit.objects.exists()

    def it_keeps_the_first_time_when_a_second_tap_loses_the_race(monkeypatch: pytest.MonkeyPatch):
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9, 5))
        # The second tap read the row before the first one wrote it: its conditional update then matches nothing.
        real_get = QuerySet.get

        def read_before_the_first_tap(self: QuerySet, *args: Any, **kwargs: Any) -> Any:
            row = real_get(self, *args, **kwargs)
            if isinstance(row, EquipmentReservation):
                row.session_started_at = None
            return row

        monkeypatch.setattr(QuerySet, "get", read_before_the_first_tap)
        result = booking.start_session(member, now=_at(9, 6))
        assert (result.started_at, result.already_started) == (_at(9, 5), True)
        assert booking.session_started_at == _at(9, 5)

    @pytest.mark.parametrize(
        ("setup", "message"),
        [
            ("other_member", "Only the member who booked this can start or stop its session."),
            ("block", "Only the member who booked this can start or stop its session."),
            ("season", "A season reservation has no sessions."),
            ("switch_off", "Sessions aren't turned on for this equipment."),
            ("free_item", "Sessions aren't turned on for this equipment."),
            ("cancelled", "Only a confirmed reservation has a session."),
            ("declined", "Only a confirmed reservation has a session."),
            ("pending_approval", "Only a confirmed reservation has a session."),
            ("pending_payment", "Only a confirmed reservation has a session."),
            ("before_start", "This reservation starts at 9:00 AM. Start your session then."),
            ("after_end", "This reservation already ended."),
        ],
    )
    def it_refuses_and_records_nothing(setup: str, message: str):
        item = _kiln()
        member = _member()
        actor = member
        now = _at(10)
        booking = _booking(item, member)
        rows = EquipmentReservation.objects.filter(pk=booking.pk)
        if setup == "other_member":
            actor = _member("hollowmere")
        elif setup == "block":
            rows.update(kind=EquipmentReservation.Kind.BLOCK)
        elif setup == "season":
            rows.update(is_season=True)
        elif setup == "switch_off":
            Equipment.objects.filter(pk=item.pk).update(sessions_enabled=False)
        elif setup == "free_item":
            Equipment.objects.filter(pk=item.pk).update(pricing=Equipment.Pricing.FREE)
        elif setup in ("cancelled", "declined", "pending_approval", "pending_payment"):
            rows.update(status=setup)
        elif setup == "before_start":
            now = _at(8, 50)
        elif setup == "after_end":
            now = _at(16)
        with pytest.raises(EquipmentError, match=f"^{message}$".replace(".", r"\.")):
            booking.start_session(actor, now=now)
        booking.refresh_from_db()
        assert booking.session_started_at is None


def describe_stop_session():
    def it_records_the_stop_and_credits_the_rest_exactly_as_end_early_does():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9, 5))
        mail.outbox.clear()

        result = booking.stop_session(member, now=_at(10, 20))

        # 5 hours 40 minutes were left; 6 hours come back, the booking ends at 10:00.
        assert result == SessionStop(stopped_at=_at(10, 20), minutes=360, note="", already_stopped=False)
        booking.refresh_from_db()
        assert (booking.session_started_at, booking.session_stopped_at) == (_at(9, 5), _at(10, 20))
        assert booking.ends_at == _at(10)
        assert _credit_lines(member, item) == [(EquipmentCredit.Kind.ENDED_EARLY, 360)]
        assert EquipmentCredit.objects.get().reservation_id == booking.pk
        # No fee, no refund, no email, and nothing on My Tab.
        assert booking.status == EquipmentReservation.Status.CONFIRMED
        assert booking.amount_paid_cents == 17500
        assert mail.outbox == []
        assert not TabEntry.objects.exists()

    @pytest.mark.parametrize("stop", [(9, 40), (10, 20), (12, 0), (15, 29)])
    def it_matches_end_early_minute_for_minute(stop: tuple[int, int]):
        member = _member()
        stopped = _booking(_kiln(), member)
        ended = _booking(_kiln(name="Quillfern Router", early_end_credit=True, sessions_enabled=False), member)
        stopped.start_session(member, now=_at(9))
        assert stopped.stop_session(member, now=_at(*stop)).minutes == ended.end_early(member, now=_at(*stop))
        stopped.refresh_from_db()
        ended.refresh_from_db()
        assert stopped.ends_at == ended.ends_at

    def it_credits_even_with_the_end_early_switch_off():
        item = _kiln(early_end_credit=False)
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9))
        assert booking.stop_session(member, now=_at(13)).minutes == 180

    def it_rounds_a_second_past_the_mark_in_the_members_favour():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9))
        result = booking.stop_session(member, now=_at(12) + timedelta(seconds=1))
        assert result.minutes == 240
        booking.refresh_from_db()
        assert booking.ends_at == _at(12)

    def it_never_ends_inside_the_first_half_hour():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9))
        result = booking.stop_session(member, now=_at(9, 10))
        # Capped at the booking's length, as End Early is.
        assert result.minutes == 420
        booking.refresh_from_db()
        assert booking.ends_at == _at(9, 30)

    def it_records_the_stop_and_credits_nothing_with_under_half_an_hour_left():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9))

        result = booking.stop_session(member, now=_at(15, 40))

        assert result == SessionStop(
            stopped_at=_at(15, 40),
            minutes=0,
            note="Less than half an hour is left, so there is nothing to credit.",
            already_stopped=False,
        )
        booking.refresh_from_db()
        assert booking.session_stopped_at == _at(15, 40)
        assert booking.ends_at == _at(16)
        assert not EquipmentCredit.objects.exists()

    def it_records_the_stop_and_credits_nothing_once_a_manager_credited_unused_time():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9))
        EquipmentCredit.objects.create(
            equipment=item, member=member, minutes=60, kind=EquipmentCredit.Kind.UNUSED, reservation=booking
        )
        result = booking.stop_session(member, now=_at(11))
        assert (result.minutes, result.note) == (0, "Unused time on this reservation was already credited.")
        assert _credit_lines(member, item) == [(EquipmentCredit.Kind.UNUSED, 60)]

    def it_does_not_credit_twice_on_a_second_stop():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9))
        booking.stop_session(member, now=_at(10, 20))

        again = booking.stop_session(member, now=_at(10, 25))
        stale = EquipmentReservation.objects.get(pk=booking.pk).stop_session(member, now=_at(10, 26))

        assert again == SessionStop(stopped_at=_at(10, 20), minutes=0, note="", already_stopped=True)
        assert stale.already_stopped
        assert _credit_lines(member, item) == [(EquipmentCredit.Kind.ENDED_EARLY, 360)]

    @pytest.mark.parametrize(
        ("setup", "message"),
        [
            ("other_member", "Only the member who booked this can start or stop its session."),
            ("season", "A season reservation has no sessions."),
            ("cancelled", "Only a confirmed reservation has a session."),
            ("not_started", "Start your session first."),
            ("after_end", "This reservation already ended, so its session ended with it."),
        ],
    )
    def it_refuses_and_changes_nothing(setup: str, message: str):
        item = _kiln()
        member = _member()
        actor = member
        now = _at(10, 20)
        booking = _booking(item, member, session_started_at=_at(9))
        rows = EquipmentReservation.objects.filter(pk=booking.pk)
        if setup == "other_member":
            actor = _member("hollowmere")
        elif setup == "season":
            rows.update(is_season=True)
        elif setup == "switch_off":
            Equipment.objects.filter(pk=item.pk).update(sessions_enabled=False)
        elif setup == "cancelled":
            rows.update(status=EquipmentReservation.Status.CANCELLED)
        elif setup == "not_started":
            rows.update(session_started_at=None)
        elif setup == "after_end":
            now = _at(16, 5)
        with pytest.raises(EquipmentError, match=f"^{message}$".replace(".", r"\.")):
            booking.stop_session(actor, now=now)
        booking.refresh_from_db()
        assert booking.session_stopped_at is None
        assert booking.ends_at == _at(16)
        assert not EquipmentCredit.objects.exists()


def describe_the_shown_stop_time():
    def it_is_empty_before_a_start_and_while_running():
        item = _kiln()
        member = _member()
        now = timezone.now()
        booking = _booking(item, member, starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=2))
        assert booking.session_ended_at is None
        booking.session_started_at = now - timedelta(minutes=50)
        assert booking.session_ended_at is None

    def it_reads_the_booked_end_once_a_session_left_running_is_over():
        item = _kiln()
        member = _member()
        now = timezone.now()
        booking = _booking(
            item,
            member,
            starts_at=now - timedelta(hours=3),
            ends_at=now - timedelta(minutes=5),
            session_started_at=now - timedelta(hours=3),
        )
        assert booking.session_ended_at == booking.ends_at
        # Nothing is written and nothing is charged: no overtime, no My Tab line.
        booking.refresh_from_db()
        assert booking.session_stopped_at is None
        assert not TabEntry.objects.exists()
        assert not EquipmentCredit.objects.exists()

    def it_reads_the_stop_session_time_once_stopped():
        item = _kiln()
        member = _member()
        booking = _booking(item, member, session_started_at=_at(9), session_stopped_at=_at(10, 20))
        assert booking.session_ended_at == _at(10, 20)


def describe_which_button_shows():
    def it_offers_start_then_stop_on_the_members_own_booking_in_progress():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        now = _at(10)
        assert booking.can_start_session(member, now=now)
        assert not booking.can_stop_session(member, now=now)
        booking.session_started_at = _at(9, 5)
        assert not booking.can_start_session(member, now=now)
        assert booking.can_stop_session(member, now=now)
        booking.session_stopped_at = _at(9, 50)
        assert not booking.can_stop_session(member, now=now)

    def it_offers_nothing_before_the_start_after_the_end_or_to_anyone_else():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        assert not booking.can_start_session(member, now=_at(8, 59))
        assert not booking.can_start_session(member, now=_at(16))
        assert not booking.can_start_session(None, now=_at(10))
        assert not booking.can_start_session(_member("hollowmere"), now=_at(10))
        booking.is_season = True
        assert not booking.can_start_session(member, now=_at(10))
        booking.is_season = False
        item.sessions_enabled = False
        assert not booking.can_start_session(member, now=_at(10))

    def it_says_what_stop_would_credit():
        item = _kiln()
        booking = _booking(item, _member())
        assert booking.session_stop_minutes(_at(10, 20), credited_minutes=0) == 360
        assert booking.session_stop_minutes(_at(15, 40), credited_minutes=0) == 0
        assert booking.session_stop_minutes(_at(10, 20), credited_minutes=30) == 0

    def it_attaches_the_offer_the_lists_render():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.attach_session_offer(member, now=_at(10), credited_minutes=0)
        assert (booking.session_action, booking.session_stop_hours) == ("start", "")
        booking.session_started_at = _at(9, 5)
        booking.attach_session_offer(member, now=_at(10, 20), credited_minutes=0)
        assert (booking.session_action, booking.session_stop_hours) == ("stop", "6 hours")
        late = _booking(item, member, starts_at=_at(17), ends_at=_at(18), session_started_at=_at(17))
        late.attach_session_offer(member, now=_at(17, 40), credited_minutes=0)
        assert (late.session_action, late.session_stop_hours) == ("stop", "")
        other = _booking(item, member, starts_at=_at(19), ends_at=_at(20))
        other.attach_session_offer(member, now=_at(10), credited_minutes=0)
        assert (other.session_action, other.session_stop_hours) == ("", "")

    def it_takes_end_early_off_the_row_while_sessions_are_on():
        item = _kiln(early_end_credit=True)
        member = _member()
        booking = _booking(item, member)
        assert not booking.can_end_early(member, now=_at(10, 20), credited_minutes=0)
        item.sessions_enabled = False
        assert booking.can_end_early(member, now=_at(10, 20), credited_minutes=0)


def describe_the_session_qr():
    def it_encodes_one_stable_url_per_item_never_a_reservation():
        item = _kiln(slug="quillfern-laser")
        assert item.session_qr_url.endswith("/equipment/quillfern-laser/session/")
        assert item.session_qr_svg().startswith("<svg")
        assert item.session_qr_png_bytes().startswith(b"\x89PNG")

    def it_counts_sessions_only_on_an_hourly_item_with_the_switch_on():
        assert _kiln().has_sessions
        assert not _kiln(name="Quillfern Saw", sessions_enabled=False).has_sessions
        assert not EquipmentFactory(name="Quillfern Free Press", sessions_enabled=True).has_sessions


def describe_fix_round():
    def it_refuses_end_early_while_sessions_are_on():
        item = _kiln(early_end_credit=True)
        member = _member()
        booking = _booking(item, member)
        with pytest.raises(EquipmentError, match="^Sessions are on for this equipment. Use Stop Session instead.$"):
            booking.end_early(member, now=_at(10, 20))
        booking.refresh_from_db()
        assert booking.ends_at == _at(16)
        assert not EquipmentCredit.objects.exists()

    def it_still_stops_a_started_session_after_the_switch_goes_off():
        item = _kiln()
        member = _member()
        booking = _booking(item, member)
        booking.start_session(member, now=_at(9))
        Equipment.objects.filter(pk=item.pk).update(sessions_enabled=False)
        fresh = EquipmentReservation.objects.select_related("equipment").get(pk=booking.pk)
        assert fresh.can_stop_session(member, now=_at(10, 20))
        assert booking.stop_session(member, now=_at(10, 20)).minutes == 360
        assert _credit_lines(member, item) == [(EquipmentCredit.Kind.ENDED_EARLY, 360)]

    def it_still_refuses_a_start_or_a_stranger_once_the_switch_is_off():
        item = _kiln()
        member = _member()
        booking = _booking(item, member, session_started_at=_at(9))
        Equipment.objects.filter(pk=item.pk).update(sessions_enabled=False)
        with pytest.raises(EquipmentError, match="^Only the member who booked this"):
            booking.stop_session(_member("hollowmere"), now=_at(10))
        unstarted = _booking(item, member, starts_at=_at(17), ends_at=_at(18))
        with pytest.raises(EquipmentError, match="^Sessions aren't turned on for this equipment.$"):
            unstarted.start_session(member, now=_at(17, 5))
        with pytest.raises(EquipmentError, match="^Sessions aren't turned on for this equipment.$"):
            unstarted.stop_session(member, now=_at(17, 5))
