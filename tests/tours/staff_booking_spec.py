"""Staff moves, cancels and marks a facility tour (#949): the locks, no cutoff, the emails and who is told."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core import mail

from core.models import Notification, UserProfile
from membership.models import Member
from tests.membership.factories import MemberFactory
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours import signals
from tours.availability import BookingWindow, open_tour_at, open_tours
from tours.booking import (
    GONE_MESSAGE,
    SAME_TIME_MESSAGE,
    TourFullError,
    TourLockedError,
    TourUnavailableError,
    cancel_tour_by_staff,
    mark_tour_outcome,
    reschedule_tour_by_staff,
)
from tours.models import FacilityTourSettings, TourBooking
from tours.notify import person_name

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday; tours run Tuesday the 8th, 4:00 and 5:00 PM.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
FIVE = local(TUESDAY, 17)
#: Three hours before the 4:00 tour: inside the 24 hour cancel cutoff and the 24 hour notice.
CLOSE = FOUR - timedelta(hours=3)


def _ics(message: Any) -> str:
    content = message.attachments[0][1]
    return content.decode() if isinstance(content, bytes) else str(content)


def _user(member: Member) -> User:
    """The member's account, made when missing (a guide needs one to be emailed and to act)."""
    signed_in(member)
    member.refresh_from_db()
    assert member.user is not None
    return member.user


@pytest.fixture
def hours(guide: Member) -> Any:
    return TourHoursFactory(guide=guide, seats=4)


@pytest.fixture
def booked(guide: Member, hours: Any) -> TourBooking:
    return TourBookingFactory(
        guide=guide, starts_at=FOUR, party_size=2, visitor_name="Sam Rivera", visitor_email="sam@example.com"
    )


@pytest.fixture
def other() -> Member:
    other = make_guide(full_legal_name="Olive Other")
    TourHoursFactory(guide=other)
    return other


def _to(address: str) -> list[Any]:
    return [message for message in mail.outbox if message.to == [address]]


def describe_the_staff_window():
    def it_drops_the_minimum_notice_but_keeps_the_horizon(guide: Member, hours: Any):
        settings = FacilityTourSettings.load()
        assert BookingWindow.at(CLOSE, settings, staff=True).earliest_start == CLOSE
        assert open_tour_at(guide, FOUR, now=CLOSE) is None
        assert open_tour_at(guide, FOUR, now=CLOSE, staff=True) is not None
        assert [tour.starts_at for tour in open_tours(TUESDAY, TUESDAY, now=CLOSE, staff=True)] == [FOUR, FIVE]
        far = local(TUESDAY + timedelta(days=35), 16)
        assert open_tour_at(guide, far, now=CLOSE, staff=True) is None

    def it_never_offers_a_tour_that_has_started(guide: Member, hours: Any):
        assert open_tour_at(guide, FOUR, now=FOUR + timedelta(minutes=1), staff=True) is None


def describe_reschedule_tour_by_staff():
    def it_moves_inside_the_cutoff_and_raises_the_sequence(guide: Member, booked: TourBooking):
        moved = reschedule_tour_by_staff(booked, guide=guide, starts_at=FIVE, actor=_user(guide), now=CLOSE)
        booked.refresh_from_db()
        assert (booked.starts_at, booked.ends_at, booked.sequence, moved.pk) == (
            FIVE,
            FIVE + timedelta(hours=1),
            1,
            booked.pk,
        )

    def it_reassigns_to_another_guides_tour(guide: Member, booked: TourBooking, other: Member, admin: Member):
        reschedule_tour_by_staff(booked, guide=other, starts_at=FOUR, actor=_user(admin), now=NOW)
        booked.refresh_from_db()
        assert booked.guide == other

    def it_refuses_the_booking_own_time(guide: Member, booked: TourBooking):
        with pytest.raises(TourUnavailableError, match=SAME_TIME_MESSAGE):
            reschedule_tour_by_staff(booked, guide=guide, starts_at=FOUR, actor=_user(guide), now=NOW)

    def it_refuses_a_time_no_hours_yield(guide: Member, booked: TourBooking):
        with pytest.raises(TourUnavailableError, match=GONE_MESSAGE):
            reschedule_tour_by_staff(booked, guide=guide, starts_at=local(TUESDAY, 19), actor=_user(guide), now=NOW)

    def it_never_overbooks(guide: Member, booked: TourBooking):
        TourBookingFactory(guide=guide, starts_at=FIVE, party_size=3)
        with pytest.raises(TourFullError):
            reschedule_tour_by_staff(booked, guide=guide, starts_at=FIVE, actor=_user(guide), now=NOW)
        booked.refresh_from_db()
        assert (booked.starts_at, booked.sequence) == (FOUR, 0)

    def it_refuses_a_cancelled_or_started_tour(guide: Member, booked: TourBooking):
        actor = _user(guide)
        with pytest.raises(TourLockedError) as started:
            reschedule_tour_by_staff(booked, guide=guide, starts_at=FIVE, actor=actor, now=FOUR)
        assert started.value.reason == "past"
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        with pytest.raises(TourLockedError) as cancelled:
            reschedule_tour_by_staff(booked, guide=guide, starts_at=FIVE, actor=actor, now=NOW)
        assert cancelled.value.reason == "cancelled"

    def it_refuses_a_tour_already_marked(guide: Member, booked: TourBooking):
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.ATTENDED)
        with pytest.raises(TourLockedError) as refused:
            reschedule_tour_by_staff(booked, guide=guide, starts_at=FIVE, actor=_user(guide), now=NOW)
        assert refused.value.reason == "past"

    def it_emails_the_visitor_the_move_with_the_message_and_tells_no_guide_who_acted(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        actor = _user(guide)
        with django_capture_on_commit_callbacks(execute=True):
            reschedule_tour_by_staff(
                booked, guide=guide, starts_at=FIVE, actor=actor, message="  Running late, sorry!  ", now=CLOSE
            )
        visitor = _to("sam@example.com")
        assert [message.subject for message in visitor] == ["Your Past Lives tour moved to Tue, Jan 8 at 5:00 PM"]
        invite = _ics(visitor[0])
        assert f"UID:tour-{booked.pk}@pastlives" in invite and "SEQUENCE:1" in invite
        assert "A note from Gina:\nRunning late, sorry!" in visitor[0].body
        assert "Running late, sorry!" in str(visitor[0].alternatives[0][0])
        assert _to(actor.email) == []
        assert not Notification.objects.filter(user=actor).exists()

    def it_tells_the_guide_when_an_admin_moved_their_tour(
        guide: Member, booked: TourBooking, admin: Member, django_capture_on_commit_callbacks: Any
    ):
        guide_user, admin_user = _user(guide), _user(admin)
        with django_capture_on_commit_callbacks(execute=True):
            reschedule_tour_by_staff(booked, guide=guide, starts_at=FIVE, actor=admin_user, message="", now=NOW)
        told = _to(guide_user.email)
        assert [message.subject for message in told] == ["Tour moved: Tue, Jan 8 at 4:00 PM to Tue, Jan 8 at 5:00 PM"]
        assert "Ada Admin moved Sam Rivera's tour for 2 people from" in told[0].body
        assert "A note from" not in _to("sam@example.com")[0].body
        bell = Notification.objects.get(user=guide_user)
        assert (bell.title, bell.body, bell.url) == (
            "Tour moved",
            "Ada Admin moved Sam Rivera's tour on Tue, Jan 8 at 4:00 PM to Tue, Jan 8 at 5:00 PM.",
            "/facility-tours/",
        )
        assert not Notification.objects.filter(user=admin_user).exists()

    def it_tells_the_old_and_new_guide_on_a_reassignment(
        guide: Member, booked: TourBooking, other: Member, admin: Member, django_capture_on_commit_callbacks: Any
    ):
        guide_user, other_user, admin_user = _user(guide), _user(other), _user(admin)
        with django_capture_on_commit_callbacks(execute=True):
            reschedule_tour_by_staff(
                booked, guide=other, starts_at=FOUR, actor=admin_user, message="Gina is sick", now=NOW
            )
        old = _to(guide_user.email)
        assert len(old) == 1 and "to Olive. It is off your schedule." in old[0].body
        assert "Their note to the visitor:\nGina is sick" in old[0].body
        new = _to(other_user.email)
        assert [message.subject for message in new] == ["Tour moved to you: Tue, Jan 8 at 4:00 PM"]
        assert "Ada Admin moved Sam Rivera's tour for 2 people to you." in new[0].body
        assert Notification.objects.get(user=guide_user).body == (
            "Ada Admin moved Sam Rivera's tour on Tue, Jan 8 at 4:00 PM to Olive."
        )
        assert Notification.objects.get(user=other_user).title == "Tour moved to you"
        assert _to(admin_user.email) == []

    def it_tells_only_the_new_guide_when_the_tour_had_none(
        booked: TourBooking, other: Member, admin: Member, django_capture_on_commit_callbacks: Any
    ):
        TourBooking.objects.filter(pk=booked.pk).update(guide=None)
        booked.refresh_from_db()
        other_user = _user(other)
        with django_capture_on_commit_callbacks(execute=True):
            reschedule_tour_by_staff(booked, guide=other, starts_at=FOUR, actor=_user(admin), now=NOW)
        assert [message.subject for message in _to(other_user.email)] == ["Tour moved to you: Tue, Jan 8 at 4:00 PM"]
        assert Notification.objects.count() == 1

    def it_sends_the_part_5_seam_with_who_acted(
        guide: Member, booked: TourBooking, admin: Member, django_capture_on_commit_callbacks: Any
    ):
        heard: list[dict[str, Any]] = []

        def receiver(sender: object, **kwargs: Any) -> None:
            heard.append(kwargs)

        actor = _user(admin)
        signals.tour_booking_changed.connect(receiver)
        try:
            with django_capture_on_commit_callbacks(execute=False) as callbacks:
                reschedule_tour_by_staff(booked, guide=guide, starts_at=FIVE, actor=actor, message="hi", now=NOW)
            assert heard == []
            for callback in callbacks:
                callback()
        finally:
            signals.tour_booking_changed.disconnect(receiver)
        assert len(heard) == 1
        assert (heard[0]["change"], heard[0]["previous_guide"], heard[0]["previous_starts_at"]) == (
            signals.MOVED,
            guide,
            FOUR,
        )
        assert (heard[0]["actor"], heard[0]["staff_message"]) == (actor, "hi")


def describe_cancel_tour_by_staff():
    def it_cancels_inside_the_cutoff_and_frees_the_seats(guide: Member, booked: TourBooking):
        actor = _user(guide)
        cancel_tour_by_staff(booked, actor=actor, now=CLOSE)
        booked.refresh_from_db()
        assert (booked.status, booked.cancelled_at, booked.cancelled_by, booked.sequence) == (
            TourBooking.Status.CANCELLED,
            CLOSE,
            actor,
            1,
        )
        tour = open_tour_at(guide, FOUR, now=NOW)
        assert tour is not None and tour.seats_left == 4

    def it_refuses_a_second_cancel(guide: Member, booked: TourBooking):
        actor = _user(guide)
        cancel_tour_by_staff(booked, actor=actor, now=NOW)
        with pytest.raises(TourLockedError) as refused:
            cancel_tour_by_staff(booked, actor=actor, now=NOW)
        assert refused.value.reason == "cancelled"

    def it_emails_the_visitor_the_cancel_with_the_message(
        guide: Member, booked: TourBooking, admin: Member, django_capture_on_commit_callbacks: Any
    ):
        guide_user = _user(guide)
        with django_capture_on_commit_callbacks(execute=True):
            cancel_tour_by_staff(booked, actor=_user(admin), message="The building is closed for a leak.", now=CLOSE)
        visitor = _to("sam@example.com")
        assert [message.subject for message in visitor] == [
            "Your Past Lives tour on Tue, Jan 8 at 4:00 PM is cancelled"
        ]
        assert "We are sorry, we had to cancel your tour" in visitor[0].body
        assert "A note from Ada:\nThe building is closed for a leak." in visitor[0].body
        invite = _ics(visitor[0])
        assert "METHOD:CANCEL" in invite and "SEQUENCE:1" in invite
        told = _to(guide_user.email)
        assert [message.subject for message in told] == ["Tour cancelled: Tue, Jan 8 at 4:00 PM"]
        assert "Ada Admin cancelled Sam Rivera's tour" in told[0].body
        assert Notification.objects.get(user=guide_user).body == (
            "Ada Admin cancelled Sam Rivera's tour on Tue, Jan 8 at 4:00 PM."
        )

    def it_tells_no_guide_when_the_guide_cancelled_or_there_is_none(
        guide: Member, booked: TourBooking, admin: Member, django_capture_on_commit_callbacks: Any
    ):
        with django_capture_on_commit_callbacks(execute=True):
            cancel_tour_by_staff(booked, actor=_user(guide), now=NOW)
        unassigned = TourBookingFactory(guide=None, starts_at=FIVE)
        with django_capture_on_commit_callbacks(execute=True):
            cancel_tour_by_staff(unassigned, actor=_user(admin), now=NOW)
        assert not Notification.objects.exists()
        assert all(message.to[0].startswith(("sam@", "visitor")) for message in mail.outbox)


def describe_mark_tour_outcome():
    def it_marks_attended_and_stamps_the_visitor_once(booked: TourBooking):
        user = _user(MemberFactory(full_legal_name="Vera Visitor"))
        TourBooking.objects.filter(pk=booked.pk).update(user=user)
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FOUR + timedelta(hours=2))
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.ATTENDED
        assert UserProfile.objects.get(user=user).completed_tour_at == FOUR

    def it_never_overwrites_an_earlier_stamp(booked: TourBooking):
        user = _user(MemberFactory())
        earlier = FOUR - timedelta(days=400)
        UserProfile.objects.update_or_create(user=user, defaults={"completed_tour_at": earlier})
        TourBooking.objects.filter(pk=booked.pk).update(user=user)
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        assert UserProfile.objects.get(user=user).completed_tour_at == earlier

    def it_makes_the_profile_when_the_account_has_none(booked: TourBooking):
        user = User.objects.create_user("noprofile", "noprofile@example.com")
        UserProfile.objects.filter(user=user).delete()
        TourBooking.objects.filter(pk=booked.pk).update(user=user)
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        assert UserProfile.objects.get(user=user).completed_tour_at == FOUR

    def it_never_stamps_a_no_show_and_keeps_a_stamp_when_changed_to_one(booked: TourBooking):
        user = _user(MemberFactory())
        TourBooking.objects.filter(pk=booked.pk).update(user=user)
        mark_tour_outcome(booked, TourBooking.Status.NO_SHOW, now=FIVE)
        assert UserProfile.objects.get_or_create(user=user)[0].completed_tour_at is None
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        mark_tour_outcome(booked, TourBooking.Status.NO_SHOW, now=FIVE)
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.NO_SHOW
        assert UserProfile.objects.get(user=user).completed_tour_at == FOUR

    def it_marks_a_booking_with_no_account_without_stamping(booked: TourBooking):
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.ATTENDED

    def it_refuses_a_tour_that_has_not_started_or_was_cancelled(booked: TourBooking):
        with pytest.raises(TourLockedError) as early:
            mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=NOW)
        assert early.value.reason == "upcoming"
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        with pytest.raises(TourLockedError) as cancelled:
            mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        assert cancelled.value.reason == "cancelled"

    def it_refuses_an_unknown_outcome(booked: TourBooking):
        with pytest.raises(ValueError, match="Unknown tour outcome 'booked'"):
            mark_tour_outcome(booked, TourBooking.Status.BOOKED, now=FIVE)


def describe_person_name():
    def it_names_the_member_then_the_account_then_the_email():
        member = MemberFactory(full_legal_name="Mona Member")
        assert person_name(_user(member)) == "Mona Member"
        bare = User.objects.create_user("bare", "bare@example.com", first_name="Bea", last_name="Bare")
        Member.objects.filter(user=bare).delete()
        bare.refresh_from_db()
        assert person_name(bare) == "Bea Bare"
        nameless = User.objects.create_user("nameless", "nameless@example.com")
        Member.objects.filter(user=nameless).delete()
        nameless.refresh_from_db()
        assert person_name(nameless) == "nameless@example.com"


def describe_the_booking_lists_and_labels():
    def it_lists_upcoming_soonest_first_and_past_newest_first(guide: Member):
        later = TourBookingFactory(guide=guide, starts_at=FIVE)
        sooner = TourBookingFactory(guide=guide, starts_at=FOUR)
        TourBookingFactory(guide=guide, starts_at=FOUR, status=TourBooking.Status.CANCELLED)
        assert list(TourBooking.objects.upcoming(NOW)) == [sooner, later]
        assert list(TourBooking.objects.past(NOW)) == []
        after = FIVE + timedelta(hours=2)
        assert list(TourBooking.objects.upcoming(after)) == []
        assert list(TourBooking.objects.past(after)) == [later, sooner]

    def it_labels_the_party_the_phone_and_the_status():
        booking = TourBookingFactory.build(party_size=1, visitor_phone="503 555 0142")
        assert (booking.party_label, booking.visitor_phone_href, booking.status_badge) == (
            "1 person",
            "5035550142",
            "muted",
        )
        booking.party_size, booking.visitor_phone = 3, " +1 503-555-0142"
        assert (booking.party_label, booking.visitor_phone_href) == ("3 people", "+15035550142")
        badges = {}
        for status in TourBooking.Status:
            booking.status = status
            badges[status.value] = booking.status_badge
        assert badges == {"booked": "muted", "cancelled": "muted", "attended": "ok", "no_show": "fail"}

    def it_records_how_it_was_booked(guide: Member):
        assert TourBookingFactory(guide=guide).source == TourBooking.Source.WEBSITE
