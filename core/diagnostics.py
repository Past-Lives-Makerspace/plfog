"""Temporary secret-gated diagnostics for the app-store reviewer login.

TEMPORARY — delete this module, its URL and its spec once the reviewer login is
confirmed working in production.

Why this exists: Render one-off jobs report ``succeeded`` regardless of the
process exit code, and neither job stdout nor request-time logs reach the log
stream. That leaves no way to observe what the running web process actually
sees. This endpoint is the observation channel: it answers, over HTTPS where the
response can simply be read, whether the deployed build and its environment
match what we think they are.

It reports presence and fingerprints only. No secret value is ever returned.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from typing import Any

import allauth
from allauth.account import app_settings as account_app_settings
from allauth.account.forms import ConfirmLoginCodeForm
from allauth.utils import get_form_class
from django.http import Http404, HttpRequest, JsonResponse

from plfog.version import VERSION

TOKEN_ENV_VAR = "DIAG_TOKEN"


def _fingerprint(value: str) -> str:
    """Return a short SHA-256 prefix, so a secret can be matched but never read."""
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def _dotted_path(obj: type) -> str:
    """Return the importable dotted path of a class, for comparing what is wired up."""
    return f"{obj.__module__}.{obj.__qualname__}"


def reviewer_login_diagnostics(request: HttpRequest) -> JsonResponse:
    """Report what the running web process sees about the reviewer login carve-out.

    Gated on the ``DIAG_TOKEN`` environment variable. When that variable is unset,
    or the supplied ``?t=`` value does not match it, the endpoint raises 404 so it
    is indistinguishable from a route that does not exist. Removing the env var
    disables the endpoint without a code change.

    Returns:
        A JSON body describing the deployed build, the wired-up confirm form, and
        whether ``PLAY_REVIEW_CODE`` is visible to this process (length and
        fingerprint only, never the value).

    Raises:
        Http404: If ``DIAG_TOKEN`` is unset or the supplied token does not match.
    """
    expected = os.environ.get(TOKEN_ENV_VAR, "").strip()
    supplied = request.GET.get("t", "")
    if not expected or not secrets.compare_digest(supplied, expected):
        raise Http404("Not found.")

    from plfog import adapters

    review_code = os.environ.get("PLAY_REVIEW_CODE", "").strip()
    confirm_form = get_form_class(account_app_settings.FORMS, "confirm_login_code", ConfirmLoginCodeForm)

    payload: dict[str, Any] = {
        # Which build is actually serving this request.
        "app_version": VERSION,
        "render_git_commit": os.environ.get("RENDER_GIT_COMMIT", ""),
        "allauth_version": ".".join(str(part) for part in allauth.VERSION[:3]),
        # Is the merged code really here?
        "golden_form_present": hasattr(adapters, "GoldenTicketConfirmLoginCodeForm"),
        "adapter_overrides_generate_login_code": "generate_login_code" in adapters.AdminRedirectAccountAdapter.__dict__,
        # Is our form the one allauth will actually use for the confirm step?
        "confirm_login_code_form": _dotted_path(confirm_form),
        # Can this process see the secret at all?
        "play_review_code_present": bool(review_code),
        "play_review_code_length": len(review_code),
        "play_review_code_fingerprint": _fingerprint(review_code) if review_code else "",
    }
    return JsonResponse(payload)
