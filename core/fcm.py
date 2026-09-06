"""Native (Capacitor) push delivery via Firebase Cloud Messaging HTTP v1.

Sibling of :mod:`core.push` (web push): best-effort, **never raises to callers**, and
reaps a :class:`core.models.FcmDevice` row when FCM reports the token is unregistered.

Credentials come from ``settings.FCM_SERVICE_ACCOUNT_JSON`` (raw service-account JSON for
the firebase-adminsdk service account behind ``mobile/android/app/google-services.json``),
minted into a short-lived OAuth token via ``google-auth`` — the same service-account
mechanism :mod:`core.integrations.google_calendar` uses. Blank creds → graceful no-op, so
local dev and CI never reach the network.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx
from django.conf import settings
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

if TYPE_CHECKING:
    from core.models import FcmDevice

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]
_FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
_TIMEOUT = 10.0

# The four coarse Android notification channels the app creates (static/js/native-push.js).
# A member tunes each independently in system settings; every message names exactly one.
# These ids MUST match the ids created client-side. core.events.channels routes each event
# to one of these; core.push_admin sends its diagnostic push on GENERAL.
PUSH_CHANNEL_URGENT = "urgent"
PUSH_CHANNEL_GUILDS = "guilds"
PUSH_CHANNEL_CLASSES = "classes"
PUSH_CHANNEL_GENERAL = "general"


def _android_priority(channel_id: str) -> str:
    """FCM message priority for a channel — the Doze lever, distinct from channel importance.

    Channel *importance* (set client-side) decides whether the tray posts a heads-up banner;
    message *priority* decides whether Android delivers it at once or batches it under Doze
    while the phone is locked and idle. Urgent notices (a class starting soon, a cancellation,
    a freed waitlist seat, a failed charge) go ``high`` so they wake the device immediately;
    everything else rides ``normal`` — batched, easier on battery, and fine for a notice that
    can land a few minutes late.
    """
    return "high" if channel_id == PUSH_CHANNEL_URGENT else "normal"


def _apns_priority(channel_id: str) -> str:
    """APNs delivery priority — the iOS counterpart of :func:`_android_priority`.

    ``"10"`` delivers immediately; ``"5"`` lets iOS batch for power. Urgent notices
    (a class starting soon, a cancellation, a freed waitlist seat, a failed charge)
    go ``10``; everything else rides ``5``.
    """
    return "10" if channel_id == PUSH_CHANNEL_URGENT else "5"


def _access_token_and_project() -> tuple[str, str] | None:
    """Mint an OAuth access token + resolve the project id from the service account.

    Returns ``None`` when the credential env var is blank, unparseable, or the token
    refresh fails — every one of which degrades :func:`send_fcm` to a no-op instead of
    raising (matching the web-push path's graceful degradation).
    """
    raw = (settings.FCM_SERVICE_ACCOUNT_JSON or "").strip()
    if not raw:
        return None
    try:
        info = json.loads(raw)
        project_id = info["project_id"]
        credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        credentials.refresh(GoogleAuthRequest())
    except Exception as exc:  # bad JSON / bad key / transport failure → no-op, never raise
        logger.warning("FCM credential init failed: %s", exc)
        return None
    return credentials.token, project_id


def send_fcm(device: FcmDevice, *, title: str, body: str, url: str, channel_id: str) -> bool:
    """Send one notification to one device token. Reaps the device on 404 (unregistered).

    Returns True when FCM accepted the message, False on any failure — missing
    credentials, a transport error, or a rejected/unregistered token. The boolean lets
    the admin push-test tool report a real delivered tally; the event spine ignores it.

    ``channel_id`` names the Android notification channel the tray posts this under.
    The native app creates the channels (see ``static/js/native-push.js``); a member
    controls each one independently in system settings. It must match a created channel
    id or Android falls back to a generic channel. On iOS the same id rides through as
    the APNs ``thread-id`` (iOS has no channels), so the tray still groups by kind.
    """
    auth = _access_token_and_project()
    if auth is None:
        return False
    token, project_id = auth
    payload = {
        "message": {
            "token": device.token,
            "notification": {"title": title, "body": body},
            "android": {"priority": _android_priority(channel_id), "notification": {"channel_id": channel_id}},
            # iOS has no notification channels, so ``channel_id`` doubles as the APNs
            # ``thread-id``: the tray groups a member's notices by kind, which is the
            # closest iOS analogue. No badge count — that needs an unread tally this
            # path does not have, and a wrong badge is worse than none.
            "apns": {
                "headers": {
                    "apns-priority": _apns_priority(channel_id),
                    "apns-push-type": "alert",
                },
                "payload": {"aps": {"sound": "default", "thread-id": channel_id}},
            },
            "data": {"url": url},
        }
    }
    try:
        response = httpx.post(
            _FCM_ENDPOINT.format(project_id=project_id),
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("Push to device %s failed: %s", device.pk, exc)
        return False
    if response.status_code == 404:
        device.delete()
        logger.info("Reaped unregistered FCM device %s (HTTP 404).", device.pk)
        return False
    if response.status_code >= 400:
        logger.warning("Push to device %s failed: HTTP %s %s", device.pk, response.status_code, response.text)
        return False
    return True
