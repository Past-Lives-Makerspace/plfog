"""Legacy CMS image proxy view.

Fallback only. Every offering migrated by ``download_legacy_images`` serves its
picture straight out of our own storage, and the templates always prefer
``offering.image``; this view exists solely for a class that has just arrived
from the legacy feed and whose image has not been pulled across yet.

It is also the one piece of the app that makes an outbound HTTP request while a
page is rendering, so it refuses to fetch from any hostname this deployment
itself answers on (see :func:`_is_own_host`).
"""

from __future__ import annotations

import logging
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse


logger = logging.getLogger(__name__)

ALLOWED_PREFIX = "https://classes.pastlives.space/"
CACHE_TIMEOUT = 86400  # 24 hours


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse all redirects so a compromised upstream can't SSRF via 301/302."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _is_own_host(hostname: str, request: HttpRequest) -> bool:
    """Whether ``hostname`` is a name this deployment answers on itself.

    ``classes.pastlives.space`` currently resolves to the Drupal server, but the
    plan is to repoint that DNS record at this app. The moment that happens (and
    the host joins ``DJANGO_ALLOWED_HOSTS``), a catalog page rendering two dozen
    fallbacks would fire two dozen HTTP requests from the app back into the app —
    self-inflicted worker starvation on a two-worker gunicorn. So the guard keys
    off exactly the fact that makes the fetch dangerous: the target is us.
    """
    if not hostname:
        return False
    own = {request.get_host().split(":")[0].strip().lower()}
    own |= {h.strip().lstrip("*.").lower() for h in settings.ALLOWED_HOSTS if h.strip() not in ("", "*")}
    return hostname.strip().lower() in own


def legacy_image(request: HttpRequest) -> HttpResponse:
    """Proxy an image from the legacy CMS, with 24-hour caching.

    Query parameter: ?url=<encoded-url>

    Only fetches from https://classes.pastlives.space/ to prevent SSRF.
    Redirects are refused so a compromised upstream cannot redirect to an
    internal address, and the app never fetches from one of its own hostnames.
    """
    url = request.GET.get("url", "")
    if not url.startswith(ALLOWED_PREFIX):
        return HttpResponse("Forbidden", status=403)

    hostname = urlsplit(url).hostname or ""
    if _is_own_host(hostname, request):
        logger.error(
            "Legacy image proxy refused to fetch %s: %s now resolves to this app, so proxying it "
            "would make the app request itself. The legacy CMS is gone — run "
            "download_legacy_images so these classes serve their own stored images.",
            url,
            hostname,
        )
        return HttpResponse("Not Found", status=404)

    cache_key = f"legacy_image:{url}"
    cached = cache.get(cache_key)
    if cached:
        content_type, data = cached
        return HttpResponse(data, content_type=content_type)

    try:
        with _OPENER.open(url, timeout=10) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
    except Exception:
        return HttpResponse("Not Found", status=404)

    cache.set(cache_key, (content_type, data), CACHE_TIMEOUT)
    return HttpResponse(data, content_type=content_type)
