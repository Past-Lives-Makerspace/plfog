"""Tests for ServiceWorkerAllowedMiddleware."""

import pytest

pytestmark = pytest.mark.django_db


def describe_service_worker_allowed_middleware():
    """Test the ServiceWorkerAllowedMiddleware adds correct header."""

    def it_adds_header_for_sw_js(client):
        """Middleware should add Service-Worker-Allowed header for /sw.js."""
        response = client.get("/sw.js")
        assert response.status_code == 200
        assert response["Service-Worker-Allowed"] == "/"

    def it_does_not_add_header_for_other_paths(client):
        """Middleware should not add header for other paths."""
        response = client.get("/")
        assert response.get("Service-Worker-Allowed") is None
