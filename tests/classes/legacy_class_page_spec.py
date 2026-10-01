"""BDD specs for old class links from the Drupal site that lived at classes.pastlives.space.

That address now serves this app, so ``/class/<alias>`` (Discord posts, flyers, search results)
forwards to the class imported from that page, or to the catalog when there is none to show.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db

ON_CLASS_SITE = dict(
    ALLOWED_HOSTS=["classes.pastlives.space", "testserver"],
    PUBLIC_HOSTS=["classes.pastlives.space"],
)


def _listed(**kwargs) -> ClassOffering:
    return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, **kwargs)


def _get(client: Client, path: str):
    return client.get(path, HTTP_HOST="classes.pastlives.space")


def describe_old_class_page_link():
    @override_settings(**ON_CLASS_SITE)
    def it_forwards_to_the_class_imported_from_that_page(client: Client):
        _listed(legacy_cms_id="node-abc", slug="sewing-pattern")
        response = _get(client, "/class/sewing-pattern")
        assert response.status_code == 301
        assert response["Location"] == "/classes/sewing-pattern/"

    @override_settings(**ON_CLASS_SITE)
    def it_accepts_a_trailing_slash(client: Client):
        _listed(legacy_cms_id="node-abc", slug="sewing-pattern")
        assert _get(client, "/class/sewing-pattern/")["Location"] == "/classes/sewing-pattern/"

    @override_settings(**ON_CLASS_SITE)
    def it_finds_a_class_whose_name_was_taken_here_first(client: Client):
        _listed(slug="sewing-pattern")
        _listed(legacy_cms_id="node-abc", slug="sewing-pattern-legacy")
        assert _get(client, "/class/sewing-pattern")["Location"] == "/classes/sewing-pattern-legacy/"

    @override_settings(**ON_CLASS_SITE)
    def it_does_not_claim_a_class_written_here_with_the_same_name(client: Client):
        _listed(slug="sewing-pattern")
        response = _get(client, "/class/sewing-pattern")
        assert response.status_code == 301
        assert response["Location"] == "/classes/"

    @override_settings(**ON_CLASS_SITE)
    def it_sends_a_class_that_is_no_longer_listed_to_the_catalog(client: Client):
        ClassOfferingFactory(legacy_cms_id="node-abc", slug="sewing-pattern", status=ClassOffering.Status.ARCHIVED)
        assert _get(client, "/class/sewing-pattern")["Location"] == "/classes/"

    @override_settings(**ON_CLASS_SITE)
    def it_sends_an_unknown_class_to_the_catalog(client: Client):
        response = _get(client, "/class/never-existed")
        assert response.status_code == 301
        assert response["Location"] == "/classes/"
