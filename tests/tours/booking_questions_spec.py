"""The booking questions SimplyBook asked (feedback request #32): asked on the booking form, kept on the
booking, and shown to the guide in their email and on the Upcoming tab (the calendar event reads the same fields, #33)."""

from __future__ import annotations

from datetime import UTC, date, timedelta
from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.forms import TourBookingForm
from tours.models import TourBooking

pytestmark = pytest.mark.django_db

HOST = "tours.pastlives.space"

ANSWERS = {
    "pronouns": "she/her",
    "interests": "Ceramics and Writing",
    "found_us_through": "A friend or family member",
    "guest_notes": "Bringing my kid",
    "referred_by": "Jo Member",
}


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    cache.clear()


def _day() -> date:
    return timezone.localdate() + timedelta(days=4)


def _key(guide: Member, hour: int = 16) -> str:
    return f"{guide.pk}-{local(_day(), hour).astimezone(UTC):%Y%m%dT%H%MZ}"


def _post(guide: Member, **values: str) -> dict[str, str]:
    data = {
        "tour": _key(guide),
        "name": "Olivia Vittitow",
        "email": "olivia@example.com",
        "phone": "502 693 4099",
        "party_size": "1",
        **ANSWERS,
    }
    data.update(values)
    return data


def _form(**values: str) -> TourBookingForm:
    data = {"tour": "1-20300101T0000Z", "name": "Olivia", "email": "o@example.com", "phone": "5035550142"}
    data.update({"party_size": "1", **values})
    return TourBookingForm(data, max_party=6, offer_account=False)


@pytest.fixture
def hours(guide: Member) -> Any:
    return TourHoursFactory(guide=guide, weekday=_day().weekday(), seats=4)


def describe_the_form():
    def it_asks_the_questions_in_simplybooks_order():
        assert list(_form().fields) == [
            "tour",
            "name",
            "email",
            "phone",
            "party_size",
            "pronouns",
            "interests",
            "found_us_through",
            "referred_by",
            "guest_notes",
            "website",
        ]

    def it_requires_a_phone():
        form = _form(phone="")
        assert not form.is_valid()
        assert "phone" in form.errors
        assert form.fields["phone"].label == "Phone"

    def it_takes_every_answer_as_optional_but_the_phone():
        assert _form().is_valid()

    def it_offers_simplybooks_found_us_choices_in_their_words():
        choices = [label for _, label in _form().fields["found_us_through"].choices]
        assert choices[0] == "Pick one"
        assert choices[1:] == [
            "Google Search",
            "Google Maps",
            "A friend or family member",
            "A colleague or coworker",
            "Instagram",
            "Facebook",
            "Reddit",
            "Another makerspace or community org",
            "Walked or drove by",
            "Attended an event here",
            "Other",
        ]

    def it_refuses_a_found_us_answer_off_the_list():
        assert "found_us_through" in _form(found_us_through="TikTok").errors

    def it_never_asks_about_stairs():
        assert not any("stair" in str(field.label).lower() for field in _form().fields.values())

    def it_asks_exactly_what_a_booking_keeps():
        from tours.booking import ANSWER_FIELDS
        from tours.forms import QUESTION_FIELDS

        assert set(QUESTION_FIELDS) == set(ANSWER_FIELDS)
        assert all(TourBooking._meta.get_field(name) for name in ANSWER_FIELDS)


def describe_booking_on_the_website():
    def _client() -> Client:
        return Client(HTTP_HOST=HOST)

    def it_keeps_every_answer_on_the_booking(guide: Member, hours: Any):
        response = _client().post("/", _post(guide))
        assert response.status_code == 302
        booking = TourBooking.objects.get()
        assert booking.visitor_phone == "502 693 4099"
        assert {name: getattr(booking, name) for name in ANSWERS} == ANSWERS

    def it_trims_the_answers(guide: Member, hours: Any):
        _client().post("/", _post(guide, pronouns="  they/them  ", guest_notes="\n hi \n"))
        booking = TourBooking.objects.get()
        assert (booking.pronouns, booking.guest_notes) == ("they/them", "hi")

    def it_books_with_only_the_required_answers(guide: Member, hours: Any):
        blanks = {name: "" for name in ANSWERS}
        assert _client().post("/", _post(guide, **blanks)).status_code == 302
        booking = TourBooking.objects.get()
        assert all(getattr(booking, name) == "" for name in ANSWERS)

    def it_shows_the_questions_on_the_page(guide: Member, hours: Any):
        content = _client().get("/", {"tour": _key(guide)}).content.decode()
        assert 'name="pronouns"' in content
        assert "What areas or disciplines are you most interested in?" in content
        assert "how you found your way to us" in content
        assert "If someone here referred you, their name" in content
        assert "Anything else you&#x27;d like us to know before your tour?" in content
        assert "stairs" not in content.lower()


def describe_the_guides_email():
    def it_lists_the_answers(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        signed_in(guide)  # gives the guide an account, so their email has an address
        with django_capture_on_commit_callbacks(execute=True):
            Client(HTTP_HOST=HOST).post("/", _post(guide))
        email = next(message for message in mail.outbox if message.subject.startswith("New tour booked"))
        html = email.alternatives[0][0]
        for text in ("she/her", "Ceramics and Writing", "A friend or family member", "Jo Member", "Bringing my kid"):
            assert text in email.body
            assert text in html
        assert "Interested in: Ceramics and Writing" in email.body
        assert "Referred by: Jo Member" in email.body

    def it_leaves_out_unanswered_questions(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        blanks = {name: "" for name in ANSWERS}
        signed_in(guide)  # gives the guide an account, so their email has an address
        with django_capture_on_commit_callbacks(execute=True):
            Client(HTTP_HOST=HOST).post("/", _post(guide, **blanks))
        email = next(message for message in mail.outbox if message.subject.startswith("New tour booked"))
        for label in ("Pronouns", "Interested in", "Found us through", "Referred by", "Notes from the guest"):
            assert label not in email.body
            assert label not in email.alternatives[0][0]


def describe_the_upcoming_tab():
    def _booking(guide: Member) -> TourBooking:
        return TourBookingFactory(guide=guide, starts_at=local(_day(), 16), **ANSWERS)

    def it_shows_the_answers_to_the_tours_guide(guide: Member):
        _booking(guide)
        content = signed_in(guide).get(reverse("tours:upcoming")).content.decode()
        assert "data-tour-booking-answers" in content
        for text in ANSWERS.values():
            assert text in content

    def it_keeps_them_from_another_guide(guide: Member):
        _booking(guide)
        other = make_guide(full_legal_name="Olive Other")
        content = signed_in(other).get(reverse("tours:upcoming")).content.decode()
        assert "data-tour-booking-answers" not in content
        assert "Bringing my kid" not in content and "she/her" not in content

    def it_shows_nothing_extra_when_nothing_was_answered(guide: Member):
        TourBookingFactory(guide=guide, starts_at=local(_day(), 16))
        content = signed_in(guide).get(reverse("tours:upcoming")).content.decode()
        assert "data-tour-booking-answers" not in content
