"""BDD specs for the Simplybook JSON-RPC client wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.integrations import simplybook as sb


@pytest.fixture(autouse=True)
def _reset_token_cache():
    sb._token_cache.clear()
    yield
    sb._token_cache.clear()


def _json_response(payload: dict, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.ok = 200 <= status < 300
    response.status_code = status
    response.text = ""
    response.json.return_value = payload
    return response


def describe_SimplybookClient():
    def describe_from_settings():
        def it_returns_disabled_when_api_key_blank(settings):
            settings.SIMPLYBOOK_API_KEY = ""
            settings.SIMPLYBOOK_COMPANY_LOGIN = "co"
            client = sb.SimplybookClient.from_settings()
            assert client.enabled is False

        def it_returns_disabled_when_company_login_blank(settings):
            settings.SIMPLYBOOK_API_KEY = "key"
            settings.SIMPLYBOOK_COMPANY_LOGIN = ""
            client = sb.SimplybookClient.from_settings()
            assert client.enabled is False

        def it_returns_enabled_when_both_set(settings):
            settings.SIMPLYBOOK_API_KEY = "key"
            settings.SIMPLYBOOK_COMPANY_LOGIN = "co"
            client = sb.SimplybookClient.from_settings()
            assert client.enabled is True

    def describe_has_completed_tour():
        def it_returns_false_when_disabled():
            client = sb.SimplybookClient(config=None)
            assert client.has_completed_tour("x@y.com") is False

        def it_returns_false_on_network_error():
            client = sb.SimplybookClient(config=sb.SimplybookConfig(api_key="k", company_login="c"))
            with patch("requests.post", side_effect=requests.ConnectionError("down")):
                assert client.has_completed_tour("x@y.com") is False

        def it_returns_true_when_simplybook_lists_an_approved_booking():
            client = sb.SimplybookClient(config=sb.SimplybookConfig(api_key="k", company_login="c"))
            login_response = _json_response({"result": "sessiontoken"})
            bookings_response = _json_response({"result": [{"id": 1}]})
            with patch("requests.post", side_effect=[login_response, bookings_response]):
                assert client.has_completed_tour("x@y.com") is True

        def it_returns_false_when_bookings_is_empty():
            client = sb.SimplybookClient(config=sb.SimplybookConfig(api_key="k", company_login="c"))
            login_response = _json_response({"result": "sessiontoken"})
            bookings_response = _json_response({"result": []})
            with patch("requests.post", side_effect=[login_response, bookings_response]):
                assert client.has_completed_tour("x@y.com") is False

        def it_uses_5s_timeout_on_requests():
            client = sb.SimplybookClient(config=sb.SimplybookConfig(api_key="k", company_login="c"))
            login_response = _json_response({"result": "tok"})
            bookings_response = _json_response({"result": []})
            with patch("requests.post", side_effect=[login_response, bookings_response]) as m:
                client.has_completed_tour("x@y.com")
            # Both calls (login + bookings) must specify the 5s timeout.
            for call in m.call_args_list:
                assert call.kwargs["timeout"] == 5.0

        def it_caches_token_across_calls():
            client = sb.SimplybookClient(config=sb.SimplybookConfig(api_key="k", company_login="c"))
            responses = [
                _json_response({"result": "tok"}),  # login (cached after)
                _json_response({"result": []}),  # first bookings call
                _json_response({"result": [{"id": 1}]}),  # second bookings call (no second login)
            ]
            with patch("requests.post", side_effect=responses) as m:
                client.has_completed_tour("a@b.com")
                client.has_completed_tour("c@d.com")
            # 1 login + 2 bookings = 3 calls total; login not repeated.
            assert m.call_count == 3
