"""BDD specs for the no-login orientation action page (email links)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from membership import orientations
from membership.models import OrientationBooking
from tests.membership.factories import OrientationBookingFactory

pytestmark = pytest.mark.django_db


def _url(booking: OrientationBooking, action: str) -> str:
    return reverse("hub_orientation_action", args=[orientations.make_action_token(booking, action)])


def describe_orientation_action():
    def it_shows_a_confirmation_page_on_get(client: Client):
        booking = OrientationBookingFactory()
        response = client.get(_url(booking, "confirm"))
        assert response.status_code == 200
        assert b"Confirm this orientation?" in response.content

    def it_does_not_mutate_on_get(client: Client):
        booking = OrientationBookingFactory()
        client.get(_url(booking, "confirm"))
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.REQUESTED

    def it_confirms_on_post(client: Client):
        booking = OrientationBookingFactory()
        response = client.post(_url(booking, "confirm"))
        assert response.status_code == 200
        assert b"Orientation Confirmed" in response.content
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_declines_on_post(client: Client):
        booking = OrientationBookingFactory()
        response = client.post(_url(booking, "decline"))
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.DECLINED
        assert b"Request Declined" in response.content

    def it_cancels_on_post(client: Client):
        booking = OrientationBookingFactory()
        booking.confirm()
        response = client.post(_url(booking, "cancel"))
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert b"Orientation Cancelled" in response.content

    def it_reports_already_handled_for_a_resolved_booking(client: Client):
        booking = OrientationBookingFactory()
        booking.decline()
        response = client.post(_url(booking, "confirm"))
        assert b"Already Handled" in response.content

    def it_rejects_an_invalid_token(client: Client):
        response = client.get(reverse("hub_orientation_action", args=["bogus-token"]))
        assert response.status_code == 400
        assert b"No Longer Valid" in response.content
