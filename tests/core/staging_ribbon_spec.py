"""BDD specs for the STAGING ribbon: on every page of a staging deployment, on none elsewhere.

Two bases render it: ``base.html`` (the home and account pages on the members host) and
``hub/base.html``, which the public catalog's ``classes/base_public.html`` extends, so the
catalog on the book host stands in for every hub-based page.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

_RIBBON = 'class="pl-staging-ribbon"'
_STYLESHEET = "css/staging-ribbon.css"
_BOOK_HOST = "book.pastlives.space"


@pytest.fixture
def book_surface(settings):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, _BOOK_HOST]
    settings.PUBLIC_HOSTS = [_BOOK_HOST]


def describe_the_public_base():
    def it_shows_the_ribbon_and_links_its_stylesheet_on_staging(client, settings):
        settings.IS_STAGING = True
        response = client.get(reverse("home"))
        content = response.content.decode()
        assert response.status_code == 200
        assert _RIBBON in content
        assert _STYLESHEET in content

    def it_ships_nothing_for_it_off_staging(client, settings):
        settings.IS_STAGING = False
        content = client.get(reverse("home")).content.decode()
        assert _RIBBON not in content
        assert _STYLESHEET not in content


def describe_the_hub_base():
    def it_shows_the_ribbon_on_the_public_catalog_on_staging(client, settings, book_surface):
        settings.IS_STAGING = True
        response = client.get(reverse("classes:public_list"), HTTP_HOST=_BOOK_HOST)
        content = response.content.decode()
        assert response.status_code == 200
        assert _RIBBON in content
        assert _STYLESHEET in content

    def it_ships_nothing_for_it_off_staging(client, settings, book_surface):
        settings.IS_STAGING = False
        content = client.get(reverse("classes:public_list"), HTTP_HOST=_BOOK_HOST).content.decode()
        assert _RIBBON not in content
        assert _STYLESHEET not in content
