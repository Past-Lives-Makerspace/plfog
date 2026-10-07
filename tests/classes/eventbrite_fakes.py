"""A recording stand-in for the Eventbrite client and builders for its order shape (#652, part 2).

The order and attendee dicts follow Eventbrite's documented Order (``expand=attendees``) and
Attendee objects: currency amounts are ``{"value": cents}``. No spec reaches Eventbrite.
"""

from __future__ import annotations

from typing import Any

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering
from core.integrations.eventbrite import EventbriteError


class FakeEventbrite:
    """Records every call; ``fail`` maps a method name to the error it raises."""

    enabled = True
    venue_id = "venue-1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail: dict[str, EventbriteError] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.quantity_sold = 0
        self.timeout = 10.0

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.fail:
            raise self.fail[name]

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def args(self, name: str) -> tuple[Any, ...]:
        return next(args for called, args in self.calls if called == name)

    def get_order(self, order_id: str) -> dict[str, Any]:
        self._record("get_order", order_id)
        return self.orders[order_id]

    def get_ticket_class(self, event_id: str, ticket_class_id: str) -> dict[str, Any]:
        self._record("get_ticket_class", event_id, ticket_class_id)
        return {"id": ticket_class_id, "quantity_sold": self.quantity_sold}

    def update_ticket_class(self, event_id: str, ticket_class_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("update_ticket_class", event_id, ticket_class_id, body)
        return {"id": ticket_class_id}

    def update_event(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("update_event", event_id, body)
        return {"id": event_id}

    def set_description(self, event_id: str, html: str) -> None:
        self._record("set_description", event_id, html)

    def refund_order(self, order_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("refund_order", order_id, body)
        return {}


def attendee(
    attendee_id: str,
    *,
    first: str = "Ada",
    last: str = "Lovelace",
    email: str = "ada@example.com",
    gross: int = 5000,
    eventbrite_fee: int = 364,
    payment_fee: int = 145,
    tax: int = 0,
    refunded: bool = False,
    cancelled: bool = False,
) -> dict[str, Any]:
    """One ticket on an order, as ``GET /orders/{id}/?expand=attendees`` lists it."""
    return {
        "id": attendee_id,
        "profile": {"first_name": first, "last_name": last, "email": email},
        "costs": {
            "gross": {"value": gross},
            "eventbrite_fee": {"value": eventbrite_fee},
            "payment_fee": {"value": payment_fee},
            "tax": {"value": tax},
        },
        "refunded": refunded,
        "cancelled": cancelled,
    }


def order(order_id: str, *attendees: dict[str, Any], event_id: str = "ev-9") -> dict[str, Any]:
    return {"id": order_id, "event_id": event_id, "attendees": list(attendees)}


def listed_class(**kwargs: Any) -> ClassOffering:
    """A live class on Eventbrite, as a publish leaves it."""
    listed: dict[str, Any] = {
        "ready": True,
        "status": ClassOffering.Status.PUBLISHED,
        "eventbrite_enabled": True,
        "eventbrite_event_id": "ev-9",
        "eventbrite_ticket_class_id": "tc-9",
        "eventbrite_sync_state": ClassOffering.EventbriteSyncState.LISTED,
    }
    return ClassOfferingFactory(**{**listed, **kwargs})
