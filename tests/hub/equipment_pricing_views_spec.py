"""BDD specs for the screens of priced equipment reservations (#749, part 1).

The Hours & Limits tab's Pricing card and its form; the member's schedule copy for every
pricing and approval pair (chip, terms line, total, button, the four confirm variants); the
reserve endpoint's road to Stripe Checkout; the return, cancelled and Pay now pages; Cancel on
an unpaid hold; the refund wording on the decline and manager cancel modals and messages; the
manage page's Upcoming list; the member's rows; and the Bookings tab pills. Stripe is mocked.
Names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing.models import PaymentRefund
from membership import equipment as equipment_service
from membership.models import Equipment, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

HOLD = EquipmentReservation.Status.PENDING_PAYMENT
WAITING = EquipmentReservation.Status.PENDING_APPROVAL
_SESSION = {"id": "cs_view_1", "url": "https://checkout.stripe.example/cs_view_1"}
#: Stripe refund ids, unique across the module, since the ledger keeps them unique.
_REFUND_IDS = itertools.count(1)


def _login(client: Client, username: str, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name or f"{username.title()} Bramblequist"
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _day():
    return timezone.localdate() + timedelta(days=2)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _tool(**kwargs) -> Equipment:
    equipment = EquipmentFactory(**kwargs)
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _hourly(**kwargs) -> Equipment:
    return _tool(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, **kwargs)


def _donation(**kwargs) -> Equipment:
    return _tool(pricing=Equipment.Pricing.DONATION, **kwargs)


def _manage_login(client: Client, equipment: Equipment, username: str = "pricekeeper") -> Member:
    member = _login(client, username)
    EquipmentStaffMembershipFactory(equipment=equipment, member=member)
    return member


def _toast(response) -> tuple[str, str]:
    toast = json.loads(response["HX-Trigger"])["showToast"]
    return toast["message"], toast["type"]


def _messages(response) -> list[str]:
    return [str(message) for message in response.wsgi_request._messages]


def _schedule(client: Client, equipment: Equipment) -> str:
    return client.get(reverse("hub_equipment_schedule", args=[equipment.slug])).content.decode()


def _manage(client: Client, equipment: Equipment, tab: str) -> str:
    response = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab={tab}")
    assert response.status_code == 200
    return response.content.decode()


def _hold(equipment: Equipment, member: Member, **kwargs) -> EquipmentReservation:
    return EquipmentReservationFactory(
        equipment=equipment,
        member=member,
        starts_at=_at(_day(), 10),
        ends_at=_at(_day(), 11, 30),
        status=HOLD,
        stripe_session_id=kwargs.pop("stripe_session_id", "cs_view_1"),
        **kwargs,
    )


def _paid(equipment: Equipment, member: Member, *, status=EquipmentReservation.Status.CONFIRMED, hour: int = 13):
    return EquipmentReservationFactory(
        equipment=equipment,
        member=member,
        starts_at=_at(_day(), hour),
        ends_at=_at(_day(), hour + 1),
        status=status,
        amount_paid_cents=5000,
        stripe_payment_id="pi_view_1",
    )


def _retrieved(**overrides):
    session = {
        "id": "cs_view_1",
        "url": _SESSION["url"],
        "status": "open",
        "payment_status": "unpaid",
        "payment_intent": "",
        "amount_total": None,
    }
    session.update(overrides)
    return session


def _paid_session():
    return _retrieved(status="complete", payment_status="paid", payment_intent="pi_view_9", amount_total=3750)


def _reserve(client: Client, equipment: Equipment, *, htmx: bool = True, **data: str):
    payload = {"starts_at": _at(_day(), 14).isoformat(), "duration_minutes": "90", "purpose": "", **data}
    headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
    return client.post(reverse("hub_equipment_reserve", args=[equipment.slug]), payload, **headers)


def describe_the_pricing_card():
    def _settings(**overrides: str) -> dict[str, str]:
        return {
            "hours-TOTAL_FORMS": "0",
            "hours-INITIAL_FORMS": "0",
            "hours-MIN_NUM_FORMS": "0",
            "hours-MAX_NUM_FORMS": "1000",
            "reservations_open_shown": "1",
            "reservations_open": "on",
            "closed_message": "",
            "min_duration_minutes": "30",
            "max_duration_minutes": "240",
            "max_advance_days": "30",
            "max_active_reservations_per_member": "2",
            **overrides,
        }

    def _save(client: Client, equipment: Equipment, **overrides: str):
        return client.post(reverse("hub_equipment_hours_save", args=[equipment.slug]), _settings(**overrides))

    def it_comes_after_limits_with_the_one_save_at_its_bottom(client: Client):
        equipment = _tool(name="Corvidane Router")
        _manage_login(client, equipment)
        content = _manage(client, equipment, "hours")
        form = content[
            content.index('id="equip-hours-form"') : content.index("</form>", content.index('id="equip-hours-form"'))
        ]
        assert form.index(">Limits<") < form.index("data-pricing-card") < form.rindex('type="submit"')
        assert form.count('type="submit"') == 1
        assert "What members pay to reserve the Corvidane Router. They see it before they book." in form
        for line in (
            "Members reserve at no cost.",
            "Members choose what they pay for each reservation.",
            "A rate per hour, charged for the time booked.",
            "In dollars, from $1 to $500.",
        ):
            assert line in form
        assert 'data-pricing-option="free" checked' in form

    def it_saves_an_hourly_rate_and_shows_the_example(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        response = _save(client, equipment, pricing="hourly", hourly_rate="25")
        assert response.status_code == 302
        equipment.refresh_from_db()
        assert (equipment.pricing, equipment.hourly_rate_cents) == ("hourly", 2500)
        content = _manage(client, equipment, "hours")
        assert 'data-pricing-option="hourly" checked' in content
        assert 'value="25.00"' in content
        assert "A 1 hour 30 minute reservation costs" in content
        assert ">$37.50</strong>" in content

    def it_refuses_a_missing_out_of_range_or_part_cent_rate(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        for rate, message in (
            ("", "Enter an hourly rate, or pick Free."),
            ("0.50", "The hourly rate must be between $1 and $500."),
            ("500.01", "The hourly rate must be between $1 and $500."),
            ("25.505", "The hourly rate must be in whole cents."),
            ("123456789", "The hourly rate must be between $1 and $500."),
        ):
            response = _save(client, equipment, pricing="hourly", hourly_rate=rate)
            assert response.status_code == 200
            assert message in response.content.decode(), rate
        equipment.refresh_from_db()
        assert equipment.pricing == "free"

    def it_saves_donation_floors_with_the_orientation_rules(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        _save(client, equipment, pricing="donation", donation_minimum="", donation_suggested="10")
        equipment.refresh_from_db()
        assert (equipment.pricing, equipment.donation_minimum_cents, equipment.donation_suggested_cents) == (
            "donation",
            0,
            1000,
        )
        for minimum, suggested, message in (
            ("0.50", "", "Set the minimum to $0 or at least $1."),
            ("600", "", "Enter an amount up to $500."),
            ("5", "2", "Set the suggestion to at least $5, or leave it blank."),
            ("", "600", "Enter an amount up to $500."),
            ("abc", "", "Enter a number."),
        ):
            response = _save(
                client, equipment, pricing="donation", donation_minimum=minimum, donation_suggested=suggested
            )
            assert message in response.content.decode(), (minimum, suggested)

    def it_ignores_the_hidden_boxes_and_keeps_a_stored_rate_when_free_is_picked(client: Client):
        equipment = _tool(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500)
        _manage_login(client, equipment)
        response = _save(
            client, equipment, pricing="free", hourly_rate="abc", donation_minimum="x", donation_suggested="y"
        )
        assert response.status_code == 302
        equipment.refresh_from_db()
        assert (equipment.pricing, equipment.hourly_rate_cents) == ("free", 2500)

    def it_changes_nothing_when_the_page_had_no_pricing_card(client: Client):
        equipment = _tool(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500)
        _manage_login(client, equipment)
        response = _save(client, equipment, hourly_rate="nonsense")
        assert response.status_code == 302
        equipment.refresh_from_db()
        assert (equipment.pricing, equipment.hourly_rate_cents) == ("hourly", 2500)

    def it_keeps_the_picked_option_after_a_refused_save(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        content = _save(client, equipment, pricing="donation", donation_minimum="0.10").content.decode()
        assert 'data-pricing-option="donation" checked' in content
        assert 'data-pricing-option="free" checked' not in content

    def it_draws_no_example_for_a_rate_that_is_not_a_number():
        from hub.forms import EquipmentSettingsForm

        equipment = EquipmentFactory()
        for rate in ("abc", "NaN", "", "-5"):
            form = EquipmentSettingsForm({"pricing": "hourly", "hourly_rate": rate}, instance=equipment)
            assert form.hourly_example_total == "", rate
        form = EquipmentSettingsForm(
            instance=EquipmentFactory(pricing="donation", donation_minimum_cents=500, donation_suggested_cents=800)
        )
        assert (str(form["donation_minimum"].value()), str(form["donation_suggested"].value())) == ("5.00", "8.00")
        assert form.pricing_value == "donation"


def describe_the_member_schedule():
    def it_shows_the_hourly_chip_terms_total_and_button(client: Client):
        equipment = _hourly()
        _login(client, "hourly_member")
        content = _schedule(client, equipment)
        assert '<span class="pl-price-chip" data-price-chip>$25 per hour</span>' in content
        assert "You pay when you reserve. Cancel and you get an automatic full refund." in content
        assert "data-reserve-total" in content
        assert "Reserve and Pay</button>" in content
        assert "Reserve and pay?" in content
        assert "Continue to Payment" in content
        assert (
            "now through our secure checkout, and your reservation confirms when the payment goes through." in content
        )
        durations = json.loads(
            content.split('id="equip-durations-data" type="application/json">')[1].split("</script>")[0]
        )
        option = next(o for o in durations[_at(_day(), 14).isoformat()] if o["v"] == 90)
        assert option == {"v": 90, "label": "1.5 hours", "span": "2:00 PM to 3:30 PM", "total": "$37.50"}

    def it_asks_for_a_paid_request_on_hourly_equipment_that_needs_approval(client: Client):
        equipment = _hourly(requires_approval=True)
        _login(client, "hourly_request")
        content = _schedule(client, equipment)
        assert (
            "You pay when you reserve. A manager approves each reservation; if they decline, "
            "you get an automatic full refund."
        ) in content
        assert "Request this time?" in content
        assert "until they do, the time is held for you but not booked." in content
        assert "Continue to Payment" in content

    def it_shows_the_donation_chip_amount_box_and_plain_reserve(client: Client):
        equipment = _donation(donation_suggested_cents=1000)
        _login(client, "donor")
        content = _schedule(client, equipment)
        assert "Donation, $10 suggested" in content
        assert "data-reserve-total" not in content
        assert ">Reserve</button>" in content
        assert "Your amount ($)" in content
        assert "amount: '10'" in content
        assert "$0 reserves for free. Any other amount is at least $1, paid by card when you reserve." in content
        assert (
            "and your reservation confirms when it is paid. If you cancel, you get an automatic full refund." in content
        )
        assert 'name="amount"' in content

    def it_swaps_the_middle_sentence_for_a_donation_request(client: Client):
        equipment = _donation(requires_approval=True)
        _login(client, "donor_request")
        content = _schedule(client, equipment)
        assert "Pay what you can" in content
        assert "Any amount above $0 goes through our secure checkout. Then a manager approves" in content

    def it_keeps_free_equipment_as_it_was(client: Client):
        equipment = _tool()
        _login(client, "free_member")
        content = _schedule(client, equipment)
        assert "data-price-chip" not in content
        assert "pl-equip-book__terms" not in content
        assert "data-reserve-total" not in content
        assert "Reserve this time?" in content
        assert "Your reservation confirms right away." in content

    def it_adds_the_summary_line_to_the_free_request(client: Client):
        equipment = _tool(requires_approval=True, name="Larkspindle Loom")
        _login(client, "free_request")
        content = _schedule(client, equipment)
        assert "data-confirm-summary" in content
        assert "A manager approves each reservation on the Larkspindle Loom." in content

    def it_tells_a_manager_the_instant_terms_on_their_own_equipment(client: Client):
        equipment = _hourly(requires_approval=True)
        _manage_login(client, equipment, "hourly_mgr")
        content = _schedule(client, equipment)
        assert "You pay when you reserve. Cancel and you get an automatic full refund." in content
        assert "Reserve and pay?" in content

    def it_puts_the_chip_on_the_reservations_card(client: Client):
        _hourly(name="Ottercomb Planer")
        _login(client, "card_viewer")
        content = client.get(reverse("hub_equipment_index")).content.decode()
        card = content[content.index("Ottercomb Planer") :]
        assert "$25 per hour" in card[: card.index("pl-equip-card__state") if "pl-equip-card__state" in card else 2000]

    def it_shows_a_hold_on_the_timeline_as_held(client: Client):
        equipment = _hourly()
        _hold(equipment, MemberFactory(full_legal_name="Wexley Pondmarsh"))
        _login(client, "timeline_viewer")
        content = _schedule(client, equipment)
        assert '<span class="pl-equip-slot__label">Held</span>' in content
        assert "Wexley Pondmarsh" in content  # the upcoming list names who holds it, marked Held
        assert "· Held</span>" in content


def describe_reserving_priced_equipment():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_holds_the_time_and_redirects_htmx_to_stripe(mock_create, client: Client):
        equipment = _hourly()
        member = _login(client, "payer")
        response = _reserve(client, equipment)
        assert response["HX-Redirect"] == _SESSION["url"]
        hold = EquipmentReservation.objects.get(member=member)
        assert hold.status == HOLD
        assert mock_create.call_args.kwargs["amount_cents"] == 3750
        assert "Awaiting payment" in response.content.decode()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_redirects_a_plain_post_straight_to_stripe(mock_create, client: Client):
        equipment = _hourly()
        _login(client, "plainpayer")
        response = _reserve(client, equipment, htmx=False)
        assert (response.status_code, response["Location"]) == (302, _SESSION["url"])

    @patch("billing.stripe_utils.create_checkout_session")
    def it_reserves_a_zero_donation_for_free_with_no_stripe_round_trip(mock_create, client: Client):
        equipment = _donation()
        member = _login(client, "zerodonor")
        response = _reserve(client, equipment, amount="0")
        mock_create.assert_not_called()
        assert _toast(response)[0].startswith("Reserved. See you ")
        assert EquipmentReservation.objects.get(member=member).status == EquipmentReservation.Status.CONFIRMED

    @patch("billing.stripe_utils.create_checkout_session")
    def it_refuses_fifty_cents(mock_create, client: Client):
        equipment = _donation()
        _login(client, "halfdonor")
        response = _reserve(client, equipment, amount="0.50")
        assert _toast(response) == ("Enter an amount from $1 to $500, or $0 to reserve for free.", "error")
        mock_create.assert_not_called()
        assert not EquipmentReservation.objects.exists()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_charges_ten_dollars_for_ten(mock_create, client: Client):
        equipment = _donation()
        _login(client, "tendonor")
        response = _reserve(client, equipment, amount="$10")
        assert response["HX-Redirect"] == _SESSION["url"]
        assert mock_create.call_args.kwargs["amount_cents"] == 1000

    @patch("billing.stripe_utils.create_checkout_session")
    def it_refuses_an_amount_that_is_not_dollars(mock_create, client: Client):
        equipment = _donation()
        _login(client, "oddDonor")
        for amount in ("ten", "10.005", "Infinity"):
            response = _reserve(client, equipment, amount=amount)
            assert _toast(response) == ("Enter the amount in dollars, like 10 or 12.50.", "error"), amount
        assert _toast(_reserve(client, equipment))[0] == "Enter what you'd like to pay. $0 is fine."

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_says_so_when_checkout_cannot_start(mock_create, client: Client):
        equipment = _hourly()
        _login(client, "downpayer")
        response = _reserve(client, equipment)
        assert _toast(response) == ("We couldn't start the payment checkout. Please try again in a minute.", "error")
        assert not EquipmentReservation.objects.exists()


def describe_the_checkout_pages():
    def _return_url(reservation: EquipmentReservation, slug: str | None = None) -> str:
        token = equipment_service.make_checkout_token(reservation)
        return reverse("hub_equipment_checkout_return", args=[slug or reservation.equipment.slug, token])

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_paid_session())
    def it_lands_a_paid_hold_as_confirmed(mock_retrieve, client: Client):
        member = _login(client, "lander")
        hold = _hold(_hourly(name="Pemberwick Lathe"), member)
        content = client.get(_return_url(hold)).content.decode()
        assert 'data-checkout-state="confirmed"' in content
        assert "You paid $37.50. Your reservation is confirmed." in content
        assert "Back to the Pemberwick Lathe" in content
        hold.refresh_from_db()
        assert hold.status == EquipmentReservation.Status.CONFIRMED

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_paid_session())
    def it_lands_a_paid_request_as_sent(mock_retrieve, client: Client):
        member = _login(client, "request_lander")
        hold = _hold(_hourly(requires_approval=True), member)
        content = client.get(_return_url(hold)).content.decode()
        assert 'data-checkout-state="pending_approval"' in content
        assert "If they decline, your $37.50 comes back automatically." in content

    def it_describes_a_reservation_that_moved_on(client: Client):
        member = _login(client, "late_lander")
        row = _paid(_hourly(), member, status=EquipmentReservation.Status.DECLINED)
        content = client.get(_return_url(row)).content.decode()
        assert "This reservation was declined. Your $50.00 comes back automatically as a full refund." in content

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved())
    def it_polls_while_the_payment_is_finalizing_and_then_settles(mock_retrieve, client: Client):
        member = _login(client, "poller")
        hold = _hold(_hourly(), member)
        url = _return_url(hold)
        assert 'data-checkout-state="pending"' in client.get(url).content.decode()
        fragment = client.get(f"{url}?n=20", HTTP_HX_REQUEST="true")
        body = fragment.content.decode()
        assert 'data-checkout-state="still_processing"' in body
        assert "<html" not in body
        assert 'data-checkout-state="pending"' in client.get(f"{url}?n=junk").content.decode()

    def it_refuses_a_bad_token_or_another_items_link(client: Client):
        member = _login(client, "badtoken")
        hold = _hold(_hourly(), member)
        other = _hourly(name="Other Bench")
        bad = client.get(reverse("hub_equipment_checkout_return", args=[hold.equipment.slug, "nope"]))
        assert bad.status_code == 400
        assert "We Couldn't Find That Reservation" in bad.content.decode()
        assert client.get(_return_url(hold, slug=other.slug)).status_code == 400

    def describe_cancelled():
        def _url(reservation: EquipmentReservation) -> str:
            token = equipment_service.make_checkout_token(reservation)
            return reverse("hub_equipment_checkout_cancelled", args=[reservation.equipment.slug, token])

        def it_asks_on_get_and_never_releases_on_a_prefetch(client: Client):
            member = _login(client, "canceller")
            hold = _hold(_hourly(), member)
            content = client.get(_url(hold)).content.decode()
            assert "Cancel This Reservation?" in content
            assert "If you haven't paid, nothing is charged." in content
            assert EquipmentReservation.objects.filter(pk=hold.pk).exists()

        @patch("billing.stripe_utils.expire_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved())
        def it_releases_on_post_after_asking_stripe(mock_retrieve, mock_expire, client: Client):
            member = _login(client, "releaser")
            hold = _hold(_hourly(), member)
            response = client.post(_url(hold))
            assert response["Location"] == reverse("hub_equipment_detail", args=[hold.equipment.slug])
            assert _messages(response) == ["Cancelled. You were not charged."]
            assert not EquipmentReservation.objects.filter(pk=hold.pk).exists()

        def it_sends_a_settled_row_or_a_bad_link_away(client: Client):
            member = _login(client, "settled")
            row = _paid(_hourly(), member)
            assert client.get(_url(row))["Location"] == reverse("hub_equipment_detail", args=[row.equipment.slug])
            bad = client.get(reverse("hub_equipment_checkout_cancelled", args=[row.equipment.slug, "nope"]))
            assert bad["Location"] == reverse("hub_equipment_index")

    def describe_pay_now():
        def _pay(client: Client, reservation: EquipmentReservation):
            return client.post(
                reverse("hub_equipment_reservation_pay", args=[reservation.equipment.slug, reservation.pk])
            )

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved())
        def it_reopens_the_same_checkout(mock_retrieve, client: Client):
            member = _login(client, "resumer")
            response = _pay(client, _hold(_hourly(), member))
            assert response["Location"] == _SESSION["url"]

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_paid_session())
        def it_lands_an_already_paid_hold_on_the_return_page(mock_retrieve, client: Client):
            member = _login(client, "alreadypaid")
            hold = _hold(_hourly(), member)
            response = _pay(client, hold)
            assert f"/equipment/{hold.equipment.slug}/checkout/" in response["Location"]

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
        def it_releases_an_expired_checkout(mock_retrieve, client: Client):
            member = _login(client, "expiredpayer")
            hold = _hold(_hourly(), member)
            response = _pay(client, hold)
            assert _messages(response) == [
                "That checkout expired, so the time was released. Pick a time to start again."
            ]

        @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("down"))
        def it_says_so_when_stripe_is_down(mock_retrieve, client: Client):
            member = _login(client, "downresumer")
            response = _pay(client, _hold(_hourly(), member))
            assert _messages(response) == ["We couldn't check your payment just now. Try again in a minute."]

        def it_is_only_for_the_members_own_unpaid_hold(client: Client):
            _login(client, "stranger")
            other = _hold(_hourly(), MemberFactory())
            assert _pay(client, other).status_code == 403
            member = _login(client, "paidalready")
            row = _paid(_hourly(name="Settled Saw"), member)
            assert _pay(client, row)["Location"] == reverse("hub_equipment_detail", args=[row.equipment.slug])


def describe_cancelling_an_unpaid_hold():
    def _cancel(client: Client, reservation: EquipmentReservation, **data: str):
        url = reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk])
        return client.post(url, data, HTTP_HX_REQUEST="true")

    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved())
    def it_releases_it_from_the_schedule(mock_retrieve, mock_expire, client: Client):
        member = _login(client, "holdcanceller")
        hold = _hold(_hourly(), member)
        response = _cancel(client, hold)
        assert _toast(response) == ("Cancelled. You were not charged.", "success")
        assert not EquipmentReservation.objects.filter(pk=hold.pk).exists()

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_paid_session())
    def it_keeps_a_hold_that_turns_out_to_be_paid(mock_retrieve, client: Client):
        member = _login(client, "surprisepaid")
        hold = _hold(_hourly(), member)
        message, level = _toast(_cancel(client, hold))
        assert level == "info"
        assert message.startswith("Your payment already went through")
        hold.refresh_from_db()
        assert hold.status == EquipmentReservation.Status.CONFIRMED

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("down"))
    def it_lands_on_the_bookings_tab_with_a_next(mock_retrieve, client: Client):
        member = _login(client, "tabcanceller")
        hold = _hold(_hourly(), member)
        response = _cancel(client, hold, next="/equipment/?view=bookings")
        assert response["Location"] == "/equipment/?view=bookings"
        assert _messages(response) == ["We couldn't check your payment just now. Try again in a minute."]


def describe_refund_wording():
    def _refund_ok():
        ids = _REFUND_IDS
        return patch(
            "billing.stripe_utils.create_refund",
            side_effect=lambda **kwargs: {"id": f"re_v{next(ids)}", "status": "succeeded", "amount": 5000},
        )

    def _refund_refused():
        return patch("billing.stripe_utils.create_refund", side_effect=stripe.StripeError("The charge is disputed."))

    def it_says_the_paid_request_is_refunded_in_the_decline_modal(client: Client):
        equipment = _hourly(requires_approval=True)
        _manage_login(client, equipment)
        _paid(equipment, MemberFactory(full_legal_name="Osgood Fennimore"), status=WAITING)
        content = _manage(client, equipment, "reservations")
        assert "Declining frees the time and tells them why. Their $50.00 is refunded in full." in content

    def it_says_so_in_the_manager_cancel_modal_and_lists_a_hold_with_no_cancel(client: Client):
        equipment = _hourly()
        _manage_login(client, equipment)
        paid = _paid(equipment, MemberFactory(full_legal_name="Paidra Collins"))
        hold = _hold(equipment, MemberFactory(full_legal_name="Holdon Tight"))
        content = _manage(client, equipment, "reservations")
        assert "Cancelling frees it and tells them why. Their $50.00 is refunded in full." in content
        assert f'data-awaiting-payment-row="{hold.pk}"' in content
        assert "Released if unpaid by" in content
        assert f"mgr-cancel-{hold.pk}" not in content
        assert f"mgr-cancel-{paid.pk}" in content
        assert "Paid $50.00" in content

    def it_toasts_a_refunded_decline(client: Client):
        equipment = _hourly(requires_approval=True)
        _manage_login(client, equipment)
        row = _paid(equipment, MemberFactory(full_legal_name="Imogen Thatchley"), status=WAITING)
        with _refund_ok():
            response = client.post(
                reverse("hub_equipment_reservation_decline", args=[equipment.slug, row.pk]), {"reason": "Booked"}
            )
        assert _messages(response) == ["Declined. Imogen has been emailed and refunded."]

    def it_flags_a_decline_whose_refund_failed(client: Client):
        equipment = _hourly(requires_approval=True)
        _manage_login(client, equipment)
        row = _paid(equipment, MemberFactory(full_legal_name="Imogen Thatchley"), status=WAITING)
        with _refund_refused():
            response = client.post(
                reverse("hub_equipment_reservation_decline", args=[equipment.slug, row.pk]), {"reason": "Booked"}
            )
        assert _messages(response) == [
            "Declined. Imogen has been emailed. The refund didn't go through; it is flagged for the Billing Administrators."
        ]
        row.refresh_from_db()
        assert row.status == EquipmentReservation.Status.DECLINED

    def it_says_a_manager_cancel_refunded_the_member(client: Client):
        equipment = _hourly()
        manager = _manage_login(client, equipment)
        url = lambda row: reverse("hub_equipment_reservation_cancel", args=[equipment.slug, row.pk])  # noqa: E731
        row = _paid(equipment, MemberFactory())
        with _refund_ok():
            assert _messages(client.post(url(row), {"reason": "Broken"})) == [
                "Reservation cancelled. The member has been told and refunded."
            ]
        failed = _paid(equipment, MemberFactory(), hour=15)
        with _refund_refused():
            assert _messages(client.post(url(failed), {"reason": "Broken"}))[-1:] == [
                "Reservation cancelled. The member has been told. The refund didn't go through; "
                "it is flagged for the Billing Administrators."
            ]
        own = _paid(equipment, manager, hour=11)
        with _refund_ok():
            assert _messages(client.post(url(own), {"reason": "Mine"}))[-1:] == [
                "Reservation cancelled. Your payment is being refunded."
            ]
        own_failed = _paid(equipment, manager, hour=9)
        with _refund_refused():
            assert _messages(client.post(url(own_failed), {"reason": "Mine"}))[-1:] == [
                "Reservation cancelled. The refund didn't go through; it is flagged for the Billing Administrators."
            ]

    def it_toasts_a_members_own_refund(client: Client):
        equipment = _hourly()
        member = _login(client, "selfrefund")
        row = _paid(equipment, member)
        url = reverse("hub_equipment_reservation_cancel", args=[equipment.slug, row.pk])
        with _refund_ok():
            response = client.post(url, HTTP_HX_REQUEST="true")
        assert _toast(response) == ("Reservation cancelled. Your $50.00 is being refunded to your card.", "success")
        failed = _paid(equipment, member, hour=15)
        url = reverse("hub_equipment_reservation_cancel", args=[equipment.slug, failed.pk])
        with _refund_refused():
            response = client.post(url, {"next": "/equipment/?view=bookings"})
        assert _messages(response) == ["Reservation cancelled. Your $50.00 refund is being processed."]


def describe_the_members_rows():
    def it_reads_each_paid_state(client: Client):
        equipment = _hourly()
        member = _login(client, "rowreader")
        _hold(equipment, member)
        _paid(equipment, member, status=WAITING, hour=13)
        confirmed = _paid(equipment, member, hour=15)
        declined = _paid(equipment, member, status=EquipmentReservation.Status.DECLINED, hour=16)
        declined.cancelled_by = MemberFactory(full_legal_name="Sami Brackenridge")
        declined.cancelled_reason = "The spindle is out for repair that week."
        declined.save()
        PaymentRefund.objects.create(reservation=declined, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
        content = _schedule(client, equipment)
        assert "Awaiting payment</span> Your payment hasn't come through. We hold the time for 2 hours." in content
        assert "Pay now" in content
        assert "If you haven't paid, nothing is charged." in content
        assert "Paid $50.00. A manager will approve or decline it. Automatic full refund if it's declined." in content
        assert "Confirmed</span> Paid $50.00" in content
        assert "Sami Brackenridge: The spindle is out for repair that week. Your $50.00 was refunded." in content
        assert "You&#x27;ll get an automatic full refund." in content  # composed with |add:, so escaped
        assert f"cancel-my-res-{confirmed.pk}" in content

    def it_notes_the_refund_on_a_manager_cancelled_row(client: Client):
        equipment = _hourly()
        member = _login(client, "mgrcancelled")
        row = _paid(equipment, member)
        row.status = EquipmentReservation.Status.CANCELLED
        row.cancelled_by = MemberFactory()
        row.cancelled_reason = "Spindle broke."
        row.save()
        PaymentRefund.objects.create(reservation=row, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
        assert "Cancelled by the manager: Spindle broke. Your $50.00 was refunded." in _schedule(client, equipment)


def describe_the_bookings_tab():
    def it_adds_paid_and_refunded_pills(client: Client):
        equipment = _hourly()
        member = _login(client, "pillreader")
        paid = _paid(equipment, member)
        refunded = _paid(equipment, member, status=EquipmentReservation.Status.CANCELLED, hour=15)
        PaymentRefund.objects.create(reservation=refunded, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
        _hold(equipment, member)
        content = client.get(f"{reverse('hub_equipment_bookings')}?show=all").content.decode()
        assert content.count("data-booking-paid>Paid $50.00</span>") == 2
        assert content.count("data-booking-refunded>Refunded</span>") == 1
        assert 'hub-pill--warn" data-reservation-status="pending_payment">Awaiting payment' in content
        assert paid.pk
