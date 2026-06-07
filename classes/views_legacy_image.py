"""Legacy CMS image proxy view."""

from __future__ import annotations

import urllib.request

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse


ALLOWED_PREFIX = "https://classes.pastlives.space/"
CACHE_TIMEOUT = 86400  # 24 hours


def legacy_image(request: HttpRequest) -> HttpResponse:
    """Proxy an image from the legacy CMS, with 24-hour caching.

    Query parameter: ?url=<encoded-url>

    Only fetches from https://classes.pastlives.space/ to prevent SSRF.
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
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
    except Exception:
        return HttpResponse("Not Found", status=404)

    cache.set(cache_key, (content_type, data), CACHE_TIMEOUT)
    return HttpResponse(data, content_type=content_type)
