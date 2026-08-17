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


def send_fcm(device: FcmDevice, *, title: str, body: str, url: str) -> None:
    """Send one notification to one device token. Reaps the device on 404 (unregistered)."""
    auth = _access_token_and_project()
    if auth is None:
        return
    token, project_id = auth
    payload = {
        "message": {
            "token": device.token,
            "notification": {"title": title, "body": body},
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
        return
    if response.status_code == 404:
        device.delete()
        logger.info("Reaped unregistered FCM device %s (HTTP 404).", device.pk)
    elif response.status_code >= 400:
        logger.warning("Push to device %s failed: HTTP %s %s", device.pk, response.status_code, response.text)
