"""BDD specs for the Mailchimp cancel over-removal fix (classes.services.mailchimp_subscribe).

Before the fix, cancelling ONE of a member's two confirmed registrations stripped
EVERY tag ``derive_tags`` produced for the cancelled registration — including tags
(``class-registrant``, a shared category) the member still legitimately holds via
their other, still-confirmed registration. The fix computes what the member's
OTHER confirmed registrations still justify (:func:`_tags_still_justified`) and
only ever removes the tags unique to the one being cancelled.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, RegistrationFactory
from classes.models import Registration
from classes.services.mailchimp_subscribe import _tags_still_justified, derive_tags, unsubscribe_registration

pytestmark = pytest.mark.django_db


class _StubClient:
    """A minimal stand-in for MailchimpClient — enabled, with a spy tag-remove call."""

    def __init__(self, *, remove_result: bool = True) -> None:
        self.enabled = True
        self.member_tags_remove = MagicMock(return_value=remove_result)


def _two_confirmed_registrations(*, same_instructor: bool):
    """Two CONFIRMED registrations at the same email, same category, under either
    the same or different instructors — the shared/unique tag split hinges on this."""
    category = CategoryFactory(name="Woodworking", slug="woodworking")
    alice = InstructorFactory(instructor_slug="alice")
    offering_a = ClassOfferingFactory(category=category, instructor=alice)
    offering_b = ClassOfferingFactory(
        category=category, instructor=alice if same_instructor else InstructorFactory(instructor_slug="bob")
    )
    reg_a = RegistrationFactory(
        class_offering=offering_a,
        email="member@example.com",
        status=Registration.Status.CONFIRMED,
        subscribed_to_mailchimp=True,
    )
    reg_b = RegistrationFactory(
        class_offering=offering_b,
        email="member@example.com",
        status=Registration.Status.CONFIRMED,
    )
    return reg_a, reg_b


def describe__tags_still_justified():
    def it_unions_tags_from_the_registrants_other_confirmed_registrations():
        reg_a, reg_b = _two_confirmed_registrations(same_instructor=False)
        assert _tags_still_justified(reg_a) == set(derive_tags(reg_b))

    def it_returns_empty_when_no_other_registration_exists_at_this_email():
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        assert _tags_still_justified(reg) == set()

    def it_ignores_a_cancelled_sibling_registration():
        category = CategoryFactory(slug="glass")
        offering_a = ClassOfferingFactory(category=category)
        offering_b = ClassOfferingFactory(category=category)
        reg_a = RegistrationFactory(
            class_offering=offering_a, email="x@example.com", status=Registration.Status.CONFIRMED
        )
        RegistrationFactory(class_offering=offering_b, email="x@example.com", status=Registration.Status.CANCELLED)
        assert _tags_still_justified(reg_a) == set()


def describe_unsubscribe_registration():
    def it_only_removes_tags_unique_to_the_cancelled_registration():
        reg_a, _reg_b = _two_confirmed_registrations(same_instructor=False)
        stub = _StubClient(remove_result=True)

        with patch("core.integrations.mailchimp.MailchimpClient.from_site_config", return_value=stub):
            unsubscribe_registration(reg_a)

        # class-registrant and category-woodworking are shared with reg_b (still confirmed) —
        # only the instructor tag is unique to reg_a and gets sent for removal.
        stub.member_tags_remove.assert_called_once_with("member@example.com", ["instructor-alice"])
        reg_a.refresh_from_db()
        assert reg_a.subscribed_to_mailchimp is False

    def it_clears_the_flag_without_calling_mailchimp_when_no_unique_tags_remain():
        reg_a, _reg_b = _two_confirmed_registrations(same_instructor=True)

        with patch("core.integrations.mailchimp.MailchimpClient.from_site_config") as mock_from_config:
            unsubscribe_registration(reg_a)

        mock_from_config.assert_not_called()
        reg_a.refresh_from_db()
        assert reg_a.subscribed_to_mailchimp is False

    def it_keeps_the_flag_set_and_logs_a_warning_when_the_remove_call_fails(caplog):
        reg_a, _reg_b = _two_confirmed_registrations(same_instructor=False)
        stub = _StubClient(remove_result=False)

        with patch("core.integrations.mailchimp.MailchimpClient.from_site_config", return_value=stub):
            with caplog.at_level(logging.WARNING, logger="classes.services.mailchimp_subscribe"):
                unsubscribe_registration(reg_a)

        stub.member_tags_remove.assert_called_once()
        reg_a.refresh_from_db()
        assert reg_a.subscribed_to_mailchimp is True
        assert "did not complete" in caplog.text
