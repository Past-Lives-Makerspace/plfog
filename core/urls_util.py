"""Absolute-URL helpers shared across apps."""

from __future__ import annotations

from django.conf import settings


def book_absolute_url(path: str) -> str:
    """Turn a relative path into an absolute URL on the public book/classes site.

    The base is ``BOOK_BASE_URL`` (the host the classes URLs live on). This is
    the public home of what was ``classes.emails._absolute_url``, so other apps
    (e.g. billing's refund engine) don't reach into a private cross-app helper;
    ``classes.emails`` delegates here.
    """
    base = getattr(settings, "BOOK_BASE_URL", "https://book.pastlives.space").rstrip("/")
    return f"{base}{path}"
