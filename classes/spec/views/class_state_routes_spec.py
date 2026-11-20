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
        ["/classes/picture-framing/", "/classes/picture-framing-2024-06-01/"],
    )
    def it_sends_its_page_and_old_run_addresses_to_its_class_type_for_a_day(client, path):
        _retired()

        response = client.get(path)

        assert response.status_code == 301
        assert response["Location"] == CLASS_TYPE
        assert response["Cache-Control"] == "max-age=86400"

    def it_forwards_an_old_register_address_to_the_runs_own_route(client):
        run = _retired()

        response = client.get("/classes/picture-framing-2024-06-01/register/success/?reg=tok")

        assert response["Location"] == f"/classes/picture-framing/register/{run.pk}/success/?reg=tok"

    def it_sends_its_qr_permalink_there_too(client):
        run = _retired()

        response = client.get(reverse("classes:class_permalink", kwargs={"pk": run.pk}))

        assert (response.status_code, response["Location"]) == (301, CLASS_TYPE)
        assert response["Cache-Control"] == "max-age=86400"

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
        assert response["Cache-Control"] == "max-age=86400"

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


def describe_a_class_no_longer_offered_with_a_date_to_come():
    def _marked() -> ClassOffering:
        run = _run("picture-framing-2026-12-01", days=10)
        run.class_record.mark_no_longer_offered(None)
        run.refresh_from_db()
        return run

    @pytest.mark.parametrize(
        ("suffix", "route_suffix", "query"),
        [("register/", "", "waitlist_token=abc123"), ("register/success/", "success/", "reg=tok")],
    )
    def it_still_forwards_a_registrants_old_link_with_its_token(client, suffix, route_suffix, query):
        run = _marked()

        response = client.get(f"/classes/picture-framing-2026-12-01/{suffix}?{query}")

        assert response.status_code == 301
        assert response["Location"] == f"/classes/picture-framing/register/{run.pk}/{route_suffix}?{query}"

    def it_sends_visitors_of_its_page_to_its_class_type(client):
        _marked()

        assert client.get("/classes/picture-framing/")["Location"] == CLASS_TYPE

    def it_links_its_registrants_to_the_dates_register_page():
        run = _marked()

        assert run.public_path == run.register_path

    def it_leaves_the_catalog_class_type_page_and_sitemap(client):
        run = _marked()

        assert run not in ClassOffering.objects.bookable()
        assert run not in ClassOffering.objects.indexable()
        assert run.register_path not in client.get(CLASS_TYPE).content.decode()
        assert "Picture Framing" not in client.get(reverse("classes:public_list")).content.decode()
