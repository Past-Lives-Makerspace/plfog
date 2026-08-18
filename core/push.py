"""Browser push delivery via pywebpush. Best-effort; never raises to callers."""

from __future__ import annotations

import json
import logging

from django.conf import settings
from pywebpush import WebPushException, webpush

from core.models import PushSubscription

logger = logging.getLogger(__name__)


def send_web_push(subscription: PushSubscription, *, title: str, body: str, url: str) -> bool:
    """Send one push to one subscription. Reaps the subscription on 404/410.

    Returns True when the push was accepted, False on any failure (a reaped/dead
    subscription or a transport error). The boolean lets the admin push-test tool
    report a real delivered tally; the event spine ignores it.
    """
    payload = json.dumps({"title": title, "body": body, "url": url})
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=payload,
            vapid_private_key=settings.WEBPUSH_SETTINGS["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": f"mailto:{settings.WEBPUSH_SETTINGS['VAPID_ADMIN_EMAIL']}"},
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            subscription.delete()
            logger.info("Reaped dead push subscription %s (HTTP %s).", subscription.pk, status)
        else:
            logger.warning("Push to subscription %s failed: %s", subscription.pk, exc)
        return False
    return True
