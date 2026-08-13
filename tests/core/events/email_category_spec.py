"""BDD specs for the email-category wiring fix in core.events.channels.

``email_category_for`` resolves an event key to its registry category for the
``X-Category`` header; ``EmailAdapter.deliver`` threads that category through to
the email choke-point so the sent message actually carries the header.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail

from core.events.channels import EmailAdapter, Message, email_category_for
from core.events.registry import get_event

pytestmark = pytest.mark.django_db


def describe_email_category_for():
    def it_returns_the_events_registry_category_for_a_known_key():
        expected = get_event("class_published").category
        assert email_category_for("class_published") == expected

    def it_returns_none_for_an_empty_key():
        assert email_category_for("") is None

    def it_returns_none_for_an_unknown_key():
        with pytest.raises(KeyError):
            get_event("not_a_real_event_key")  # sanity: the registry really doesn't know this key
        assert email_category_for("not_a_real_event_key") is None


def describe_EmailAdapter_category():
    def it_sets_the_x_category_header_to_the_events_registry_category():
        user = User.objects.create_user(username="catuser", email="cat@example.com")
        message = Message(title="T", body="B", trigger_kind="class_published")

        EmailAdapter().deliver(user, message)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].extra_headers["X-Category"] == get_event("class_published").category

    def it_omits_the_header_when_the_trigger_kind_is_not_a_registered_event():
        user = User.objects.create_user(username="catuser2", email="cat2@example.com")
        message = Message(title="T", body="B", trigger_kind="")

        EmailAdapter().deliver(user, message)

        assert len(mail.outbox) == 1
        assert "X-Category" not in mail.outbox[0].extra_headers
