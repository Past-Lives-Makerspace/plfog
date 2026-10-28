"""BDD specs for renting a cubby from the guild page (#978): hub/cubby_rental_views.py and what the tab draws.

The Rent This Cubby sheet and its refusals; Continue to Payment going to Stripe (HX-Redirect) or
landing back on the tab with a toast; Stripe's return and back pages; the Your Cubby card's Change
Amount, Cancel Rental, Keep My Cubby and Manage Payment, each refused with a 404 to anyone but the
renter or an admin; the card's states (active, cancelling, past due), the full card, the tab with
the switch off, Home's line, and staff refused archiving or closing a rented cubby. Stripe is
patched at ``billing.stripe_utils``. Assertions anchor on markup and factory strings (STANDARDS.md 8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership import cubby_rentals
from membership.models import Cubby, CubbyRental, Member
from tests.billing.factories import TabFactory
from tests.membership.factories import (
    CubbyFactory,
    CubbyRentalFactory,
    GuildFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db

Status = CubbyRental.Status
_HX = {"HTTP_HX_REQUEST": "true"}
_SESSION = {"id": "cs_view", "url": "https://checkout.stripe.example/cs_view"}
_PERIOD_END = datetime(2026, 11, 27, 18, tzinfo=UTC)


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Loamhurst"
    member.save(update_fields=["fog_role", "status", "full_legal_name"])
    member.sync_user_permissions()
    TabFactory(member=member, stripe_customer_id=f"cus_{username}")
    client.login(username=username, password="pass")
    return member


def _cubby(label: str = "B3", *, show: bool = True, payee: Member | None = None, **kwargs: Any) -> Cubby:
    guild = GuildFactory(name=f"Mossbank Guild {label}", show_cubbies_tab=show, cubby_payee=payee)
    unit = ShelfUnitFactory(guild=guild, name="Glaze Wall", rows=2, columns=3)
    kwargs.setdefault("floor_cents", 1500)
    kwargs.setdefault("suggested_cents", 2000)
    return CubbyFactory(unit=unit, label=label, **kwargs)


def _rental(member: Member, cubby: Cubby | None = None, **kwargs: Any) -> CubbyRental:
    kwargs.setdefault("current_period_end", _PERIOD_END)
    return CubbyRentalFactory(member=member, cubby=cubby or _cubby(), **kwargs)


def _messages(response: Any) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _tab(client: Client, guild: Any) -> str:
    response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
    assert response.status_code == 200
    return response.content.decode()


def describe_the_rent_sheet():
    def it_offers_the_pay_what_you_want_chips_and_the_floor(client: Client):
        _login(client, "sheet_open")
        cubby = _cubby(size="12 x 12 x 16 in", note="By the glaze sink")
        content = client.get(reverse("hub_cubby_rent_sheet", args=[cubby.pk])).content.decode()
        assert 'data-cubby-rent-sheet="B3"' in content
        assert "The guild asks at least $15 a month. Pick what feels right for you." in content
        for value, sub in (("$15", "Minimum"), ("$20", "Suggested"), ("$25", "Extra Help"), ("Other", "You Choose")):
            assert (
                f'<span class="pl-cubby-amount__value">{value}</span><span class="pl-cubby-amount__sub">{sub}</span>'
                in content
            )
        assert 'value="suggested" x-model="picked" @change="touched = true" checked' in content
        assert f'hx-post="{reverse("hub_cubby_rent", args=[cubby.pk])}"' in content
        assert f"charged today and on the {cubby_rentals.ordinal(timezone.localdate().day)} each month" in content
        assert "Continue to Payment" in content
        assert "B3 is held for you while you pay." in content
        assert "pl-cubby-why" not in content  # nobody is paid for this guild's cubbies

    def it_says_the_guild_gets_most_of_it_when_the_guild_has_a_payee(client: Client):
        payee = _login(client, "sheet_payee")
        cubby = _cubby(payee=payee)
        content = client.get(reverse("hub_cubby_rent_sheet", args=[cubby.pk])).content.decode()
        assert f"Most of what you pay goes to the {cubby.unit.guild.name}." in content

    def it_says_why_when_the_cubby_cannot_be_offered(client: Client):
        member = _login(client, "sheet_taken")
        taken = _cubby("T1")
        CubbyRentalFactory(cubby=taken)
        mine = _cubby("M1")
        CubbyRentalFactory(cubby=mine, member=member)
        closed = _cubby("X1", out_of_service=True)
        for cubby, why in (
            (taken, "Someone just rented T1. Pick another open cubby."),
            (mine, "M1 is already yours."),
            (closed, "X1 is out of service right now."),
        ):
            content = client.get(reverse("hub_cubby_rent_sheet", args=[cubby.pk])).content.decode()
            assert f"data-cubby-unavailable>{why}</p>" in content
            assert "Continue to Payment" not in content

    def it_is_a_404_for_an_archived_cubby(client: Client):
        _login(client, "sheet_archived")
        cubby = _cubby(archived_at=timezone.now())
        assert client.get(reverse("hub_cubby_rent_sheet", args=[cubby.pk])).status_code == 404

    def it_is_a_404_without_a_member(client: Client):
        _login(client, "sheet_nomember")
        with patch("hub.cubby_rental_views._get_member", return_value=None):
            assert client.get(reverse("hub_cubby_rent_sheet", args=[_cubby().pk])).status_code == 404


def describe_renting():
    @patch("billing.stripe_utils.create_subscription_checkout_session", return_value=_SESSION)
    def it_holds_the_cubby_and_sends_htmx_to_stripe(mock_create, client: Client):
        member = _login(client, "rent_hx")
        cubby = _cubby()
        response = client.post(reverse("hub_cubby_rent", args=[cubby.pk]), {"choice": "extra"}, **_HX)
        assert response.status_code == 204
        assert response["HX-Redirect"] == _SESSION["url"]
        hold = CubbyRental.objects.get(member=member)
        assert (hold.status, hold.amount_cents) == (Status.PENDING_PAYMENT, 2500)

    @patch("billing.stripe_utils.create_subscription_checkout_session", return_value=_SESSION)
    def it_redirects_a_plain_post_and_takes_an_other_amount(mock_create, client: Client):
        _login(client, "rent_plain")
        cubby = _cubby()
        response = client.post(reverse("hub_cubby_rent", args=[cubby.pk]), {"choice": "other", "other": "22.50"})
        assert response.status_code == 302
        assert response["Location"] == _SESSION["url"]
        assert mock_create.call_args.kwargs["amount_cents"] == 2250

    @patch("billing.stripe_utils.create_subscription_checkout_session")
    def it_puts_a_below_floor_amount_back_on_the_sheet(mock_create, client: Client):
        _login(client, "rent_low")
        cubby = _cubby("A4")
        response = client.post(reverse("hub_cubby_rent", args=[cubby.pk]), {"choice": "other", "other": "8"}, **_HX)
        content = response.content.decode()
        assert response.status_code == 200
        assert "The lowest you can pay for A4 is $15 a month. Pick $15 or more." in content
        assert 'value="other" x-model="picked" @change="touched = true" checked' in content
        mock_create.assert_not_called()
        assert not CubbyRental.objects.exists()

    def it_puts_a_floor_raised_meanwhile_on_the_sheet(client: Client):
        _login(client, "rent_raced_floor")
        cubby = _cubby()
        with patch.object(cubby_rentals, "start_rental", side_effect=cubby_rentals.AmountError("Raised meanwhile.")):
            response = client.post(reverse("hub_cubby_rent", args=[cubby.pk]), {"choice": "floor"}, **_HX)
        assert "Raised meanwhile." in response.content.decode()

    def it_sends_the_member_back_to_the_tab_with_a_toast_when_someone_just_rented_it(client: Client):
        _login(client, "rent_taken")
        cubby = _cubby("A3")
        CubbyRentalFactory(cubby=cubby)
        response = client.post(reverse("hub_cubby_rent", args=[cubby.pk]), {"choice": "floor"}, **_HX)
        assert response.status_code == 204
        assert response["HX-Redirect"] == f"{reverse('hub_guild_detail', args=[cubby.unit.guild.slug])}?tab=cubbies"
        assert _messages(response) == ["Someone just rented A3. Pick another open cubby."]

    def it_only_takes_a_post(client: Client):
        _login(client, "rent_get")
        assert client.get(reverse("hub_cubby_rent", args=[_cubby().pk])).status_code == 405


def describe_stripes_pages():
    def _token(rental: CubbyRental) -> str:
        return cubby_rentals.make_checkout_token(rental)

    @pytest.mark.parametrize(
        ("outcome", "message"),
        [
            ("active", "B3 is yours. We emailed you the details."),
            ("pending", "Your payment is finishing up. B3 will show as yours in a minute."),
            ("gone", "That checkout ended before it was paid. Pick an open cubby to try again."),
        ],
    )
    def it_lands_the_return_on_the_tab_with_what_happened(client: Client, outcome: str, message: str):
        member = _login(client, f"return_{outcome}")
        hold = _rental(member, status=Status.PENDING_PAYMENT)
        with patch.object(cubby_rentals, "finish_checkout", return_value=outcome):
            response = client.get(reverse("hub_cubby_checkout_return", args=[_token(hold)]))
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=cubbies")
        assert _messages(response) == [message]

    def it_says_gone_when_the_hold_was_already_released(client: Client):
        member = _login(client, "return_released")
        hold = _rental(member, status=Status.PENDING_PAYMENT)
        token = _token(hold)
        hold.delete()
        response = client.get(reverse("hub_cubby_checkout_return", args=[token]))
        assert _messages(response) == ["That checkout ended before it was paid. Pick an open cubby to try again."]

    def it_is_a_404_for_a_bad_token_or_someone_elses_rental(client: Client):
        _login(client, "return_other")
        other = CubbyRentalFactory(cubby=_cubby())
        assert client.get(reverse("hub_cubby_checkout_return", args=["nonsense"])).status_code == 404
        assert client.get(reverse("hub_cubby_checkout_return", args=[_token(other)])).status_code == 404
        assert client.get(reverse("hub_cubby_checkout_cancelled", args=[_token(other)])).status_code == 404

    @pytest.mark.parametrize(
        ("outcome", "messages"),
        [
            ("released", []),
            ("paid", ["Your payment went through, so B3 is yours."]),
            ("unknown", ["We couldn't reach Stripe. B3 is held for you for up to an hour."]),
        ],
    )
    def it_frees_the_cubby_on_the_back_link_unless_stripe_says_it_was_paid(
        client: Client, outcome: str, messages: list[str]
    ):
        member = _login(client, f"back_{outcome}")
        hold = _rental(member, status=Status.PENDING_PAYMENT)
        with patch.object(cubby_rentals, "release_hold_if_unpaid", return_value=outcome) as release:
            response = client.get(reverse("hub_cubby_checkout_cancelled", args=[_token(hold)]))
        release.assert_called_once()
        assert response["Location"].endswith("?tab=cubbies")
        assert _messages(response) == messages

    def it_lands_on_the_tab_when_the_back_link_finds_the_hold_gone(client: Client):
        member = _login(client, "back_gone")
        hold = _rental(member, status=Status.PENDING_PAYMENT)
        token = _token(hold)
        hold.delete()
        response = client.get(reverse("hub_cubby_checkout_cancelled", args=[token]))
        assert response.status_code == 302 and _messages(response) == []


def describe_your_cubby_buttons():
    def describe_change_amount():
        def it_opens_on_what_the_member_pays_now_with_the_next_charge(client: Client):
            member = _login(client, "change_sheet")
            rental = _rental(member, amount_cents=2200)
            content = client.get(reverse("hub_cubby_rental_amount", args=[rental.pk])).content.decode()
            assert "You pay $22 a month for B3 now. The guild asks at least $15." in content
            assert (
                '<span class="pl-cubby-amount__value">$22</span><span class="pl-cubby-amount__sub">Current</span>'
                in content
            )
            assert 'value="current" x-model="picked" @change="touched = true" checked' in content
            assert "Starting <strong>Fri, Nov 27</strong>, you'll pay" in content
            assert 'id="change-' in content or "x-model" in content

        @patch("billing.stripe_utils.update_subscription_amount")
        def it_saves_and_says_when_the_new_amount_starts(mock_update, client: Client):
            member = _login(client, "change_save")
            rental = _rental(member)
            response = client.post(reverse("hub_cubby_rental_amount", args=[rental.pk]), {"choice": "extra"}, **_HX)
            assert response.status_code == 204
            assert _messages(response) == ["New amount starts Fri, Nov 27."]
            rental.refresh_from_db()
            assert rental.amount_cents == 2500

        @patch("billing.stripe_utils.update_subscription_amount")
        def it_changes_nothing_for_the_current_amount(mock_update, client: Client):
            member = _login(client, "change_same")
            rental = _rental(member)
            response = client.post(reverse("hub_cubby_rental_amount", args=[rental.pk]), {"choice": "current"})
            assert _messages(response) == ["You still pay $22 a month."]
            mock_update.assert_not_called()

        def it_puts_a_refusal_back_on_the_sheet(client: Client):
            member = _login(client, "change_bad")
            rental = _rental(member)
            response = client.post(reverse("hub_cubby_rental_amount", args=[rental.pk]), {"choice": "nope"})
            assert response.status_code == 200
            assert "data-cubby-change-form" in response.content.decode()
            with patch.object(
                cubby_rentals, "change_amount", side_effect=cubby_rentals.AmountError("Raised meanwhile.")
            ):
                response = client.post(reverse("hub_cubby_rental_amount", args=[rental.pk]), {"choice": "floor"})
            assert "Raised meanwhile." in response.content.decode()

        def it_lands_on_the_tab_when_the_rental_is_not_billing(client: Client):
            member = _login(client, "change_ended")
            rental = _rental(member, status=Status.ENDED)
            response = client.post(reverse("hub_cubby_rental_amount", args=[rental.pk]), {"choice": "extra"})
            assert response.status_code == 302
            assert _messages(response) == ["This rental isn't active any more."]

    @patch("billing.stripe_utils.set_subscription_cancel_at_period_end")
    def it_cancels_and_keeps(mock_set, client: Client):
        member = _login(client, "cancel_keep")
        rental = _rental(member)
        response = client.post(reverse("hub_cubby_rental_cancel", args=[rental.pk]))
        assert _messages(response) == ["B3 stays yours until Fri, Nov 27. You won't be charged again."]
        response = client.post(reverse("hub_cubby_rental_keep", args=[rental.pk]), **_HX)
        assert response.status_code == 204
        assert _messages(response)[-1] == "You're keeping B3."

    def it_refuses_cancelling_a_rental_that_ended(client: Client):
        member = _login(client, "cancel_ended")
        rental = _rental(member, status=Status.ENDED)
        response = client.post(reverse("hub_cubby_rental_cancel", args=[rental.pk]))
        assert _messages(response) == ["This rental isn't active any more."]

    def describe_manage_payment():
        @patch(
            "billing.stripe_utils.create_payment_method_portal_session", return_value="https://billing.stripe.example/p"
        )
        def it_sends_the_member_to_the_stripe_card_page(mock_portal, client: Client):
            member = _login(client, "portal_go")
            rental = _rental(member)
            response = client.get(reverse("hub_cubby_rental_portal", args=[rental.pk]))
            assert response.status_code == 302
            assert response["Location"] == "https://billing.stripe.example/p"

        def it_lands_on_the_tab_for_an_ended_rental_or_when_stripe_fails(client: Client):
            member = _login(client, "portal_fail")
            ended = _rental(member, status=Status.ENDED)
            response = client.get(reverse("hub_cubby_rental_portal", args=[ended.pk]))
            assert _messages(response) == ["This rental isn't active any more."]
            live = _rental(member, cubby=_cubby("P2"))
            with patch(
                "billing.stripe_utils.create_payment_method_portal_session",
                side_effect=stripe.APIConnectionError("down"),
            ):
                response = client.get(reverse("hub_cubby_rental_portal", args=[live.pk]))
            assert response.status_code == 302
            assert _messages(response)[-1] == "We couldn't open Stripe right now. Try again in a minute."

    @pytest.mark.parametrize("name", ["amount", "cancel", "keep", "portal"])
    def it_is_a_404_for_another_members_crafted_request(client: Client, name: str):
        _login(client, f"crafted_{name}")
        other = _rental(CubbyRentalFactory().member)
        url = reverse(f"hub_cubby_rental_{name}", args=[other.pk])
        response = client.get(url) if name == "portal" else client.post(url, {"choice": "extra"})
        assert response.status_code == 404

    @patch("billing.stripe_utils.set_subscription_cancel_at_period_end")
    def it_lets_an_admin_act_on_any_rental(mock_set, client: Client):
        _login(client, "rental_admin", fog_role=Member.FogRole.ADMIN)
        other = _rental(CubbyRentalFactory().member)
        response = client.post(reverse("hub_cubby_rental_cancel", args=[other.pk]))
        assert response.status_code == 302
        mock_set.assert_called_once_with(subscription_id=other.stripe_subscription_id, cancel=True)


def describe_the_tab_with_rentals():
    def it_puts_your_cubby_first_with_the_amount_the_next_charge_and_the_thanks(client: Client):
        member = _login(client, "tab_mine")
        payee = CubbyRentalFactory().member
        rental = _rental(member, cubby=_cubby(payee=payee), amount_cents=2200)
        CubbyFactory(unit=rental.cubby.unit, label="B4", column=2)
        content = _tab(client, rental.cubby.unit.guild)
        assert content.index('data-cubby-mine="B3"') < content.index('id="cubbies-title"')
        assert "B3 on the Glaze Wall" in content
        assert "<dt>You Pay</dt><dd>$22 a month</dd>" in content
        assert "<dt>Next Charge</dt><dd>Fri, Nov 27</dd>" in content
        assert "You pay $7 more than the minimum. Thank you, that helps the guild keep the studio stocked." in content
        assert 'aria-label="B3, your cubby"' in content
        assert "pl-cubby-chip--active" in content
        assert reverse("hub_cubby_rental_amount", args=[rental.pk]) in content
        assert (
            f'href="{reverse("hub_cubby_rental_portal", args=[rental.pk])}" class="pl-btn pl-btn--ghost" hx-boost="false"'
            in content
        )
        assert rental.cancel_message in content.replace("&#x27;", "'")
        assert 'aria-label="B4, open, from $15 a month. Rent it."' in content

    def it_shows_a_cancelling_rental_with_keep_my_cubby(client: Client):
        member = _login(client, "tab_cancelling")
        rental = _rental(member, cancel_at_period_end=True)
        content = _tab(client, rental.cubby.unit.guild)
        assert "<dt>Yours Until</dt><dd>Fri, Nov 27</dd>" in content
        assert "data-cubby-keep>Keep My Cubby</button>" in content
        assert "data-cubby-cancel" not in content
        assert "pl-cubby-chip--cancelling" in content

    def it_shows_a_past_due_rental_with_update_card_first_and_when_stripe_tries_again(client: Client):
        member = _login(client, "tab_past_due")
        rental = _rental(
            member,
            status=Status.PAST_DUE,
            payment_failed_at=datetime(2026, 10, 27, 18, tzinfo=UTC),
            next_payment_attempt=datetime(2026, 11, 10, 18, tzinfo=UTC),
        )
        content = _tab(client, rental.cubby.unit.guild).replace("&#x27;", "'")
        assert "pl-cubby-mine--past-due" in content
        assert (
            "<strong>Your Payment Didn't Go Through</strong>We tried your card on Oct 27 and it was declined. "
            "We'll try your card again Tue, Nov 10. If it still doesn't go through, your rental ends and B3 opens "
            "up for someone else." in content
        )
        assert "data-cubby-update-card>Update Card</a>" in content
        assert "<dt>Next Try</dt><dd>Tue, Nov 10</dd>" in content
        assert "Keep It By" not in content
        assert 'pl-btn pl-btn--primary" hx-boost="false" data-cubby-manage-payment' in content

    def it_omits_the_dates_stripe_has_not_given(client: Client):
        member = _login(client, "tab_past_due_bare")
        rental = _rental(member, status=Status.PAST_DUE)
        content = _tab(client, rental.cubby.unit.guild).replace("&#x27;", "'")
        assert "Your last payment didn't go through. If it still doesn't go through" in content
        assert "Next Try" not in content

    def it_shows_others_rentals_as_rented_and_says_every_cubby_is_taken(client: Client):
        _login(client, "tab_full")
        rental = CubbyRentalFactory(cubby=_cubby())
        CubbyFactory(unit=rental.cubby.unit, label="B5", column=2, out_of_service=True)
        content = _tab(client, rental.cubby.unit.guild)
        assert "data-cubbies-full" in content
        assert "Every Cubby Is Taken" in content
        assert 'aria-label="B3, rented"' in content
        assert "data-cubby-mine" not in content

    def it_keeps_a_renters_card_on_the_page_with_the_switch_off(client: Client):
        member = _login(client, "tab_off_renter")
        rental = _rental(member, cubby=_cubby(show=False))
        content = _tab(client, rental.cubby.unit.guild)
        assert 'data-guild-tab="cubbies"' in content
        assert 'data-cubby-mine="B3"' in content
        assert "data-cubby-wall" not in content

    def it_shows_no_card_for_a_hold_still_awaiting_payment(client: Client):
        member = _login(client, "tab_hold")
        rental = _rental(member, status=Status.PENDING_PAYMENT)
        content = _tab(client, rental.cubby.unit.guild)
        assert "data-cubby-mine=" not in content
        assert 'aria-label="B3, your cubby"' in content  # the hold already keeps others off it

    def it_lists_each_rental_on_home(client: Client):
        member = _login(client, "home_lines")
        first = _rental(member, cubby=_cubby("A3"))
        _rental(member, cubby=_cubby("C1"))
        _rental(member, cubby=_cubby("E9"), status=Status.ENDED)
        content = client.get(reverse("hub_home")).content.decode()
        assert content.count("Your Cubby: ") == 2
        assert "Your Cubby: A3, Glaze Wall →" in content
        assert f'href="{reverse("hub_guild_detail", args=[first.cubby.unit.guild.slug])}?tab=cubbies"' in content


def describe_staff_cannot_close_a_rented_cubby():
    def it_refuses_archiving_a_rented_cubby_or_its_shelf(client: Client):
        lead = _login(client, "staff_archive")
        rental = CubbyRentalFactory(cubby=_cubby())
        guild = rental.cubby.unit.guild
        guild.guild_lead = lead
        guild.save()
        response = client.post(reverse("hub_cubby_archive", args=[rental.cubby_id]))
        assert response.status_code == 302
        assert _messages(response) == ["Someone rents cubby B3, so it stays until their rental ends."]
        response = client.post(reverse("hub_shelf_unit_archive", args=[rental.cubby.unit_id]))
        assert (
            _messages(response)[-1]
            == "Someone rents cubby B3 on this shelf, so the shelf stays until their rental ends."
        )
        assert Cubby.objects.get(pk=rental.cubby_id).archived_at is None
        assert rental.cubby.unit.__class__.objects.get(pk=rental.cubby.unit_id).archived_at is None

    def it_refuses_putting_a_rented_cubby_out_of_service(client: Client):
        lead = _login(client, "staff_out")
        rental = CubbyRentalFactory(cubby=_cubby())
        guild = rental.cubby.unit.guild
        guild.guild_lead = lead
        guild.save()
        response = client.post(
            reverse("hub_cubby_edit", args=[rental.cubby_id]),
            {"label": "B3", "row": "1", "column": "1", "shape": "one", "out_of_service": "on"},
        )
        assert response.status_code == 200
        assert "Someone rents this cubby, so it stays in service until their rental ends." in response.content.decode()
        assert Cubby.objects.get(pk=rental.cubby_id).out_of_service is False

    def it_draws_a_rented_cubby_as_rented_in_the_builder(client: Client):
        lead = _login(client, "staff_draw")
        rental = CubbyRentalFactory(cubby=_cubby())
        guild = rental.cubby.unit.guild
        guild.guild_lead = lead
        guild.save()
        content = client.get(reverse("hub_cubby_edit", args=[rental.cubby_id])).content.decode()
        assert "pl-cubby-chip--rented" in content
        assert "Edit B3, rented" in content or "Editing B3, rented" in content
