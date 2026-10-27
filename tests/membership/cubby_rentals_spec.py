"""BDD specs for renting a guild cubby (#978): membership/cubby_rentals.py and the rental models.

The hold made under the guild and cubby locks and its Checkout; resuming a member's own hold; the
one mirror of a Stripe subscription (every status, cancel at period end, ended is final, the card
moved to the customer); a paid Checkout whose hold is gone (the cubby taken back if free, else
cancelled and refunded); invoice.paid once per invoice with the payee frozen; a failed charge told
once per invoice; Change Amount, Cancel, Keep and Manage Payment; the sweep; account deletion.

Stripe is faked the way the equipment pricing specs fake it: each ``billing.stripe_utils`` call is
patched with the plain dict it returns. Names are factory strings no changelog could contain
(STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Notification
from membership import cubby_rentals
from membership.cubby_rentals import AmountError, CubbyRentalError, CubbyTakenError, CubbyUnavailableError
from membership.models import Cubby, CubbyPayment, CubbyRental, Member
from tests.billing.factories import TabFactory
from tests.membership.factories import (
    CubbyFactory,
    CubbyPaymentFactory,
    CubbyRentalFactory,
    GuildFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db

Status = CubbyRental.Status
_SESSION = {"id": "cs_cub_1", "url": "https://checkout.stripe.example/cs_cub_1"}
_PERIOD_END = int(datetime(2026, 11, 27, 18, 0, tzinfo=UTC).timestamp())


def _member(username: str = "quillon") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Bramblewick"
    member.save(update_fields=["status", "full_legal_name"])
    TabFactory(member=member, stripe_customer_id=f"cus_{username}")
    return member


def _cubby(label: str = "B3", *, payee: Member | None = None, **kwargs: Any) -> Cubby:
    guild = GuildFactory(name=f"Thistlecraft Guild {label}", show_cubbies_tab=True, cubby_payee=payee)
    unit = ShelfUnitFactory(guild=guild, name="Glaze Wall")
    kwargs.setdefault("floor_cents", 1500)
    kwargs.setdefault("suggested_cents", 2000)
    return CubbyFactory(unit=unit, label=label, **kwargs)


def _hold(cubby: Cubby, member: Member, **kwargs: Any) -> CubbyRental:
    kwargs.setdefault("stripe_checkout_session_id", "cs_cub_1")
    return CubbyRental.objects.create(
        cubby=cubby,
        member=member,
        amount_cents=kwargs.pop("amount_cents", 2200),
        status=Status.PENDING_PAYMENT,
        **kwargs,
    )


def _snapshot(rental: CubbyRental, **overrides: Any) -> dict[str, Any]:
    """The dict ``stripe_utils.retrieve_subscription`` returns for ``rental``'s subscription."""
    snapshot: dict[str, Any] = {
        "id": rental.stripe_subscription_id or f"sub_{rental.pk}",
        "status": "active",
        "customer": "cus_quillon",
        "metadata": {
            "kind": "cubby_rental",
            "rental_pk": str(rental.pk),
            "cubby_pk": str(rental.cubby_id),
            "member_pk": str(rental.member_id),
        },
        "cancel_at_period_end": False,
        "cancel_at": None,
        "default_payment_method": "",
        "latest_invoice": f"in_{rental.pk}",
        "item_id": "si_cub",
        "product_id": "prod_cub",
        "unit_amount": rental.amount_cents,
        "current_period_end": _PERIOD_END,
    }
    snapshot.update(overrides)
    return snapshot


def _invoice(rental: CubbyRental, **overrides: Any) -> dict[str, Any]:
    invoice: dict[str, Any] = {
        "id": f"in_{rental.pk}",
        "amount_paid": rental.amount_cents,
        "amount_due": rental.amount_cents,
        "status": "paid",
        "status_transitions": {"paid_at": _PERIOD_END - 30 * 86400},
        "lines": {"data": [{"period": {"start": _PERIOD_END - 30 * 86400, "end": _PERIOD_END}}]},
        "parent": {
            "type": "subscription_details",
            "subscription_details": {
                "subscription": rental.stripe_subscription_id or f"sub_{rental.pk}",
                "metadata": {"kind": "cubby_rental", "rental_pk": str(rental.pk)},
            },
        },
    }
    invoice.update(overrides)
    return invoice


def _subjects() -> list[str]:
    return [message.subject for message in mail.outbox]


# ── Models ───────────────────────────────────────────────────────────────────


def describe_models():
    def describe_Cubby_state():
        def it_reads_rented_while_a_live_rental_holds_it_and_open_again_once_it_ended():
            cubby = _cubby()
            assert cubby.state == Cubby.State.OPEN
            rental = CubbyRentalFactory(cubby=cubby)
            assert Cubby.objects.get(pk=cubby.pk).state == Cubby.State.RENTED
            rental.status = Status.ENDED
            rental.save()
            assert Cubby.objects.get(pk=cubby.pk).state == Cubby.State.OPEN

        def it_counts_a_hold_awaiting_payment_as_rented():
            cubby = _cubby()
            _hold(cubby, _member())
            assert Cubby.objects.get(pk=cubby.pk).state == Cubby.State.RENTED

        def it_reads_the_renter_from_the_annotation_without_a_query(django_assert_num_queries):
            rental = CubbyRentalFactory(cubby=_cubby())
            cubby = Cubby.objects.with_renter().get(pk=rental.cubby_id)
            with django_assert_num_queries(0):
                assert cubby.renter_id == rental.member_id
                assert cubby.state == Cubby.State.RENTED

        def it_draws_one_of_four_things_in_a_rented_cubby():
            assert {CubbyFactory().stuff_icon for _ in range(4)} == {"s-jars", "s-box", "s-bin", "s-tools"}

    def describe_CubbyRental():
        def it_allows_one_live_rental_per_cubby_but_any_number_ended():
            cubby = _cubby()
            CubbyRentalFactory(cubby=cubby, status=Status.ENDED)
            CubbyRentalFactory(cubby=cubby, status=Status.ENDED)
            CubbyRentalFactory(cubby=cubby)
            with pytest.raises(IntegrityError), transaction.atomic():
                CubbyRentalFactory(cubby=cubby, status=Status.PENDING_PAYMENT)

        def it_lets_one_member_rent_any_number_of_cubbies():
            member = _member()
            CubbyRentalFactory(member=member, cubby=_cubby("A1"))
            CubbyRentalFactory(member=member, cubby=_cubby("A2"))
            assert CubbyRental.objects.live().filter(member=member).count() == 2

        def it_keeps_the_amount_between_one_and_five_hundred_dollars():
            with pytest.raises(IntegrityError), transaction.atomic():
                CubbyRentalFactory(amount_cents=99)
            with pytest.raises(IntegrityError), transaction.atomic():
                CubbyRentalFactory(amount_cents=50001)

        def it_shows_what_the_card_and_the_confirm_need():
            rental = CubbyRentalFactory(
                cubby=_cubby(), amount_cents=2200, current_period_end=datetime(2026, 11, 27, 18, tzinfo=UTC)
            )
            assert rental.amount_display == "$22"
            assert rental.over_floor_display == "$7"
            assert rental.change_modal_id == f"cubby-change-{rental.pk}"
            assert rental.cancel_confirm_id == f"cubby-cancel-{rental.pk}"
            assert rental.cancel_url == f"/cubbies/rentals/{rental.pk}/cancel/"
            assert rental.cancel_message.startswith("B3 stays yours until Fri, Nov 27, the end of the month")
            assert str(rental).startswith("B3 rented by ")
            rental.current_period_end = None
            assert rental.cancel_message.startswith("B3 stays yours, the end of the month")

        def it_is_past_due_cancelling_and_live_by_its_status():
            rental = CubbyRentalFactory(status=Status.PAST_DUE, cancel_at_period_end=True)
            assert rental.is_past_due and rental.is_cancelling and rental.is_live
            rental.status = Status.ENDED
            assert not rental.is_live

        def it_owes_nothing_extra_at_or_below_the_floor():
            rental = CubbyRentalFactory(cubby=_cubby(), amount_cents=1000)
            assert rental.over_floor_cents == 0

        def it_lets_the_renter_act_and_an_admin_act_on_any():
            mine = CubbyRentalFactory()
            other = CubbyRentalFactory()
            assert list(CubbyRental.objects.for_actor(mine.member, is_admin=False)) == [mine]
            assert set(CubbyRental.objects.for_actor(mine.member, is_admin=True)) == {mine, other}

    def describe_CubbyPayment():
        def it_records_one_row_per_invoice():
            payment = CubbyPaymentFactory(stripe_invoice_id="in_once")
            assert str(payment).endswith("(in_once)")
            with pytest.raises(IntegrityError), transaction.atomic():
                CubbyPaymentFactory(stripe_invoice_id="in_once")


# ── Amounts and small helpers ────────────────────────────────────────────────


def describe_amount_problem():
    def it_takes_the_floor_and_up_to_five_hundred_dollars():
        cubby = _cubby()
        assert cubby_rentals.amount_problem(cubby, 1500) == ""
        assert cubby_rentals.amount_problem(cubby, 50000) == ""

    def it_refuses_below_the_floor_in_the_mockups_words():
        assert (
            cubby_rentals.amount_problem(_cubby(), 800)
            == "The lowest you can pay for B3 is $15 a month. Pick $15 or more."
        )

    def it_refuses_over_five_hundred_dollars():
        assert cubby_rentals.amount_problem(_cubby(), 50001) == "The most a cubby can be is $500 a month."


def describe_helpers():
    def it_writes_ordinals():
        assert [cubby_rentals.ordinal(d) for d in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 27, 31)] == [
            "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "27th", "31st",
        ]  # fmt: skip

    def it_writes_dates_the_card_way():
        assert cubby_rentals.date_display(None) == ""
        assert cubby_rentals.date_display(datetime(2026, 11, 27, 18, tzinfo=UTC)) == "Fri, Nov 27"
        assert cubby_rentals.date_display(datetime(2026, 11, 9).date()) == "Mon, Nov 9"

    def it_says_when_stripe_tries_again_or_nothing():
        rental = CubbyRentalFactory(next_payment_attempt=datetime(2026, 11, 10, 18, tzinfo=UTC))
        assert cubby_rentals.retry_sentence(rental) == "We'll try your card again Tue, Nov 10."
        rental.next_payment_attempt = None
        assert cubby_rentals.retry_sentence(rental) == ""

    def it_round_trips_a_checkout_token():
        rental = CubbyRentalFactory()
        assert cubby_rentals.read_checkout_token(cubby_rentals.make_checkout_token(rental)) == (
            rental.pk,
            rental.cubby_id,
        )

    def it_lists_a_members_live_rentals_in_one_guild():
        member = _member()
        here = CubbyRentalFactory(member=member, cubby=_cubby("A1"))
        CubbyRentalFactory(member=member, cubby=_cubby("A2"))
        CubbyRentalFactory(member=member, cubby=here.cubby, status=Status.ENDED)
        assert list(cubby_rentals.live_rentals_for(member, here.cubby.unit.guild)) == [here]
        assert cubby_rentals.live_rentals_for(member).count() == 2

    def describe_unavailable_reason():
        def it_is_empty_for_an_open_cubby_and_names_why_otherwise():
            cubby = _cubby()
            assert cubby_rentals.unavailable_reason(cubby) == ""
            cubby.out_of_service = True
            assert cubby_rentals.unavailable_reason(cubby) == "B3 is out of service right now."
            cubby.out_of_service = False
            cubby.unit.guild.show_cubbies_tab = False
            assert cubby_rentals.unavailable_reason(cubby) == "B3 isn't available to rent right now."
            cubby.unit.guild.show_cubbies_tab = True
            cubby.archived_at = timezone.now()
            assert cubby_rentals.unavailable_reason(cubby) == "B3 isn't available to rent right now."


# ── Rent ─────────────────────────────────────────────────────────────────────


def describe_start_rental():
    @patch("billing.stripe_utils.create_subscription_checkout_session", return_value=_SESSION)
    def it_holds_the_cubby_and_opens_a_monthly_subscription_checkout(mock_create):
        cubby = _cubby()
        member = _member()

        url = cubby_rentals.start_rental(cubby, member, 2200)

        assert url == _SESSION["url"]
        hold = CubbyRental.objects.get(member=member)
        assert (hold.status, hold.amount_cents, hold.stripe_checkout_session_id) == (
            Status.PENDING_PAYMENT,
            2200,
            "cs_cub_1",
        )
        assert hold.stripe_customer_id == "cus_quillon"
        kwargs = mock_create.call_args.kwargs
        assert kwargs["customer_id"] == "cus_quillon"
        assert kwargs["amount_cents"] == 2200
        assert kwargs["metadata"] == {
            "kind": "cubby_rental",
            "rental_pk": str(hold.pk),
            "cubby_pk": str(cubby.pk),
            "member_pk": str(member.pk),
        }
        assert kwargs["idempotency_key"] == f"cubby-checkout-{hold.pk}"
        assert kwargs["product_name"] == f"Cubby B3, Glaze Wall ({cubby.unit.guild.name})"
        assert kwargs["description"] == f"Cubby B3 on the Glaze Wall, {cubby.unit.guild.name}"
        assert "/cubbies/checkout/" in kwargs["success_url"]
        assert kwargs["cancel_url"].endswith("/cancelled/")
        assert 3500 < kwargs["expires_at"] - timezone.now().timestamp() <= 3600
        assert mail.outbox == []

    @patch("billing.stripe_utils.create_customer", return_value="cus_fresh")
    @patch("billing.stripe_utils.create_subscription_checkout_session", return_value=_SESSION)
    def it_makes_the_members_stripe_customer_on_their_tab_when_they_have_none(mock_create, mock_customer):
        member = _member()
        member.tab.stripe_customer_id = ""
        member.tab.save()
        cubby_rentals.start_rental(_cubby(), member, 2200)
        assert mock_create.call_args.kwargs["customer_id"] == "cus_fresh"
        member.tab.refresh_from_db()
        assert member.tab.stripe_customer_id == "cus_fresh"

    @patch("billing.stripe_utils.create_subscription_checkout_session")
    def it_refuses_a_cubby_someone_else_holds_without_reaching_stripe(mock_create):
        cubby = _cubby("A3")
        CubbyRentalFactory(cubby=cubby)
        with pytest.raises(CubbyTakenError, match="Someone just rented A3. Pick another open cubby."):
            cubby_rentals.start_rental(cubby, _member(), 2200)
        mock_create.assert_not_called()

    @patch("billing.stripe_utils.create_subscription_checkout_session")
    def it_refuses_the_members_own_active_cubby(mock_create):
        rental = CubbyRentalFactory(cubby=_cubby(), member=_member())
        with pytest.raises(CubbyUnavailableError, match="B3 is already yours."):
            cubby_rentals.start_rental(rental.cubby, rental.member, 2200)
        mock_create.assert_not_called()

    @pytest.mark.parametrize("change", ["out_of_service", "archived", "unit_archived", "tab_off"])
    @patch("billing.stripe_utils.create_subscription_checkout_session")
    def it_refuses_a_cubby_nobody_can_rent(mock_create, change: str):
        cubby = _cubby()
        if change == "out_of_service":
            Cubby.objects.filter(pk=cubby.pk).update(out_of_service=True)
        elif change == "archived":
            Cubby.objects.filter(pk=cubby.pk).update(archived_at=timezone.now())
        elif change == "unit_archived":
            cubby.unit.archived_at = timezone.now()
            cubby.unit.save()
        else:
            cubby.unit.guild.show_cubbies_tab = False
            cubby.unit.guild.save()
        with pytest.raises(CubbyUnavailableError):
            cubby_rentals.start_rental(cubby, _member(), 2200)
        mock_create.assert_not_called()

    @patch("billing.stripe_utils.create_subscription_checkout_session")
    def it_checks_the_floor_as_it_is_now_under_the_lock(mock_create):
        cubby = _cubby()
        Cubby.objects.filter(pk=cubby.pk).update(floor_cents=3000, suggested_cents=3000)
        with pytest.raises(AmountError, match=r"\$30 a month"):
            cubby_rentals.start_rental(cubby, _member(), 2200)
        mock_create.assert_not_called()
        assert not CubbyRental.objects.exists()

    @patch("billing.stripe_utils.create_subscription_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_releases_the_hold_when_stripe_fails(mock_create):
        with pytest.raises(RuntimeError):
            cubby_rentals.start_rental(_cubby(), _member(), 2200)
        assert not CubbyRental.objects.exists()

    def describe_a_double_tap():
        @patch("billing.stripe_utils.create_subscription_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_resumes_the_same_open_checkout(mock_retrieve, mock_create):
            cubby, member = _cubby(), _member()
            hold = _hold(cubby, member)
            mock_retrieve.return_value = {
                "status": "open",
                "url": "https://checkout.stripe.example/again",
                "subscription": "",
            }
            assert cubby_rentals.start_rental(cubby, member, 2200) == "https://checkout.stripe.example/again"
            mock_create.assert_not_called()
            assert list(CubbyRental.objects.all()) == [hold]

        @patch("billing.stripe_utils.expire_checkout_session")
        @patch("billing.stripe_utils.create_subscription_checkout_session", return_value=_SESSION)
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_starts_again_at_a_new_amount_after_expiring_the_old_checkout(mock_retrieve, mock_create, mock_expire):
            cubby, member = _cubby(), _member()
            old = _hold(cubby, member, amount_cents=2000, stripe_checkout_session_id="cs_old")
            mock_retrieve.return_value = {
                "status": "open",
                "url": "https://checkout.stripe.example/old",
                "subscription": "",
            }
            assert cubby_rentals.start_rental(cubby, member, 2500) == _SESSION["url"]
            mock_expire.assert_called_once_with(session_id="cs_old")
            assert not CubbyRental.objects.filter(pk=old.pk).exists()
            assert CubbyRental.objects.get().amount_cents == 2500

        @pytest.mark.parametrize("answer", ["expired", "missing", "no_session"])
        @patch("billing.stripe_utils.create_subscription_checkout_session", return_value=_SESSION)
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_starts_again_when_the_old_checkout_is_dead(mock_retrieve, mock_create, answer: str):
            from billing.stripe_utils import CheckoutSessionNotFound

            cubby, member = _cubby(), _member()
            _hold(cubby, member, stripe_checkout_session_id="" if answer == "no_session" else "cs_old")
            if answer == "missing":
                mock_retrieve.side_effect = CheckoutSessionNotFound("cs_old")
            else:
                mock_retrieve.return_value = {"status": "expired", "url": "", "subscription": ""}
            assert cubby_rentals.start_rental(cubby, member, 2200) == _SESSION["url"]
            assert CubbyRental.objects.get().stripe_checkout_session_id == "cs_cub_1"

        @patch("billing.stripe_utils.retrieve_subscription")
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_says_the_cubby_is_theirs_when_the_old_checkout_was_paid(mock_retrieve, mock_sub):
            cubby, member = _cubby(), _member()
            hold = _hold(cubby, member)
            mock_retrieve.return_value = {"status": "complete", "url": "", "subscription": "sub_paid"}
            mock_sub.return_value = _snapshot(hold, id="sub_paid")
            with pytest.raises(CubbyUnavailableError, match="already yours"):
                cubby_rentals.start_rental(cubby, member, 2200)
            hold.refresh_from_db()
            assert hold.status == Status.ACTIVE

        @patch("billing.stripe_utils.create_subscription_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_gives_up_after_one_retry_while_a_hold_keeps_coming_back(mock_retrieve, mock_create):
            # A second tab paying the same cubby in the gap: every attempt finds a hold of its own.
            cubby, member = _cubby(), _member()
            mock_retrieve.return_value = {"status": "expired", "url": "", "subscription": ""}
            _hold(cubby, member, stripe_checkout_session_id="cs_first")
            with patch.object(cubby_rentals, "_delete_hold", side_effect=lambda rental, **kw: None):
                with pytest.raises(CubbyTakenError, match="being paid for right now"):
                    cubby_rentals.start_rental(cubby, member, 2200)
            mock_create.assert_not_called()


# ── Settling a subscription ──────────────────────────────────────────────────


def describe_settle_subscription():
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_activates_a_hold_once_and_tells_the_member_once(mock_sub):
        hold = _hold(_cubby(), _member())
        mock_sub.return_value = _snapshot(hold)

        assert cubby_rentals.settle_subscription(f"sub_{hold.pk}") == "settled"
        assert cubby_rentals.settle_subscription(f"sub_{hold.pk}") == "settled"

        hold.refresh_from_db()
        assert hold.status == Status.ACTIVE
        assert hold.start_date == timezone.localdate()
        assert (hold.stripe_subscription_id, hold.stripe_subscription_item_id, hold.stripe_product_id) == (
            f"sub_{hold.pk}",
            "si_cub",
            "prod_cub",
        )
        assert hold.current_period_end == datetime.fromtimestamp(_PERIOD_END, tz=UTC)
        assert _subjects() == ["B3 is yours"]
        assert Notification.objects.filter(user=hold.member.user, title="B3 is yours").count() == 1

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_ignores_a_subscription_that_is_not_a_cubbys(mock_sub):
        mock_sub.return_value = {"metadata": {"kind": "something_else"}}
        assert cubby_rentals.settle_subscription("sub_x") == "ignored"

    @patch("billing.stripe_utils.move_subscription_card_to_customer")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_moves_the_checkout_card_to_the_customer_so_the_portal_card_pays(mock_sub, mock_move):
        hold = _hold(_cubby(), _member())
        mock_sub.return_value = _snapshot(hold, default_payment_method="pm_checkout")
        cubby_rentals.settle_subscription(f"sub_{hold.pk}")
        mock_move.assert_called_once_with(
            customer_id="cus_quillon", subscription_id=f"sub_{hold.pk}", payment_method_id="pm_checkout"
        )

    @patch("billing.stripe_utils.move_subscription_card_to_customer")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_leaves_the_card_of_an_ended_subscription_alone(mock_sub, mock_move):
        rental = CubbyRentalFactory(cubby=_cubby())
        mock_sub.return_value = _snapshot(rental, status="canceled", default_payment_method="pm_checkout")
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        mock_move.assert_not_called()

    @pytest.mark.parametrize(
        ("stripe_status", "expected"),
        [
            ("active", Status.ACTIVE),
            ("trialing", Status.ACTIVE),
            ("past_due", Status.PAST_DUE),
            ("incomplete", Status.ACTIVE),
        ],
    )
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_mirrors_the_status_of_a_billing_rental(mock_sub, stripe_status: str, expected: str):
        rental = CubbyRentalFactory(cubby=_cubby(), status=Status.ACTIVE)
        mock_sub.return_value = _snapshot(rental, status=stripe_status)
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        rental.refresh_from_db()
        assert rental.status == expected

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_keeps_a_hold_awaiting_payment_while_stripe_says_incomplete(mock_sub):
        hold = _hold(_cubby(), _member())
        mock_sub.return_value = _snapshot(hold, status="incomplete")
        cubby_rentals.settle_subscription(f"sub_{hold.pk}")
        hold.refresh_from_db()
        assert hold.status == Status.PENDING_PAYMENT
        assert mail.outbox == []

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_clears_the_decline_once_a_charge_goes_through(mock_sub):
        rental = CubbyRentalFactory(
            cubby=_cubby(),
            status=Status.PAST_DUE,
            payment_failed_at=timezone.now(),
            next_payment_attempt=timezone.now(),
        )
        mock_sub.return_value = _snapshot(rental)
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        rental.refresh_from_db()
        assert (rental.status, rental.payment_failed_at, rental.next_payment_attempt) == (Status.ACTIVE, None, None)

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_mirrors_amount_period_end_and_cancel_either_way_stripe_says_it(mock_sub):
        rental = CubbyRentalFactory(cubby=_cubby())
        mock_sub.return_value = _snapshot(rental, unit_amount=3100, cancel_at=_PERIOD_END)
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        rental.refresh_from_db()
        assert (rental.amount_cents, rental.cancel_at_period_end) == (3100, True)
        mock_sub.return_value = _snapshot(rental, cancel_at_period_end=False, cancel_at=None)
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        rental.refresh_from_db()
        assert rental.cancel_at_period_end is False

    @patch("billing.stripe_utils.cancel_subscription")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_cancels_a_subscription_stripe_gave_up_on(mock_sub, mock_cancel):
        rental = CubbyRentalFactory(cubby=_cubby())
        mock_sub.return_value = _snapshot(rental, status="unpaid")
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        rental.refresh_from_db()
        assert rental.status == Status.PAST_DUE
        mock_cancel.assert_called_once_with(subscription_id=rental.stripe_subscription_id)

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_ends_the_rental_frees_the_cubby_and_tells_the_member_once(mock_sub):
        rental = CubbyRentalFactory(cubby=_cubby(), member=_member())
        mock_sub.return_value = _snapshot(rental, status="canceled")
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        rental.refresh_from_db()
        assert (rental.status, rental.end_date) == (Status.ENDED, timezone.localdate())
        assert Cubby.objects.get(pk=rental.cubby_id).state == Cubby.State.OPEN
        assert _subjects() == ["Your rental of B3 has ended"]

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_never_brings_an_ended_rental_back(mock_sub):
        rental = CubbyRentalFactory(cubby=_cubby(), status=Status.ENDED)
        mock_sub.return_value = _snapshot(rental, status="active", unit_amount=4000)
        cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        rental.refresh_from_db()
        assert (rental.status, rental.amount_cents) == (Status.ENDED, 2200)

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_tells_nobody_when_a_hold_ends_before_it_ever_started(mock_sub):
        hold = _hold(_cubby(), _member())
        mock_sub.return_value = _snapshot(hold, status="incomplete_expired")
        cubby_rentals.settle_subscription(f"sub_{hold.pk}")
        hold.refresh_from_db()
        assert hold.status == Status.ENDED
        assert mail.outbox == []

    def describe_a_paid_checkout_whose_hold_is_gone():
        def _lost(cubby: Cubby, member: Member) -> CubbyRental:
            hold = _hold(cubby, member)
            CubbyRental.objects.filter(pk=hold.pk).delete()
            return hold

        @patch("billing.stripe_utils.retrieve_subscription")
        def it_gives_the_member_the_cubby_when_it_is_still_free(mock_sub):
            cubby, member = _cubby(), _member()
            lost = _lost(cubby, member)
            mock_sub.return_value = _snapshot(lost)
            assert cubby_rentals.settle_subscription(f"sub_{lost.pk}") == "recovered"
            rental = CubbyRental.objects.get(pk=lost.pk)
            assert (rental.status, rental.member, rental.cubby) == (Status.ACTIVE, member, cubby)
            assert _subjects() == ["B3 is yours"]

        @patch(
            "billing.stripe_utils.create_refund", return_value={"id": "re_cub", "status": "succeeded", "amount": 2200}
        )
        @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_cub")
        @patch("billing.stripe_utils.cancel_subscription")
        @patch("billing.stripe_utils.retrieve_subscription")
        def it_cancels_and_refunds_in_full_when_someone_else_has_it(mock_sub, mock_cancel, mock_pi, mock_refund):
            cubby, member = _cubby(), _member()
            lost = _lost(cubby, member)
            CubbyRentalFactory(cubby=cubby)  # someone else got it in between
            mock_sub.return_value = _snapshot(lost)

            assert cubby_rentals.settle_subscription(f"sub_{lost.pk}") == "refunded"

            rental = CubbyRental.objects.get(pk=lost.pk)
            assert (rental.status, rental.start_date) == (Status.ENDED, None)
            mock_cancel.assert_called_once_with(subscription_id=f"sub_{lost.pk}")
            mock_pi.assert_called_once_with(invoice_id=f"in_{lost.pk}")
            mock_refund.assert_called_once_with(
                payment_intent_id="pi_cub", idempotency_key=f"cubby-refund-in_{lost.pk}"
            )
            payment = CubbyPayment.objects.get(stripe_invoice_id=f"in_{lost.pk}")
            assert (payment.stripe_refund_id, payment.amount_cents, payment.payee) == ("re_cub", 2200, None)
            assert _subjects() == ["You've been refunded for B3"]

            # A re-delivery (Stripe now says canceled) refunds and tells nothing more.
            mock_sub.return_value = _snapshot(lost, status="canceled")
            assert cubby_rentals.settle_subscription(f"sub_{lost.pk}") == "settled"
            assert mock_refund.call_count == 1
            assert mock_cancel.call_count == 1
            assert len(mail.outbox) == 1

        @pytest.mark.parametrize("why", ["out_of_service", "deleted_account"])
        @patch(
            "billing.stripe_utils.create_refund", return_value={"id": "re_cub", "status": "succeeded", "amount": 2200}
        )
        @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_cub")
        @patch("billing.stripe_utils.cancel_subscription")
        @patch("billing.stripe_utils.retrieve_subscription")
        def it_refunds_when_the_cubby_closed_or_the_account_is_gone(
            mock_sub, mock_cancel, mock_pi, mock_refund, why: str
        ):
            cubby, member = _cubby(), _member()
            lost = _lost(cubby, member)
            if why == "out_of_service":
                Cubby.objects.filter(pk=cubby.pk).update(out_of_service=True)
            else:
                Member.objects.filter(pk=member.pk).update(deleted_at=timezone.now())
            mock_sub.return_value = _snapshot(lost)
            assert cubby_rentals.settle_subscription(f"sub_{lost.pk}") == "refunded"
            mock_refund.assert_called_once()

        @patch("billing.stripe_utils.create_refund")
        @patch("billing.stripe_utils.invoice_payment_intent", return_value="")
        @patch("billing.stripe_utils.cancel_subscription")
        @patch("billing.stripe_utils.retrieve_subscription")
        def it_cancels_but_refunds_nothing_when_nothing_was_paid(mock_sub, mock_cancel, mock_pi, mock_refund):
            cubby, member = _cubby(), _member()
            lost = _lost(cubby, member)
            CubbyRentalFactory(cubby=cubby)
            mock_sub.return_value = _snapshot(lost)
            cubby_rentals.settle_subscription(f"sub_{lost.pk}")
            mock_refund.assert_not_called()
            mock_sub.return_value = _snapshot(lost, latest_invoice="")
            cubby_rentals.settle_subscription(f"sub_{lost.pk}")
            assert mock_pi.call_count == 1

        @patch("billing.stripe_utils.retrieve_subscription")
        def it_finds_the_rental_a_concurrent_call_just_made(mock_sub):
            cubby, member = _cubby(), _member()
            lost = _lost(cubby, member)
            # Another delivery made the rental again between the first check and the lock.
            CubbyRental.objects.create(
                pk=lost.pk, cubby=cubby, member=member, amount_cents=2200, status=Status.PENDING_PAYMENT
            )
            assert cubby_rentals._recover_lost_hold(_snapshot(lost), lost.pk) == "exists"

    @patch("billing.stripe_utils.create_refund", return_value={"id": "re_late", "status": "succeeded", "amount": 2200})
    @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_late")
    @patch("billing.stripe_utils.cancel_subscription")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_refunds_a_rental_ended_before_it_began_whose_subscription_still_bills(
        mock_sub, mock_cancel, mock_pi, mock_refund
    ):
        rental = CubbyRentalFactory(cubby=_cubby(), status=Status.ENDED, start_date=None)
        mock_sub.return_value = _snapshot(rental)
        assert cubby_rentals.settle_subscription(rental.stripe_subscription_id) == "refunded"
        mock_refund.assert_called_once()


# ── Invoices ─────────────────────────────────────────────────────────────────


def describe_record_invoice_paid():
    @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_paid")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_records_one_payment_per_invoice_with_the_payee_frozen(mock_sub, mock_pi):
        payee = _member("hazelgrove")
        rental = CubbyRentalFactory(cubby=_cubby(payee=payee))
        mock_sub.return_value = _snapshot(rental)

        first = cubby_rentals.record_invoice_paid(_invoice(rental))
        guild = rental.cubby.unit.guild
        guild.cubby_payee = None
        guild.save()
        again = cubby_rentals.record_invoice_paid(_invoice(rental))

        assert first == again
        payment = CubbyPayment.objects.get()
        assert (payment.amount_cents, payment.stripe_payment_intent_id, payment.payee) == (2200, "pi_paid", payee)
        assert payment.period_end == datetime.fromtimestamp(_PERIOD_END, tz=UTC)
        assert payment.paid_at == datetime.fromtimestamp(_PERIOD_END - 30 * 86400, tz=UTC)

    @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_first")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_activates_a_hold_when_it_arrives_before_the_checkout_completes(mock_sub, mock_pi):
        hold = _hold(_cubby(), _member())
        mock_sub.return_value = _snapshot(hold)
        cubby_rentals.record_invoice_paid(_invoice(hold))
        hold.refresh_from_db()
        assert hold.status == Status.ACTIVE
        assert CubbyPayment.objects.get().rental == hold
        assert _subjects() == ["B3 is yours"]

    @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_x")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_records_the_money_of_an_ended_rental_without_reviving_it(mock_sub, mock_pi):
        rental = CubbyRentalFactory(cubby=_cubby(), status=Status.ENDED)
        mock_sub.return_value = _snapshot(rental, status="canceled")
        cubby_rentals.record_invoice_paid(_invoice(rental, status_transitions={}, lines={"data": []}))
        rental.refresh_from_db()
        assert rental.status == Status.ENDED
        payment = CubbyPayment.objects.get()
        assert payment.period_start is None
        assert abs((payment.paid_at - timezone.now()).total_seconds()) < 60

    def it_ignores_an_invoice_that_is_not_a_cubbys():
        assert cubby_rentals.record_invoice_paid({"id": "in_other", "parent": None}) is None
        assert (
            cubby_rentals.record_invoice_paid(
                {"id": "in_x", "parent": {"subscription_details": {"metadata": {"kind": "cubby_rental"}}}}
            )
            is None
        )

    @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_x")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_reads_an_expanded_subscription_and_keeps_the_row_already_recorded(mock_sub, mock_pi):
        rental = CubbyRentalFactory(cubby=_cubby())
        mock_sub.return_value = _snapshot(rental)
        invoice = _invoice(rental)
        invoice["parent"]["subscription_details"]["subscription"] = {"id": rental.stripe_subscription_id}
        # Recorded by an earlier or a racing delivery, or by the refund of a cubby someone else got.
        existing = CubbyPaymentFactory(rental=rental, stripe_invoice_id=invoice["id"], stripe_refund_id="re_x")
        assert cubby_rentals.record_invoice_paid(invoice) == existing
        assert CubbyPayment.objects.get().stripe_refund_id == "re_x"
        mock_sub.assert_called_once_with(subscription_id=rental.stripe_subscription_id)


def describe_record_payment_failed():
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_marks_past_due_and_tells_the_member_once_per_invoice(mock_sub):
        rental = CubbyRentalFactory(cubby=_cubby(), member=_member())
        mock_sub.return_value = _snapshot(rental, status="past_due")
        retry = _PERIOD_END + 3 * 86400
        invoice = _invoice(rental, status="open", next_payment_attempt=retry)

        cubby_rentals.record_payment_failed(invoice)
        cubby_rentals.record_payment_failed(invoice)

        rental.refresh_from_db()
        assert rental.status == Status.PAST_DUE
        assert rental.next_payment_attempt == datetime.fromtimestamp(retry, tz=UTC)
        assert rental.payment_failed_at is not None
        assert _subjects() == ["Your cubby payment didn't go through"]
        body = mail.outbox[0].body
        assert "We'll try your card again Mon, Nov 30." in body
        assert f"/cubbies/rentals/{rental.pk}/payment/" in body

        cubby_rentals.record_payment_failed(_invoice(rental, id="in_next", status="open", next_payment_attempt=None))
        assert len(mail.outbox) == 2
        assert "try your card again" not in mail.outbox[1].body

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_changes_nothing_for_a_failure_delivered_after_a_later_payment(mock_sub):
        rental = CubbyRentalFactory(cubby=_cubby())
        mock_sub.return_value = _snapshot(rental, status="active")
        cubby_rentals.record_payment_failed(_invoice(rental, status="open"))
        rental.refresh_from_db()
        assert rental.status == Status.ACTIVE
        assert mail.outbox == []

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_tells_nobody_about_an_invoice_paid_since(mock_sub):
        rental = CubbyRentalFactory(cubby=_cubby())
        mock_sub.return_value = _snapshot(rental, status="past_due")
        cubby_rentals.record_payment_failed(_invoice(rental, status="paid"))
        assert mail.outbox == []

    def it_ignores_an_invoice_that_is_not_a_cubbys():
        cubby_rentals.record_payment_failed({"id": "in_other", "parent": {"subscription_details": None}})


# ── The member's buttons ─────────────────────────────────────────────────────


def describe_change_amount():
    @patch("billing.stripe_utils.update_subscription_amount")
    def it_reprices_the_subscription_item_on_its_product_from_the_next_charge(mock_update):
        rental = CubbyRentalFactory(cubby=_cubby())
        changed = cubby_rentals.change_amount(rental, 2500)
        mock_update.assert_called_once_with(
            subscription_id=rental.stripe_subscription_id,
            item_id=rental.stripe_subscription_item_id,
            product_id=rental.stripe_product_id,
            amount_cents=2500,
        )
        assert changed.amount_cents == 2500
        rental.refresh_from_db()
        assert rental.amount_cents == 2500

    @patch("billing.stripe_utils.update_subscription_amount")
    def it_follows_a_raised_floor_but_keeps_the_current_amount_allowed(mock_update):
        rental = CubbyRentalFactory(cubby=_cubby(), amount_cents=1500)
        Cubby.objects.filter(pk=rental.cubby_id).update(floor_cents=2000, suggested_cents=2500)
        with pytest.raises(AmountError, match=r"\$20 a month"):
            cubby_rentals.change_amount(rental, 1800)
        assert cubby_rentals.change_amount(rental, 1500).amount_cents == 1500
        mock_update.assert_not_called()

    @pytest.mark.parametrize("status", [Status.PENDING_PAYMENT, Status.ENDED])
    def it_refuses_a_rental_that_is_not_billing(status: str):
        rental = CubbyRentalFactory(cubby=_cubby(), status=status)
        with pytest.raises(CubbyRentalError, match="isn't active any more"):
            cubby_rentals.change_amount(rental, 2500)


def describe_set_cancelling():
    @patch("billing.stripe_utils.set_subscription_cancel_at_period_end")
    def it_cancels_at_the_end_of_the_month_and_keeps_it_again(mock_set):
        rental = CubbyRentalFactory(cubby=_cubby())
        assert cubby_rentals.set_cancelling(rental, cancel=True).cancel_at_period_end is True
        rental.refresh_from_db()
        assert rental.cancel_at_period_end is True
        assert cubby_rentals.set_cancelling(rental, cancel=False).cancel_at_period_end is False
        assert [c.kwargs["cancel"] for c in mock_set.call_args_list] == [True, False]


def describe_portal_url():
    @patch("billing.stripe_utils.create_payment_method_portal_session", return_value="https://billing.stripe.example/p")
    def it_opens_the_portal_on_the_members_customer_and_comes_back_to_the_tab(mock_portal):
        rental = CubbyRentalFactory(cubby=_cubby(), stripe_customer_id="cus_portal")
        assert cubby_rentals.portal_url(rental) == "https://billing.stripe.example/p"
        kwargs = mock_portal.call_args.kwargs
        assert kwargs["customer_id"] == "cus_portal"
        assert kwargs["return_url"].endswith(f"/guilds/{rental.cubby.unit.guild.slug}/?tab=cubbies")


# ── Holds ────────────────────────────────────────────────────────────────────


def describe_holds():
    def describe_finish_checkout():
        @patch("billing.stripe_utils.retrieve_subscription")
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_settles_a_completed_checkout_on_the_return_page(mock_session, mock_sub):
            hold = _hold(_cubby(), _member())
            mock_session.return_value = {"status": "complete", "subscription": f"sub_{hold.pk}"}
            mock_sub.return_value = _snapshot(hold)
            assert cubby_rentals.finish_checkout(hold.pk) == "active"

        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_waits_while_stripe_is_not_done_or_unreachable(mock_session):
            hold = _hold(_cubby(), _member())
            mock_session.return_value = {"status": "open", "subscription": ""}
            assert cubby_rentals.finish_checkout(hold.pk) == "pending"
            mock_session.side_effect = RuntimeError("down")
            assert cubby_rentals.finish_checkout(hold.pk) == "pending"

        def it_says_gone_for_a_released_or_ended_rental():
            assert cubby_rentals.finish_checkout(987654) == "gone"
            ended = CubbyRentalFactory(status=Status.ENDED)
            assert cubby_rentals.finish_checkout(ended.pk) == "gone"
            assert cubby_rentals.finish_checkout(CubbyRentalFactory().pk) == "active"

    def describe_release_hold_if_unpaid():
        @patch("billing.stripe_utils.expire_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_releases_an_unpaid_hold_and_expires_its_open_checkout(mock_session, mock_expire):
            hold = _hold(_cubby(), _member())
            mock_session.return_value = {"status": "open", "subscription": ""}
            assert cubby_rentals.release_hold_if_unpaid(hold) == "released"
            assert not CubbyRental.objects.exists()
            mock_expire.assert_called_once_with(session_id="cs_cub_1")

        @patch("billing.stripe_utils.expire_checkout_session", side_effect=RuntimeError("already expired"))
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_releases_even_when_expiring_fails(mock_session, mock_expire):
            hold = _hold(_cubby(), _member())
            mock_session.return_value = {"status": "open", "subscription": ""}
            assert cubby_rentals.release_hold_if_unpaid(hold) == "released"

        @patch("billing.stripe_utils.retrieve_subscription")
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_keeps_a_hold_stripe_says_was_paid(mock_session, mock_sub):
            hold = _hold(_cubby(), _member())
            mock_session.return_value = {"status": "complete", "subscription": f"sub_{hold.pk}"}
            mock_sub.return_value = _snapshot(hold)
            assert cubby_rentals.release_hold_if_unpaid(hold) == "paid"
            hold.refresh_from_db()
            assert hold.status == Status.ACTIVE

        @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("down"))
        def it_keeps_the_hold_while_stripe_is_unreachable(mock_session):
            hold = _hold(_cubby(), _member())
            assert cubby_rentals.release_hold_if_unpaid(hold) == "unknown"
            assert CubbyRental.objects.filter(pk=hold.pk).exists()

        def it_releases_a_hold_with_no_checkout_and_answers_for_rows_already_moved_on():
            hold = _hold(_cubby(), _member(), stripe_checkout_session_id=None)
            assert cubby_rentals.release_hold_if_unpaid(hold) == "released"
            assert cubby_rentals.release_hold_if_unpaid(CubbyRentalFactory()) == "paid"
            assert cubby_rentals.release_hold_if_unpaid(CubbyRentalFactory(status=Status.ENDED)) == "released"

        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_releases_a_hold_whose_checkout_stripe_has_never_heard_of(mock_session):
            from billing.stripe_utils import CheckoutSessionNotFound

            mock_session.side_effect = CheckoutSessionNotFound("cs_cub_1")
            hold = _hold(_cubby(), _member())
            assert cubby_rentals.release_hold_if_unpaid(hold) == "released"

    def describe_release_stale_holds():
        @patch("billing.stripe_utils.retrieve_subscription")
        @patch("billing.stripe_utils.expire_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session")
        def it_asks_stripe_about_each_hold_older_than_two_hours(mock_session, mock_expire, mock_sub):
            member = _member()
            old_unpaid = _hold(_cubby("A1"), member, stripe_checkout_session_id="cs_unpaid")
            old_paid = _hold(_cubby("A2"), member, stripe_checkout_session_id="cs_paid")
            old_unknown = _hold(_cubby("A3"), member, stripe_checkout_session_id="cs_down")
            fresh = _hold(_cubby("A4"), member, stripe_checkout_session_id="cs_fresh")
            two_hours_ago = timezone.now() - timedelta(hours=2, minutes=1)
            CubbyRental.objects.exclude(pk=fresh.pk).update(created_at=two_hours_ago)

            def session(*, session_id: str) -> dict[str, Any]:
                if session_id == "cs_down":
                    raise RuntimeError("down")
                if session_id == "cs_paid":
                    return {"status": "complete", "subscription": f"sub_{old_paid.pk}"}
                return {"status": "expired", "subscription": ""}

            mock_session.side_effect = session
            mock_sub.return_value = _snapshot(old_paid)

            assert cubby_rentals.release_stale_holds() == (1, 1)
            assert not CubbyRental.objects.filter(pk=old_unpaid.pk).exists()
            assert CubbyRental.objects.get(pk=old_paid.pk).status == Status.ACTIVE
            assert CubbyRental.objects.get(pk=old_unknown.pk).status == Status.PENDING_PAYMENT
            assert CubbyRental.objects.get(pk=fresh.pk).status == Status.PENDING_PAYMENT
            mock_session.assert_any_call(session_id="cs_unpaid")
            assert all(call.kwargs["session_id"] != "cs_fresh" for call in mock_session.call_args_list)

    def describe_release_expired_hold():
        def it_deletes_the_hold_its_checkout_held_and_nothing_else():
            hold = _hold(_cubby(), _member())
            active = CubbyRentalFactory()
            cubby_rentals.release_expired_hold(
                {"metadata": {"kind": "equipment_reservation", "rental_pk": str(hold.pk)}}
            )
            assert CubbyRental.objects.filter(pk=hold.pk).exists()
            cubby_rentals.release_expired_hold({"metadata": {"kind": "cubby_rental", "rental_pk": str(active.pk)}})
            cubby_rentals.release_expired_hold({"metadata": {"kind": "cubby_rental", "rental_pk": str(hold.pk)}})
            assert list(CubbyRental.objects.all()) == [active]

    def describe_complete_checkout():
        def it_ignores_another_kind_and_a_session_without_a_subscription():
            assert cubby_rentals.complete_checkout({"metadata": {"kind": "class_registration"}}) == "ignored"
            assert cubby_rentals.complete_checkout({"id": "cs_x", "metadata": {"kind": "cubby_rental"}}) == "ignored"

        @patch("billing.stripe_utils.retrieve_subscription")
        def it_settles_the_subscription_by_id_or_expanded(mock_sub):
            hold = _hold(_cubby(), _member())
            mock_sub.return_value = _snapshot(hold)
            meta = {"kind": "cubby_rental", "rental_pk": str(hold.pk)}
            assert cubby_rentals.complete_checkout({"metadata": meta, "subscription": {"id": "sub_e"}}) == "settled"
            mock_sub.assert_called_once_with(subscription_id="sub_e")


def describe_end_for_deleted_account():
    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.cancel_subscription")
    def it_cancels_every_billing_rental_now_and_releases_holds_telling_nobody(mock_cancel, mock_expire):
        member = _member()
        active = CubbyRentalFactory(member=member, cubby=_cubby("A1"))
        past_due = CubbyRentalFactory(member=member, cubby=_cubby("A2"), status=Status.PAST_DUE)
        no_sub = CubbyRentalFactory(member=member, cubby=_cubby("A4"), stripe_subscription_id=None)
        hold = _hold(_cubby("A3"), member)
        ended = CubbyRentalFactory(member=member, cubby=_cubby("A5"), status=Status.ENDED)

        cubby_rentals.end_for_deleted_account(member)

        assert sorted(c.kwargs["subscription_id"] for c in mock_cancel.call_args_list) == sorted(
            [active.stripe_subscription_id, past_due.stripe_subscription_id]
        )
        for rental in (active, past_due, no_sub):
            rental.refresh_from_db()
            assert (rental.status, rental.end_date) == (Status.ENDED, timezone.localdate())
        assert not CubbyRental.objects.filter(pk=hold.pk).exists()
        mock_expire.assert_called_once_with(session_id="cs_cub_1")
        assert CubbyRental.objects.get(pk=ended.pk).end_date is None
        assert mail.outbox == []


def describe_emails():
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_sends_the_rented_email_with_manage_payment_and_the_thanks(mock_sub):
        payee = _member("hazelgrove")
        hold = _hold(_cubby(payee=payee, size="12 x 12 x 16 in"), _member())
        mock_sub.return_value = _snapshot(hold)
        cubby_rentals.settle_subscription(f"sub_{hold.pk}")
        message = mail.outbox[-1]
        assert message.to == ["quillon@example.com"]
        assert "Next charge Fri, Nov 27, then the 27th each month" in message.body
        assert "Thank you for paying $7 over the minimum. It goes to the guild." in message.body
        assert f"/cubbies/rentals/{hold.pk}/payment/" in message.body
        assert "billing history" not in message.body
        html = message.alternatives[0][0]
        assert "See Your Cubby" in html and "Manage Payment" in html
        assert "Past Lives Makerspace" in html

    def it_sends_the_ended_and_refunded_emails():
        rental = CubbyRentalFactory(cubby=_cubby(), member=_member())
        cubby_rentals.notify_ended(rental)
        cubby_rentals.notify_refunded(rental, 2200)
        assert _subjects() == ["Your rental of B3 has ended", "You've been refunded for B3"]
        assert "refunded your $22" in mail.outbox[1].body


def describe_edges():
    @patch("billing.stripe_utils.expire_checkout_session")
    def it_releases_a_deleted_accounts_hold_that_never_got_a_checkout(mock_expire):
        member = _member()
        _hold(_cubby(), member, stripe_checkout_session_id=None)
        cubby_rentals.end_for_deleted_account(member)
        assert not CubbyRental.objects.exists()
        mock_expire.assert_not_called()

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_mirrors_a_rental_a_concurrent_delivery_just_made_again(mock_sub):
        cubby, member = _cubby(), _member()
        lost = _hold(cubby, member)
        CubbyRental.objects.filter(pk=lost.pk).delete()
        mock_sub.return_value = _snapshot(lost)

        def made_meanwhile(snapshot: dict[str, Any], rental_pk: int) -> str:
            CubbyRental.objects.create(
                pk=rental_pk, cubby=cubby, member=member, amount_cents=2200, status=Status.PENDING_PAYMENT
            )
            return "exists"

        with patch.object(cubby_rentals, "_recover_lost_hold", side_effect=made_meanwhile):
            assert cubby_rentals.settle_subscription(f"sub_{lost.pk}") == "settled"
        assert CubbyRental.objects.get(pk=lost.pk).status == Status.ACTIVE

    @patch("billing.stripe_utils.create_refund", return_value={"id": "re_c", "status": "succeeded", "amount": 2200})
    @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_c")
    @patch("billing.stripe_utils.cancel_subscription")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_refunds_without_cancelling_a_subscription_stripe_already_ended(mock_sub, mock_cancel, mock_pi, mock_refund):
        cubby, member = _cubby(), _member()
        lost = _hold(cubby, member)
        CubbyRental.objects.filter(pk=lost.pk).delete()
        CubbyRentalFactory(cubby=cubby)
        mock_sub.return_value = _snapshot(lost, status="canceled")
        assert cubby_rentals.settle_subscription(f"sub_{lost.pk}") == "refunded"
        mock_cancel.assert_not_called()
        mock_refund.assert_called_once()
