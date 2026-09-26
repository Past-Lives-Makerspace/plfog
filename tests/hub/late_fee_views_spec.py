"""BDD specs for the late cancellation fee views and pages (#456, part 2).

The Pay POST, the fee's own page, the Stripe return landing in each state, the cancelled
landing, another member's fee (404) and login; the two self cancel views going straight to
Checkout; and the guild page's block notice and cancel modal fee line.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing import late_fees
from billing.models import LateCancellationFee
from core.models import SiteConfiguration
from membership.models import EquipmentReservation, Guild, Member, OrientationBooking
from tests.billing.factories import LateCancellationFeeFactory
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_fee_view_1", "url": "https://checkout.stripe.example/cs_fee_view_1"}


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    client.login(username=username, password="pass")
    return user


def _own_fee(user: User, **overrides: Any) -> LateCancellationFee:
    return LateCancellationFeeFactory(orientation_booking=OrientationBookingFactory(member=user.member), **overrides)


def _site(*, enabled: bool = True) -> None:
    config = SiteConfiguration.load()
    config.late_cancel_fees_enabled = enabled
    config.save()


def _messages(response: HttpResponse) -> list[str]:
    return [str(m) for m in django_messages.get_messages(response.wsgi_request)]


def _retrieved(**overrides: Any) -> dict[str, Any]:
    session: dict[str, Any] = {
        "id": "cs_fee_view_1",
        "url": "https://checkout.stripe.example/cs_fee_view_1",
        "status": "open",
        "payment_status": "unpaid",
        "payment_intent": "",
        "amount_total": None,
    }
    session.update(overrides)
    return session


def describe_hub_late_fee_pay():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_mints_a_session_and_redirects_to_stripe(mock_create, client: Client):
        user = _login(client, "lfp_pay")
        fee = _own_fee(user)
        response = client.post(reverse("hub_late_fee_pay", args=[fee.pk]))
        assert response.status_code == 302
        assert response["Location"] == _SESSION["url"]
        fee.refresh_from_db()
        assert fee.stripe_session_id == "cs_fee_view_1"
        assert fee.checkout_attempts == 1

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_404s_another_members_fee(mock_create, client: Client):
        _login(client, "lfp_stranger")
        fee = LateCancellationFeeFactory()
        assert client.post(reverse("hub_late_fee_pay", args=[fee.pk])).status_code == 404
        assert not mock_create.called

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_404s_a_fee_that_is_not_unpaid(mock_create, client: Client):
        user = _login(client, "lfp_paid")
        fee = _own_fee(user, status=LateCancellationFee.Status.PAID)
        assert client.post(reverse("hub_late_fee_pay", args=[fee.pk])).status_code == 404
        assert not mock_create.called

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_returns_to_the_fee_page_with_an_error_when_stripe_fails(mock_create, client: Client):
        user = _login(client, "lfp_down")
        fee = _own_fee(user)
        response = client.post(reverse("hub_late_fee_pay", args=[fee.pk]))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_late_fee_detail", args=[fee.pk])
        assert any("couldn't open the payment page" in m for m in _messages(response))

    def it_requires_login(client: Client):
        fee = LateCancellationFeeFactory()
        response = client.post(reverse("hub_late_fee_pay", args=[fee.pk]))
        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]

    def it_rejects_get(client: Client):
        user = _login(client, "lfp_get")
        fee = _own_fee(user)
        assert client.get(reverse("hub_late_fee_pay", args=[fee.pk])).status_code == 405


def describe_hub_late_fee_detail():
    def it_shows_the_fee_with_its_pay_button(client: Client):
        user = _login(client, "lfd_unpaid")
        fee = _own_fee(user)
        response = client.get(reverse("hub_late_fee_detail", args=[fee.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-late-fee-status="unpaid"' in content
        assert f'action="{reverse("hub_late_fee_pay", args=[fee.pk])}"' in content
        assert fee.item_label in content
        assert f'href="{fee.owner_page_path()}"' in content

    def it_shows_a_paid_fee_without_the_pay_button(client: Client):
        user = _login(client, "lfd_paid")
        fee = _own_fee(user, status=LateCancellationFee.Status.PAID, paid_at=timezone.now())
        content = client.get(reverse("hub_late_fee_detail", args=[fee.pk])).content.decode()
        assert 'data-late-fee-status="paid"' in content
        assert reverse("hub_late_fee_pay", args=[fee.pk]) not in content

    def it_404s_another_members_fee(client: Client):
        _login(client, "lfd_stranger")
        assert client.get(reverse("hub_late_fee_detail", args=[LateCancellationFeeFactory().pk])).status_code == 404

    def it_404s_a_viewer_with_no_member(client: Client):
        user = _login(client, "lfd_no_member")
        user.member.delete()
        assert client.get(reverse("hub_late_fee_detail", args=[LateCancellationFeeFactory().pk])).status_code == 404

    def it_requires_login(client: Client):
        response = client.get(reverse("hub_late_fee_detail", args=[LateCancellationFeeFactory().pk]))
        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]


def describe_hub_late_fee_return():
    def _url(fee: LateCancellationFee) -> str:
        return reverse("hub_late_fee_return", args=[late_fees.make_checkout_token(fee)])

    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_reconciles_a_paid_session_and_says_thank_you(mock_retrieve, client: Client):
        mock_retrieve.return_value = _retrieved(payment_status="paid", payment_intent="pi_view_1", amount_total=1500)
        user = _login(client, "lfr_paid")
        fee = _own_fee(user, stripe_session_id="cs_fee_view_1")
        response = client.get(_url(fee))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-late-fee-state="paid"' in content
        assert 'data-late-fee-status="paid"' in content
        assert reverse("hub_late_fee_pay", args=[fee.pk]) not in content
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID

    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_keeps_the_pay_button_while_stripe_has_not_confirmed(mock_retrieve, client: Client):
        mock_retrieve.return_value = _retrieved()
        user = _login(client, "lfr_pending")
        fee = _own_fee(user, stripe_session_id="cs_fee_view_1")
        content = client.get(_url(fee)).content.decode()
        assert 'data-late-fee-state="pending"' in content
        assert f'action="{reverse("hub_late_fee_pay", args=[fee.pk])}"' in content

    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_never_asks_stripe_about_a_fee_already_paid(mock_retrieve, client: Client):
        user = _login(client, "lfr_already")
        fee = _own_fee(user, status=LateCancellationFee.Status.PAID, paid_at=timezone.now())
        content = client.get(_url(fee)).content.decode()
        assert 'data-late-fee-state="paid"' in content
        assert not mock_retrieve.called

    def it_says_nothing_more_to_pay_for_a_settled_fee(client: Client):
        user = _login(client, "lfr_settled")
        fee = _own_fee(user, status=LateCancellationFee.Status.WAIVED)
        content = client.get(_url(fee)).content.decode()
        assert 'data-late-fee-state="settled"' in content
        assert reverse("hub_late_fee_pay", args=[fee.pk]) not in content

    def it_renders_400_for_a_bad_token(client: Client):
        _login(client, "lfr_bad")
        response = client.get(reverse("hub_late_fee_return", args=["bogus-token"]))
        assert response.status_code == 400
        assert 'data-late-fee-state="invalid"' in response.content.decode()

    def it_404s_another_members_token(client: Client):
        _login(client, "lfr_stranger")
        assert client.get(_url(LateCancellationFeeFactory())).status_code == 404

    def it_requires_login(client: Client):
        response = client.get(_url(LateCancellationFeeFactory()))
        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]


def describe_hub_late_fee_checkout_cancelled():
    def _url(fee: LateCancellationFee) -> str:
        return reverse("hub_late_fee_checkout_cancelled", args=[late_fees.make_checkout_token(fee)])

    def it_says_the_fee_is_still_due_and_returns_to_the_owner_page(client: Client):
        user = _login(client, "lfc_due")
        reservation = EquipmentReservationFactory(member=user.member, status="cancelled")
        fee = LateCancellationFeeFactory(for_reservation=True, reservation=reservation)
        response = client.get(_url(fee))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_equipment_detail", args=[reservation.equipment.slug])
        assert any("still due" in m for m in _messages(response))

    def it_returns_quietly_for_a_settled_fee(client: Client):
        user = _login(client, "lfc_settled")
        fee = _own_fee(user, status=LateCancellationFee.Status.PAID)
        response = client.get(_url(fee))
        assert response["Location"] == fee.owner_page_path()
        assert _messages(response) == []

    def it_goes_home_on_a_bad_token(client: Client):
        _login(client, "lfc_bad")
        response = client.get(reverse("hub_late_fee_checkout_cancelled", args=["bogus-token"]))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_home")

    def it_404s_another_members_token(client: Client):
        _login(client, "lfc_stranger")
        assert client.get(_url(LateCancellationFeeFactory())).status_code == 404

    def it_requires_login(client: Client):
        response = client.get(_url(LateCancellationFeeFactory()))
        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]


def _late_confirmed_booking(user: User, *, hours_ahead: int = 3) -> OrientationBooking:
    settings_obj = GuildOrientationSettingsFactory(late_cancel_fee_cents=1500)
    starts = timezone.now() + timedelta(hours=hours_ahead)
    slot = OrientationSlotFactory(guild=settings_obj.guild, starts_at=starts, ends_at=starts + timedelta(hours=1))
    return OrientationBookingFactory(slot=slot, member=user.member, status=OrientationBooking.Status.CONFIRMED)


def describe_orientation_cancel_mine_when_late():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_cancels_and_sends_the_member_straight_to_checkout(mock_create, client: Client):
        _site()
        user = _login(client, "ocm_late")
        booking = _late_confirmed_booking(user)
        response = client.post(reverse("hub_orientation_cancel_mine", args=[booking.pk]))
        assert response.status_code == 302
        assert response["Location"] == _SESSION["url"]
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        fee = LateCancellationFee.objects.get(orientation_booking=booking)
        assert fee.stripe_session_id == "cs_fee_view_1"
        assert mock_create.call_args.kwargs["metadata"]["kind"] == "late_cancel_fee"

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_keeps_the_cancel_and_points_at_the_pay_button_when_checkout_cannot_open(mock_create, client: Client):
        _site()
        user = _login(client, "ocm_down")
        booking = _late_confirmed_booking(user)
        response = client.post(reverse("hub_orientation_cancel_mine", args=[booking.pk]))
        assert response.status_code == 302
        assert response["Location"] == booking.orientation_type.orientation_anchor_path()
        assert any("use the Pay button" in m for m in _messages(response))
        assert LateCancellationFee.objects.filter(orientation_booking=booking, status="unpaid").exists()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_cancels_an_early_booking_with_no_fee_and_no_checkout(mock_create, client: Client):
        _site()
        user = _login(client, "ocm_early")
        booking = _late_confirmed_booking(user, hours_ahead=40)
        response = client.post(reverse("hub_orientation_cancel_mine", args=[booking.pk]))
        assert response["Location"] == booking.orientation_type.orientation_anchor_path()
        assert not LateCancellationFee.objects.exists()
        assert not mock_create.called


def describe_reservation_self_cancel_when_late():
    def _late_reservation(user: User) -> EquipmentReservation:
        equipment = EquipmentFactory(late_cancel_fee_cents=1500)
        starts = timezone.now() + timedelta(hours=3)
        return EquipmentReservationFactory(
            equipment=equipment, member=user.member, starts_at=starts, ends_at=starts + timedelta(hours=1)
        )

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_answers_the_schedule_swap_with_an_hx_redirect_to_checkout(mock_create, client: Client):
        _site()
        user = _login(client, "rsc_late")
        reservation = _late_reservation(user)
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk])
        )
        assert response.status_code == 200
        assert response["HX-Redirect"] == _SESSION["url"]
        reservation.refresh_from_db()
        assert reservation.status == "cancelled"
        assert LateCancellationFee.objects.filter(reservation=reservation).exists()

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_toasts_the_fee_when_checkout_cannot_open(mock_create, client: Client):
        _site()
        user = _login(client, "rsc_down")
        reservation = _late_reservation(user)
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk])
        )
        assert response.status_code == 200
        assert not response.has_header("HX-Redirect")
        toast = json.loads(response["HX-Trigger"])["showToast"]
        assert toast["type"] == "info"
        assert "use the Pay button" in toast["message"]
        assert LateCancellationFee.objects.filter(reservation=reservation, status="unpaid").exists()


def describe_guild_page_block():
    def _guild_page(client: Client, guild: Guild) -> str:
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        return response.content.decode()

    def it_shows_the_sentence_with_a_pay_button_while_a_fee_is_unpaid(client: Client):
        user = _login(client, "gpb_unpaid")
        settings_obj = GuildOrientationSettingsFactory()
        OrientationSlotFactory(guild=settings_obj.guild)
        fee = _own_fee(user)
        content = _guild_page(client, settings_obj.guild)
        assert f'data-late-fee-notice="{fee.pk}"' in content
        assert f'action="{reverse("hub_late_fee_pay", args=[fee.pk])}"' in content
        assert "Pay your $15.00 late cancellation fee to book again." in content

    def it_shows_nothing_once_the_fee_is_paid(client: Client):
        user = _login(client, "gpb_paid")
        settings_obj = GuildOrientationSettingsFactory()
        OrientationSlotFactory(guild=settings_obj.guild)
        fee = _own_fee(user, status=LateCancellationFee.Status.PAID)
        content = _guild_page(client, settings_obj.guild)
        assert "data-late-fee-notice" not in content
        assert reverse("hub_late_fee_pay", args=[fee.pk]) not in content

    def _cancel_modal(content: str, booking: OrientationBooking) -> str:
        start = content.index(f"=== 'cancel-my-orientation-{booking.pk}') open = true")
        return content[start : content.index("</template>", start)]

    def it_appends_the_fee_line_to_the_cancel_modal_when_late(client: Client):
        _site()
        user = _login(client, "gpb_modal")
        booking = _late_confirmed_booking(user)
        content = _guild_page(client, booking.guild)
        modal = _cancel_modal(content, booking)
        assert "so a $15.00 late cancellation fee applies" in modal
        assert 'hx-boost="false"' in modal

    def it_keeps_the_early_cancel_modal_free_of_the_fee_line_but_still_unboosted(client: Client):
        # A cancel that turns late after page load answers with a redirect to Stripe, which a
        # boosted form could not follow, so the member self cancel form is never boosted.
        _site()
        user = _login(client, "gpb_early")
        booking = _late_confirmed_booking(user, hours_ahead=40)
        modal = _cancel_modal(_guild_page(client, booking.guild), booking)
        assert "late cancellation fee applies" not in modal
        assert 'hx-boost="false"' in modal

    def it_leaves_the_cancel_modal_alone_for_a_requested_booking(client: Client):
        _site()
        user = _login(client, "gpb_requested")
        booking = _late_confirmed_booking(user)
        booking.status = OrientationBooking.Status.REQUESTED
        booking.save(update_fields=["status"])
        modal = _cancel_modal(_guild_page(client, booking.guild), booking)
        assert "late cancellation fee applies" not in modal


def describe_equipment_page_block():
    def it_appends_the_fee_line_to_the_equipment_owned_orientation_cancel_modal_when_late(client: Client):
        from tests.membership.factories import OrientationTypeFactory

        _site()
        user = _login(client, "epb_modal")
        equipment = EquipmentFactory(late_cancel_fee_cents=2500)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        starts = timezone.now() + timedelta(hours=3)
        slot = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=starts,
            ends_at=starts + timedelta(hours=1),
        )
        booking = OrientationBookingFactory(slot=slot, member=user.member, status=OrientationBooking.Status.CONFIRMED)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        start = content.index(f"=== 'cancel-equip-orientation-{booking.pk}') open = true")
        modal = content[start : content.index("</template>", start)]
        assert "so a $25.00 late cancellation fee applies" in modal
        assert 'hx-boost="false"' in modal

    def it_hides_the_book_form_and_shows_the_fee_notice_in_the_banner(client: Client):
        user = _login(client, "epb_unpaid")
        equipment = EquipmentFactory(name="Open Bench")
        day = timezone.localdate() + timedelta(days=2)
        EquipmentHoursFactory(equipment=equipment, weekday=day.weekday())
        fee = _own_fee(user)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert f'data-late-fee-notice="{fee.pk}"' in content
        assert "You're all set." not in content
        assert 'id="equip-reserve-form"' not in content
