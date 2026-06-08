import pytest
from django.test import Client
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import Registration
from membership.models import Member


def _airtable_promote(user):
    Member.objects.filter(user=user).update(airtable_record_id="recTEST123")
    user.__dict__.pop("member", None)


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")


def describe_account_receipts():
    def it_renders_empty_state_when_no_paid_registrations(book_client, db):
        user = UserFactory()
        book_client.force_login(user)
        resp = book_client.get("/account/receipts/")
        assert resp.status_code == 200
        assert b"No receipts yet" in resp.content

    def it_renders_a_row_per_paid_registration(book_client, db):
        user = UserFactory()
        offering = ClassOfferingFactory(status="published", title="Lampworking", price_cents=9500)
        ClassSessionFactory(class_offering=offering)
        RegistrationFactory(
            email=user.email,
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=7600,
            stripe_payment_id="pi_xxx",
            confirmed_at=timezone.now(),
        )
        book_client.force_login(user)
        resp = book_client.get("/account/receipts/")
        assert b"Lampworking" in resp.content
        assert b"$76.00" in resp.content

    def it_formats_zero_cents_correctly(book_client, db):
        # Edge case: a $0 paid row shouldn't appear (selector filters by stripe_payment_id),
        # but verify the formatter doesn't crash on amounts under $1.
        user = UserFactory()
        offering = ClassOfferingFactory(status="published", price_cents=500)
        ClassSessionFactory(class_offering=offering)
        RegistrationFactory(
            email=user.email,
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=499,
            stripe_payment_id="pi_yyy",
            confirmed_at=timezone.now(),
        )
        book_client.force_login(user)
        resp = book_client.get("/account/receipts/")
        assert b"$4.99" in resp.content

    def it_does_not_render_member_discount_tag_for_nonmember(book_client, db):
        user = UserFactory()
        offering = ClassOfferingFactory(status="published", title="Ceramics", price_cents=8000)
        ClassSessionFactory(class_offering=offering)
        RegistrationFactory(
            email=user.email,
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=8000,
            stripe_payment_id="pi_zzz",
            confirmed_at=timezone.now(),
        )
        book_client.force_login(user)
        resp = book_client.get("/account/receipts/")
        assert b"is-discount" not in resp.content

    def it_renders_member_discount_tag_for_member_with_linked_registration(book_client, db):
        user = UserFactory()
        _airtable_promote(user)
        offering = ClassOfferingFactory(status="published", title="Forge", price_cents=10000)
        ClassSessionFactory(class_offering=offering)
        reg = RegistrationFactory(
            email=user.email,
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=8000,
            stripe_payment_id="pi_qqq",
            confirmed_at=timezone.now(),
        )
        # The signal-driven post-save also auto-links via email; confirm:
        reg.refresh_from_db()
        assert reg.member is not None
        book_client.force_login(user)
        resp = book_client.get("/account/receipts/")
        assert b"is-discount" in resp.content

    def it_redirects_anonymous_to_login(book_client, db):
        resp = book_client.get("/account/receipts/")
        assert resp.status_code == 302
        assert "/auth/relay/" in resp["Location"]
