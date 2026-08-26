"""Toast notification utility for HTMX responses."""

from __future__ import annotations

import json

from django.http import HttpResponse


def trigger_toast(response: HttpResponse, message: str, toast_type: str = "success") -> None:
    """Set the HX-Trigger header to show a toast notification on the client.

    Args:
        response: The HttpResponse to add the header to.
        message: The toast message text.
        toast_type: One of "success", "error", "info".
    """
    response["HX-Trigger"] = json.dumps({"showToast": {"message": message, "type": toast_type}})


def trigger_client_event(response: HttpResponse, event_name: str) -> None:
    """Merge a bare HTMX client event into the response's HX-Trigger header.

    Keeps any toast already set by :func:`trigger_toast` — HX-Trigger is a single
    header, so both payloads must share one JSON object.

    Args:
        response: The HttpResponse to add the header to.
        event_name: The client event name (e.g. ``"refund-done"``).
    """
    existing = json.loads(response["HX-Trigger"]) if response.has_header("HX-Trigger") else {}
    existing[event_name] = True
    response["HX-Trigger"] = json.dumps(existing)
