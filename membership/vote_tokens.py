"""HMAC-signed voting tokens for stateless link authentication."""

from __future__ import annotations

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

SEPARATOR = "|"


def _signer() -> TimestampSigner:
    return TimestampSigner()


def generate_vote_token(member_record_id: str, session_id: int) -> str:
    """Generate a URL-safe signed token encoding member + session."""
    payload = f"{member_record_id}{SEPARATOR}{session_id}"
    return _signer().sign(payload)


def verify_vote_token(token: str) -> dict[str, str | int]:
    """Verify and decode a vote token.

    Returns dict with member_record_id and session_id.
    Raises BadSignature or SignatureExpired on failure.
    """
    max_age = getattr(settings, "VOTE_TOKEN_MAX_AGE", 60 * 60 * 24 * 30)
    payload = _signer().unsign(token, max_age=max_age)
    parts = payload.rsplit(SEPARATOR, 1)
    if len(parts) != 2:
        raise BadSignature("Invalid token payload")
    return {
        "member_record_id": parts[0],
        "session_id": int(parts[1]),
    }
