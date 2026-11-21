"""The Tours page for every Tour Guide (#1274): the Calendar tab, the bookings table's filters and menus, and notes."""

from __future__ import annotations

import uuid
from datetime import date, time, timedelta
import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourDayOffFactory, TourHoursFactory, local
from tours import booking as booking_service
from tours import coverage
from tours.models import FacilityTourSettings, TourBooking, TourBookingNote

pytestmark = pytest.mark.django_db

UPCOMING = reverse("tours:upcoming")
PAST = reverse("tours:past")
CALENDAR = reverse("tours:calendar")
HOURS = reverse("tours:hours")
HTMX = {"HTTP_HX_REQUEST": "true"}
TODAY = timezone.localdate()
DAY = TODAY + timedelta(days=3)
#: Wednesday next week: always in the calendar's next week, and never started.
NEXT_WEEK = TODAY - timedelta(days=TODAY.weekday()) + timedelta(days=9)


def _flashes(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _notes_url(booking: TourBooking) -> str:
    return reverse("tours:booking_notes", args=[booking.pk])


def _note_url(booking: TourBooking) -> str:
    return reverse("tours:booking_note", args=[booking.pk])


def _short_horizon(days: int = 6) -> None:
    house = FacilityTourSettings.load()
    house.booking_horizon_days = days
    house.save()


@pytest.fixture
def other() -> Member:
    return make_guide(full_legal_name="Olive Other")


@pytest.fixture
def booked(guide: Member) -> TourBooking:
    return TourBookingFactory(guide=guide, starts_at=local(DAY, 16), visitor_name="Sam Rivera", party_size=2)


def describe_coverage():
    def it_finds_the_days_no_guide_covers(guide: Member, other: Member):
        _short_horizon(6)
        weekly_day = TODAY + timedelta(days=1)
        TourHoursFactory(guide=guide, weekday=weekly_day.weekday())
        dated = TODAY + timedelta(days=2)
        TourHoursFactory(guide=other, weekday=dated.weekday(), date=dated)
        TourDayOffFactory(guide=guide, starts_on=weekly_day, ends_on=weekly_day)
        gaps = [gap.day for gap in coverage.uncovered_days(today=TODAY)]
        assert dated not in gaps
        assert weekly_day in gaps
        assert len(gaps) == 6
        assert coverage.uncovered_days(today=TODAY)[0].add_url == f"{HOURS}?add={TODAY.isoformat()}#available-on"

    def it_leaves_out_the_hours_of_someone_who_no_longer_gives_tours(guide: Member):
        _short_horizon(0)
        TourHoursFactory(guide=guide, weekday=TODAY.weekday())
        assert coverage.uncovered_days(today=TODAY) == []
        guide.admin_capabilities.all().delete()
        assert [gap.day for gap in coverage.uncovered_days(today=TODAY)] == [TODAY]

    def it_puts_every_guides_hours_and_tours_on_the_calendar(guide: Member, other: Member, admin: Member):
        TourHoursFactory(guide=guide, weekday=NEXT_WEEK.weekday(), start_time=time(11), end_time=time(13))
        TourHoursFactory(
            guide=other, weekday=NEXT_WEEK.weekday(), date=NEXT_WEEK, start_time=time(14), end_time=time(15)
        )
        booked = TourBookingFactory(
            guide=other, starts_at=local(NEXT_WEEK, 14), visitor_name="Sam Rivera", party_size=3
        )
        TourBookingFactory(
            guide=guide, starts_at=local(NEXT_WEEK, 11), visitor_name="Rae Quest", status=TourBooking.Status.PENDING
        )
        TourBookingFactory(guide=None, starts_at=local(NEXT_WEEK, 12), visitor_name="No One")
        TourBookingFactory(
            guide=guide, starts_at=local(NEXT_WEEK, 12), visitor_name="Gone Gwen", status=TourBooking.Status.CANCELLED
        )
        cal = coverage.calendar_context(viewer=guide, is_admin=False, week_offset=1)
        titles = [event.chip_title for event in cal["week_events"]]
        assert "Gina G. hours" in titles and "Olive O. hours" in titles
        assert "Tour: Sam R. (3)" in titles and "Request: Rae Q. (2)" in titles and "Tour: No O. (2)" in titles
        assert not any("Gwen" in title for title in titles)
        assert [chip["label"] for chip in cal["legend"]] == ["Gina Guide", "Olive Other", "No guide"]
        urls = {event.chip_title: event.url for event in cal["week_events"]}
        assert urls["Gina G. hours"] == HOURS
        assert urls["Olive O. hours"] == ""
        assert urls["Tour: Sam R. (3)"] == f"{UPCOMING}#tour-booking-{booked.pk}"
        admin_urls = {
            event.chip_title: event.url
            for event in coverage.calendar_context(viewer=admin, is_admin=True, week_offset=1)["week_events"]
        }
        assert admin_urls["Olive O. hours"] == f"{HOURS}?guide={other.pk}"

    def it_links_a_past_tour_to_the_past_table(guide: Member):
        attended = TourBookingFactory(
            guide=guide,
            starts_at=local(TODAY, 0, 30),
            visitor_name="Pat Past",
            status=TourBooking.Status.ATTENDED,
        )
        events = coverage.calendar_context(viewer=guide, is_admin=False)["week_events"]
        assert [event.url for event in events] == [f"{PAST}#tour-booking-{attended.pk}"]

    def it_names_a_one_word_guide_as_they_are(guide: Member):
        Member.objects.filter(pk=guide.pk).update(full_legal_name="Cher", preferred_name="")
        guide.refresh_from_db()
        assert coverage._short_name(guide) == guide.staff_label.strip()


def describe_calendar_tab():
    def it_shows_every_guide_the_calendar_and_the_gaps(guide: Member, other: Member):
        _short_horizon(2)
        TourHoursFactory(guide=other, weekday=TODAY.weekday())
        content = signed_in(guide).get(CALENDAR).content.decode()
        assert 'aria-current="page" data-tours-tab="calendar"' in content
        assert "Olive Other" in content and "data-tours-calendar" in content
        assert f'data-tours-gap="{(TODAY + timedelta(days=1)).isoformat()}"' in content
        assert f'data-tours-gap="{TODAY.isoformat()}"' not in content
        assert f'href="{HOURS}?add={(TODAY + timedelta(days=1)).isoformat()}#available-on"' in content

    def it_says_so_when_every_day_is_covered(guide: Member):
        _short_horizon(0)
        TourHoursFactory(guide=guide, weekday=TODAY.weekday())
        content = signed_in(guide).get(CALENDAR).content.decode()
        assert "data-tours-gaps-empty" in content and "Every day in the next 0 days" in content

    def it_steps_weeks_over_htmx_and_sends_a_plain_get_to_the_tab(guide: Member):
        events = reverse("tours:calendar_events")
        TourHoursFactory(guide=guide, weekday=(TODAY + timedelta(days=7)).weekday())
        client = signed_in(guide)
        fragment = client.get(events, {"week_offset": 1}, **HTMX).content.decode()
        assert "<html" not in fragment and "Gina G. hours" in fragment
        assert 'pl-calendar-grid__chip-time">4:00 PM<' in fragment
        plain = client.get(events)
        assert (plain.status_code, plain["Location"]) == (302, CALENDAR)

    def it_sends_a_plain_member_to_give_tours(plain: Member):
        assert signed_in(plain).get(CALENDAR)["Location"] == reverse("tours:give")


def describe_add_from_a_gap():
    def it_opens_add_dates_with_the_day_picked(guide: Member):
        day = TODAY + timedelta(days=4)
        content = signed_in(guide).get(HOURS, {"add": day.isoformat()}).content.decode()
        assert f'"picked": ["{day.isoformat()}"]' in content
        assert "plTourDates('tour-dates-config', true)" in content

    @pytest.mark.parametrize("raw", ["nope", "2001-01-01", (date.today() + timedelta(days=900)).isoformat()])
    def it_ignores_a_day_add_dates_could_not_pick(guide: Member, raw: str):
        content = signed_in(guide).get(HOURS, {"add": raw}).content.decode()
        assert '"picked": []' in content
        assert "plTourDates('tour-dates-config', false)" in content


def describe_table():
    def it_filters_by_status_and_counts_the_requests(guide: Member, booked: TourBooking):
        request_ = TourBookingFactory(guide=guide, starts_at=local(DAY, 17), status=TourBooking.Status.PENDING)
        client = signed_in(guide)
        everything = client.get(UPCOMING).content.decode()
        assert (
            'data-tours-chip="requested">Requested<span class="pl-equip-chip__count" data-tours-chip-count>1'
            in everything
        )
        assert 'data-tour-booking-status="booked">Confirmed' in everything
        requested = client.get(UPCOMING, {"show": "requested"}).content.decode()
        assert f'data-tour-booking="{request_.pk}"' in requested and f'data-tour-booking="{booked.pk}"' not in requested
        assert '<input type="hidden" name="show" value="requested">' in requested
        confirmed = client.get(UPCOMING, {"show": "confirmed", "guide": guide.pk}).content.decode()
        assert f'data-tour-booking="{booked.pk}"' in confirmed and f'data-tour-booking="{request_.pk}"' not in confirmed
        assert f'href="{UPCOMING}?guide={guide.pk}&amp;show=requested"' in confirmed
        assert client.get(UPCOMING, {"show": "bogus"}).status_code == 200

    def it_says_when_no_request_matches(guide: Member, booked: TourBooking):
        content = signed_in(guide).get(UPCOMING, {"show": "requested"}).content.decode()
        assert "No requests coming up." in content and "data-tours-clear" in content
        confirmed = signed_in(guide).get(UPCOMING, {"show": "confirmed", "guide": make_guide().pk}).content.decode()
        assert "No confirmed tours coming up for" in confirmed

    def it_offers_only_notes_on_another_guides_tour(guide: Member, other: Member, booked: TourBooking):
        content = signed_in(other).get(UPCOMING).content.decode()
        assert f'data-tour-notes="{booked.pk}"' in content
        assert f'data-tour-reschedule="{booked.pk}"' not in content
        assert f'id="tour-cancel-{booked.pk}"' not in content
        own = signed_in(guide).get(UPCOMING).content.decode()
        assert f'data-tour-reschedule="{booked.pk}"' in own and "Edit Guide or Time" in own
        assert "data-tour-booking-guide>You<" in own

    def it_offers_marks_only_to_whoever_may_act(guide: Member, other: Member):
        starts = timezone.now() - timedelta(days=1)
        past = TourBookingFactory(guide=guide, starts_at=starts, ends_at=starts + timedelta(hours=1))
        assert 'data-tour-mark="attended"' not in signed_in(other).get(PAST).content.decode()
        assert 'data-tour-mark="attended"' in signed_in(guide).get(PAST).content.decode()
        row = signed_in(guide).post(
            reverse("tours:booking_mark", args=[past.pk]),
            {"outcome": "attended"},
            HTTP_HX_CURRENT_URL=f"http://testserver{PAST}?guide={guide.pk}",
            **HTMX,
        )
        assert f"next={PAST}%3Fguide%3D{guide.pk}" in row.content.decode()

    def it_counts_the_notes_on_a_row(guide: Member, booked: TourBooking):
        TourBookingNote.write(author=guide.user, body="Parking is around back.", booking=booked)
        assert "data-tour-note-count>1 note<" in signed_in(guide).get(UPCOMING).content.decode()


def describe_notes():
    def it_shows_any_guide_the_notes_and_a_box(guide: Member, other: Member, booked: TourBooking):
        signed_in(guide)
        guide.refresh_from_db()
        TourBookingNote.write(author=guide.user, body="Bring the big ladder story.", booking=booked)
        content = signed_in(other).get(_notes_url(booked), {"next": UPCOMING}, **HTMX).content.decode()
        assert "Bring the big ladder story." in content and "Add Note" in content
        assert 'name="email_visitor"' not in content
        assert "or an admin can email the visitor." in content
        own = signed_in(guide).get(_notes_url(booked), **HTMX).content.decode()
        assert 'name="email_visitor"' in own

    def it_sends_a_plain_get_to_the_table_with_the_notes_open(guide: Member, booked: TourBooking):
        response = signed_in(guide).get(_notes_url(booked))
        assert (response.status_code, response["Location"]) == (302, f"{UPCOMING}?notes={booked.pk}")
        page = signed_in(guide).get(UPCOMING, {"notes": booked.pk}).content.decode()
        assert "data-tour-notes-autoload" in page
        assert "data-tour-notes-autoload" not in signed_in(guide).get(UPCOMING, {"notes": "999999"}).content.decode()

    def it_tells_the_guide_and_earlier_writers_but_never_the_author(
        guide: Member, other: Member, admin: Member, booked: TourBooking
    ):
        signed_in(guide)
        client = signed_in(other)
        response = client.post(
            _note_url(booked), {"body": "I can cover this one.", "next": f"{UPCOMING}?show=confirmed"}
        )
        assert response["Location"] == f"{UPCOMING}?show=confirmed&notes={booked.pk}"
        assert _flashes(response) == ["Note added. The other guides on this tour were told."]
        bell = Notification.objects.get(user=guide.user, trigger="tour.note_added")
        assert bell.body == "Olive Other: I can cover this one."
        assert bell.url == f"{UPCOMING}?notes={booked.pk}#tour-booking-{booked.pk}"
        assert not Notification.objects.filter(user=other.user, trigger="tour.note_added").exists()
        signed_in(guide).post(_note_url(booked), {"body": "Thanks!"})
        assert Notification.objects.filter(user=other.user, trigger="tour.note_added").count() == 1
        assert Notification.objects.filter(user=guide.user, trigger="tour.note_added").count() == 1
        assert mail.outbox == []
        assert [note.body for note in booked.notes.all()] == ["I can cover this one.", "Thanks!"]

    def it_tells_nobody_of_the_guides_own_first_note(guide: Member, booked: TourBooking):
        response = signed_in(guide).post(_note_url(booked), {"body": "Note to self."})
        assert _flashes(response) == ["Note added."]
        assert not Notification.objects.filter(trigger="tour.note_added").exists()

    def it_tells_the_admins_of_a_note_on_a_tour_with_no_guide(guide: Member, admin: Member, booked: TourBooking):
        signed_in(admin)
        TourBooking.objects.filter(pk=booked.pk).update(guide=None)
        signed_in(guide).post(_note_url(booked), {"body": "Who has this one?"})
        assert Notification.objects.filter(user=admin.user, trigger="tour.note_added").exists()

    def it_emails_the_visitor_for_the_tours_guide(guide: Member, booked: TourBooking):
        response = signed_in(guide).post(_note_url(booked), {"body": "Park on 9th, please.", "email_visitor": "on"})
        assert _flashes(response) == ["Note added. We emailed it to Sam."]
        [email] = mail.outbox
        assert email.to == [booked.visitor_email]
        assert email.subject.startswith("A message about your Past Lives tour on ")
        assert "Park on 9th, please." in email.body and "A note from Gina" in email.body
        assert "/reschedule/" in email.body
        note = booked.notes.get()
        assert note.sent_to_visitor and note.thread_marker == "Emailed to the visitor"
        content = signed_in(guide).get(_notes_url(booked), **HTMX).content.decode()
        assert "data-thread-marker>Emailed to the visitor<" in content
        stamp = timezone.localtime(note.created_at)
        assert f"{stamp:%a, %b} {stamp.day} · {stamp:%-I:%M %p}" in content

    def it_leaves_the_change_links_out_once_the_tour_cannot_change(guide: Member):
        starts = timezone.now() - timedelta(days=1)
        past = TourBookingFactory(guide=guide, starts_at=starts, ends_at=starts + timedelta(hours=1))
        signed_in(guide).post(_note_url(past), {"body": "Thanks for coming!", "email_visitor": "on"})
        assert "/reschedule/" not in mail.outbox[0].body

    def it_refuses_to_email_the_visitor_of_a_cancelled_tour(guide: Member, booked: TourBooking):
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        client = signed_in(guide)
        content = client.get(_notes_url(booked), **HTMX).content.decode()
        assert 'name="email_visitor"' not in content and "This tour is cancelled, so notes stay here." in content
        response = client.post(_note_url(booked), {"body": "Sorry we missed you.", "email_visitor": "on"})
        assert response.status_code == 403
        assert mail.outbox == [] and not booked.notes.exists()

    def it_refuses_to_email_the_visitor_for_another_guide(other: Member, booked: TourBooking):
        response = signed_in(other).post(_note_url(booked), {"body": "Hi!", "email_visitor": "on"})
        assert response.status_code == 403
        assert not booked.notes.exists() and mail.outbox == []

    def it_lets_an_admin_email_any_visitor(admin: Member, booked: TourBooking):
        signed_in(admin).post(_note_url(booked), {"body": "See you soon.", "email_visitor": "on"})
        assert len(mail.outbox) == 1

    def it_refuses_an_empty_note(guide: Member, booked: TourBooking):
        response = signed_in(guide).post(_note_url(booked), {"body": ""})
        assert response["Location"] == f"{UPCOMING}?notes={booked.pk}"
        assert _flashes(response)[0].startswith("Not added. ")
        assert not booked.notes.exists()

    def it_lands_on_upcoming_for_a_next_off_the_tables(guide: Member, booked: TourBooking):
        for next_url in ("https://evil.example/", reverse("tours:booking_mark", args=[booked.pk])):
            response = signed_in(guide).post(_note_url(booked), {"body": "x", "next": next_url})
            assert response["Location"] == f"{UPCOMING}?notes={booked.pk}"

    def it_refuses_a_plain_member(plain: Member, booked: TourBooking):
        client = signed_in(plain)
        assert client.get(_notes_url(booked), **HTMX)["HX-Redirect"] == reverse("tours:give")
        assert client.post(_note_url(booked), {"body": "x"}).status_code == 403


def describe_editing_a_note():
    def _note(author: Member, booking: TourBooking) -> TourBookingNote:
        signed_in(author)
        author.refresh_from_db()
        return TourBookingNote.write(author=author.user, body="First words.", booking=booking)

    def it_lets_its_author_edit_and_delete_it(guide: Member, booked: TourBooking):
        note = _note(guide, booked)
        client = signed_in(guide)
        edited = client.post(note.edit_url, {"body": "Better words."}, **HTMX)
        assert edited.status_code == 200
        note.refresh_from_db()
        assert note.body == "Better words."
        assert client.post(note.delete_url, **HTMX).status_code == 200
        assert not TourBookingNote.objects.filter(pk=note.pk).exists()

    def it_refuses_another_guide_and_hides_it_from_a_plain_member(
        guide: Member, other: Member, plain: Member, booked: TourBooking
    ):
        note = _note(guide, booked)
        assert signed_in(other).post(note.edit_url, {"body": "Mine now."}, **HTMX).status_code == 403
        assert signed_in(plain).post(note.delete_url, **HTMX).status_code == 404


def describe_the_note_model():
    def it_has_no_visitor_side(guide: Member, booked: TourBooking):
        note = TourBookingNote(booking=booked, body="x")
        assert note.member_user_ids == set() and note.member_side_name == "Sam Rivera"
        assert note._tell_staff_side() is False
        assert super(TourBookingNote, note).thread_marker == ""
        assert str(note).startswith("Note None on tour booking")

    def it_names_its_photo(guide: Member, booked: TourBooking):
        from tours.models import TourBookingNotePhoto

        photo = TourBookingNotePhoto(message=TourBookingNote(pk=4, booking=booked, body="x"))
        assert str(photo) == "Photo None on tour note 4"


def describe_a_shared_request():
    """A No preference request (#1255): one row per guide, one visitor, so one Notes thread."""

    @pytest.fixture
    def rows(guide: Member, other: Member) -> list[TourBooking]:
        group = uuid.uuid4()
        starts = local(TODAY + timedelta(days=4), 16)
        return [
            TourBookingFactory(
                guide=who,
                starts_at=starts,
                request_group=group,
                status=TourBooking.Status.PENDING,
                visitor_name="Sam Rivera",
            )
            for who in (guide, other)
        ]

    def _note(member: Member, booking: TourBooking, body: str) -> TourBookingNote:
        signed_in(member)
        member.refresh_from_db()
        return TourBookingNote.write(author=member.user, body=body, booking=booking)

    def it_shows_every_rows_notes_on_each_row(guide: Member, other: Member, rows: list[TourBooking]):
        _note(guide, rows[0], "Gina's note.")
        _note(other, rows[1], "Olive's note.")
        for row in rows:
            content = signed_in(guide).get(_notes_url(row), **HTMX).content.decode()
            assert "Gina&#x27;s note." in content and "Olive&#x27;s note." in content
        table = signed_in(guide).get(UPCOMING).content.decode()
        assert table.count("data-tour-note-count>2 notes<") == 2

    def it_never_emails_the_visitor_while_shared(guide: Member, other: Member, admin: Member, rows: list[TourBooking]):
        for member, row in ((other, rows[1]), (guide, rows[0]), (admin, rows[1])):
            client = signed_in(member)
            content = client.get(_notes_url(row), **HTMX).content.decode()
            assert 'name="email_visitor"' not in content
            assert client.post(_note_url(row), {"body": "Hi!", "email_visitor": "on"}).status_code == 403
        assert mail.outbox == []
        assert not TourBookingNote.objects.exists()

    def it_moves_the_notes_onto_the_row_a_guide_confirms(guide: Member, other: Member, rows: list[TourBooking]):
        _note(guide, rows[0], "Gina's note.")
        _note(other, rows[1], "Olive's note.")
        booking_service.confirm_tour_request(rows[1], actor=other.user)
        assert sorted(rows[1].notes.values_list("body", flat=True)) == ["Gina's note.", "Olive's note."]
        assert not rows[0].notes.exists()

    def it_moves_the_notes_to_the_row_taking_the_link_on_a_decline(
        guide: Member, other: Member, rows: list[TourBooking]
    ):
        _note(guide, rows[0], "Gina's note.")
        booking_service.decline_tour_request(rows[0], actor=guide.user)
        assert list(rows[1].notes.values_list("body", flat=True)) == ["Gina's note."]
        booking_service.decline_tour_request(rows[1], actor=other.user)
        assert list(rows[1].notes.values_list("body", flat=True)) == ["Gina's note."]

    def it_moves_the_notes_to_the_row_with_the_link_on_a_lapse(guide: Member, other: Member, rows: list[TourBooking]):
        _note(other, rows[1], "Olive's note.")
        booking_service.lapse_request(rows[1].pk, now=rows[1].confirm_by)
        assert list(rows[0].notes.values_list("body", flat=True)) == ["Olive's note."]

    def it_moves_the_notes_to_the_row_the_visitor_cancels(guide: Member, other: Member, rows: list[TourBooking]):
        _note(other, rows[1], "Olive's note.")
        booking_service.cancel_tour_by_visitor(rows[0])
        assert list(rows[0].notes.values_list("body", flat=True)) == ["Olive's note."]


def describe_review_fixes():
    def it_stops_belling_a_writer_who_no_longer_gives_tours(guide: Member, other: Member, booked: TourBooking):
        signed_in(guide)
        signed_in(other).post(_note_url(booked), {"body": "Olive here."})
        other.admin_capabilities.all().delete()
        signed_in(guide).post(_note_url(booked), {"body": "Thanks, Olive."})
        assert not Notification.objects.filter(user=other.user, trigger="tour.note_added").exists()

    def it_marks_an_emailed_note_edited_after_sending(guide: Member, booked: TourBooking):
        signed_in(guide).post(_note_url(booked), {"body": "Park on 9th.", "email_visitor": "on"})
        note = booked.notes.get()
        note.edit(body="Park on 9th, by the ramp.")
        assert note.thread_marker == "Emailed to the visitor, edited after sending"

    def it_sends_a_started_tour_to_the_past_table(guide: Member):
        starts = timezone.now() - timedelta(hours=1)
        started = TourBookingFactory(guide=guide, starts_at=starts, ends_at=starts + timedelta(hours=1))
        from tours.notify import notes_url

        assert notes_url(started).startswith(f"{PAST}?notes=")
        assert started.table_url_name(timezone.now()) == "tours:past"
