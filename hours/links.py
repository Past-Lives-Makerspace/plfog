"""My Hours' address for a request that named a person (#1300): the old editors' routes redirect here.

``to_my_hours`` answers such a request; ``my_hours_url`` is the address.

An old Edit Hours or Days Off link named its person with ``orienter``; My Hours names them with
``member``. The viewer's own, or no one, is the bare page.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse


def my_hours_url(request: HttpRequest, raw: str) -> str:
    """``/hours/``, with ``?member=<pk>`` when ``raw`` names someone other than the viewer."""
    url = reverse("hours:my_hours")
    member = getattr(request.user, "member", None)
    if not raw.isdigit() or (member is not None and raw == str(member.pk)):
        return url
    return f"{url}?member={raw}"


def to_my_hours(request: HttpRequest, raw: str, anchor: str = "") -> HttpResponse:
    """Send a request for an old editor to My Hours: ``HX-Redirect`` over HTMX (a whole page, never into a modal), else a 302."""
    url = f"{my_hours_url(request, raw)}{anchor}"
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return HttpResponseRedirect(url)
