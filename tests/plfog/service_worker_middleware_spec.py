"""Tests for ServiceWorkerAllowedMiddleware."""

from django.http import HttpRequest, HttpResponse

from plfog.service_worker_middleware import ServiceWorkerAllowedMiddleware


def _make_request(path: str) -> HttpRequest:
    """Create an HttpRequest with the given path."""
    request = HttpRequest()
    request.path = path
    return request


def _make_middleware() -> ServiceWorkerAllowedMiddleware:
    """Create a middleware instance with a passthrough get_response."""
    return ServiceWorkerAllowedMiddleware(get_response=lambda r: HttpResponse())


def describe_service_worker_allowed_middleware():
    """Test the ServiceWorkerAllowedMiddleware adds correct header."""

    def it_adds_header_for_sw_js():
        """Middleware should add Service-Worker-Allowed header for /sw.js."""
        middleware = _make_middleware()
        request = _make_request("/sw.js")
        response = middleware(request)
        assert response["Service-Worker-Allowed"] == "/"

    def it_does_not_add_header_for_other_paths():
        """Middleware should not add header for non-/sw.js paths."""
        middleware = _make_middleware()
        request = _make_request("/")
        response = middleware(request)
        assert "Service-Worker-Allowed" not in response

    def it_does_not_add_header_for_similar_paths():
        """Middleware should not add header for paths that look similar to /sw.js."""
        middleware = _make_middleware()
        for path in ["/sw.js/", "/static/sw.js", "/sw.json", "/sw.jsx"]:
            request = _make_request(path)
            response = middleware(request)
            assert "Service-Worker-Allowed" not in response, f"Header should not be set for path {path}"
