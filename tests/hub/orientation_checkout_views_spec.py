"""BDD specs for the hub checkout views: booking branch, return page, cancels, and resume."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership import orientations
from membership.models import OrientationBooking, OrientationSlot
from tests.membership.factories import (
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_view_1", "url": "https://checkout.stripe.example/cs_view_1"}


def _user(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    return user


def _paid_slot(price_cents: int = 1500) -> OrientationSlot:
    settings_obj = GuildOrientationSettingsFactory(price_cents=price_cents)
    return OrientationSlotFactory(guild=settings_obj.guild)


def _retrieved(**overrides):
    session = {
        "id": "cs_view_1",
        "url": "https://checkout.stripe.example/cs_view_1",
        "status": "open",
        "payment_status": "unpaid",
        "payment_intent": "",
        "amount_total": None,
    }
    session.update(overrides)
    return session


def _hold_for(user: User, slot: OrientationSlot | None = None) -> OrientationBooking:
    slot = slot or _paid_slot()
    return OrientationBookingFactory(
        slot=slot,
        member=user.member,
        status=OrientationBooking.Status.PENDING_PAYMENT,
        amount_paid_cents=1500,
        stripe_session_id="cs_view_1",
    )


def describe_orientation_book_branching():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_redirects_a_paid_guild_booking_to_stripe(mock_create, client: Client):
        user = _user("payer1")
        slot = _paid_slot()
        client.login(username="payer1", password="pass")

        response = client.post(reverse("hub_orientation_book", args=[slot.pk]))

        assert response.status_code == 302
        assert response["Location"] == _SESSION["url"]
        assert OrientationBooking.objects.filter(
            member=user.member, status=OrientationBooking.Status.PENDING_PAYMENT
        ).exists()

    def it_keeps_the_free_flow_untouched(client: Client):
        user = _user("payer2")
        slot = OrientationSlotFactory()
        client.login(username="payer2", password="pass")
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]))
        assert response.status_code == 302
        booking = OrientationBooking.objects.get(member=user.member)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.amount_paid_cents == 0

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_surfaces_a_friendly_error_on_stripe_failure(mock_create, client: Client):
        _user("payer3")
        slot = _paid_slot()
        client.login(username="payer3", password="pass")
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]), follow=True)
        assert response.status_code == 200
        assert OrientationBooking.objects.count() == 0
        assert any("checkout" in str(m).lower() for m in response.context["messages"])


def describe_custom_request_branching():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_redirects_a_paid_custom_request_to_stripe(mock_create, client: Client):
        _user("payer4")
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500, allow_custom_requests=True)
        client.login(username="payer4", password="pass")
        starts = timezone.localtime(timezone.now() + timedelta(days=3))
        response = client.post(
            reverse("hub_guild_orientation_request_custom", args=[settings_obj.guild.pk]),
            {"starts_at": starts.strftime("%Y-%m-%dT%H:%M"), "note": ""},
        )
        assert response.status_code == 302
        assert response["Location"] == _SESSION["url"]


def describe_orientation_checkout_return():
    def it_renders_state_a_when_the_webhook_landed(client: Client):
        user = _user("ret1")
        booking = OrientationBookingFactory(
            slot=_paid_slot(), member=user.member, amount_paid_cents=1500, stripe_payment_id="pi_1"
        )
        client.login(username="ret1", password="pass")
        response = client.get(
            reverse("hub_orientation_checkout_return", args=[orientations.make_checkout_token(booking)])
        )
        assert response.status_code == 200
        assert "Pending Confirmation" in response.content.decode()
        assert "$15" in response.content.decode()

    def it_renders_the_polling_state_while_the_webhook_lags(client: Client):
        user = _user("ret2")
        hold = _hold_for(user)
        client.login(username="ret2", password="pass")
        response = client.get(reverse("hub_orientation_checkout_return", args=[orientations.make_checkout_token(hold)]))
        content = response.content.decode()
        assert "Finalizing Your Payment" in content
        assert "hx-trigger" in content

    def it_calms_down_after_twenty_polls(client: Client):
        user = _user("ret3")
        hold = _hold_for(user)
        client.login(username="ret3", password="pass")
        url = reverse("hub_orientation_checkout_return", args=[orientations.make_checkout_token(hold)])
        response = client.get(f"{url}?n=20", HTTP_HX_REQUEST="true")
        content = response.content.decode()
        assert "Still Processing" in content
        assert "hx-trigger" not in content

    def it_returns_a_friendly_400_for_a_bad_token(client: Client):
        _user("ret4")
        client.login(username="ret4", password="pass")
        response = client.get(reverse("hub_orientation_checkout_return", args=["not-a-token"]))
        assert response.status_code == 400
        assert "We Couldn't Find That Booking" in response.content.decode()


def describe_orientation_checkout_cancelled():
    def it_confirms_on_get_without_touching_the_hold(client: Client):
        user = _user("can1")
        hold = _hold_for(user)
        client.login(username="can1", password="pass")
        response = client.get(
            reverse("hub_orientation_checkout_cancelled", args=[orientations.make_checkout_token(hold)])
        )
        assert response.status_code == 200
        assert "Cancel This Booking?" in response.content.decode()
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    def it_releases_an_unpaid_hold_on_post(mock_retrieve, client: Client):
        user = _user("can2")
        hold = _hold_for(user)
        client.login(username="can2", password="pass")
        response = client.post(
            reverse("hub_orientation_checkout_cancelled", args=[orientations.make_checkout_token(hold)]),
            follow=True,
        )
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()
        assert any("not billed" in str(m).lower() for m in response.context["messages"])

    @patch(
        "billing.stripe_utils.retrieve_checkout_session",
        return_value=_retrieved(status="complete", payment_status="paid", payment_intent="pi_9", amount_total=1500),
    )
    def it_keeps_a_paid_hold_and_reports_the_good_news(mock_retrieve, client: Client):
        user = _user("can3")
        hold = _hold_for(user)
        client.login(username="can3", password="pass")
        response = client.post(
            reverse("hub_orientation_checkout_cancelled", args=[orientations.make_checkout_token(hold)]),
            follow=True,
        )
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED
        assert any("already went through" in str(m).lower() for m in response.context["messages"])

    def it_redirects_quietly_when_the_hold_is_already_gone(client: Client):
        user = _user("can4")
        booking = OrientationBookingFactory(slot=_paid_slot(), member=user.member)  # already REQUESTED
        client.login(username="can4", password="pass")
        response = client.get(
            reverse("hub_orientation_checkout_cancelled", args=[orientations.make_checkout_token(booking)])
        )
        assert response.status_code == 302


def describe_orientation_checkout_cancel_hold():
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    def it_releases_the_members_own_unpaid_hold(mock_retrieve, client: Client):
        user = _user("ch1")
        hold = _hold_for(user)
        client.login(username="ch1", password="pass")
        response = client.post(reverse("hub_orientation_checkout_cancel_hold", args=[hold.pk]), follow=True)
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()
        assert any("not charged" in str(m).lower() for m in response.context["messages"])

    def it_403s_on_anyone_elses_hold(client: Client):
        _user("ch2")
        other = _user("ch2other")
        hold = _hold_for(other)
        client.login(username="ch2", password="pass")
        response = client.post(reverse("hub_orientation_checkout_cancel_hold", args=[hold.pk]))
        assert response.status_code == 403
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()

    @patch(
        "billing.stripe_utils.retrieve_checkout_session",
        return_value=_retrieved(status="complete", payment_status="paid", payment_intent="pi_7", amount_total=1500),
    )
    def it_keeps_a_finalizing_payment(mock_retrieve, client: Client):
        user = _user("ch3")
        hold = _hold_for(user)
        client.login(username="ch3", password="pass")
        response = client.post(reverse("hub_orientation_checkout_cancel_hold", args=[hold.pk]), follow=True)
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED
        assert any("finalizing" in str(m).lower() for m in response.context["messages"])

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_reports_unknown_when_stripe_is_unreachable(mock_retrieve, client: Client):
        user = _user("ch4")
        hold = _hold_for(user)
        client.login(username="ch4", password="pass")
        response = client.post(reverse("hub_orientation_checkout_cancel_hold", args=[hold.pk]), follow=True)
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()
        assert any("try again in a minute" in str(m).lower() for m in response.context["messages"])


def describe_orientation_checkout_resume():
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="open"))
    def it_redirects_to_the_same_live_session(mock_retrieve, client: Client):
        user = _user("res1")
        hold = _hold_for(user)
        client.login(username="res1", password="pass")
        response = client.post(reverse("hub_orientation_checkout_resume", args=[hold.pk]))
        assert response.status_code == 302
        assert response["Location"] == _retrieved()["url"]

    @patch(
        "billing.stripe_utils.retrieve_checkout_session",
        return_value=_retrieved(status="complete", payment_status="paid", payment_intent="pi_res", amount_total=1500),
    )
    def it_recovers_a_paid_session_and_lands_on_the_return_page(mock_retrieve, client: Client):
        user = _user("res2")
        hold = _hold_for(user)
        client.login(username="res2", password="pass")
        response = client.post(reverse("hub_orientation_checkout_resume", args=[hold.pk]))
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED
        assert "/orientation/checkout/return/" in response["Location"]

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired", url=""))
    def it_releases_an_expired_session_and_bounces_to_the_guild_page(mock_retrieve, client: Client):
        user = _user("res3")
        hold = _hold_for(user)
        client.login(username="res3", password="pass")
        response = client.post(reverse("hub_orientation_checkout_resume", args=[hold.pk]), follow=True)
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()
        assert any("expired" in str(m).lower() for m in response.context["messages"])

    def it_403s_on_anyone_elses_hold(client: Client):
        _user("res4")
        other = _user("res4other")
        hold = _hold_for(other)
        client.login(username="res4", password="pass")
        assert client.post(reverse("hub_orientation_checkout_resume", args=[hold.pk])).status_code == 403


def describe_edge_branches():
    def it_treats_a_garbled_poll_count_as_zero(client: Client):
        user = _user("edge1")
        hold = _hold_for(user)
        client.login(username="edge1", password="pass")
        url = reverse("hub_orientation_checkout_return", args=[orientations.make_checkout_token(hold)])
        response = client.get(f"{url}?n=notanumber")
        assert response.status_code == 200
        assert "Finalizing Your Payment" in response.content.decode()

    def it_serves_the_poll_fragment_without_the_page_chrome(client: Client):
        user = _user("edge2")
        hold = _hold_for(user)
        client.login(username="edge2", password="pass")
        url = reverse("hub_orientation_checkout_return", args=[orientations.make_checkout_token(hold)])
        content = client.get(url, HTTP_HX_REQUEST="true").content.decode()
        assert "Finalizing Your Payment" in content
        assert "<html" not in content

    def it_returns_the_fragment_variant_of_the_error_card(client: Client):
        _user("edge3")
        client.login(username="edge3", password="pass")
        response = client.get(reverse("hub_orientation_checkout_return", args=["bad-token"]), HTTP_HX_REQUEST="true")
        assert response.status_code == 400
        assert "We Couldn't Find That Booking" in response.content.decode()

    def it_bounces_a_cancelled_landing_with_a_dead_token_to_the_directory(client: Client):
        _user("edge4")
        client.login(username="edge4", password="pass")
        response = client.get(reverse("hub_orientation_checkout_cancelled", args=["bad-token"]))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_guild_directory")

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_reports_unknown_on_the_cancelled_post_when_stripe_is_unreachable(mock_retrieve, client: Client):
        user = _user("edge5")
        hold = _hold_for(user)
        client.login(username="edge5", password="pass")
        response = client.post(
            reverse("hub_orientation_checkout_cancelled", args=[orientations.make_checkout_token(hold)]),
            follow=True,
        )
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()
        assert any("try again in a minute" in str(m).lower() for m in response.context["messages"])

    def it_redirects_cancel_hold_quietly_when_the_hold_already_advanced(client: Client):
        user = _user("edge6")
        booking = OrientationBookingFactory(slot=_paid_slot(), member=user.member, amount_paid_cents=1500)
        client.login(username="edge6", password="pass")
        response = client.post(reverse("hub_orientation_checkout_cancel_hold", args=[booking.pk]))
        assert response.status_code == 302
        assert OrientationBooking.objects.filter(pk=booking.pk).exists()

    def it_403s_resume_when_the_hold_already_advanced(client: Client):
        user = _user("edge7")
        booking = OrientationBookingFactory(slot=_paid_slot(), member=user.member, amount_paid_cents=1500)
        client.login(username="edge7", password="pass")
        assert client.post(reverse("hub_orientation_checkout_resume", args=[booking.pk])).status_code == 403

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_reports_unknown_on_resume_when_stripe_is_unreachable(mock_retrieve, client: Client):
        user = _user("edge8")
        hold = _hold_for(user)
        client.login(username="edge8", password="pass")
        response = client.post(reverse("hub_orientation_checkout_resume", args=[hold.pk]), follow=True)
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()
        assert any("try again in a minute" in str(m).lower() for m in response.context["messages"])

    @patch(
        "billing.stripe_utils.create_checkout_session",
        side_effect=RuntimeError("stripe down"),
    )
    def it_surfaces_a_friendly_error_when_a_custom_checkout_fails(mock_create, client: Client):
        _user("edge9")
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500, allow_custom_requests=True)
        client.login(username="edge9", password="pass")
        starts = timezone.localtime(timezone.now() + timedelta(days=3))
        response = client.post(
            reverse("hub_guild_orientation_request_custom", args=[settings_obj.guild.pk]),
            {"starts_at": starts.strftime("%Y-%m-%dT%H:%M"), "note": ""},
            follow=True,
        )
        assert OrientationBooking.objects.count() == 0
        assert any("checkout" in str(m).lower() for m in response.context["messages"])

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_surfaces_the_domain_error_when_a_custom_checkout_is_guarded(mock_create, client: Client):
        user = _user("edge10")
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500, allow_custom_requests=True)
        OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=settings_obj.guild),
            member=user.member,
            status=OrientationBooking.Status.PENDING_PAYMENT,
            amount_paid_cents=1500,
        )
        client.login(username="edge10", password="pass")
        starts = timezone.localtime(timezone.now() + timedelta(days=3))
        response = client.post(
            reverse("hub_guild_orientation_request_custom", args=[settings_obj.guild.pk]),
            {"starts_at": starts.strftime("%Y-%m-%dT%H:%M"), "note": ""},
            follow=True,
        )
        assert any("checkout in progress" in str(m).lower() for m in response.context["messages"])
