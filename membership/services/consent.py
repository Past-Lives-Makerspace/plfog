"""Fingerprinting the document a member consented to.

The Member Agreement is not served by fog — Site Settings holds a URL, normally into the
Knowledge Base, and the prompt links out to it. So at the moment a member clicks "I agree", fog
knows who and when but has never seen the text.

That is the gap this closes. Before writing the acceptance row, fog fetches the document and
stores a SHA-256 of exactly what came back. It does not prove the member read it; it proves what
was published at that URL when they agreed, which is the question that gets asked when someone
later says the terms were different.

⚠️ This is evidence of consent, not a signature. It is the interim measure until PLM's
self-hosted DocuSeal is built out, at which point members should be asked to SIGN the Member
Agreement properly. A hash of a web page is good enough to re-consent to a revised Code of
Conduct; it is not what you want standing behind a contract.

Deliberately best-effort. If the KB is slow or down, the member still gets in and the row is
written with a blank hash — blank meaning "not captured", never "unchanged". Blocking someone
from the hub because a fingerprint fetch timed out would be a worse failure than a thinner record.
"""

from __future__ import annotations

import hashlib
import logging

import httpx

logger = logging.getLogger(__name__)

# Short on purpose: this runs inside the POST that accepts the agreement, so it is time the
# member spends watching a spinner. A slow KB costs the fingerprint, not the acceptance.
_TIMEOUT_SECONDS = 3.0

# A published agreement is a document, not a download. Anything larger is not the thing we meant
# to hash, and streaming an unbounded response into memory on a web worker is how a page becomes
# an outage.
_MAX_BYTES = 5_000_000


def fingerprint_agreement(url: str) -> tuple[str, int | None]:
    """Return ``(sha256_hex, byte_length)`` for the document at ``url``.

    Returns ``("", None)`` if it cannot be fetched, is too large, or the URL is blank.
    """
    if not url:
        return "", None

    # Streamed and hashed a chunk at a time, and abandoned the moment it runs over. The previous
    # form read `response.content` — which buffers the whole body — and only THEN compared its
    # length to the ceiling, so the ceiling stopped an oversized document being hashed but did
    # nothing about the memory it had already taken. That is the outage this function's own
    # comment warns about, so the check has to happen while reading rather than after it.
    hasher = hashlib.sha256()
    size = 0
    try:
        with httpx.stream("GET", url, timeout=_TIMEOUT_SECONDS, follow_redirects=True) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_BYTES:
                    logger.warning("Member agreement at %s exceeds %d bytes; not fingerprinted", url, _MAX_BYTES)
                    return "", None
                hasher.update(chunk)
    except httpx.HTTPError:
        # Logged, not raised: the caller is mid-acceptance and must not fail because of this.
        logger.warning("Could not fingerprint the member agreement at %s", url, exc_info=True)
        return "", None

    return hasher.hexdigest(), size
