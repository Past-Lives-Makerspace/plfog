"""BDD specs for Google Analytics coverage of the Django admin.

FOG is measured end to end, so the admin is tracked like everything else. The context
processor has no path exclusion and ``templates/admin/base.html`` pulls in the shared
partial via Unfold's ``extrahead`` block. These specs render real admin pages so a
regression in either half is caught, not just the context processor in isolation.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from core.models import SiteConfiguration


@pytest.mark.django_db
def describe_google_analytics_on_the_admin():
    def _sign_in_staff(client: Client) -> User:
        admin = User.objects.create_superuser(username="ga-admin", email="ga-admin@example.com", password="pass")
        client.force_login(admin)
        return admin

    def it_injects_the_tag_on_the_admin_index_when_configured(client: Client):
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-ADMIN123"
        config.save()
        _sign_in_staff(client)

        response = client.get("/admin/")

        assert response.status_code == 200
        assert b"googletagmanager.com" in response.content
        assert b"G-ADMIN123" in response.content

    def it_injects_the_tag_on_an_admin_changelist(client: Client):
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-ADMIN123"
        config.save()
        _sign_in_staff(client)

        response = client.get("/admin/auth/group/")

        assert response.status_code == 200
        assert b"G-ADMIN123" in response.content

    def it_omits_the_tag_on_the_admin_when_no_id_is_set(client: Client):
        _sign_in_staff(client)

        response = client.get("/admin/")

        assert response.status_code == 200
        assert b"googletagmanager.com" not in response.content
