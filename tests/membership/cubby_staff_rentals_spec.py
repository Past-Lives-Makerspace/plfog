"""BDD specs for guild staff running cubby rentals (#979): membership/cubby_rentals.py's staff half.

Assign a Cubby (Comped or Paid Another Way, under the same lock as renting), End Rental (a
Stripe rental's subscription cancelled now with no proration under the row lock, an assigned one
with no Stripe call, the member told the reason), a member's Cancel Rental on an assigned rental,
the three staff notices (a new rental, Past Due through the 8 AM digest that drops a paid one, an
ended rental's "Clear A3's shelf"), the rental's staff state, and the Rentals tab's read models and
CSV row. Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.events import resolvers
from core.models import EventDelivery, Notification
from membership import cubbies, cubby_rentals
from membership.cubby_exports import RENTALS_CSV_HEADERS, guild_rentals, rental_row
from membership.cubby_rentals import AmountError, CubbyRentalError, CubbyTakenError, CubbyUnavailableError
from membership.models import Cubby, CubbyRental, Guild, Member
from tests.core.events.digest_support import digest_emails
from tests.membership.factories import (
    CubbyFactory,
    CubbyRentalFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db

Status = CubbyRental.Status
Source = CubbyRental.Source
_PERIOD_END = int(datetime(2026, 11, 27, 18, 0, tzinfo=UTC).timestamp())


def _person(username: str, name: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _guild() -> tuple[Guild, Member, Member]:
    """A guild with a lead and one staff member, both with accounts."""
    lead = _person("hollis_lead", "Hollis Fenwright")
    staff = _person("marigold_staff", "Marigold Ashcombe")
    guild = GuildFactory(name="Brindlewood Clay Guild", show_cubbies_tab=True, guild_lead=lead)
    GuildStaffMembershipFactory(guild=guild, member=staff)
    return guild, lead, staff


def _cubby(guild: Guild, label: str = "A3", **kwargs: Any) -> Cubby:
    unit = ShelfUnitFactory(guild=guild, name="Glaze Wall")
    return CubbyFactory(unit=unit, label=label, **kwargs)


def _staff_notices(event_key: str) -> list[Notification]:
    return list(Notification.objects.filter(trigger=event_key).select_related("user"))


def describe_staff_state():
    def it_reads_ended_past_due_cancelling_comped_and_active():
        guild, _lead, _staff = _guild()
        cases = [
            (dict(status=Status.ENDED), "ended", "Ended"),
            (dict(status=Status.PAST_DUE), "past_due", "Past Due"),
            (dict(cancel_at_period_end=True), "cancelling", "Cancelling"),
            (dict(source=Source.COMPED, amount_cents=0), "comped", "Comped"),
            (dict(), "active", "Active"),
        ]
        for index, (fields, state, label) in enumerate(cases):
            rental = CubbyRentalFactory(cubby=_cubby(guild, f"S{index}"), **fields)
            assert (rental.staff_state, rental.staff_state_label) == (state, label)
            assert rental.end_confirm_id == f"cubby-end-{rental.pk}"
            assert rental.end_url == f"/cubbies/rentals/{rental.pk}/end/"

    def it_words_an_assigned_cancel_as_ending_today():
        guild, _lead, _staff = _guild()
        rental = CubbyRentalFactory(cubby=_cubby(guild), source=Source.OUTSIDE, stripe_subscription_id=None)
        assert rental.is_assigned and not rental.is_comped
        assert rental.cancel_message.startswith("A3 stops being yours today")

    def it_allows_a_free_rental_only_when_comped():
        guild, _lead, _staff = _guild()
        with pytest.raises(IntegrityError), transaction.atomic():
            CubbyRentalFactory(cubby=_cubby(guild), source=Source.OUTSIDE, amount_cents=0)
        CubbyRentalFactory(cubby=_cubby(guild, "A4"), source=Source.COMPED, amount_cents=0)


def describe_assign_rental():
    def it_gives_a_cubby_comped_and_tells_the_member_and_the_other_staff():
        guild, lead, staff = _guild()
        cubby = _cubby(guild)
        renter = _person("pip_renter", "Pip Laddersmith")
        rental = cubby_rentals.assign_rental(cubby, renter, source=Source.COMPED, amount_cents=1500, actor=lead)
        assert (rental.status, rental.source, rental.amount_cents, rental.start_date) == (
            Status.ACTIVE,
            Source.COMPED,
            0,
            timezone.localdate(),
        )
        assert rental.stripe_subscription_id is None
        assert Cubby.objects.with_renter().get(pk=cubby.pk).state == Cubby.State.RENTED
        assert "A3 is yours" in [m.subject for m in mail.outbox]
        rented = next(m for m in mail.outbox if m.subject == "A3 is yours")
        assert "nothing is ever charged" in rented.body and "Manage Payment" not in rented.body
        # The lead assigned it, so only the staff member hears about the new rental.
        started = _staff_notices("cubbies.rental_started")
        assert [n.user.username for n in started] == ["marigold_staff"]
        assert started[0].title == "Pip Laddersmith has A3"
        assert started[0].url == f"/guilds/{guild.pk}/cubbies/rentals/"

    def it_records_a_paid_another_way_amount():
        guild, lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        rental = cubby_rentals.assign_rental(
            _cubby(guild), renter, source=Source.OUTSIDE, amount_cents=1800, actor=lead
        )
        assert (rental.source, rental.amount_cents) == (Source.OUTSIDE, 1800)
        rented = next(m for m in mail.outbox if m.subject == "A3 is yours")
        assert "paid to the guild directly" in rented.body

    def it_refuses_an_amount_outside_one_to_five_hundred_dollars():
        guild, lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        for cents in (50, 50001):
            with pytest.raises(AmountError, match=r"between \$1 and \$500"):
                cubby_rentals.assign_rental(
                    _cubby(guild), renter, source=Source.OUTSIDE, amount_cents=cents, actor=lead
                )

    def it_refuses_stripe_as_a_staff_source():
        guild, lead, _staff = _guild()
        with pytest.raises(ValueError):
            cubby_rentals.assign_rental(_cubby(guild), lead, source=Source.STRIPE, amount_cents=1500, actor=lead)

    def it_refuses_a_taken_held_archived_or_out_of_service_cubby():
        guild, lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        taken = _cubby(guild, "T1")
        CubbyRentalFactory(cubby=taken, member=lead)
        with pytest.raises(CubbyTakenError, match="already rented by Hollis Fenwright"):
            cubby_rentals.assign_rental(taken, renter, source=Source.COMPED, amount_cents=0, actor=lead)
        held = _cubby(guild, "H1")
        CubbyRental.objects.create(cubby=held, member=lead, amount_cents=2000, status=Status.PENDING_PAYMENT)
        with pytest.raises(CubbyTakenError, match="being paid for right now"):
            cubby_rentals.assign_rental(held, renter, source=Source.COMPED, amount_cents=0, actor=lead)
        archived = _cubby(guild, "R1", archived_at=timezone.now())
        with pytest.raises(CubbyUnavailableError, match="R1 is archived"):
            cubby_rentals.assign_rental(archived, renter, source=Source.COMPED, amount_cents=0, actor=lead)
        broken = _cubby(guild, "B1", out_of_service=True)
        with pytest.raises(CubbyUnavailableError, match="out of service"):
            cubby_rentals.assign_rental(broken, renter, source=Source.COMPED, amount_cents=0, actor=lead)

    def it_assigns_while_the_cubbies_tab_is_off():
        guild, lead, _staff = _guild()
        Guild.objects.filter(pk=guild.pk).update(show_cubbies_tab=False)
        rental = cubby_rentals.assign_rental(_cubby(guild), lead, source=Source.COMPED, amount_cents=0, actor=None)
        assert rental.is_live
        # No actor: the whole leadership hears.
        assert len(_staff_notices("cubbies.rental_started")) == 2


def describe_end_rental():
    @patch("billing.stripe_utils.cancel_subscription")
    def it_cancels_a_stripe_rental_now_and_tells_everyone_once(mock_cancel):
        guild, _lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        rental = CubbyRentalFactory(cubby=_cubby(guild), member=renter, status=Status.PAST_DUE)
        ended = cubby_rentals.end_rental(rental, reason="  Moving you to the bigger cubby.  ")
        mock_cancel.assert_called_once_with(
            subscription_id=rental.stripe_subscription_id, idempotency_key=f"cubby-end-{rental.pk}"
        )
        assert (ended.status, ended.end_date, ended.end_reason) == (
            Status.ENDED,
            timezone.localdate(),
            "Moving you to the bigger cubby.",
        )
        assert Cubby.objects.with_renter().get(pk=rental.cubby_id).state == Cubby.State.OPEN
        member_email = next(m for m in mail.outbox if m.subject == "Your rental of A3 has ended")
        assert "Guild staff said: Moving you to the bigger cubby." in member_email.body
        assert "won't be charged again" in member_email.body
        shelf = _staff_notices("cubbies.staff_rental_ended")
        assert {n.title for n in shelf} == {"Clear A3's shelf"}
        assert len(shelf) == 2
        assert "The reason given: Moving you to the bigger cubby." in shelf[0].body
        sent = len(mail.outbox)
        # Stripe's customer.subscription.deleted that follows finds it Ended and adds nothing.
        snapshot = {
            "id": rental.stripe_subscription_id,
            "status": "canceled",
            "customer": rental.stripe_customer_id,
            "metadata": {"kind": "cubby_rental", "rental_pk": str(rental.pk), "cubby_pk": str(rental.cubby_id)},
            "cancel_at_period_end": False,
            "cancel_at": None,
            "default_payment_method": "",
            "latest_invoice": "",
            "item_id": "si",
            "product_id": "prod",
            "unit_amount": rental.amount_cents,
            "current_period_end": _PERIOD_END,
        }
        with patch("billing.stripe_utils.retrieve_subscription", return_value=snapshot):
            assert cubby_rentals.settle_subscription(rental.stripe_subscription_id) == "settled"
        assert len(mail.outbox) == sent
        assert len(_staff_notices("cubbies.staff_rental_ended")) == 2

    @patch("billing.stripe_utils.cancel_subscription")
    def it_ends_an_assigned_rental_with_no_stripe_call(mock_cancel):
        guild, _lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        rental = CubbyRentalFactory(
            cubby=_cubby(guild), member=renter, source=Source.COMPED, amount_cents=0, stripe_subscription_id=None
        )
        cubby_rentals.end_rental(rental, reason="Season over.")
        mock_cancel.assert_not_called()
        member_email = next(m for m in mail.outbox if m.subject == "Your rental of A3 has ended")
        assert "won't be charged again" not in member_email.body

    def it_refuses_a_rental_that_already_ended():
        guild, _lead, _staff = _guild()
        rental = CubbyRentalFactory(cubby=_cubby(guild), status=Status.ENDED)
        with pytest.raises(CubbyRentalError, match="isn't active any more"):
            cubby_rentals.end_rental(rental, reason="Again.")

    @patch("billing.stripe_utils.cancel_subscription", side_effect=stripe.APIConnectionError("down"))
    def it_changes_nothing_when_stripe_fails(_mock_cancel):
        guild, _lead, _staff = _guild()
        rental = CubbyRentalFactory(cubby=_cubby(guild))
        with pytest.raises(stripe.APIConnectionError):
            cubby_rentals.end_rental(rental, reason="Moving.")
        rental.refresh_from_db()
        assert rental.status == Status.ACTIVE

    def it_lets_a_member_cancel_an_assigned_rental_today_but_never_keep_it():
        guild, _lead, _staff = _guild()
        rental = CubbyRentalFactory(
            cubby=_cubby(guild), source=Source.OUTSIDE, amount_cents=1500, stripe_subscription_id=None
        )
        with pytest.raises(CubbyRentalError, match="isn't ending"):
            cubby_rentals.set_cancelling(rental, cancel=False)
        ended = cubby_rentals.set_cancelling(rental, cancel=True)
        assert (ended.status, ended.end_reason) == (Status.ENDED, "")
        assert len(_staff_notices("cubbies.staff_rental_ended")) == 2


def describe_staff_notices_from_stripe():
    def _snapshot(rental: CubbyRental, status: str) -> dict[str, Any]:
        return {
            "id": rental.stripe_subscription_id,
            "status": status,
            "customer": rental.stripe_customer_id,
            "metadata": {"kind": "cubby_rental", "rental_pk": str(rental.pk), "cubby_pk": str(rental.cubby_id)},
            "cancel_at_period_end": False,
            "cancel_at": None,
            "default_payment_method": "",
            "latest_invoice": "",
            "item_id": "si",
            "product_id": "prod",
            "unit_amount": rental.amount_cents,
            "current_period_end": _PERIOD_END,
        }

    def it_tells_the_staff_about_a_new_rental_and_an_ended_one():
        guild, _lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        rental = CubbyRentalFactory(cubby=_cubby(guild), member=renter, status=Status.PENDING_PAYMENT, start_date=None)
        with patch("billing.stripe_utils.retrieve_subscription", return_value=_snapshot(rental, "active")):
            cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        started = _staff_notices("cubbies.rental_started")
        assert len(started) == 2 and started[0].title == "Pip Laddersmith has A3"
        with patch("billing.stripe_utils.retrieve_subscription", return_value=_snapshot(rental, "canceled")):
            cubby_rentals.settle_subscription(rental.stripe_subscription_id)
        assert len(_staff_notices("cubbies.staff_rental_ended")) == 2
        # Email: a new rental is bell only by default; an ended one emails at once.
        assert "Pip Laddersmith has A3" not in [m.subject for m in mail.outbox]
        assert "Clear A3's shelf" in [m.subject for m in mail.outbox]

    def it_queues_past_due_for_the_digest_and_drops_it_once_paid():
        guild, _lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        rental = CubbyRentalFactory(cubby=_cubby(guild), member=renter)
        invoice = {
            "id": "in_late_1",
            "amount_due": 2200,
            "status": "open",
            "next_payment_attempt": _PERIOD_END,
            "parent": {
                "subscription_details": {
                    "subscription": rental.stripe_subscription_id,
                    "metadata": {"kind": "cubby_rental", "rental_pk": str(rental.pk)},
                }
            },
        }
        with patch("billing.stripe_utils.retrieve_subscription", return_value=_snapshot(rental, "past_due")):
            cubby_rentals.record_payment_failed(invoice)
        notices = _staff_notices("cubbies.rental_past_due")
        assert len(notices) == 2 and notices[0].title == "A3 is past due"
        assert "A3 is past due" not in [m.subject for m in mail.outbox]
        row = EventDelivery.objects.filter(event_key="cubbies.rental_past_due", channel="digest").first()
        assert row is not None and row.request_ref == f"cubby_past_due:{rental.pk}"
        assert resolvers.open_request_urls("cubbies.rental_past_due", [row]) == {row.url}
        CubbyRental.objects.filter(pk=rental.pk).update(status=Status.ACTIVE)
        assert resolvers.open_request_urls("cubbies.rental_past_due", [row]) == set()
        assert digest_emails("hollis_lead@example.com") == []

    def it_carries_a_still_past_due_rental_in_the_morning_digest():
        guild, _lead, _staff = _guild()
        rental = CubbyRentalFactory(cubby=_cubby(guild), status=Status.PAST_DUE)
        cubby_rentals.notify_staff_past_due(rental, {"id": "in_late_2"})
        (email,) = digest_emails("hollis_lead@example.com")
        assert "A3 is past due" in email.body

    @patch("billing.stripe_utils.cancel_subscription")
    def it_asks_the_staff_to_clear_a_deleted_accounts_shelf(_mock_cancel):
        guild, _lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        CubbyRentalFactory(cubby=_cubby(guild), member=renter)
        cubby_rentals.end_for_deleted_account(renter)
        assert len(_staff_notices("cubbies.staff_rental_ended")) == 2
        assert "Your rental of A3 has ended" not in [m.subject for m in mail.outbox]


def describe_rentals_tab_read_models():
    def _fill(guild: Guild) -> dict[str, CubbyRental]:
        payee = _person("payee_person", "Tamsin Orrell")
        Guild.objects.filter(pk=guild.pk).update(cubby_payee=payee)
        guild.refresh_from_db()
        priya = _person("priya_n", "Priya Nairnwick")
        rentals = {
            "stripe": CubbyRentalFactory(cubby=_cubby(guild, "A1"), amount_cents=2000),
            "late": CubbyRentalFactory(
                cubby=_cubby(guild, "A2"),
                member=priya,
                amount_cents=1500,
                status=Status.PAST_DUE,
                next_payment_attempt=timezone.now() + timedelta(days=3),
            ),
            "ending": CubbyRentalFactory(cubby=_cubby(guild, "A3"), amount_cents=2000, cancel_at_period_end=True),
            "comped": CubbyRentalFactory(
                cubby=_cubby(guild, "B1"), source=Source.COMPED, amount_cents=0, stripe_subscription_id=None
            ),
            "outside": CubbyRentalFactory(
                cubby=_cubby(guild, "B2"), source=Source.OUTSIDE, amount_cents=1500, stripe_subscription_id=None
            ),
            "ended": CubbyRentalFactory(
                cubby=_cubby(guild, "C1"), status=Status.ENDED, end_date=timezone.localdate() - timedelta(days=4)
            ),
        }
        CubbyRental.objects.create(
            cubby=_cubby(guild, "C2"), member=payee, amount_cents=2000, status=Status.PENDING_PAYMENT
        )
        _cubby(guild, "D1")
        _cubby(guild, "D2", out_of_service=True)
        return rentals

    def it_sums_the_tiles_and_counts_the_chips():
        guild, _lead, _staff = _guild()
        _fill(guild)
        summary = cubbies.RentalsSummary.load(guild)
        # Stripe live: 2000 + 1500 + 2000 = 5500 (85% to the payee: 4675); Paid Another Way 1500 all to the guild.
        assert (summary.monthly_cents, summary.to_guild_cents) == (7000, 6175)
        assert (summary.monthly_display, summary.to_guild_display) == ("$70", "$61.75")
        # Open: C1 (its rental ended) and D1; A1 to B2 rented, C2 held, D2 out of service.
        assert (summary.open_count, summary.cubby_count) == (2, 9)
        assert (summary.rented_count, summary.rented_sub) == (5, "1 comped, 1 paid another way, 1 ending")
        assert summary.past_due_sub == "Priya Nairnwick, A2"
        assert summary.filters == [
            ("current", "Current", 5),
            ("past_due", "Past Due", 1),
            ("ended", "Ended", 1),
            ("all", "All", 6),
        ]

    def it_words_the_tiles_when_there_is_little_to_say():
        guild, _lead, _staff = _guild()
        empty = cubbies.RentalsSummary.load(guild)
        assert (empty.rented_sub, empty.past_due_sub, empty.to_guild_cents) == (
            "None yet",
            "Every card went through",
            0,
        )
        CubbyRentalFactory(cubby=_cubby(guild, "A1"))
        CubbyRentalFactory(cubby=_cubby(guild, "A2"), status=Status.PAST_DUE)
        CubbyRentalFactory(cubby=_cubby(guild, "A3"), status=Status.PAST_DUE)
        summary = cubbies.RentalsSummary.load(guild)
        assert summary.rented_sub == "All paid by card"
        assert summary.past_due_sub.endswith("and 1 more")
        assert summary.to_guild_cents == 0  # no payee picked: the Stripe money stays with Past Lives

    def it_filters_the_table_by_chip():
        guild, _lead, _staff = _guild()
        rentals = _fill(guild)
        labels = lambda show: sorted(r.cubby.label for r in cubbies.staff_rentals(guild, show))  # noqa: E731
        assert labels("current") == ["A1", "A2", "A3", "B1", "B2"]
        assert labels("past_due") == ["A2"]
        assert labels("ended") == ["C1"]
        assert labels("all") == ["A1", "A2", "A3", "B1", "B2", "C1"]
        assert cubbies.rental_filter("nonsense") == "current"
        assert rentals["stripe"].pk in {r.pk for r in cubbies.staff_rentals(guild, "")}

    def it_words_each_row_for_the_table_and_its_card():
        guild, _lead, _staff = _guild()
        rentals = _fill(guild)
        rows = {key: cubbies.RentalRow.build(rental) for key, rental in rentals.items()}
        assert rows["stripe"].next_note == "" and rows["stripe"].card_label == "Next Charge"
        assert rows["stripe"].amount == "$20/mo"
        assert (rows["late"].next_main, rows["late"].card_label) == ("Retrying", "Next Try")
        assert rows["late"].next_note.startswith("Next try ")
        assert rows["late"].initials == "PN"
        assert rows["late"].email == "priya_n@example.com"
        assert (rows["ending"].next_main, rows["ending"].card_label) == ("None", "Ends")
        assert (rows["comped"].amount, rows["comped"].next_note) == ("$0", "Never charged")
        assert (rows["outside"].amount_note, rows["outside"].card_value) == ("Paid another way", "None")
        assert (rows["ended"].next_main, rows["ended"].card_label) == ("None", "Ended")
        assert rows["ending"].end_message.endswith("check the shelf is clear first.")
        assert "was already cancelling on" in rows["ending"].end_message
        assert "Nothing is refunded." in rows["stripe"].end_message
        assert "stops being charged" not in rows["comped"].end_message
        CubbyRental.objects.filter(pk=rentals["late"].pk).update(next_payment_attempt=None)
        rentals["late"].refresh_from_db()
        assert cubbies.RentalRow.build(rentals["late"]).next_note == "No more tries"

    def it_takes_initials_from_any_name():
        assert (cubbies.initials("Priya Nair"), cubbies.initials("Cher"), cubbies.initials("")) == ("PN", "C", "")


def describe_the_csv_row():
    def it_is_the_flat_row_a_later_airtable_sync_would_send():
        guild, _lead, _staff = _guild()
        renter = _person("pip_renter", "Pip Laddersmith")
        rental = CubbyRentalFactory(
            cubby=_cubby(guild),
            member=renter,
            source=Source.OUTSIDE,
            amount_cents=1550,
            status=Status.ENDED,
            end_date=timezone.localdate(),
        )
        row = rental_row(rental)
        assert list(row) == RENTALS_CSV_HEADERS
        assert row == {
            "Member": "Pip Laddersmith",
            "Cubby": "A3",
            "Unit": "Glaze Wall",
            "Amount": "15.50",
            "Start": rental.start_date.isoformat(),
            "End": timezone.localdate().isoformat(),
            "Status": "Ended",
            "Source": "Paid Another Way",
        }
        CubbyRental.objects.create(
            cubby=_cubby(guild, "Z9"), member=renter, amount_cents=2000, status=Status.PENDING_PAYMENT
        )
        assert [r.cubby.label for r in guild_rentals(guild)] == ["A3"]
