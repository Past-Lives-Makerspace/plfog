"""BDD specs for the Eventbrite client (#652). HTTP is mocked with respx; nothing reaches Eventbrite."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from core.integrations.eventbrite import API_BASE, EventbriteClient, EventbriteError, estimate_fee_cents
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def _credentials(settings: Any) -> None:
    settings.EVENTBRITE_PRIVATE_TOKEN = "secret"
    settings.EVENTBRITE_ORGANIZATION_ID = "org-1"
    settings.EVENTBRITE_VENUE_ID = "venue-1"


def _switch_on() -> None:
    config = SiteConfiguration.load()
    config.eventbrite_sync_enabled = True
    config.save(update_fields=["eventbrite_sync_enabled"])


def _client() -> EventbriteClient:
    return EventbriteClient(token="secret", organization_id="org-1", venue_id="venue-1")


def describe_from_settings():
    def it_is_disabled_while_the_site_toggle_is_off(settings: Any):
        _credentials(settings)

        assert EventbriteClient.from_settings().enabled is False

    def it_is_enabled_with_the_toggle_on_and_every_credential_set(settings: Any):
        _credentials(settings)
        _switch_on()

        client = EventbriteClient.from_settings()

        assert client.enabled is True
        assert (client.organization_id, client.venue_id) == ("org-1", "venue-1")

    @pytest.mark.parametrize("name", ["EVENTBRITE_PRIVATE_TOKEN", "EVENTBRITE_ORGANIZATION_ID", "EVENTBRITE_VENUE_ID"])
    def it_is_disabled_when_any_credential_is_blank(settings: Any, name: str):
        _credentials(settings)
        _switch_on()
        setattr(settings, name, "")

        assert EventbriteClient.from_settings().enabled is False

    def it_goes_dark_on_staging(settings: Any):
        _credentials(settings)
        _switch_on()
        settings.IS_STAGING = True

        assert EventbriteClient.from_settings().enabled is False


def describe_requests():
    @respx.mock
    def it_creates_the_event_under_the_organization_with_a_bearer_token():
        route = respx.post(f"{API_BASE}/organizations/org-1/events/").respond(json={"id": "ev-1"})

        assert _client().create_event({"event": {}}) == {"id": "ev-1"}

        assert route.calls.last.request.headers["Authorization"] == "Bearer secret"

    @respx.mock
    @pytest.mark.parametrize(
        ("call", "path"),
        [
            (lambda c: c.update_event("ev-1", {}), "/events/ev-1/"),
            (lambda c: c.create_ticket_class("ev-1", {}), "/events/ev-1/ticket_classes/"),
            (lambda c: c.update_ticket_class("ev-1", "tc-1", {}), "/events/ev-1/ticket_classes/tc-1/"),
            (lambda c: c.publish("ev-1"), "/events/ev-1/publish/"),
            (lambda c: c.unpublish("ev-1"), "/events/ev-1/unpublish/"),
        ],
    )
    def it_posts_to_each_endpoint(call: Any, path: str):
        route = respx.post(f"{API_BASE}{path}").respond(json={"id": "x"})

        call(_client())

        assert route.called

    @respx.mock
    def it_raises_with_the_status_on_an_error_response():
        respx.post(f"{API_BASE}/events/ev-1/unpublish/").respond(400, json={"error": "HAS_ORDERS"})

        with pytest.raises(EventbriteError) as raised:
            _client().unpublish("ev-1")

        assert raised.value.status == 400
        assert "HAS_ORDERS" in str(raised.value)

    @respx.mock
    def it_raises_without_a_status_when_eventbrite_cannot_be_reached():
        respx.post(f"{API_BASE}/events/ev-1/publish/").mock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(EventbriteError) as raised:
            _client().publish("ev-1")

        assert raised.value.status is None

    @respx.mock
    def it_publishes_the_description_as_the_next_structured_content_version():
        respx.get(f"{API_BASE}/events/ev-1/structured_content/edit/").respond(json={"page_version_number": "3"})
        route = respx.post(f"{API_BASE}/events/ev-1/structured_content/4/").respond(json={})

        _client().set_description("ev-1", "<p>Hi</p>")

        body = json.loads(route.calls.last.request.content)
        assert body["publish"] is True
        assert body["modules"][0]["data"]["body"]["text"] == "<p>Hi</p>"

    @respx.mock
    def it_uploads_an_image_in_three_steps_and_returns_its_media_id():
        respx.get(f"{API_BASE}/media/upload/").respond(
            json={
                "upload_url": "https://uploads.example.com/",
                "upload_data": {"key": "k"},
                "file_parameter_name": "file",
                "upload_token": "tok",
            }
        )
        bucket = respx.post("https://uploads.example.com/").respond(204)
        finish = respx.post(f"{API_BASE}/media/upload/").respond(json={"id": 77})

        assert _client().upload_logo("hero.jpg", b"jpeg") == "77"

        assert bucket.called
        assert json.loads(finish.calls.last.request.content) == {"upload_token": "tok"}

    @respx.mock
    def it_raises_when_the_image_bucket_cannot_be_reached():
        respx.get(f"{API_BASE}/media/upload/").respond(
            json={"upload_url": "https://uploads.example.com/", "upload_data": {}, "file_parameter_name": "f"}
        )
        respx.post("https://uploads.example.com/").mock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(EventbriteError):
            _client().upload_logo("hero.jpg", b"jpeg")


def describe_malformed_answers():
    @respx.mock
    def it_raises_on_a_success_that_is_not_json():
        respx.post(f"{API_BASE}/events/ev-1/publish/").respond(200, text="<html>maintenance</html>")

        with pytest.raises(EventbriteError, match="not JSON"):
            _client().publish("ev-1")

    @respx.mock
    def it_raises_on_json_that_is_not_an_object():
        respx.post(f"{API_BASE}/events/ev-1/publish/").respond(json=True)

        with pytest.raises(EventbriteError, match="expected an object"):
            _client().publish("ev-1")

    @respx.mock
    def it_raises_when_the_description_version_is_missing():
        respx.get(f"{API_BASE}/events/ev-1/structured_content/edit/").respond(json={})

        with pytest.raises(EventbriteError, match="page_version_number"):
            _client().set_description("ev-1", "<p>Hi</p>")

    @respx.mock
    @pytest.mark.parametrize("version", ["x", None, {}, []])
    def it_raises_when_the_description_version_is_not_a_number(version: Any):
        respx.get(f"{API_BASE}/events/ev-1/structured_content/edit/").respond(json={"page_version_number": version})

        with pytest.raises(EventbriteError, match="Unreadable"):
            _client().set_description("ev-1", "<p>Hi</p>")

    @respx.mock
    def it_raises_when_the_upload_ticket_is_incomplete():
        respx.get(f"{API_BASE}/media/upload/").respond(json={"file_parameter_name": "file", "upload_url": "u"})

        with pytest.raises(EventbriteError, match="upload_data"):
            _client().upload_logo("hero.jpg", b"jpeg")

    @respx.mock
    def it_raises_when_the_image_bucket_refuses_the_file():
        respx.get(f"{API_BASE}/media/upload/").respond(
            json={"upload_url": "https://uploads.example.com/", "upload_data": {}, "file_parameter_name": "f"}
        )
        respx.post("https://uploads.example.com/").respond(403, text="denied")

        with pytest.raises(EventbriteError) as raised:
            _client().upload_logo("hero.jpg", b"jpeg")

        assert raised.value.status == 403


def describe_estimate_fee_cents():
    def it_adds_the_percentages_and_the_per_ticket_fee():
        # 3.7% + 2.9% of $50 is $3.30, plus $1.79.
        assert estimate_fee_cents(5000) == 509


def describe_order_and_ticket_reads():
    @respx.mock
    def it_fetches_an_order_with_its_attendees():
        route = respx.get(f"{API_BASE}/orders/o-1/", params={"expand": "attendees"}).respond(json={"id": "o-1"})

        assert _client().get_order("o-1") == {"id": "o-1"}
        assert route.called

    @respx.mock
    def it_fetches_a_ticket_class():
        route = respx.get(f"{API_BASE}/events/ev-1/ticket_classes/tc-1/").respond(json={"quantity_sold": 3})

        assert _client().get_ticket_class("ev-1", "tc-1") == {"quantity_sold": 3}
        assert route.called

    @respx.mock
    def it_posts_a_refund_to_the_order():
        route = respx.post(f"{API_BASE}/orders/o-1/refunds/").respond(json={})

        _client().refund_order("o-1", {"reason": "no_longer_able_to_attend"})

        assert json.loads(route.calls.last.request.content) == {"reason": "no_longer_able_to_attend"}

    def it_waits_only_as_long_as_the_clients_timeout():
        client = _client()
        client.timeout = 3.0

        with patch("httpx.request", return_value=httpx.Response(200, json={})) as request:
            client.get_ticket_class("ev-1", "tc-1")

        assert request.call_args.kwargs["timeout"] == 3.0
