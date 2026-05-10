"""BDD specs for the Registration -> Mailchimp subscribe bridge."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from classes.factories import RegistrationFactory
from classes.services.mailchimp_subscribe import subscribe_registration
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


@pytest.fixture
def site_with_mailchimp():
    site = SiteConfiguration.load()
    site.mailchimp_api_key = "abc-us17"
    site.mailchimp_list_id = "LISTID"
    site.save()
    return site


def describe_subscribe_registration():
    def it_does_nothing_when_user_did_not_opt_in(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=False)
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_registration(reg)
        spy.assert_not_called()

    def it_does_nothing_when_already_subscribed(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=True, subscribed_to_mailchimp=True)
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_registration(reg)
        spy.assert_not_called()

    def it_does_nothing_when_mailchimp_disabled():
        # No site config — client.enabled is False
        reg = RegistrationFactory(wants_newsletter=True)
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_registration(reg)
        spy.assert_not_called()
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is False

    def it_calls_subscribe_with_class_registrant_tag(site_with_mailchimp):
        reg = RegistrationFactory(
            wants_newsletter=True,
            email="ada@example.com",
            first_name="Ada",
            last_name="Lovelace",
        )
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ) as spy:
            subscribe_registration(reg)
        spy.assert_called_once_with(
            email="ada@example.com",
            first_name="Ada",
            last_name="Lovelace",
            tags=["class-registrant"],
        )

    def it_sets_subscribed_flag_on_success(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=True)
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ):
            subscribe_registration(reg)
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is True

    def it_does_not_set_flag_on_failure(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=True)
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=False,
        ):
            subscribe_registration(reg)
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is False
