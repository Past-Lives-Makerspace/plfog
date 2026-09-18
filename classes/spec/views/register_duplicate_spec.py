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


def _session(*, session_id: str = "cs_test_first", url: str = CHECKOUT_URL, status: str = "open") -> dict:
    """A ``retrieve_checkout_session`` payload shaped like billing.stripe_utils returns."""
    return {
        "id": session_id,
        "url": url,
        "status": status,
        "payment_status": "unpaid",
        "payment_intent": "",
        "amount_total": 10000,
    }


def _register_url(offering: ClassOffering) -> str:
    return reverse("classes:register", kwargs={"slug": offering.slug})


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

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_session(status="expired"))
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_sends_them_to_their_registration_when_the_session_is_no_longer_open(
            mock_create, _mock_retrieve, paid_offering, client
        ):
            mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}
            client.post(_register_url(paid_offering), data=_post_data())

            response = client.post(_register_url(paid_offering), data=_post_data())

            existing = Registration.objects.get(class_offering=paid_offering, email="sam@example.com")
            assert response.status_code == 302
            assert response.url == reverse("classes:my_registration", kwargs={"token": existing.self_serve_token})
            assert mock_create.call_count == 1

        @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("stripe down"))
        @patch("billing.stripe_utils.create_class_checkout_session")
        def it_does_not_duplicate_when_stripe_is_unreachable(mock_create, _mock_retrieve, paid_offering, client):
            mock_create.return_value = {"id": "cs_test_first", "url": CHECKOUT_URL}
            client.post(_register_url(paid_offering), data=_post_data())

            response = client.post(_register_url(paid_offering), data=_post_data())

            assert response.status_code == 302
            assert Registration.objects.filter(class_offering=paid_offering, email="sam@example.com").count() == 1

        def it_sends_an_already_confirmed_registrant_back_to_their_registration(paid_offering, client):
            existing = RegistrationFactory(
                class_offering=paid_offering,
                email="sam@example.com",
                status=Registration.Status.CONFIRMED,
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
            winner = RegistrationFactory(
                class_offering=paid_offering,
                email="sam@example.com",
                status=Registration.Status.PENDING,
                stripe_session_id="cs_test_first",
            )

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
    def it_carries_the_submit_guard_that_swallows_the_second_click(paid_offering, client):
        body = client.get(_register_url(paid_offering)).content.decode()

        assert "__plRegisterSubmitGuard" in body  # installed once per document
        assert "document.addEventListener('submit'" in body  # delegated, survives a boosted arrival
        assert "btn.disabled = true" in body
