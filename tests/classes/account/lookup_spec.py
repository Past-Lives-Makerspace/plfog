import pytest
from django.test import Client

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    RegistrationFactory,
)
from classes.models import Registration


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")


def _booking(last_name="Sandoval"):
    offering = ClassOfferingFactory(status="published")
    ClassSessionFactory(class_offering=offering)
    return RegistrationFactory(
        last_name=last_name,
        class_offering=offering,
        status=Registration.Status.CONFIRMED,
    )


def describe_guest_lookup():
    def it_renders_empty_form_for_anonymous_user(book_client, db):
        resp = book_client.get("/account/lookup/")
        assert resp.status_code == 200
        assert b"Find your class" in resp.content
        assert b"Last name" in resp.content
        assert b"Order number" in resp.content

    def it_finds_a_booking_with_matching_name_and_order_number(book_client, db):
        reg = _booking()
        resp = book_client.post(
            "/account/lookup/",
            {
                "last_name": reg.last_name,
                "order_number": reg.order_number,
            },
        )
        assert resp.status_code == 200
        assert reg.class_offering.title.encode() in resp.content
        assert b"Found" in resp.content

    def it_is_case_insensitive_on_last_name(book_client, db):
        reg = _booking(last_name="Sandoval")
        resp = book_client.post(
            "/account/lookup/",
            {
                "last_name": "sandoval",
                "order_number": reg.order_number,
            },
        )
        assert b"Found" in resp.content

    def it_normalizes_order_number_to_uppercase(book_client, db):
        reg = _booking()
        resp = book_client.post(
            "/account/lookup/",
            {
                "last_name": reg.last_name,
                "order_number": reg.order_number.lower(),  # lowercase
            },
        )
        assert b"Found" in resp.content

    def it_shows_notfound_when_no_match(book_client, db):
        resp = book_client.post(
            "/account/lookup/",
            {
                "last_name": "Nobody",
                "order_number": "PL-XXXX-99",
            },
        )
        assert resp.status_code == 200
        assert b"couldn&#x27;t find that one" in resp.content or b"couldn't find that one" in resp.content

    def it_rejects_malformed_order_number_format(book_client, db):
        resp = book_client.post(
            "/account/lookup/",
            {
                "last_name": "Sandoval",
                "order_number": "garbage",
            },
        )
        assert resp.status_code == 200
        assert b"PL-XXXX-YY" in resp.content or b"look like PL" in resp.content

    def it_does_not_leak_other_users_bookings_via_partial_match(book_client, db):
        _booking(last_name="Sandoval")
        _booking(last_name="Smith")
        # Smith's order number; query asks for Sandoval — must NOT find Smith's booking.
        smiths_order = Registration.objects.get(last_name="Smith").order_number
        resp = book_client.post(
            "/account/lookup/",
            {
                "last_name": "Sandoval",
                "order_number": smiths_order,
            },
        )
        assert b"couldn&#x27;t find that one" in resp.content or b"couldn't find that one" in resp.content
