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
    "found_us_through": "A friend or family member",
    "guest_notes": "I throw pots at home and want a kiln and other potters.",
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
    return TourBookingForm(data, max_party=6)


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
            "found_us_through",
            "referred_by",
            "guest_notes",
            "wants_studio",
            "studio_description",
            "wants_newsletter",
            "website",
        ]

    def it_requires_a_phone():
        form = _form(phone="")
        assert not form.is_valid()
        assert "phone" in form.errors

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
        # One open question in place of interests and anything else (Morlock, 2026-11-22).
        assert "What brings you to Past Lives?" in content
        assert "most interested in" not in content and "Anything else you" not in content
        assert "how you found your way to us" in content
        assert "If someone here referred you, their name" in content
        assert "stairs" not in content.lower()


def describe_the_guides_email():
    def it_lists_the_answers(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        signed_in(guide)  # gives the guide an account, so their email has an address
        with django_capture_on_commit_callbacks(execute=True):
            Client(HTTP_HOST=HOST).post("/", _post(guide))
        email = next(message for message in mail.outbox if message.subject.startswith("New tour booked"))
        html = email.alternatives[0][0]
        for text in ("she/her", "A friend or family member", "Jo Member", "a kiln and other potters"):
            assert text in email.body
            assert text in html
        assert "What brings them here: I throw pots at home" in email.body
        assert "Referred by: Jo Member" in email.body

    def it_leaves_out_unanswered_questions(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        blanks = {name: "" for name in ANSWERS}
        signed_in(guide)  # gives the guide an account, so their email has an address
        with django_capture_on_commit_callbacks(execute=True):
            Client(HTTP_HOST=HOST).post("/", _post(guide, **blanks))
        email = next(message for message in mail.outbox if message.subject.startswith("New tour booked"))
        for label in ("Pronouns", "Found us through", "Referred by", "What brings them here"):
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


def describe_required_and_checkbox_fields():
    """Morlock's review of the preview: say which fields are required, offer the newsletter, and
    ask about a dedicated studio (feedback request #7 for the newsletter)."""

    def it_marks_name_email_and_phone_required():
        form = _form()
        assert [form.fields[name].label for name in ("name", "email", "phone")] == [
            "Your name (required)",
            "Email (required)",
            "Phone (required)",
        ]
        assert all(form.fields[name].required for name in ("name", "email", "phone"))
        assert "email" in _form(email="").errors

    def it_says_optional_nowhere_since_required_is_marked():
        assert not any("(optional)" in str(field.label) for field in _form().fields.values())

    def it_offers_the_newsletter_and_the_studio_unticked():
        form = _form()
        assert form.fields["wants_newsletter"].label == "Sign me up for the Past Lives newsletter"
        assert form.fields["wants_studio"].label == "I'm interested in a dedicated studio space"
        assert not form.fields["wants_newsletter"].initial and not form.fields["wants_studio"].initial

    def it_keeps_both_boxes_on_the_booking(guide: Member, hours: Any):
        Client(HTTP_HOST=HOST).post("/", _post(guide, wants_newsletter="on", wants_studio="on"))
        booking = TourBooking.objects.get()
        assert (booking.wants_newsletter, booking.wants_studio) == (True, True)

    def it_leaves_both_off_when_unticked(guide: Member, hours: Any):
        Client(HTTP_HOST=HOST).post("/", _post(guide))
        booking = TourBooking.objects.get()
        assert (booking.wants_newsletter, booking.wants_studio) == (False, False)

    def it_tells_the_guide_about_studio_interest(guide: Member):
        booking = TourBookingFactory(guide=guide, starts_at=local(_day(), 16), wants_studio=True)
        assert ("Interested in a studio", "Yes") in booking.guest_answers
        assert all(label != "Interested in a studio" for label, _ in TourBookingFactory(guide=guide).guest_answers)


def describe_the_newsletter_signup():
    class _Mailchimp:
        enabled = True

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def subscribe(self, **kwargs: Any) -> bool:
            self.calls.append(kwargs)
            return True

    def _book(guide: Member, capture: Any, **values: str) -> list[dict[str, Any]]:
        from unittest.mock import patch

        fake = _Mailchimp()
        with patch("core.integrations.mailchimp.MailchimpClient.from_site_config", return_value=fake):
            with capture(execute=True):
                Client(HTTP_HOST=HOST).post("/", _post(guide, **values))
        return fake.calls

    def it_subscribes_a_visitor_who_ticked_it(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        calls = _book(guide, django_capture_on_commit_callbacks, wants_newsletter="on")
        assert calls == [
            {"email": "olivia@example.com", "first_name": "Olivia", "last_name": "Vittitow", "tags": ["tour-visitor"]}
        ]

    def it_tags_studio_interest(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        calls = _book(guide, django_capture_on_commit_callbacks, wants_newsletter="on", wants_studio="on")
        assert calls[0]["tags"] == ["tour-visitor", "tour-studio-interest"]

    def it_never_subscribes_one_who_did_not(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        assert _book(guide, django_capture_on_commit_callbacks) == []
        assert _book(guide, django_capture_on_commit_callbacks, wants_studio="on", email="b@example.com") == []

    def it_invites_the_rest_from_the_confirmation_email(
        guide: Member, hours: Any, django_capture_on_commit_callbacks: Any
    ):
        _book(guide, django_capture_on_commit_callbacks)
        _book(guide, django_capture_on_commit_callbacks, wants_newsletter="on", email="joined@example.com")
        invited = next(message for message in mail.outbox if message.to == ["olivia@example.com"])
        joined = next(message for message in mail.outbox if message.to == ["joined@example.com"])
        link = "https://www.pastlives.space/#footer-sections"
        assert "Join our newsletter" in invited.body and link in invited.body
        assert link in invited.alternatives[0][0]
        assert "Join our newsletter" not in joined.body and link not in joined.alternatives[0][0]


def describe_the_ideal_studio_question():
    """Morlock: ticking the studio box opens a field to describe their ideal studio."""

    def it_follows_the_studio_box_on_the_form():
        form = _form()
        assert form.fields["studio_description"].label == "Describe your ideal studio and what you'd do with it"
        assert list(form.fields).index("studio_description") == list(form.fields).index("wants_studio") + 1

    def it_shows_only_while_the_box_is_ticked(guide: Member, hours: Any):
        content = Client(HTTP_HOST=HOST).get("/", {"tour": _key(guide)}).content.decode()
        assert 'x-model="studio"' in content
        assert 'data-tour-studio-description x-show="studio"' in content

    def it_keeps_the_description_with_the_box(guide: Member, hours: Any):
        Client(HTTP_HOST=HOST).post("/", _post(guide, wants_studio="on", studio_description="  A kiln corner  "))
        booking = TourBooking.objects.get()
        assert booking.studio_description == "A kiln corner"
        assert ("Ideal studio", "A kiln corner") in booking.guest_answers

    def it_drops_a_description_typed_before_the_box_was_unticked(guide: Member, hours: Any):
        Client(HTTP_HOST=HOST).post("/", _post(guide, studio_description="A kiln corner"))
        assert TourBooking.objects.get().studio_description == ""


def describe_the_one_open_question():
    """Morlock, 2026-11-22: interests and "anything else" become one warm question about their work and why they came."""

    def it_asks_what_brings_them_and_what_they_make():
        field = _form().fields["guest_notes"]
        assert field.label == "What brings you to Past Lives?"
        assert "what you make" in field.help_text and "hoping to find" in field.help_text

    def it_no_longer_asks_for_interests_separately():
        assert "interests" not in _form().fields

    def it_shows_the_guide_the_answer_as_what_brings_them(guide: Member):
        booking = TourBookingFactory(guide=guide, guest_notes="Furniture, mostly.")
        assert ("What brings them here", "Furniture, mostly.") in booking.guest_answers
