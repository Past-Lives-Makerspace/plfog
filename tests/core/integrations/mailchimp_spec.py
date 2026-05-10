"""BDD specs for the Mailchimp v3 client wrapper."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.integrations.mailchimp import MailchimpClient, MailchimpConfig
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def _ok_response(status: int = 200) -> MagicMock:
    response = MagicMock()
    response.ok = 200 <= status < 300
    response.status_code = status
    response.text = ""
    return response


def describe_MailchimpConfig():
    def describe_datacenter():
        def it_extracts_suffix_after_dash():
            cfg = MailchimpConfig(api_key="abc123-us17", list_id="LISTID")
            assert cfg.datacenter == "us17"

        def it_raises_on_missing_dash():
            cfg = MailchimpConfig(api_key="malformed", list_id="LISTID")
            with pytest.raises(ValueError, match="malformed"):
                _ = cfg.datacenter

    def describe_base_url():
        def it_includes_the_datacenter():
            cfg = MailchimpConfig(api_key="abc-us17", list_id="LISTID")
            assert cfg.base_url == "https://us17.api.mailchimp.com/3.0"


def describe_MailchimpClient():
    def describe_from_site_config():
        def it_returns_disabled_when_api_key_blank():
            site = SiteConfiguration.load()
            site.mailchimp_api_key = ""
            site.mailchimp_list_id = "LIST"
            site.save()
            client = MailchimpClient.from_site_config()
            assert client.enabled is False

        def it_returns_disabled_when_list_id_blank():
            site = SiteConfiguration.load()
            site.mailchimp_api_key = "abc-us17"
            site.mailchimp_list_id = ""
            site.save()
            client = MailchimpClient.from_site_config()
            assert client.enabled is False

        def it_returns_enabled_when_both_set():
            site = SiteConfiguration.load()
            site.mailchimp_api_key = "abc-us17"
            site.mailchimp_list_id = "LIST"
            site.save()
            client = MailchimpClient.from_site_config()
            assert client.enabled is True
            assert client.config is not None
            assert client.config.api_key == "abc-us17"
            assert client.config.list_id == "LIST"

    def describe_subscribe():
        @pytest.fixture
        def enabled_client():
            return MailchimpClient(config=MailchimpConfig(api_key="abc-us17", list_id="LISTID"))

        def it_returns_false_when_disabled():
            client = MailchimpClient(config=None)
            with patch("core.integrations.mailchimp.requests.put") as mock_put:
                assert client.subscribe(email="a@example.com") is False
                mock_put.assert_not_called()

        def it_puts_subscriber_to_correct_url(enabled_client):
            email = "Person@Example.com"
            expected_hash = hashlib.md5(b"person@example.com").hexdigest()
            with patch(
                "core.integrations.mailchimp.requests.put",
                return_value=_ok_response(200),
            ) as mock_put:
                assert enabled_client.subscribe(email=email) is True
            mock_put.assert_called_once()
            call_args = mock_put.call_args
            assert call_args.args[0] == f"https://us17.api.mailchimp.com/3.0/lists/LISTID/members/{expected_hash}"

        def it_sends_status_if_new_subscribed_and_merge_fields(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                return_value=_ok_response(200),
            ) as mock_put:
                enabled_client.subscribe(email="a@b.com", first_name="Ada", last_name="Lovelace")
            payload = mock_put.call_args.kwargs["json"]
            assert payload["email_address"] == "a@b.com"
            assert payload["status_if_new"] == "subscribed"
            assert payload["merge_fields"] == {"FNAME": "Ada", "LNAME": "Lovelace"}

        def it_includes_tags_when_provided(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                return_value=_ok_response(200),
            ) as mock_put:
                enabled_client.subscribe(email="a@b.com", tags=["class-registrant", "newsletter"])
            payload = mock_put.call_args.kwargs["json"]
            assert payload["tags"] == ["class-registrant", "newsletter"]

        def it_omits_tags_key_when_none(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                return_value=_ok_response(200),
            ) as mock_put:
                enabled_client.subscribe(email="a@b.com")
            payload = mock_put.call_args.kwargs["json"]
            assert "tags" not in payload

        def it_uses_basic_auth_with_api_key(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                return_value=_ok_response(200),
            ) as mock_put:
                enabled_client.subscribe(email="a@b.com")
            assert mock_put.call_args.kwargs["auth"] == ("anystring", "abc-us17")

        def it_uses_a_short_timeout(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                return_value=_ok_response(200),
            ) as mock_put:
                enabled_client.subscribe(email="a@b.com")
            assert mock_put.call_args.kwargs["timeout"] == 5.0

        def it_returns_false_on_4xx(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                return_value=_ok_response(400),
            ):
                assert enabled_client.subscribe(email="a@b.com") is False

        def it_returns_false_on_5xx(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                return_value=_ok_response(500),
            ):
                assert enabled_client.subscribe(email="a@b.com") is False

        def it_returns_false_on_network_error(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                side_effect=requests.ConnectionError("nope"),
            ):
                assert enabled_client.subscribe(email="a@b.com") is False

        def it_returns_false_on_timeout(enabled_client):
            with patch(
                "core.integrations.mailchimp.requests.put",
                side_effect=requests.Timeout("slow"),
            ):
                assert enabled_client.subscribe(email="a@b.com") is False

        def it_returns_false_when_api_key_has_no_datacenter():
            client = MailchimpClient(config=MailchimpConfig(api_key="malformed", list_id="LIST"))
            with patch("core.integrations.mailchimp.requests.put") as mock_put:
                assert client.subscribe(email="a@b.com") is False
                mock_put.assert_not_called()
