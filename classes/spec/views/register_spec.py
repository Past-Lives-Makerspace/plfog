"""BDD specs for the public registration views."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
)
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db


@pytest.fixture
def paid_offering(db):
    offering = ClassOfferingFactory(
        title="Forge Basics",
        slug="forge-basics",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=10000,
        member_discount_pct=10,
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
        slug="free-demo",
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


def describe_register_view():
    def it_renders_the_form(paid_offering, client):
        response = client.get(reverse("classes:register", kwargs={"slug": paid_offering.slug}))
        assert response.status_code == 200
        assert b"Liability Waiver" in response.content
        assert b"Continue to Payment" in response.content

    def it_prefills_form_for_a_logged_in_member(paid_offering, client, member_user):
        member = member_user.member
        member.full_legal_name = "Robin Hood"
        member.phone = "503-555-0100"
        member.pronouns = "they/them"
        member.save()
        client.force_login(member_user)
        response = client.get(reverse("classes:register", kwargs={"slug": paid_offering.slug}))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'value="Robin"' in body
        assert 'value="Hood"' in body
        assert 'value="member@example.com"' in body
        assert 'value="503-555-0100"' in body
        assert 'value="they/them"' in body

    def it_404s_for_unpublished_class(db, client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, slug="hidden")
        response = client.get(reverse("classes:register", kwargs={"slug": offering.slug}))
        assert response.status_code == 404

    def it_confirms_immediately_for_a_free_class(free_offering, client):
        response = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=_post_data())
        assert response.status_code == 302
        assert response.url == reverse("classes:register_success", kwargs={"slug": free_offering.slug})
        registration = Registration.objects.get(class_offering=free_offering)
        assert registration.status == Registration.Status.CONFIRMED
        assert registration.confirmed_at is not None
        assert registration.amount_paid_cents == 0
        assert len(mail.outbox) == 1
        assert "confirmed" in mail.outbox[0].subject.lower()

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_kicks_off_stripe_checkout_for_paid_classes(mock_checkout, paid_offering, client):
        mock_checkout.return_value = {"id": "cs_test_123", "url": "https://checkout.stripe.com/c/pay/cs_test_123"}

        response = client.post(reverse("classes:register", kwargs={"slug": paid_offering.slug}), data=_post_data())

        assert response.status_code == 302
        assert response.url == "https://checkout.stripe.com/c/pay/cs_test_123"
        registration = Registration.objects.get(class_offering=paid_offering)
        assert registration.status == Registration.Status.PENDING
        assert registration.stripe_session_id == "cs_test_123"
        assert registration.amount_paid_cents == 10000  # provisional, no member match

        kwargs = mock_checkout.call_args.kwargs
        assert kwargs["amount_cents"] == 10000
        assert kwargs["customer_email"] == "sam@example.com"
        assert kwargs["metadata"]["registration_id"] == str(registration.pk)
        assert kwargs["metadata"]["kind"] == "class_registration"

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_rolls_back_registration_when_stripe_fails(mock_checkout, paid_offering, client):
        mock_checkout.side_effect = RuntimeError("stripe down")
        with pytest.raises(RuntimeError):
            client.post(reverse("classes:register", kwargs={"slug": paid_offering.slug}), data=_post_data())
        assert not Registration.objects.filter(class_offering=paid_offering).exists()

    def it_blocks_registration_when_sold_out(paid_offering, client):
        for _ in range(paid_offering.capacity):
            RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        response = client.post(reverse("classes:register", kwargs={"slug": paid_offering.slug}), data=_post_data())
        assert response.status_code == 200
        assert b"sold out" in response.content.lower()

    def it_subscribes_to_mailchimp_when_free_registrant_opts_in(free_offering, client):
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        site.mailchimp_api_key = "abc-us17"
        site.mailchimp_list_id = "LIST"
        site.save()

        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ) as spy:
            client.post(
                reverse("classes:register", kwargs={"slug": free_offering.slug}),
                data=_post_data(wants_newsletter="on"),
            )
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["email"] == "sam@example.com"
        assert kwargs["tags"] == ["class-registrant"]
        registration = Registration.objects.get(class_offering=free_offering)
        assert registration.subscribed_to_mailchimp is True

    def it_does_not_subscribe_free_registrant_who_did_not_opt_in(free_offering, client):
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        site.mailchimp_api_key = "abc-us17"
        site.mailchimp_list_id = "LIST"
        site.save()

        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            client.post(
                reverse("classes:register", kwargs={"slug": free_offering.slug}),
                data=_post_data(),  # wants_newsletter not set
            )
        spy.assert_not_called()


def describe_register_success_view():
    def it_renders_a_thanks_page(paid_offering, client):
        response = client.get(reverse("classes:register_success", kwargs={"slug": paid_offering.slug}))
        assert response.status_code == 200
        assert b"You're in!" in response.content


def describe_register_cancelled_view():
    def it_deletes_the_pending_registration_when_token_provided(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.PENDING)
        url = reverse("classes:register_cancelled", kwargs={"slug": paid_offering.slug})
        response = client.get(f"{url}?reg={registration.self_serve_token}")
        assert response.status_code == 200
        assert not Registration.objects.filter(pk=registration.pk).exists()

    def it_keeps_confirmed_registrations_intact(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        url = reverse("classes:register_cancelled", kwargs={"slug": paid_offering.slug})
        client.get(f"{url}?reg={registration.self_serve_token}")
        assert Registration.objects.filter(pk=registration.pk).exists()


def describe_my_registration_view():
    def it_renders_via_token(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        response = client.get(reverse("classes:my_registration", kwargs={"token": registration.self_serve_token}))
        assert response.status_code == 200
        assert paid_offering.title.encode() in response.content

    def it_404s_on_unknown_token(db, client):
        response = client.get(reverse("classes:my_registration", kwargs={"token": "nope"}))
        assert response.status_code == 404

    def it_self_cancels_via_post(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        url = reverse("classes:my_registration_cancel", kwargs={"token": registration.self_serve_token})
        response = client.post(url)
        assert response.status_code == 302
        registration.refresh_from_db()
        assert registration.status == Registration.Status.CANCELLED
        assert registration.cancelled_at is not None

    def it_redirects_get_on_cancel_endpoint_back_to_self_serve(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        url = reverse("classes:my_registration_cancel", kwargs={"token": registration.self_serve_token})
        response = client.get(url)
        assert response.status_code == 302
        registration.refresh_from_db()
        assert registration.status == Registration.Status.CONFIRMED  # unchanged
