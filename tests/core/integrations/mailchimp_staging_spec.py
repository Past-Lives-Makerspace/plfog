"""BDD spec: the Mailchimp client is disabled on staging whatever the database holds."""

from __future__ import annotations

import pytest

from core.integrations.mailchimp import MailchimpClient
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def describe_from_site_config_on_staging():
    def it_returns_a_disabled_client_despite_a_configured_audience(settings):
        settings.IS_STAGING = True
        settings.MAILCHIMP_API_KEY = "envkey-us14"
        settings.MAILCHIMP_LIST_ID = "ENVLIST"
        site = SiteConfiguration.load()
        site.mailchimp_api_key = "abc-us17"
        site.mailchimp_list_id = "LIST"
        site.save()
        client = MailchimpClient.from_site_config()
        assert client.enabled is False
        assert client.config is None
