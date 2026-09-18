"""BDD specs for one signup per person per class (#370 item 1).

Two POSTs of the same registration used to create two rows, and each row ate a
seat. These specs pin the three layers that now stop that: the database
constraint, the view's resume-the-in-flight-checkout guard, and the handled
outcome when two POSTs genuinely race each other to the insert.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    DiscountCodeFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db

CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_test_first"
SECOND_CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_test_second"


@pytest.fixture
def paid_offering(db):
    offering = ClassOfferingFactory(
        title="Forge Basics",
        slug="forge-basics-dupe",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=10000,
        member_discount_pct=0,
        capacity=4,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    return offering


@pytest.fixture
def free_offering(db):
    offering = ClassOfferingFactory(
        title="Free Demo",
        slug="free-demo-dupe",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=0,
        member_discount_pct=0,
        capacity=4,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=3),
        ends_at=timezone.now() + timedelta(days=3, hours=2),
    )
    return offering


def _post_data(**overrides):
    data = {
        "first_name": "Sam",
        "last_name": "Smith",
        "pronouns": "",
        "email": "sam@example.com",
        "phone": "",
        "prior_experience": "",
        "looking_for": "",
        "discount_code": "",
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }
    data.update(overrides)
    return data


def _session(
    *,
    session_id: str = "cs_test_first",
    url: str = CHECKOUT_URL,
    status: str = "open",
    amount_total: int = 10000,
) -> dict:
    """A ``retrieve_checkout_session`` payload shaped like billing.stripe_utils returns."""
    return {
        "id": session_id,
        "url": url,
        "status": status,
        "payment_status": "unpaid",
        "payment_intent": "",
        "amount_total": amount_total,
    }


def _register_url(offering: ClassOffering) -> str:
    return reverse("classes:register", kwargs={"slug": offering.slug})


def _owned_by(client, registration: Registration) -> Registration:
    """Tell the test client's session it created this row.

    Resuming a signup requires proof of ownership, and a factory-built row has no
    browser behind it. Real flows record this when the row is created; a spec that
    starts from a factory row says so here instead.
    """
    session = client.session
    session["classes_registration_pks"] = [registration.pk]
    session.save()
    return registration


def describe_duplicate_registration():
    def describe_the_paid_branch():
        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session())
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_creates_one_row_for_two_identical_posts(mock_create, _mock_retrieve, paid_offering, client):
            mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}

            client.post(_register_url(paid_offering), data=_post_data())
            client.post(_register_url(paid_offering), data=_post_data())

            assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session())
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_consumes_one_seat_for_three_attempts(mock_create, _mock_retrieve, paid_offering, client):
            mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}

            for _ in range(3):
                client.post(_register_url(paid_offering), data=_post_data())

            paid_offering.refresh_from_db()
            assert paid_offering.spots_remaining == 3

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session())
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_redirects_the_second_post_to_the_same_checkout_url(mock_create, mock_retrieve, paid_offering, client):
            mock_create.side_effect = [
                {"id": "cs_test_first", "url": CHECKOUT_URL},
                {"id": "cs_test_second", "url": SECOND_CHECKOUT_URL},
            ]

            first = client.post(_register_url(paid_offering), data=_post_data())
            second = client.post(_register_url(paid_offering), data=_post_data())

            assert first.url == CHECKOUT_URL
            assert second.url == CHECKOUT_URL
            assert mock_create.call_count == 1
            assert mock_retrieve.call_args.kwargs["session_id"] == "cs_test_first"

        @patch("billing.stripe_utils.expire_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session(status="expired"))
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_mints_a_fresh_session_when_the_stored_one_expired(
            mock_create, _mock_retrieve, mock_expire, paid_offering, client
        ):
            """Coming back the next day must not be a dead end.

            Nothing reaps a PENDING row yet and the seat constraint forbids a second
            one, so reusing an expired session (or parking them on a self-serve page
            with no pay link) would leave a seat that can never be paid for or rebooked.
            """
            mock_create.side_effect = [
                {"id": "cs_test_first", "url": CHECKOUT_URL},
                {"id": "cs_test_second", "url": SECOND_CHECKOUT_URL},
            ]
            client.post(_register_url(paid_offering), data=_post_data())

            response = client.post(_register_url(paid_offering), data=_post_data())

            assert response.status_code == 302
            assert response.url == SECOND_CHECKOUT_URL
            assert mock_create.call_count == 2
            assert mock_expire.call_count == 0  # nothing to expire; Stripe already closed it
            rows = Registration.objects.filter(class_offering=paid_offering, email="sam@example.com")
            assert rows.count() == 1
            assert rows.get().stripe_session_id == "cs_test_second"
            assert rows.get().status == Registration.Status.PENDING

        @patch("billing.stripe_utils.expire_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session())
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_reprices_the_session_when_a_discount_code_arrives_on_the_second_try(
            mock_create, _mock_retrieve, mock_expire, paid_offering, client
        ):
            """A code typed on the second attempt must not be charged at the first attempt's price.

            The stored session is a $100 page. Reusing it because it happens to be open
            would take $100 from someone who just typed a valid half-price code and saw
            no error.
            """
            DiscountCodeFactory(code="HALF", discount_pct=50)
            mock_create.side_effect = [
                {"id": "cs_test_first", "url": CHECKOUT_URL},
                {"id": "cs_test_second", "url": SECOND_CHECKOUT_URL},
            ]
            client.post(_register_url(paid_offering), data=_post_data())

            response = client.post(_register_url(paid_offering), data=_post_data(discount_code="HALF"))

            assert response.url == SECOND_CHECKOUT_URL
            assert mock_create.call_count == 2
            assert mock_create.call_args.kwargs["amount_cents"] == 5000
            assert mock_expire.call_args.kwargs["session_id"] == "cs_test_first"  # old page killed first
            row = Registration.objects.get(class_offering=paid_offering, email="sam@example.com")
            assert row.discount_code is not None and row.discount_code.code == "HALF"
            assert row.amount_paid_cents == 5000

        @patch("billing.stripe_utils.expire_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session())
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_reprices_the_session_when_a_sale_starts_between_visits(
            mock_create, _mock_retrieve, mock_expire, paid_offering, client
        ):
            """``sale_enabled`` is an instant admin toggle and a session lives a day."""
            mock_create.side_effect = [
                {"id": "cs_test_first", "url": CHECKOUT_URL},
                {"id": "cs_test_second", "url": SECOND_CHECKOUT_URL},
            ]
            client.post(_register_url(paid_offering), data=_post_data())
            paid_offering.sale_enabled = True
            paid_offering.sale_kind = ClassOffering.SaleKind.PERCENT
            paid_offering.sale_percent = 60
            paid_offering.save(update_fields=["sale_enabled", "sale_kind", "sale_percent"])

            response = client.post(_register_url(paid_offering), data=_post_data())

            assert response.url == SECOND_CHECKOUT_URL
            assert mock_create.call_args.kwargs["amount_cents"] == 4000
            assert mock_expire.call_count == 1

        @patch("billing.stripe_utils.expire_checkout_session")
        @patch(
            "billing.stripe_utils.retrieve_checkout_session",
            return_value=_session(status="complete") | {"payment_status": "paid"},
        )
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_never_mints_a_second_session_over_a_payment_stripe_already_took(
            mock_create, _mock_retrieve, mock_expire, paid_offering, client
        ):
            """Paid but not yet confirmed means the webhook is behind, not that nothing happened."""
            mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}
            client.post(_register_url(paid_offering), data=_post_data())

            response = client.post(_register_url(paid_offering), data=_post_data())

            row = Registration.objects.get(class_offering=paid_offering, email="sam@example.com")
            assert response.status_code == 302
            assert response.url == (
                reverse("classes:register_success", kwargs={"slug": paid_offering.slug})
                + f"?reg={row.self_serve_token}"
            )
            assert mock_create.call_count == 1
            assert mock_expire.call_count == 0

        @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("stripe down"))
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_does_not_duplicate_when_stripe_is_unreachable(mock_create, _mock_retrieve, paid_offering, client):
            mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}
            client.post(_register_url(paid_offering), data=_post_data())

            response = client.post(_register_url(paid_offering), data=_post_data())

            assert response.status_code == 302
            assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1

        def it_sends_an_already_confirmed_registrant_back_to_their_registration(paid_offering, client):
            existing = _owned_by(
                client,
                RegistrationFactory(
                    class_offering=paid_offering,
                    email="sam@example.com",
                    status=Registration.Status.CONFIRMED,
                ),
            )

            response = client.post(_register_url(paid_offering), data=_post_data())

            assert response.status_code == 302
            assert response.url == reverse("classes:my_registration", kwargs={"token": existing.self_serve_token})
            assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1

        def it_creates_one_row_for_two_posts_on_a_free_class(free_offering, client):
            client.post(_register_url(free_offering), data=_post_data())
            client.post(_register_url(free_offering), data=_post_data())

            assert Registration.objects.filter(class_offering=free_offering, email="sam@example.com").count() == 1

    def describe_the_waitlist_branch():
        @pytest.fixture
        def sold_out(paid_offering):
            for _ in range(paid_offering.capacity):
                RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
            return paid_offering

        def it_creates_one_waitlist_row_for_two_posts(sold_out, client):
            client.post(_register_url(sold_out), data=_post_data())
            client.post(_register_url(sold_out), data=_post_data())

            assert Registration.objects.filter(class_offering=sold_out, email="sam@example.com").count() == 1

        def it_returns_the_registrant_to_the_waitlist_row_they_already_have(sold_out, client):
            client.post(_register_url(sold_out), data=_post_data())
            existing = Registration.objects.get(class_offering=sold_out, email="sam@example.com")

            response = client.post(_register_url(sold_out), data=_post_data())

            assert response.status_code == 302
            assert response.url == reverse("classes:my_registration", kwargs={"token": existing.self_serve_token})
            assert existing.status == Registration.Status.WAITLISTED

    def describe_after_a_cancellation():
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_lets_a_cancelled_registrant_register_again(mock_create, paid_offering, client):
            mock_create.side_effect = [
                {"id": "cs_test_first", "url": CHECKOUT_URL},
                {"id": "cs_test_second", "url": SECOND_CHECKOUT_URL},
            ]
            client.post(_register_url(paid_offering), data=_post_data())
            first = Registration.objects.get(class_offering=paid_offering, email="sam@example.com")
            first.status = Registration.Status.CANCELLED
            first.save(update_fields=["status"])

            response = client.post(_register_url(paid_offering), data=_post_data())

            assert response.url == SECOND_CHECKOUT_URL
            assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 2

    def describe_when_two_posts_race_the_insert():
        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session())
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_resumes_the_row_that_won_instead_of_raising(mock_create, _mock_retrieve, paid_offering, client):
            """The guard cannot see a row the other request has not committed yet.

            The lookup answering ``None`` before the insert and the winning row
            after it is exactly that window, reproduced without threads: the
            insert hits the constraint, which is what the losing request of a real
            race hits.
            """
            mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}
            winner = _owned_by(
                client,
                RegistrationFactory(
                    class_offering=paid_offering,
                    email="sam@example.com",
                    status=Registration.Status.PENDING,
                    stripe_session_id="cs_test_first",
                ),
            )

            # Patching a model method, deliberately and narrowly: the standing rule is
            # that the ORM is never mocked, and it is not mocked here. The database, the
            # rows and the constraint are all real, and the constraint is what the
            # assertion rests on. The patch only blinds ONE lookup for one call, which is
            # the interleaving a single-threaded test cannot otherwise produce.
            with patch.object(ClassOffering, "live_registration_for_email", side_effect=[None, winner]):
                response = client.post(_register_url(paid_offering), data=_post_data())

            assert response.status_code == 302
            assert response.url == CHECKOUT_URL
            assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1
            assert Registration.objects.get(pk=winner.pk).status == Registration.Status.PENDING


def describe_registration_seat_uniqueness_constraint():
    def it_rejects_a_second_seat_holding_row_for_the_same_email(paid_offering):
        RegistrationFactory(class_offering=paid_offering, email="sam@example.com", status=Registration.Status.CONFIRMED)
        with pytest.raises(IntegrityError), transaction.atomic():
            RegistrationFactory(
                class_offering=paid_offering, email="sam@example.com", status=Registration.Status.PENDING
            )

    def it_rejects_a_waitlist_row_alongside_a_live_registration(paid_offering):
        RegistrationFactory(class_offering=paid_offering, email="sam@example.com", status=Registration.Status.PENDING)
        with pytest.raises(IntegrityError), transaction.atomic():
            RegistrationFactory(
                class_offering=paid_offering, email="sam@example.com", status=Registration.Status.WAITLISTED
            )

    def it_allows_a_second_row_once_the_first_is_cancelled(paid_offering):
        first = RegistrationFactory(
            class_offering=paid_offering, email="sam@example.com", status=Registration.Status.PENDING
        )
        first.status = Registration.Status.CANCELLED
        first.save(update_fields=["status"])

        second = RegistrationFactory(
            class_offering=paid_offering, email="sam@example.com", status=Registration.Status.PENDING
        )

        assert second.pk != first.pk

    def it_allows_a_second_row_once_the_first_is_refunded(paid_offering):
        first = RegistrationFactory(
            class_offering=paid_offering, email="sam@example.com", status=Registration.Status.CONFIRMED
        )
        first.status = Registration.Status.REFUNDED
        first.save(update_fields=["status"])

        second = RegistrationFactory(
            class_offering=paid_offering, email="sam@example.com", status=Registration.Status.CONFIRMED
        )

        assert second.pk != first.pk

    def it_allows_the_same_email_on_a_different_class(paid_offering, free_offering):
        RegistrationFactory(class_offering=paid_offering, email="sam@example.com")
        second = RegistrationFactory(class_offering=free_offering, email="sam@example.com")

        assert second.pk is not None


def describe_moving_a_registration_to_a_class_the_person_is_already_in():
    def it_refuses_the_move_instead_of_hitting_the_constraint(paid_offering, free_offering):
        here = RegistrationFactory(
            class_offering=paid_offering, email="sam@example.com", status=Registration.Status.CONFIRMED
        )
        RegistrationFactory(class_offering=free_offering, email="sam@example.com", status=Registration.Status.PENDING)

        with pytest.raises(ValueError, match="already has a signup"):
            here.move_to(free_offering)

        here.refresh_from_db()
        assert here.class_offering_id == paid_offering.pk

    def it_still_moves_a_cancelled_row_because_it_holds_no_seat(paid_offering, free_offering):
        cancelled = RegistrationFactory(
            class_offering=paid_offering, email="sam@example.com", status=Registration.Status.CANCELLED
        )
        RegistrationFactory(class_offering=free_offering, email="sam@example.com", status=Registration.Status.PENDING)

        cancelled.move_to(free_offering)

        cancelled.refresh_from_db()
        assert cancelled.class_offering_id == free_offering.pk

    def it_turns_a_losing_race_into_the_same_sentence(paid_offering, free_offering):
        """Two staff moving at once, or a move racing a public signup into the target.

        The pre-check is a read, so both can pass it; the constraint is what actually
        decides. The loser must get the sentence the reader got, never a raw 500.
        """
        here = RegistrationFactory(
            class_offering=paid_offering, email="sam@example.com", status=Registration.Status.CONFIRMED
        )
        RegistrationFactory(class_offering=free_offering, email="sam@example.com", status=Registration.Status.PENDING)

        # Same deliberate, narrow patch as the register race above: real database, real
        # constraint, and the patch exists only to open the window between the check and
        # the write that two concurrent movers would open for themselves.
        with patch.object(ClassOffering, "live_registration_for_email", return_value=None):
            with pytest.raises(ValueError, match="already has a signup"):
                here.move_to(free_offering)

        here.refresh_from_db()
        assert here.class_offering_id == paid_offering.pk  # rolled back to where it was

    def it_tells_the_instructor_why_rather_than_erroring(db, client):
        user = UserFactory(username="mover@example.com")
        instructor = InstructorFactory(user=user, instructor_slug="mover")
        client.force_login(user)
        source = ClassOfferingFactory(
            slug="dupe-move-src", instructor=instructor, status=ClassOffering.Status.PUBLISHED, capacity=4
        )
        target = ClassOfferingFactory(
            slug="dupe-move-dst", instructor=instructor, status=ClassOffering.Status.PUBLISHED, capacity=4
        )
        ClassSessionFactory(class_offering=target, starts_at=timezone.now() + timedelta(days=7))
        here = RegistrationFactory(class_offering=source, email="sam@example.com", status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=target, email="sam@example.com", status=Registration.Status.PENDING)

        response = client.post(reverse("classes:registration_move", args=[here.pk]), {"target": target.pk})

        assert response.status_code == 302
        assert [m.message for m in get_messages(response.wsgi_request)] == [
            f"Test User already has a signup for {target.title}."
        ]
        here.refresh_from_db()
        assert here.class_offering_id == source.pk


def describe_the_registration_form_page():
    def it_marks_the_form_for_the_submit_guard(paid_offering, client):
        """``data-submit-guard`` is the contract between the form and the script.

        Asserted on the attribute rather than on the script's source, so reformatting
        the script does not fail a test about behaviour. The script keys off the same
        attribute for both halves of the guard: disabling on submit, and re-enabling
        on the bfcache restore that browser Back from Stripe produces.
        """
        body = client.get(_register_url(paid_offering)).content.decode()

        assert "data-submit-guard" in body
        assert body.count("data-submit-guard") >= 2  # the form, and the selector that resets it


def describe_resuming_on_a_class_with_no_seats_left():
    """The single most likely path: an impatient second click on the last seat.

    A pending row consumes a seat, so the registrant's OWN signup takes
    ``spots_remaining`` to 0. If that is read as waitlist intent, the form drops the
    discount field, the resumed price comes back undiscounted, and the resume logic
    reads its own blindness as a price rise: it expires the cheap session and charges
    full freight with the code wiped off the row. Every other resume spec in this file
    has spare capacity, so none of them can see it.
    """

    @pytest.fixture
    def last_seat_offering(db):
        offering = ClassOfferingFactory(
            title="Last Seat",
            slug="last-seat-dupe",
            category=CategoryFactory(),
            instructor=InstructorFactory(),
            status=ClassOffering.Status.PUBLISHED,
            price_cents=10000,
            member_discount_pct=0,
            capacity=1,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )
        return offering

    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session(amount_total=5000))
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_keeps_the_discount_and_the_session_on_a_second_click(
        mock_create, _mock_retrieve, mock_expire, last_seat_offering, client
    ):
        DiscountCodeFactory(code="HALF", discount_pct=50)
        mock_create.side_effect = [
            {"id": "cs_test_first", "url": CHECKOUT_URL},
            {"id": "cs_test_second", "url": SECOND_CHECKOUT_URL},
        ]
        first = client.post(_register_url(last_seat_offering), data=_post_data(discount_code="HALF"))
        assert first.url == CHECKOUT_URL
        assert mock_create.call_args.kwargs["amount_cents"] == 5000
        last_seat_offering.refresh_from_db()
        assert last_seat_offering.spots_remaining == 0  # their own pending row filled it

        second = client.post(_register_url(last_seat_offering), data=_post_data(discount_code="HALF"))

        assert second.url == CHECKOUT_URL  # the same $50 page, not a new one
        assert mock_create.call_count == 1
        assert mock_expire.call_count == 0  # the good session is left alone
        row = Registration.objects.get(class_offering=last_seat_offering, email="sam@example.com")
        assert row.discount_code is not None and row.discount_code.code == "HALF"
        assert row.amount_paid_cents == 5000
        assert row.stripe_session_id == "cs_test_first"

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session(amount_total=5000))
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_does_not_tell_them_the_class_is_sold_out_by_their_own_seat(
        mock_create, _mock_retrieve, last_seat_offering, client
    ):
        """``clean`` refuses a sold-out class. It is not sold out to the person in it."""
        DiscountCodeFactory(code="HALF", discount_pct=50)
        mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}
        client.post(_register_url(last_seat_offering), data=_post_data(discount_code="HALF"))

        second = client.post(_register_url(last_seat_offering), data=_post_data(discount_code="HALF"))

        assert second.status_code == 302  # resumed, not re-rendered with an error
        assert b"sold out" not in second.content

    def it_still_offers_the_waitlist_to_somebody_else(last_seat_offering, client):
        """The seats-full path is unchanged for a registrant who holds nothing."""
        RegistrationFactory(class_offering=last_seat_offering, status=Registration.Status.CONFIRMED)

        response = client.post(_register_url(last_seat_offering), data=_post_data(email="other@example.com"))

        assert response.status_code == 302
        row = Registration.objects.get(class_offering=last_seat_offering, email="other@example.com")
        assert row.status == Registration.Status.WAITLISTED


def describe_claiming_a_waitlist_spot():
    """The claim link is how a promoted waitlister takes the seat that opened.

    ``send_waitlist_spot_available`` mails ``/register/?waitlist_token=<token>`` and the
    form posts back to that URL. The signup guard must read that as "convert the row
    that was waiting", not as "this person already has a signup, bounce them" — they
    were just told a spot is theirs and the claim window runs out.
    """

    @pytest.fixture
    def waiting(paid_offering):
        return RegistrationFactory(
            class_offering=paid_offering,
            first_name="Sam",
            last_name="Smith",
            email="sam@example.com",
            status=Registration.Status.WAITLISTED,
        )

    def _claim_url(offering, registration) -> str:
        return f"{_register_url(offering)}?waitlist_token={registration.self_serve_token}"

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_converts_the_waiting_row_into_a_checkout(mock_create, paid_offering, waiting, client):
        mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}

        response = client.post(_claim_url(paid_offering, waiting), data=_post_data())

        assert response.status_code == 302
        assert response.url == CHECKOUT_URL
        assert mock_create.call_count == 1
        waiting.refresh_from_db()
        assert waiting.status == Registration.Status.PENDING  # holds the seat through checkout
        assert waiting.stripe_session_id == "cs_test_first"
        assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_does_not_bounce_the_claimant_with_the_already_waitlisted_note(mock_create, paid_offering, waiting, client):
        mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}

        response = client.post(_claim_url(paid_offering, waiting), data=_post_data())

        assert [m.message for m in get_messages(response.wsgi_request)] == []

    def it_confirms_a_claim_on_a_free_class_outright(free_offering, client):
        waiting = RegistrationFactory(
            class_offering=free_offering,
            first_name="Sam",
            last_name="Smith",
            email="sam@example.com",
            status=Registration.Status.WAITLISTED,
        )

        response = client.post(_claim_url(free_offering, waiting), data=_post_data())

        assert response.url == reverse("classes:register_success", kwargs={"slug": free_offering.slug})
        waiting.refresh_from_db()
        assert waiting.status == Registration.Status.CONFIRMED
        assert Registration.objects.filter(class_offering=free_offering, email="sam@example.com").count() == 1

    def it_says_the_class_refilled_rather_than_overfilling_it(paid_offering, waiting, client):
        """Too late is an honest answer. Converting anyway would seat one person over capacity."""
        for _ in range(paid_offering.capacity):
            RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)

        response = client.post(_claim_url(paid_offering, waiting), data=_post_data())

        assert response.status_code == 200
        assert b"sold out" in response.content
        waiting.refresh_from_db()
        assert waiting.status == Registration.Status.WAITLISTED

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session())
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_ignores_a_token_that_belongs_to_a_row_which_is_not_waiting(
        mock_create, _mock_retrieve, paid_offering, client
    ):
        """A stale claim link on a live signup converts nothing.

        ``_stale_claim_link_redirect`` already owns this case and answers it before the
        signup guard is reached: a claim token on a CONFIRMED or PENDING row means the
        person is in the class, so they go to their self-serve page. Pinned here because
        the claim conversion must not start reaching past it.
        """
        mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}
        client.post(_register_url(paid_offering), data=_post_data())
        live = Registration.objects.get(class_offering=paid_offering, email="sam@example.com")

        response = client.post(
            f"{_register_url(paid_offering)}?waitlist_token={live.self_serve_token}", data=_post_data()
        )

        assert response.url == reverse("classes:my_registration", kwargs={"token": live.self_serve_token})
        assert mock_create.call_count == 1  # no second session minted
        live.refresh_from_db()
        assert live.status == Registration.Status.PENDING
        assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1


def describe_a_waitlist_claim_that_stripe_refuses():
    def it_puts_the_claim_back_in_the_queue(paid_offering, client):
        """The row is flipped to PENDING before checkout, so a refusal must undo that.

        Leaving it PENDING would hold a seat nobody paid for, and nothing reaps a pending
        row yet. Back on the waitlist, the claim link still works and the next click can
        try again.
        """
        waiting = RegistrationFactory(
            class_offering=paid_offering,
            first_name="Sam",
            last_name="Smith",
            email="sam@example.com",
            status=Registration.Status.WAITLISTED,
        )
        url = f"{_register_url(paid_offering)}?waitlist_token={waiting.self_serve_token}"

        with patch("billing.stripe_utils.create_class_checkout_session", side_effect=RuntimeError("stripe down")):
            with pytest.raises(RuntimeError):
                client.post(url, data=_post_data())

        waiting.refresh_from_db()
        assert waiting.status == Registration.Status.WAITLISTED
        assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1


def describe_a_stranger_who_knows_the_address():
    """The register form authenticates nobody: the email is typed, not proved.

    So resuming had to stop trusting it. A stranger who POSTs a known address must get
    no token, cause no Stripe call, and leave the row exactly as they found it.
    """

    @pytest.fixture
    def victim(paid_offering, client):
        """A real signup made by a DIFFERENT browser, with a discount on it."""
        DiscountCodeFactory(code="HALF", discount_pct=50)
        with patch("billing.stripe_utils.create_class_checkout_session") as mock_create:
            mock_create.return_value = {"id": "cs_ONE", "url": CHECKOUT_URL}
            client.post(_register_url(paid_offering), data=_post_data(discount_code="HALF"))
        client.logout()
        client.cookies.clear()  # the attacker is a different browser entirely
        return Registration.objects.get(class_offering=paid_offering, email="sam@example.com")

    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session")
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_never_hands_over_the_self_serve_token(
        mock_create, mock_retrieve, mock_expire, paid_offering, victim, client
    ):
        response = client.post(_register_url(paid_offering), data=_post_data())

        assert response.status_code == 302
        assert victim.self_serve_token not in response["Location"]
        assert response["Location"] == reverse("classes:public_class_detail", kwargs={"slug": paid_offering.slug})
        assert mock_retrieve.call_count == 0
        assert mock_expire.call_count == 0
        assert mock_create.call_count == 0

    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session(amount_total=5000))
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_leaves_the_row_untouched(mock_create, _mock_retrieve, mock_expire, paid_offering, victim, client):
        """Status, amount, code and session all exactly as the victim left them."""
        before = (victim.status, victim.amount_paid_cents, victim.discount_code_id, victim.stripe_session_id)

        client.post(_register_url(paid_offering), data=_post_data())
        client.post(_register_url(paid_offering), data=_post_data(discount_code=""))

        victim.refresh_from_db()
        assert (victim.status, victim.amount_paid_cents, victim.discount_code_id, victim.stripe_session_id) == before
        assert mock_expire.call_count == 0  # the victim's checkout is not killed mid-payment
        assert mock_create.call_count == 0  # and not repriced against them
        assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1

    def it_emails_the_link_to_the_address_on_file_instead(paid_offering, victim, client, mailoutbox):
        from django.core.cache import cache

        cache.clear()
        mailoutbox.clear()

        client.post(_register_url(paid_offering), data=_post_data())

        assert len(mailoutbox) == 1
        sent = mailoutbox[0]
        assert sent.to == ["sam@example.com"]  # never the submitter's screen
        assert victim.self_serve_token in sent.body

    def it_does_not_mail_bomb_the_address(paid_offering, victim, client, mailoutbox):
        from django.core.cache import cache

        cache.clear()
        mailoutbox.clear()

        for _ in range(5):
            client.post(_register_url(paid_offering), data=_post_data())

        assert len(mailoutbox) == 1

    def it_says_the_same_thing_whether_or_not_the_address_is_registered(paid_offering, victim, client):
        """No confirmation that the address is signed up: the password-reset shape."""
        from django.core.cache import cache

        cache.clear()
        known = client.post(_register_url(paid_offering), data=_post_data())
        said = [m.message for m in get_messages(known.wsgi_request)]

        assert said == [
            "If you have already started signing up for this class, we just emailed that address "
            "a link to pick up where you left off."
        ]
        assert "sam@example.com" not in said[0]
        assert "already registered" not in said[0].lower()

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session(amount_total=5000))
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_still_refuses_the_strangers_insert(mock_create, _mock_retrieve, paid_offering, victim, client):
        """The constraint stays the backstop, and refusing is not a 500 or a disclosure."""
        with patch.object(ClassOffering, "live_registration_for_email", side_effect=[None, victim]):
            response = client.post(_register_url(paid_offering), data=_post_data())

        assert response.status_code == 302
        assert victim.self_serve_token not in response["Location"]
        assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1

    def it_lets_the_signed_in_owner_through(paid_offering, client, member_user):
        """Being logged in as the member the row is linked to is the other proof."""
        member = member_user.member
        registration = RegistrationFactory(
            class_offering=paid_offering,
            email=member.primary_email,
            member=member,
            status=Registration.Status.CONFIRMED,
        )
        client.force_login(member_user)

        response = client.post(
            _register_url(paid_offering), data=_post_data(email=member.primary_email, first_name="Robin")
        )

        assert response["Location"] == reverse(
            "classes:my_registration", kwargs={"token": registration.self_serve_token}
        )


def describe_the_double_click_that_started_all_this():
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session())
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_still_resumes_with_no_friction_in_the_same_browser(mock_create, _mock_retrieve, paid_offering, client):
        """The browser that made the row owns it, so the second click goes straight to Stripe."""
        mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}

        first = client.post(_register_url(paid_offering), data=_post_data())
        second = client.post(_register_url(paid_offering), data=_post_data())

        assert first.url == CHECKOUT_URL
        assert second.url == CHECKOUT_URL  # no email, no deflection, no extra step
        assert get_messages(second.wsgi_request)._queued_messages == []
        assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1
