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


def describe_late_cancel_fee_on_the_cancel_link():
    """The emailed cancel link explains the fee before the click and links the fee after (#456, part 2)."""

    from datetime import timedelta

    from django.contrib.auth.models import User
    from django.utils import timezone

    from billing.models import LateCancellationFee
    from core.models import SiteConfiguration
    from tests.membership.factories import (
        GuildOrientationSettingsFactory,
        MembershipPlanFactory,
        OrientationSlotFactory,
    )

    def _confirmed(username: str, *, hours_ahead: int = 3, status: str = OrientationBooking.Status.CONFIRMED):
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.save()
        MembershipPlanFactory()
        member = User.objects.create_user(username=username, email=f"{username}@example.com").member
        settings_obj = GuildOrientationSettingsFactory(late_cancel_fee_cents=1500)
        starts = timezone.now() + timedelta(hours=hours_ahead)
        slot = OrientationSlotFactory(guild=settings_obj.guild, starts_at=starts, ends_at=starts + timedelta(hours=1))
        return OrientationBookingFactory(slot=slot, member=member, status=status)

    def it_shows_the_fee_sentence_before_a_late_cancel(client: Client):
        booking = _confirmed("tok_late")
        content = client.get(_url(booking, "cancel")).content.decode()
        assert "data-late-cancel-warning" in content
        assert "so a $15.00 late cancellation fee applies" in content

    def it_says_nothing_before_an_early_cancel(client: Client):
        booking = _confirmed("tok_early", hours_ahead=40)
        assert "data-late-cancel-warning" not in client.get(_url(booking, "cancel")).content.decode()

    def it_says_nothing_on_the_confirm_link_or_for_a_requested_booking(client: Client):
        booking = _confirmed("tok_confirm_link")
        assert "data-late-cancel-warning" not in client.get(_url(booking, "confirm")).content.decode()
        requested = _confirmed("tok_requested", status=OrientationBooking.Status.REQUESTED)
        assert "data-late-cancel-warning" not in client.get(_url(requested, "cancel")).content.decode()

    def it_names_the_fee_and_links_its_page_after_a_late_cancel(client: Client):
        booking = _confirmed("tok_late_post")
        response = client.post(_url(booking, "cancel"))
        assert response.status_code == 200
        fee = LateCancellationFee.objects.get(orientation_booking=booking)
        content = response.content.decode()
        assert b"Orientation Cancelled" in response.content
        assert f'href="{reverse("hub_late_fee_detail", args=[fee.pk])}" data-late-fee="{fee.pk}"' in content
        assert "A $15.00 late cancellation fee applies" in content
        # The no-login page never redirects to Stripe: the member pays from the fee page once signed in.
        assert "Location" not in response

    def it_links_no_fee_after_an_early_cancel(client: Client):
        booking = _confirmed("tok_early_post", hours_ahead=40)
        content = client.post(_url(booking, "cancel")).content.decode()
        assert "Orientation Cancelled" in content
        assert "data-late-fee=" not in content
        assert not LateCancellationFee.objects.exists()
