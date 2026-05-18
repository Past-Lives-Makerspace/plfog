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

Member auth (``/accounts/*``) on the public surface is redirected to the
members host so the allauth session cookie always lands on the host where
the member dashboard lives.

The root path on the public surface redirects to ``/classes/`` so the bare
domain lands on the catalog rather than the member hub home.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect


class SurfaceMiddleware:
    """Route by hostname into one of two surfaces: public or members."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        host = request.get_host().split(":", 1)[0].lower()
        public_hosts: set[str] = set(getattr(settings, "PUBLIC_HOSTS", []))
        request.surface = "public" if host in public_hosts else "members"

        if request.surface == "public":
            short_circuit = self._handle_public_surface(request)
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

        if path.startswith("/accounts/"):
            member_host = getattr(settings, "MEMBER_HOST", "")
            if member_host:
                scheme = "https" if request.is_secure() else "http"
                query = f"?{request.META['QUERY_STRING']}" if request.META.get("QUERY_STRING") else ""
                return HttpResponseRedirect(f"{scheme}://{member_host}{path}{query}")

        member_only_prefixes: tuple[str, ...] = tuple(getattr(settings, "MEMBER_ONLY_PATH_PREFIXES", ()))
        for prefix in member_only_prefixes:
            if path.startswith(prefix):
                raise Http404("Not available on this surface.")

        return None
