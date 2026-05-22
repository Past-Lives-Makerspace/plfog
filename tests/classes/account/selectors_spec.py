from datetime import timedelta
from django.utils import timezone

from classes.account.selectors import (
    paid_registrations,
    past_registrations,
    upcoming_registrations,
)
from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import Registration


def _make_class(starts_at):
    """ClassOffering + one ClassSession at the given time."""
    offering = ClassOfferingFactory(status="published")
    ClassSessionFactory(class_offering=offering, starts_at=starts_at)
    return offering


def describe_upcoming_registrations():
    def it_returns_confirmed_registrations_with_future_sessions(db):
        user = UserFactory()
        offering = _make_class(timezone.now() + timedelta(days=7))
        reg = RegistrationFactory(
            email=user.email,
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
        )
        result = list(upcoming_registrations(user))
        assert result == [reg]

    def it_excludes_past_sessions(db):
        user = UserFactory()
        offering = _make_class(timezone.now() - timedelta(days=7))
        RegistrationFactory(email=user.email, class_offering=offering, status=Registration.Status.CONFIRMED)
        assert upcoming_registrations(user).count() == 0

    def it_includes_waitlisted(db):
        user = UserFactory()
        offering = _make_class(timezone.now() + timedelta(days=7))
        RegistrationFactory(email=user.email, class_offering=offering, status=Registration.Status.WAITLISTED)
        assert upcoming_registrations(user).count() == 1

    def it_excludes_cancelled(db):
        user = UserFactory()
        offering = _make_class(timezone.now() + timedelta(days=7))
        RegistrationFactory(email=user.email, class_offering=offering, status=Registration.Status.CANCELLED)
        assert upcoming_registrations(user).count() == 0

    def it_matches_by_user_primary_email_when_no_explicit_link(db):
        user = UserFactory(email="alice@example.com", username="alice@example.com")
        offering = _make_class(timezone.now() + timedelta(days=7))
        # Email casing differs — match must be case-insensitive.
        RegistrationFactory(email="ALICE@example.com", class_offering=offering, status=Registration.Status.CONFIRMED)
        assert upcoming_registrations(user).count() == 1

    def it_matches_by_verified_secondary_email(db):
        from allauth.account.models import EmailAddress

        user = UserFactory(email="primary@example.com", username="primary@example.com")
        EmailAddress.objects.create(user=user, email="secondary@example.com", verified=True, primary=False)
        offering = _make_class(timezone.now() + timedelta(days=7))
        RegistrationFactory(
            email="secondary@example.com", class_offering=offering, status=Registration.Status.CONFIRMED
        )
        assert upcoming_registrations(user).count() == 1

    def it_does_not_match_unverified_secondary_email(db):
        from allauth.account.models import EmailAddress

        user = UserFactory(email="primary@example.com", username="primary@example.com")
        EmailAddress.objects.create(user=user, email="side@example.com", verified=False, primary=False)
        offering = _make_class(timezone.now() + timedelta(days=7))
        RegistrationFactory(email="side@example.com", class_offering=offering, status=Registration.Status.CONFIRMED)
        assert upcoming_registrations(user).count() == 0


def describe_past_registrations():
    def it_returns_only_classes_whose_last_session_is_past(db):
        user = UserFactory()
        past_offering = _make_class(timezone.now() - timedelta(days=10))
        RegistrationFactory(email=user.email, class_offering=past_offering, status=Registration.Status.CONFIRMED)
        future_offering = _make_class(timezone.now() + timedelta(days=10))
        RegistrationFactory(email=user.email, class_offering=future_offering, status=Registration.Status.CONFIRMED)
        result = list(past_registrations(user))
        assert len(result) == 1
        assert result[0].class_offering == past_offering

    def it_excludes_cancelled_even_if_past(db):
        user = UserFactory()
        offering = _make_class(timezone.now() - timedelta(days=10))
        RegistrationFactory(email=user.email, class_offering=offering, status=Registration.Status.CANCELLED)
        assert past_registrations(user).count() == 0


def describe_paid_registrations():
    def it_returns_only_registrations_with_a_stripe_payment_id(db):
        user = UserFactory()
        offering1 = _make_class(timezone.now() - timedelta(days=10))
        RegistrationFactory(
            email=user.email,
            class_offering=offering1,
            amount_paid_cents=9500,
            stripe_payment_id="pi_aaa",
            status=Registration.Status.CONFIRMED,
            confirmed_at=timezone.now() - timedelta(days=10),
        )
        offering2 = _make_class(timezone.now() - timedelta(days=20))
        RegistrationFactory(
            email=user.email,
            class_offering=offering2,
            amount_paid_cents=0,
            stripe_payment_id="",  # free class
            status=Registration.Status.CONFIRMED,
        )
        result = list(paid_registrations(user))
        assert len(result) == 1
        assert result[0].stripe_payment_id == "pi_aaa"

    def it_orders_newest_first_by_confirmed_at(db):
        user = UserFactory()
        for days_ago, pid in [(3, "pi_3"), (10, "pi_10"), (1, "pi_1")]:
            offering = _make_class(timezone.now() - timedelta(days=days_ago + 5))
            RegistrationFactory(
                email=user.email,
                class_offering=offering,
                amount_paid_cents=5000,
                stripe_payment_id=pid,
                status=Registration.Status.CONFIRMED,
                confirmed_at=timezone.now() - timedelta(days=days_ago),
            )
        ids = [r.stripe_payment_id for r in paid_registrations(user)]
        assert ids == ["pi_1", "pi_3", "pi_10"]
