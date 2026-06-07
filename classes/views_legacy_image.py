"""Legacy CMS image proxy view."""

from __future__ import annotations

import urllib.request

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse


ALLOWED_PREFIX = "https://classes.pastlives.space/"
CACHE_TIMEOUT = 86400  # 24 hours


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse all redirects so a compromised upstream can't SSRF via 301/302."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def legacy_image(request: HttpRequest) -> HttpResponse:
    """Proxy an image from the legacy CMS, with 24-hour caching.

    Query parameter: ?url=<encoded-url>

    Only fetches from https://classes.pastlives.space/ to prevent SSRF.
    Redirects are refused so a compromised upstream cannot redirect to an
    internal address.
    """
    url = request.GET.get("url", "")
    if not url.startswith(ALLOWED_PREFIX):
        return HttpResponse("Forbidden", status=403)

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
