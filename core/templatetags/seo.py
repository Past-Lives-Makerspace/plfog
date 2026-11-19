"""The head tags search engines read (#1259): the title, the canonical address and JSON-LD.

``hub/base.html`` wraps its title block in ``{% filter page_title:brand_short_name %}`` and
writes ``{% canonical_url %}`` into its canonical block; a page that belongs at another address
overrides that block. ``{{ data|json_ld }}`` writes a schema.org object into a
``<script type="application/ld+json">``, and ``{{ url|absolute_url:request }}`` gives a media
or static path its host for an Open Graph tag.
"""

from __future__ import annotations

import json
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from django import template
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

from core import seo

register = template.Library()

#: What would let JSON close or open a tag inside the script element, as Django's ``json_script`` escapes it.
_JSON_ESCAPES = {ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026"}


@register.filter
def page_title(rendered: str, brand: str) -> SafeString:
    """The rendered title block as the page ``<title>``: the brand once, at most 60 characters.

    The block arrives already escaped, so it is unescaped to count real characters and escaped again.
    """
    return escape(seo.page_title(unescape(str(rendered)), brand))


@register.simple_tag(takes_context=True)
def canonical_url(context: template.Context, path: str = "") -> str:
    """This page's one address on the host it was served from: ``path`` (default the request's own)
    with no query string, except ``?page=N`` for a page past the first."""
    request: HttpRequest = context["request"]
    url = request.build_absolute_uri(path or request.path)
    page = request.GET.get("page", "")
    if not path and page.isdigit() and int(page) > 1:
        url += f"?page={int(page)}"
    return url


@register.filter
def json_ld(data: dict[str, Any]) -> SafeString:
    """``data`` as JSON safe to place inside a ``<script type="application/ld+json">`` element.

    Marked safe because the characters that could end the element are escaped, as ``json_script`` does.
    """
    return mark_safe(json.dumps(data, cls=DjangoJSONEncoder).translate(_JSON_ESCAPES))


@register.filter
def absolute_url(url: str, request: HttpRequest) -> str:
    """``url`` unchanged when it names a host (a storage URL), else on the request's host."""
    if urlsplit(url).netloc:
        return url
    return request.build_absolute_uri(url)
