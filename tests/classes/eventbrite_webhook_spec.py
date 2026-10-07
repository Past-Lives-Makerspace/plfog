"""BDD specs for the Eventbrite order webhook (#652, part 2): verification, then the order applied.

Eventbrite signs nothing, so a delivery must carry the secret path and name an order that plfog
then fetches itself. The client is :class:`FakeEventbrite`; no spec reaches Eventbrite.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from classes.models import Registration
from core.integrations.eventbrite import EventbriteClient, EventbriteError
from tests.classes.eventbrite_fakes import FakeEventbrite, attendee, listed_class, order

pytestmark = pytest.mark.django_db

SECRET = "s3cret-path"


@pytest.fixture
def eventbrite(settings: Any) -> Iterator[FakeEventbrite]:
    settings.EVENTBRITE_WEBHOOK_SECRET = SECRET
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


def _deliver(
    client: Client, *, secret: str = SECRET, action: str = "order.placed", order_id: str = "o-1", body: Any = None
) -> Any:
    payload = (
        body
        if body is not None
        else {
            "config": {"action": action, "webhook_id": "w-1"},
            "api_url": f"https://www.eventbriteapi.com/v3/orders/{order_id}/",
        }
    )
    url = reverse("billing_eventbrite_webhook", kwargs={"secret": secret})
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def describe_eventbrite_webhook():
    def it_applies_a_verified_order(client: Client, eventbrite: FakeEventbrite):
        listed_class()
        eventbrite.orders["1"] = order("1", attendee("a-1"))

        with patch("classes.eventbrite_orders.send_eventbrite_oversold_alert"):
            response = _deliver(client, order_id="1")

        assert response.status_code == 200
        assert Registration.objects.filter(eventbrite_attendee_id="a-1", eventbrite_order_id="1").exists()

    def it_answers_404_to_a_wrong_secret(client: Client, eventbrite: FakeEventbrite):
        assert _deliver(client, secret="guess").status_code == 404
        assert eventbrite.calls == []

    def it_answers_404_to_everything_while_no_secret_is_set(client: Client, eventbrite: FakeEventbrite, settings: Any):
        settings.EVENTBRITE_WEBHOOK_SECRET = ""

        assert _deliver(client).status_code == 404
        assert eventbrite.calls == []

    def it_rejects_an_api_url_that_is_not_an_eventbrite_order(client: Client, eventbrite: FakeEventbrite):
        body = {"config": {"action": "order.placed"}, "api_url": "https://evil.example/v3/orders/1/"}

        assert _deliver(client, body=body).status_code == 400
        assert eventbrite.calls == []

    def it_answers_200_to_an_action_it_does_not_act_on(client: Client, eventbrite: FakeEventbrite):
        assert _deliver(client, action="attendee.checked_in").status_code == 200
        assert eventbrite.calls == []

    def it_answers_503_while_sync_is_off_so_eventbrite_redelivers(client: Client, eventbrite: FakeEventbrite):
        eventbrite.enabled = False

        assert _deliver(client, order_id="1").status_code == 503

    def it_answers_503_when_the_order_cannot_be_fetched(client: Client, eventbrite: FakeEventbrite):
        eventbrite.fail["get_order"] = EventbriteError("GET /orders/1/: 404", 404)

        assert _deliver(client, order_id="1").status_code == 503

    def it_refuses_a_get(client: Client, eventbrite: FakeEventbrite):
        url = reverse("billing_eventbrite_webhook", kwargs={"secret": SECRET})

        assert client.get(url).status_code == 405
