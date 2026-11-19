"""One permanent page per class (#1260, part 2): the class address, its dates, and old run addresses."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering, ClassRecord

pytestmark = pytest.mark.django_db


def _run(slug: str, *, days: int, category=None, **extra) -> ClassOffering:
    run = ClassOfferingFactory(
        title="Picture Framing",
        slug=slug,
        category=category or CategoryFactory(),
        status=ClassOffering.Status.PUBLISHED,
        **extra,
    )
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=run, starts_at=start, ends_at=start + timedelta(hours=2))
    return run


def _class_path(run: ClassOffering) -> str:
    return reverse("classes:public_class_detail", kwargs={"slug": run.class_record.slug})


def describe_the_class_page():
    def it_shows_the_newest_runs_content_and_every_open_date_with_its_own_register(client):
        first = _run("picture-framing-2026-12-01", days=10, description="Older copy.")
        newest = _run("picture-framing-2027-01-05", days=40, category=first.category, description="Newest copy.")

        response = client.get(_class_path(first))

        assert response.status_code == 200
        assert response.context["offering"] == newest
        body = response.content.decode()
        assert "Newest copy." in body
        assert "Upcoming Dates" in body
        for run in (first, newest):
            assert f'id="date-{run.pk}"' in body
            assert f'data-register-run="{run.pk}"' in body
            assert reverse("classes:register", kwargs={"slug": run.slug}) in body

    def it_sends_a_full_date_to_its_waitlist(client):
        first = _run("framing-1", days=10, capacity=0)
        _run("framing-2", days=40, category=first.category)

        body = client.get(_class_path(first)).content.decode()

        assert reverse("classes:register", kwargs={"slug": first.slug}) + "?waitlist=1" in body
        assert "Join the waitlist" in body

    def it_lists_no_dates_section_for_a_single_date(client):
        run = _run("framing-1", days=10)

        assert "Upcoming Dates" not in client.get(_class_path(run)).content.decode()

    def it_says_how_many_times_the_class_has_run(client):
        past = _run("framing-1", days=-30)
        _run("framing-2", days=-60, category=past.category)
        _run("framing-3", days=10, category=past.category)

        body = client.get(_class_path(past)).content.decode()

        assert "Run <strong>2</strong> times" in body

    def it_is_not_found_while_no_run_is_public(client):
        run = ClassOfferingFactory(title="Draft Class", slug="draft-class-2026")

        assert client.get(_class_path(run)).status_code == 404

    def it_leaves_runs_of_the_same_class_out_of_related_classes(client):
        first = _run("framing-1", days=10)
        _run("framing-2", days=40, category=first.category)
        other = ClassOfferingFactory(
            title="Mat Cutting", slug="mat-cutting", category=first.category, status=ClassOffering.Status.PUBLISHED
        )
        ClassSessionFactory(class_offering=other, starts_at=timezone.now() + timedelta(days=5))

        related = client.get(_class_path(first)).context["related_offerings"]

        assert [run.pk for run in related] == [other.pk]


def describe_an_old_run_address():
    def it_moves_permanently_to_its_class_keeping_the_query(client):
        first = _run("picture-framing-2026-12-01", days=10)
        later = _run("picture-framing-2026-12-01-run", days=40, category=first.category)

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": later.slug}) + "?utm_source=x")

        assert response.status_code == 301
        assert response["Location"] == _class_path(later) + "?utm_source=x"

    def it_moves_without_a_query_when_there_is_none(client):
        run = _run("picture-framing-2026-12-01", days=10)

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": run.slug}))

        assert response["Location"] == _class_path(run)

    def it_is_not_found_for_a_run_nobody_may_see(client):
        live = _run("framing-1", days=10)
        draft = ClassOfferingFactory(title="Picture Framing", slug="framing-draft", category=live.category)

        assert client.get(reverse("classes:public_class_detail", kwargs={"slug": draft.slug})).status_code == 404

    def it_still_shows_a_run_with_no_class_at_its_own_address(client):
        run = _run("framing-1", days=10)
        ClassOffering.objects.filter(pk=run.pk).update(class_record=None)
        ClassRecord.objects.all().delete()

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": run.slug}))

        assert response.status_code == 200
        assert response.context["offering"] == run


def describe_links_to_a_run():
    def it_points_at_the_class(client):
        run = _run("picture-framing-2026-12-01", days=10)

        assert run.public_path == "/classes/picture-framing/"
        assert run.public_url.endswith("/classes/picture-framing/")

    def it_points_at_the_run_until_it_has_a_class():
        run = _run("framing-1", days=10)
        run.class_record_id = None

        assert run.public_path == "/classes/framing-1/"

    def it_sends_a_qr_permalink_to_the_class(client):
        run = _run("picture-framing-2026-12-01", days=10)

        response = client.get(reverse("classes:class_permalink", kwargs={"pk": run.pk}))

        assert response["Location"] == _class_path(run)

    def it_sends_an_old_site_link_to_the_class(client):
        run = _run("picture-framing-legacy", days=10, legacy_cms_id="node-1")

        response = client.get("/class/picture-framing")

        assert response.status_code == 301
        assert response["Location"] == _class_path(run)
