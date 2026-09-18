"""Reading htmx's request headers correctly.

There is one rule here and it is easy to get backwards, which is why it lives in a
function instead of being rewritten at each call site.

A view that answers some requests with a fragment and others with a whole page cannot
decide on ``HX-Request`` alone, because the hub boosts its whole body: an ordinary
navigation arrives carrying ``HX-Request`` too. The usual fix is to exclude
``HX-Boosted``, and that is only half of it.

The other half is the history restore. The hub keeps no htmx history cache
(``static/js/hub_boot.js``, issue #383), so pressing Back misses and htmx refetches the
page. That refetch sends ``HX-Request: true`` and ``HX-History-Restore-Request: true``
but **no** ``HX-Boosted``. So a restore looks exactly like the fragment case to a check
that only excludes boosted requests, and the member gets a bare fragment swapped in
where their page used to be. Measured on the public class catalog: 341,276 bytes with a
document, 339 bytes without.

A restore is a GET, so a view behind ``@require_POST`` cannot be reached this way.

There is deliberately no "treat boosted requests as fragment requests" option. The one
view that wanted it (``orientation_checkout_return``) turned out to be its own
counterexample: "Resume payment" is a plain ``<form method="post">`` inside the boosted
body, and the redirect it follows arrives carrying ``HX-Boosted``, so the option would
have swapped a bare polling card into the page on exactly that path.
"""

from __future__ import annotations

from django.http import HttpRequest


def wants_fragment(request: HttpRequest) -> bool:
    """True when this request should be answered with a partial rather than a page.

    Args:
        request: The request to inspect.

    Returns:
        Whether to render the fragment.
    """
    if request.headers.get("HX-Request") != "true":
        return False
    if request.headers.get("HX-History-Restore-Request") == "true":
        return False
    if request.headers.get("HX-Boosted") == "true":
        return False
    return True
