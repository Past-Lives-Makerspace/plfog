"""Regression specs: member-only paths must 404 (never 500) on the public surface.

``SurfaceMiddleware`` runs *before* ``AuthenticationMiddleware`` on purpose, so when it
raises ``Http404`` for a member-only path on a guest host, ``request.user`` has never been
set. The themed ``templates/404.html`` extends ``hub/base.html``, which runs every context
processor — so any processor that dereferences ``request.user`` bare turned that intended
404 into a 500.

These specs must run with ``DEBUG=False``. Under ``DEBUG=True`` Django serves its technical
404 page, which never touches the context processors — which is exactly why this bug shipped
to production unnoticed.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

# The member-only prefixes that are also plausible guest-facing links (crawlers, shared URLs).
MEMBER_ONLY_PATHS = [
    "/guilds/",
    "/guilds/woodworking-guild/",
    "/members/",
    "/settings/",
    "/billing/",
    "/tab/",
    "/feedback/",
]

GUEST_HOSTS = ["book.pastlives.space", "classes.pastlives.app"]


@pytest.fixture(autouse=True)
def _surface_hosts(settings) -> None:
    """Public + members hosts, with the real error page active (DEBUG off)."""
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "classes.pastlives.app", "members.pastlives.space"]
    settings.PUBLIC_HOSTS = ["book.pastlives.space", "classes.pastlives.app"]
    settings.MEMBER_HOST = "members.pastlives.space"


def describe_member_only_paths_on_the_public_surface():
    def describe_for_an_anonymous_visitor():
        @pytest.mark.parametrize("host", GUEST_HOSTS)
        @pytest.mark.parametrize("path", MEMBER_ONLY_PATHS)
        def it_renders_the_themed_404_instead_of_a_500(db, host: str, path: str) -> None:
            client = Client(HTTP_HOST=host, raise_request_exception=False)
            response = client.get(path)
            assert response.status_code == 404, f"{host}{path} returned {response.status_code}, expected 404"
            # Proves the real templates/404.html rendered (all context processors ran),
            # not Django's technical 404 which skips them entirely.
            assert b"We couldn't find that page." in response.content

    def describe_for_a_signed_in_member():
        @pytest.mark.parametrize("host", GUEST_HOSTS)
        @pytest.mark.parametrize("path", MEMBER_ONLY_PATHS)
        def it_renders_the_themed_404_instead_of_a_500(db, host: str, path: str) -> None:
            user = User.objects.create_user(username=f"guest-404-{abs(hash(path + host))}", password="pw-not-used")
            client = Client(HTTP_HOST=host, raise_request_exception=False)
            client.force_login(user)
            response = client.get(path)
            assert response.status_code == 404, f"{host}{path} returned {response.status_code}, expected 404"
            # Proves the real templates/404.html rendered (all context processors ran),
            # not Django's technical 404 which skips them entirely.
            assert b"We couldn't find that page." in response.content

    def describe_on_the_members_host():
        def it_does_not_404_member_only_paths(db) -> None:
            client = Client(HTTP_HOST="members.pastlives.space", raise_request_exception=False)
            response = client.get("/guilds/")
            assert response.status_code != 404
