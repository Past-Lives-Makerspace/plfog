"""Staff proposals (#1283), cancels and marks of a facility tour (#949): the locks, no cutoff, the emails and who is told."""

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
    propose_tour_time,
)
from tours.models import FacilityTourSettings, TourBooking
from tours.notify import note_label, person_name

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday; tours run Tuesday the 8th, 4:00 and 5:00 PM.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
FIVE = local(TUESDAY, 17)
#: Three hours before the 4:00 tour: inside the 24 hour cancel cutoff and the 24 hour notice.
CLOSE = FOUR - timedelta(hours=3)


def _ics(message: Any) -> str:
    return message.attachments[0].get_content()


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


def describe_propose_tour_time():
    def it_records_the_proposal_inside_the_cutoff_and_keeps_the_time(guide: Member, booked: TourBooking):
        actor = _user(guide)
        proposed = propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=actor, now=CLOSE)
        booked.refresh_from_db()
        assert (booked.starts_at, booked.sequence, proposed.pk) == (FOUR, 0, booked.pk)
        assert (booked.proposed_guide, booked.proposed_starts_at, booked.proposed_at, booked.proposed_by) == (
            guide,
            FIVE,
            CLOSE,
            actor,
        )

    def it_proposes_another_guides_tour_without_reassigning(
        guide: Member, booked: TourBooking, other: Member, admin: Member
    ):
        propose_tour_time(booked, guide=other, starts_at=FOUR, actor=_user(admin), now=NOW)
        booked.refresh_from_db()
        assert (booked.guide, booked.proposed_guide) == (guide, other)

    def it_refuses_the_booking_own_time(guide: Member, booked: TourBooking):
        with pytest.raises(TourUnavailableError, match=SAME_TIME_MESSAGE):
            propose_tour_time(booked, guide=guide, starts_at=FOUR, actor=_user(guide), now=NOW)

    def it_refuses_a_time_no_hours_yield(guide: Member, booked: TourBooking):
        with pytest.raises(TourUnavailableError, match=GONE_MESSAGE):
            propose_tour_time(booked, guide=guide, starts_at=local(TUESDAY, 19), actor=_user(guide), now=NOW)

    def it_refuses_a_tour_without_room_for_the_party(guide: Member, booked: TourBooking):
        TourBookingFactory(guide=guide, starts_at=FIVE, party_size=3)
        with pytest.raises(TourFullError):
            propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=_user(guide), now=NOW)
        booked.refresh_from_db()
        assert booked.proposed_at is None

    def it_refuses_a_cancelled_or_started_tour(guide: Member, booked: TourBooking):
        actor = _user(guide)
        with pytest.raises(TourLockedError) as started:
            propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=actor, now=FOUR)
        assert started.value.reason == "past"
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        with pytest.raises(TourLockedError) as cancelled:
            propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=actor, now=NOW)
        assert cancelled.value.reason == "cancelled"

    def it_refuses_a_tour_already_marked(guide: Member, booked: TourBooking):
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.ATTENDED)
        with pytest.raises(TourLockedError) as refused:
            propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=_user(guide), now=NOW)
        assert refused.value.reason == "past"

    def it_emails_the_visitor_accept_and_keep_with_the_message_and_no_invite(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        actor = _user(guide)
        with django_capture_on_commit_callbacks(execute=True):
            proposed = propose_tour_time(
                booked, guide=guide, starts_at=FIVE, actor=actor, message="  Running late, sorry!  ", now=CLOSE
            )
        (email,) = _to("sam@example.com")
        assert email.subject == "Can your Past Lives tour move to Tue, Jan 8 at 5:00 PM?"
        assert email.attachments == []
        link = f"/tour/b/{booked.self_serve_token}/?proposal={proposed.proposal_key}"
        assert f"{link}&answer=accept" in email.body and f"{link}&answer=keep" in email.body
        assert "A note from Gina:\nRunning late, sorry!" in email.body
        html = str(email.alternatives[0][0])
        assert "Accept New Time" in html and "Keep My Time" in html and "Running late, sorry!" in html
        assert _to(actor.email) == []
        assert not Notification.objects.exists()

    def it_sends_no_seam_since_nothing_moved(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        heard: list[dict[str, Any]] = []

        def receiver(sender: object, **kwargs: Any) -> None:
            heard.append(kwargs)

        signals.tour_booking_changed.connect(receiver)
        try:
            with django_capture_on_commit_callbacks(execute=True):
                propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=_user(guide), now=NOW)
        finally:
            signals.tour_booking_changed.disconnect(receiver)
        assert heard == []

    def it_replaces_an_open_proposal_with_a_new_key(guide: Member, booked: TourBooking, other: Member):
        actor = _user(guide)
        first = propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=actor, now=NOW).proposal_key
        second = propose_tour_time(
            booked, guide=guide, starts_at=FIVE, actor=actor, now=NOW + timedelta(minutes=5)
        ).proposal_key
        assert first and second and first != second


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

    def it_never_stamps_a_no_show_and_clears_its_own_stamp_when_corrected_to_one(booked: TourBooking):
        user = _user(MemberFactory())
        TourBooking.objects.filter(pk=booked.pk).update(user=user)
        mark_tour_outcome(booked, TourBooking.Status.NO_SHOW, now=FIVE)
        assert UserProfile.objects.get_or_create(user=user)[0].completed_tour_at is None
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        assert UserProfile.objects.get(user=user).completed_tour_at == FOUR
        mark_tour_outcome(booked, TourBooking.Status.NO_SHOW, now=FIVE)
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.NO_SHOW
        assert UserProfile.objects.get(user=user).completed_tour_at is None

    def it_keeps_an_earlier_tours_stamp_when_corrected_to_no_show(booked: TourBooking):
        user = _user(MemberFactory())
        earlier = FOUR - timedelta(days=30)
        UserProfile.objects.update_or_create(user=user, defaults={"completed_tour_at": earlier})
        TourBooking.objects.filter(pk=booked.pk).update(user=user)
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        mark_tour_outcome(booked, TourBooking.Status.NO_SHOW, now=FIVE)
        assert UserProfile.objects.get(user=user).completed_tour_at == earlier

    def it_restamps_from_a_later_tour_still_attended_when_the_first_is_corrected(booked: TourBooking):
        user = _user(MemberFactory())
        TourBooking.objects.filter(pk=booked.pk).update(user=user)
        later = TourBookingFactory(guide=booked.guide, starts_at=FIVE, user=user)
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE + timedelta(hours=2))
        mark_tour_outcome(later, TourBooking.Status.ATTENDED, now=FIVE + timedelta(hours=2))
        assert UserProfile.objects.get(user=user).completed_tour_at == FOUR
        mark_tour_outcome(booked, TourBooking.Status.NO_SHOW, now=FIVE + timedelta(hours=2))
        assert UserProfile.objects.get(user=user).completed_tour_at == FIVE

    def it_leaves_the_stamp_alone_when_attended_is_marked_again(booked: TourBooking):
        user = _user(MemberFactory())
        TourBooking.objects.filter(pk=booked.pk).update(user=user)
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        mark_tour_outcome(booked, TourBooking.Status.ATTENDED, now=FIVE)
        assert UserProfile.objects.get(user=user).completed_tour_at == FOUR

    def it_clears_nothing_for_a_no_show_with_no_account(booked: TourBooking):
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.ATTENDED)
        mark_tour_outcome(booked, TourBooking.Status.NO_SHOW, now=FIVE)
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.NO_SHOW

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
        nameless.email = ""
        assert person_name(nameless) == "Past Lives staff"


def describe_note_label():
    def it_heads_the_note_with_a_first_name_or_past_lives():
        assert note_label(None) == ""
        assert note_label(_user(MemberFactory(full_legal_name="Mona Member"))) == "A note from Mona"
        bare = User.objects.create_user("bare2", "bare2@example.com", first_name="Bea")
        Member.objects.filter(user=bare).delete()
        bare.refresh_from_db()
        assert note_label(bare) == "A note from Bea"
        nameless = User.objects.create_user("nameless2", "")
        Member.objects.filter(user=nameless).delete()
        nameless.refresh_from_db()
        assert note_label(nameless) == "A note from Past Lives"

    def it_sends_a_nameless_actors_cancel_without_failing(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        guide_user = _user(guide)
        nameless = User.objects.create_user("nameless3", "")
        Member.objects.filter(user=nameless).delete()
        nameless.refresh_from_db()
        with django_capture_on_commit_callbacks(execute=True):
            cancel_tour_by_staff(booked, actor=nameless, message="Closed today.", now=NOW)
        assert "A note from Past Lives:\nClosed today." in _to("sam@example.com")[0].body
        assert "Past Lives staff cancelled Sam Rivera's tour" in _to(guide_user.email)[0].body


def describe_plain_text_emails():
    def it_sends_quotes_and_markup_raw_in_text_and_escaped_in_html(
        guide: Member, booked: TourBooking, admin: Member, django_capture_on_commit_callbacks: Any
    ):
        TourBooking.objects.filter(pk=booked.pk).update(visitor_name="Sam O'Brien <b>x</b>")
        booked.refresh_from_db()
        guide_user = _user(guide)
        message = "We're sorry <script>alert(1)</script>"
        with django_capture_on_commit_callbacks(execute=True):
            cancel_tour_by_staff(booked, actor=_user(admin), message=message, now=NOW)
        for sent in (_to("sam@example.com")[0], _to(guide_user.email)[0]):
            assert message in sent.body
            assert "&#x27;" not in sent.body and "&lt;" not in sent.body
            html = str(sent.alternatives[0][0])
            assert "<script>alert(1)</script>" not in html
            assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "Sam O'Brien <b>x</b>" in _to(guide_user.email)[0].body
        assert "Sam O&#x27;Brien &lt;b&gt;x&lt;/b&gt;" in str(_to(guide_user.email)[0].alternatives[0][0])


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
        assert badges == {
            "booked": "muted",
            "cancelled": "muted",
            "attended": "ok",
            "no_show": "fail",
            "pending": "warn",
        }

    def it_records_how_it_was_booked(guide: Member):
        assert TourBookingFactory(guide=guide).source == TourBooking.Source.WEBSITE
