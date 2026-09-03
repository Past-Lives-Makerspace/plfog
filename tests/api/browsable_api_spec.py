"""BDD specs for the browsable API shell override (templates/rest_framework/api.html)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def describe_browsable_api_shell():
    def it_ships_a_viewport_meta_so_phones_render_at_device_width(admin_client):
        response = admin_client.get("/api/v1/", HTTP_ACCEPT="text/html")
        assert response.status_code == 200
        assert b'<meta name="viewport" content="width=device-width, initial-scale=1.0">' in response.content
