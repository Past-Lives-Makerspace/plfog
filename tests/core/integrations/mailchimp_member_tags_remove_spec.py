"""BDD specs for MailchimpClient.member_tags_remove (core.integrations.mailchimp).

Mirrors the mocking pattern in tests/core/integrations/mailchimp_spec.py — the
client wraps ``requests`` directly (not httpx), so ``requests.post`` is patched
via unittest.mock rather than respx.
"""

from __future__ import annotations

import hashlib
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.integrations.mailchimp import MailchimpClient, MailchimpConfig


def _response(status: int = 200) -> MagicMock:
    response = MagicMock()
    response.ok = 200 <= status < 300
    response.status_code = status
    response.text = ""
    return response


@pytest.fixture
def enabled_client():
    return MailchimpClient(config=MailchimpConfig(api_key="abc-us17", list_id="LISTID"))


def describe_member_tags_remove():
    def it_returns_true_on_an_ok_response(enabled_client):
        with patch("core.integrations.mailchimp.requests.post", return_value=_response(200)):
            assert enabled_client.member_tags_remove("a@example.com", ["class-registrant"]) is True

    def it_posts_inactive_status_for_each_tag_to_the_tags_endpoint(enabled_client):
        expected_hash = hashlib.md5(b"a@example.com").hexdigest()
        with patch("core.integrations.mailchimp.requests.post", return_value=_response(200)) as mock_post:
            enabled_client.member_tags_remove("a@example.com", ["class-registrant", "category-wood"])
        called_url = mock_post.call_args.args[0]
        assert called_url == f"https://us17.api.mailchimp.com/3.0/lists/LISTID/members/{expected_hash}/tags"
        payload = mock_post.call_args.kwargs["json"]
        assert payload == {
            "tags": [
                {"name": "class-registrant", "status": "inactive"},
                {"name": "category-wood", "status": "inactive"},
            ]
        }

    def it_returns_false_and_logs_a_warning_on_a_non_ok_response(enabled_client, caplog):
        with patch("core.integrations.mailchimp.requests.post", return_value=_response(400)):
            with caplog.at_level(logging.WARNING, logger="core.integrations.mailchimp"):
                result = enabled_client.member_tags_remove("a@example.com", ["x"])
        assert result is False
        assert "tag-remove failed" in caplog.text

    def it_returns_false_and_logs_a_warning_on_a_network_error(enabled_client, caplog):
        with patch("core.integrations.mailchimp.requests.post", side_effect=requests.ConnectionError("nope")):
            with caplog.at_level(logging.WARNING, logger="core.integrations.mailchimp"):
                result = enabled_client.member_tags_remove("a@example.com", ["x"])
        assert result is False
        assert "network error" in caplog.text

    def it_returns_false_when_disabled():
        client = MailchimpClient(config=None)
        with patch("core.integrations.mailchimp.requests.post") as mock_post:
            assert client.member_tags_remove("a@example.com", ["x"]) is False
        mock_post.assert_not_called()
