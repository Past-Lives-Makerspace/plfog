"""BDD specs for retired public hosts (book.pastlives.space): a read forwards to the same path
on the class site, a write is still served there as the public surface."""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db

RETIRED = dict(
    ALLOWED_HOSTS=["book.pastlives.space", "classes.pastlives.space", "testserver"],
    PUBLIC_HOSTS=["classes.pastlives.space"],
    PUBLIC_REDIRECT_HOSTS=["book.pastlives.space"],
    BOOK_BASE_URL="https://classes.pastlives.space",
)


def describe_retired_public_host():
    @override_settings(**RETIRED)
    def it_forwards_a_page_to_the_same_path_and_query_on_the_class_site(client: Client):
        response = client.get("/classes/forge-basics/?utm_source=flyer", HTTP_HOST="book.pastlives.space")
        assert response.status_code == 301
        assert response["Location"] == "https://classes.pastlives.space/classes/forge-basics/?utm_source=flyer"

    @override_settings(**RETIRED)
    def it_forwards_a_head_request_too(client: Client):
        response = client.head("/classes/", HTTP_HOST="book.pastlives.space")
        assert response.status_code == 301
        assert response["Location"] == "https://classes.pastlives.space/classes/"

    @override_settings(**{**RETIRED, "BOOK_BASE_URL": "https://classes.pastlives.space/"})
    def it_does_not_double_the_slash_when_the_base_url_ends_in_one(client: Client):
        response = client.get("/classes/", HTTP_HOST="book.pastlives.space")
        assert response["Location"] == "https://classes.pastlives.space/classes/"

    @override_settings(**RETIRED)
    def it_serves_a_post_as_the_public_site_instead_of_redirecting_it(client: Client):
        # /admin/ is closed on the public surface, so a 404 (not a 301, not the admin) shows
        # the write was served here, as the public site.
        response = client.post("/admin/", HTTP_HOST="book.pastlives.space")
        assert response.status_code == 404

    @override_settings(**{**RETIRED, "PUBLIC_HOSTS": ["classes.pastlives.space", "book.pastlives.space"]})
    def it_serves_a_host_that_is_still_listed_as_public(client: Client):
        response = client.get("/", HTTP_HOST="book.pastlives.space")
        assert response.status_code == 302
        assert response["Location"] == "/classes/"

    @override_settings(**RETIRED)
    def it_leaves_the_class_site_itself_alone(client: Client):
        response = client.get("/", HTTP_HOST="classes.pastlives.space")
        assert response.status_code == 302
        assert response["Location"] == "/classes/"
