"""Public routes by class state (#1260, part 5): a Retired class goes to its Class Type in one hop."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering, ClassRecord

pytestmark = pytest.mark.django_db


def _run(slug: str, *, days: int, status=ClassOffering.Status.PUBLISHED, **extra) -> ClassOffering:
    extra.setdefault("title", "Picture Framing")
    extra.setdefault("category", CategoryFactory(slug="woodworking"))
    run = ClassOfferingFactory(slug=slug, status=status, **extra)
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=run, starts_at=start, ends_at=start + timedelta(hours=2))
    run.refresh_from_db()
    return run


def _retired(slug: str = "picture-framing-2024-06-01", **extra) -> ClassOffering:
    return _run(slug, days=-400, **extra)


CLASS_TYPE = "/classes/category/woodworking/"


def describe_a_retired_class():
    def it_is_retired_by_its_dates():
        assert _retired().class_record.state == ClassRecord.State.RETIRED

    @pytest.mark.parametrize(
        "path",
        [
            "/classes/picture-framing/",
            "/classes/picture-framing-2024-06-01/",
            "/classes/picture-framing-2024-06-01/register/",
            "/classes/picture-framing-2024-06-01/register/success/?reg=tok",
        ],
    )
    def it_sends_every_address_to_its_class_type_in_one_hop(client, path):
        _retired()

        response = client.get(path)

        assert response.status_code == 301
        assert response["Location"] == CLASS_TYPE

    def it_sends_its_qr_permalink_there_too(client):
        run = _retired()

        response = client.get(reverse("classes:class_permalink", kwargs={"pk": run.pk}))

        assert (response.status_code, response["Location"]) == (301, CLASS_TYPE)

    def it_sends_its_old_site_link_there_too(client):
        _retired("picture-framing-legacy", legacy_cms_id="node-9")

        response = client.get("/class/picture-framing")

        assert (response.status_code, response["Location"]) == (301, CLASS_TYPE)

    def it_links_its_class_type_from_emails_and_cards():
        assert _retired().public_path == CLASS_TYPE


def describe_a_class_that_is_still_live():
    def it_moves_its_qr_permalink_permanently_to_the_class(client):
        run = _run("picture-framing-2026-12-01", days=10)

        response = client.get(reverse("classes:class_permalink", kwargs={"pk": run.pk}))

        assert (response.status_code, response["Location"]) == (301, "/classes/picture-framing/")

    def it_shows_a_class_whose_runs_are_archived(client):
        _run("picture-framing-2026-10-01", days=-30, status=ClassOffering.Status.ARCHIVED)

        assert client.get("/classes/picture-framing/").status_code == 200
        assert client.get("/classes/picture-framing-2026-10-01/")["Location"] == "/classes/picture-framing/"

    @pytest.mark.parametrize(
        "status", [ClassOffering.Status.DRAFT, ClassOffering.Status.PENDING, ClassOffering.Status.CANCELLED]
    )
    def it_still_hides_a_class_nobody_may_see(client, status):
        _run("picture-framing-2026-12-01", days=10, status=status)

        assert client.get("/classes/picture-framing/").status_code == 404
        assert client.get("/classes/picture-framing-2026-12-01/").status_code == 404

    def it_still_hides_a_private_class(client):
        _run("picture-framing-2026-12-01", days=10, is_private=True)

        assert client.get("/classes/picture-framing/").status_code == 404
