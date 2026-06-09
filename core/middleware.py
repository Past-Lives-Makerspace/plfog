"""Surface routing middleware for plfog.

Plfog answers to two hostnames:

- ``members.pastlives.space`` (and local dev, Hetzner staging, Render preview):
  the full member application. ``request.surface == "members"``.
- ``book.pastlives.space``: a public-only face that exposes the class catalog,
  class detail pages, registration, and self-serve registration management.
  Everything else (admin, billing, voting, settings, member directory, the
  classes admin/instructor dashboards, etc.) returns 404 from this surface.
  ``request.surface == "public"``.

The middleware tags every request with ``request.surface`` so templates and
views can branch on the chrome they should render, and short-circuits any
request to a member-only path that arrives on the public surface.

Member auth (``/accounts/*``) is served on both surfaces. Session cookies
are scoped to ``.pastlives.space`` so a login completed on book is
recognised on members automatically.

The root path on the public surface redirects to ``/classes/`` so the bare
domain lands on the catalog rather than the member hub home.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect


class SurfaceMiddleware:
    """Route by hostname into one of two surfaces: public or members."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        host = request.get_host().split(":", 1)[0].lower()
        public_hosts: set[str] = set(getattr(settings, "PUBLIC_HOSTS", []))
        request.surface = "public" if host in public_hosts else "members"  # type: ignore[attr-defined]

        if request.surface == "public":  # type: ignore[attr-defined]
            short_circuit = self._handle_public_surface(request)
            if short_circuit is not None:
                return short_circuit
        else:
            short_circuit = self._handle_members_surface(request)
            if short_circuit is not None:
                return short_circuit

        return self.get_response(request)

    def _handle_public_surface(self, request: HttpRequest) -> HttpResponse | None:
        """Apply the public surface's restrictions and redirects.

        Returns a response when the request should be short-circuited, or
        ``None`` to let it continue to the view.
        """
        path = request.path

        if path == "/":
            return HttpResponseRedirect("/classes/")

        # Allauth runs on both surfaces — session cookies scope to .pastlives.space
        # (see SESSION_COOKIE_DOMAIN in settings) so a login on book is recognised
        # on members. The templates branch chrome on is_public_surface.

        member_only_prefixes: tuple[str, ...] = tuple(getattr(settings, "MEMBER_ONLY_PATH_PREFIXES", ()))
        for prefix in member_only_prefixes:
            if path.startswith(prefix):
                raise Http404("Not available on this surface.")

        return None

    def _handle_members_surface(self, request: HttpRequest) -> HttpResponse | None:
        """Redirect public-only paths back to the book host.

        The members host serves the full member application. Paths like
        ``/account/`` only exist on the public surface — bouncing a member who
        typed the wrong host back to book is friendlier than a 404.
        """
        public_only_prefixes: tuple[str, ...] = tuple(getattr(settings, "PUBLIC_ONLY_PATH_PREFIXES", ()))
        if not public_only_prefixes:
            return None
        public_hosts = list(getattr(settings, "PUBLIC_HOSTS", []))
        if not public_hosts:
            return None
        book_host = public_hosts[0]
        for prefix in public_only_prefixes:
            if request.path.startswith(prefix):
                scheme = "https" if request.is_secure() else "http"
                query = f"?{request.META['QUERY_STRING']}" if request.META.get("QUERY_STRING") else ""
                return HttpResponseRedirect(f"{scheme}://{book_host}{request.path}{query}")
        return None


class ToastFlashMiddleware:
    """Carry Django messages across a redirect as a short-lived cookie.

    The member UI shows messages as toast notifications that JS drains from the
    page. That works for same-request renders and ordinary full-page redirects,
    but under htmx ``hx-boost`` a redirect's message-bearing render is not reliably
    the one swapped into the DOM, so the toast silently vanishes. A cookie survives
    every navigation kind, so for an *htmx* redirect we move pending messages into
    the ``pl_toast`` cookie for the destination page's toast script to read and
    clear.

    Scoped to htmx requests on purpose: ordinary navigations (public/login pages
    that render messages visibly, full-page member saves) keep Django's standard
    message flow untouched, and only the boosted path that would otherwise lose
    the message is rerouted through the cookie.
    """

    COOKIE = "pl_toast"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.process_response(request, self.get_response(request))

    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        # Only act on htmx-driven redirects: that is the path where the boosted
        # swap can drop the message. Consuming the storage here also stops it
        # rendering again on the destination, so the toast fires exactly once.
        is_htmx = request.headers.get("HX-Request") == "true"
        if is_htmx and 300 <= response.status_code < 400 and hasattr(request, "_messages"):
            from django.contrib.messages import get_messages

            pending = [{"message": str(m), "type": m.level_tag or "info"} for m in get_messages(request)]
            if pending:
                response.set_cookie(
                    self.COOKIE,
                    quote(json.dumps(pending)),
                    max_age=30,
                    samesite="Lax",
                    secure=request.is_secure(),
                    path="/",
                )
        return response
