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
    base = getattr(settings, "BOOK_BASE_URL", "https://classes.pastlives.space").rstrip("/")
    return f"{base}{path}"


def notification_click_url(url: str) -> str:
    """Where a bell or push click should go: our own links as a path on the member's host.

    Senders build ``url`` for every channel at once, so many are absolute on the
    classes host (``BOOK_BASE_URL``), the retired book host or the members host. Absolute
    is right for an email or a Discord post, wrong for a click inside the hub: the native
    app hands any host but its own to the system browser, where the member is signed out,
    and the classes host 404s the teach and admin pages it links to. Every path resolves
    on the members host (a public only one such as ``/account/`` redirects from there), so
    a link on one of our hosts keeps only its path, query and fragment. A foreign link is
    returned unchanged.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.netloc:
        return url
    our_hosts = {
        urlsplit(settings.MEMBER_BASE_URL).hostname,
        urlsplit(getattr(settings, "BOOK_BASE_URL", "")).hostname,
        getattr(settings, "MEMBER_HOST", ""),
        *getattr(settings, "PUBLIC_HOSTS", []),
        *getattr(settings, "PUBLIC_REDIRECT_HOSTS", []),
        *getattr(settings, "GUILDS_HOSTS", []),
    }
    if parts.hostname not in our_hosts:
        return url
    return urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment))
