"""BDD specs for old class links from the Drupal site that lived at classes.pastlives.space.

That address now serves this app, so ``/class/<alias>`` (Discord posts, flyers, search results)
forwards to the class imported from that page, or answers 410 Gone when there is none to show
(#1259): a redirect to the catalog read to a search engine as a soft 404.
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
        _listed(legacy_cms_id="node-abc", slug="sewing-pattern", title="Sewing Pattern")
        response = _get(client, "/class/sewing-pattern")
        assert response.status_code == 301
        assert response["Location"] == "/classes/sewing-pattern/"

    @override_settings(**ON_CLASS_SITE)
    def it_accepts_a_trailing_slash(client: Client):
        _listed(legacy_cms_id="node-abc", slug="sewing-pattern", title="Sewing Pattern")
        assert _get(client, "/class/sewing-pattern/")["Location"] == "/classes/sewing-pattern/"

    @override_settings(**ON_CLASS_SITE)
    def it_finds_a_class_whose_name_was_taken_here_first(client: Client):
        _listed(slug="sewing-pattern")
        imported = _listed(legacy_cms_id="node-abc", slug="sewing-pattern-legacy")
        # The imported run's class page (#1260), whatever address that class has.
        assert _get(client, "/class/sewing-pattern")["Location"] == imported.public_path

    @override_settings(**ON_CLASS_SITE)
    def it_does_not_claim_a_class_written_here_with_the_same_name(client: Client):
        _listed(slug="sewing-pattern")
        response = _get(client, "/class/sewing-pattern")
        assert response.status_code == 410

    @override_settings(**ON_CLASS_SITE)
    def it_sends_an_archived_class_long_past_to_its_class_type(client: Client):
        # Archiving no longer hides a class (#1260); one with nothing in a year is Retired.
        offering = ClassOfferingFactory(
            legacy_cms_id="node-abc", slug="sewing-pattern", status=ClassOffering.Status.ARCHIVED
        )
        response = _get(client, "/class/sewing-pattern")
        assert response.status_code == 301
        assert response["Location"] == f"/classes/category/{offering.category.slug}/"

    @override_settings(**ON_CLASS_SITE)
    def it_answers_gone_for_an_unknown_class_with_a_way_to_the_catalog(client: Client):
        response = _get(client, "/class/never-existed")
        assert response.status_code == 410
        assert 'href="/classes/"' in response.content.decode()
