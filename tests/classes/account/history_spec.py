import pytest
from datetime import timedelta
from django.test import Client
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import Registration


def _past_class(session_at):
    offering = ClassOfferingFactory(status="published")
    ClassSessionFactory(class_offering=offering, starts_at=session_at)
    return offering


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")


def describe_account_history():
    def it_renders_empty_state_when_no_past_classes(book_client, db):
        user = UserFactory()
        book_client.force_login(user)
        resp = book_client.get("/account/history/")
        assert resp.status_code == 200
        assert b"No past classes yet" in resp.content

    def it_groups_past_classes_by_year_descending(book_client, db):
        user = UserFactory()
        # Use absolute past dates so the test is stable regardless of when it runs.
        # Three classes: 2025-Q4, 2025-Q1, 2024-Q3 — should group into 2025 (2) and 2024 (1)
        # in descending order.
        from datetime import datetime, timezone as dt_tz

        for dt in [
            datetime(2025, 11, 15, 10, 0, tzinfo=dt_tz.utc),
            datetime(2025, 3, 22, 14, 0, tzinfo=dt_tz.utc),
            datetime(2024, 7, 4, 12, 0, tzinfo=dt_tz.utc),
        ]:
            offering = _past_class(dt)
            RegistrationFactory(
                email=user.email,
                class_offering=offering,
                status=Registration.Status.CONFIRMED,
            )
        book_client.force_login(user)
        resp = book_client.get("/account/history/")
        body = resp.content.decode()
        assert "2025" in body
        assert "2024" in body
        # Descending: 2025 heading should appear before 2024.
        assert body.index("2025") < body.index("2024")

    def it_renders_past_cards_with_dimmed_class(book_client, db):
        user = UserFactory()
        offering = _past_class(timezone.now() - timedelta(days=30))
        RegistrationFactory(
            email=user.email,
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
        )
        book_client.force_login(user)
        resp = book_client.get("/account/history/")
        # The bk-card is-past class is what dims it visually.
        assert b"bk-card is-past" in resp.content

    def it_shows_attended_badge_for_past_classes(book_client, db):
        user = UserFactory()
        offering = _past_class(timezone.now() - timedelta(days=30))
        RegistrationFactory(
            email=user.email,
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
        )
        book_client.force_login(user)
        resp = book_client.get("/account/history/")
        assert b">Attended<" in resp.content

    def it_redirects_anonymous_to_login(book_client, db):
        resp = book_client.get("/account/history/")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]
