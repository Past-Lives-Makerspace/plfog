"""One permanent page per class (#1260, part 2): the class address, its dates, and old run addresses."""

from __future__ import annotations

from datetime import timedelta

import json
import re

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

    def it_says_how_many_times_the_class_was_taught_before(client):
        past = _run("framing-1", days=-30)
        _run("framing-2", days=-60, category=past.category)
        _run("framing-3", days=10, category=past.category)
        _run("framing-4", days=40, category=past.category)

        body = client.get(_class_path(past)).content.decode()

        assert "Taught <strong>2</strong> times before" in body

    def it_says_nothing_of_past_runs_for_a_class_never_taught(client):
        run = _run("framing-1", days=10)

        assert "data-times-taught" not in client.get(_class_path(run)).content.decode()

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


def describe_a_class_with_several_dates():
    def _three_dates():
        soon = _run("framing-1", days=10, price_cents=7000)
        _run("framing-2", days=30, category=soon.category, capacity=0)
        _run("framing-3", days=60, category=soon.category, price_cents=9000)
        return soon

    def it_names_the_soonest_date_in_the_hero(client):
        soon = _three_dates()

        body = client.get(_class_path(soon)).content.decode()

        first = soon.sessions.get().starts_at
        assert f"Next date {timezone.localtime(first).strftime('%a, %b')} {timezone.localtime(first).day}" in body
        assert "Next session" not in body

    def it_drops_the_single_date_schedule_for_the_upcoming_dates(client):
        soon = _three_dates()

        body = client.get(_class_path(soon)).content.decode()

        assert ">Schedule<" not in body
        assert 'id="upcoming-dates"' in body

    def it_sends_the_rail_button_to_the_dates_without_a_spots_line(client):
        soon = _three_dates()

        body = client.get(_class_path(soon)).content.decode()

        assert 'data-pick-a-date href="#upcoming-dates">Pick a date</a>' in body
        assert "spots available" not in body
        assert "Register now" not in body

    def it_prices_the_rail_by_the_soonest_date(client):
        soon = _three_dates()

        response = client.get(_class_path(soon))

        assert response.context["next_run"] == soon
        assert '<div class="cp-detail__price">$70</div>' in response.content.decode()

    def it_gives_the_soonest_dates_registration_close_in_the_rail(client):
        soon = _three_dates()
        ClassOffering.objects.filter(class_record=soon.class_record).update(registration_cutoff_hours=48)

        response = client.get(_class_path(soon))

        closes = timezone.localtime(soon.sessions.get().starts_at - timedelta(hours=48))
        assert f'data-registration-closes-at="{closes.isoformat()}"' in response.content.decode()


def describe_a_class_with_one_date():
    def it_keeps_the_schedule_the_next_session_and_register_now(client):
        run = _run("framing-1", days=10)
        _run("framing-0", days=-30, category=run.category)

        response = client.get(_class_path(run))
        body = response.content.decode()

        assert response.context["offering"] == run
        assert "Next session" in body
        assert ">Schedule<" in body
        assert "Register now" in body
        assert "spots available" in body
        assert "Pick a date" not in body

    def it_books_the_soonest_upcoming_date_when_none_is_open(client):
        soon = _run("framing-1", days=1, registration_cutoff_hours=48)
        _run("framing-2", days=2, category=soon.category, registration_cutoff_hours=72)

        assert client.get(_class_path(soon)).context["offering"] == soon

    def it_shows_the_newest_run_once_nothing_is_to_come(client):
        older = _run("framing-1", days=-60)
        newer = _run("framing-2", days=-30, category=older.category)

        assert client.get(_class_path(older)).context["offering"] == newer


def _events(body: str) -> list[dict]:
    return [json.loads(raw) for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)]


def describe_search_data_for_several_dates():
    def it_gives_each_open_date_its_own_event_and_register_page(client):
        soon = _run("framing-1", days=10, price_cents=7000)
        later = _run("framing-2", days=40, category=soon.category, price_cents=9000, capacity=0)

        events = _events(client.get(_class_path(soon)).content.decode())

        assert [event["startDate"] for event in events] == [
            run.sessions.get().starts_at.isoformat() for run in (soon, later)
        ]
        assert [event["offers"]["price"] for event in events] == ["70.00", "90.00"]
        assert events[1]["offers"]["availability"] == "https://schema.org/SoldOut"
        for event, run in zip(events, (soon, later), strict=True):
            register = reverse("classes:register", kwargs={"slug": run.slug})
            assert event["url"].endswith(register)
            assert event["offers"]["url"].endswith(register)


def describe_the_rail_for_several_dates():
    def it_takes_every_fact_from_the_soonest_date(client):
        soon = _run("framing-1", days=10, capacity=4, minimum_signups=3, registration_cutoff_hours=48)
        _run("framing-2", days=40, category=soon.category, capacity=9)

        body = client.get(_class_path(soon)).content.decode()

        assert "<strong>4</strong> max class size" in body
        assert "<strong>9</strong> max class size" not in body
        assert "<strong>1</strong> session" in body
        assert "<strong>3</strong> needed to run" in body


def describe_the_preview_of_one_run():
    def it_speaks_for_the_previewed_run_whatever_its_siblings(admin_user, client):
        soon = _run("framing-1", days=10, price_cents=7000)
        _run("framing-2", days=40, category=soon.category, price_cents=9000)
        draft = ClassOfferingFactory(
            title="Picture Framing", slug="framing-draft", category=soon.category, price_cents=5500, capacity=7
        )
        start = timezone.now() + timedelta(days=20)
        ClassSessionFactory(class_offering=draft, starts_at=start, ends_at=start + timedelta(hours=2))
        draft.refresh_from_db()
        assert draft.class_record_id == soon.class_record_id
        client.force_login(admin_user)

        response = client.get(reverse("classes:class_preview", kwargs={"pk": draft.pk}))
        body = response.content.decode()

        assert response.context["several_dates"] is False
        assert response.context["next_run"] == draft
        assert "data-pick-a-date" not in body
        assert ">Schedule<" in body
        assert "Next session" in body and "data-next-date" not in body
        assert '<div class="cp-detail__price">$55</div>' in body
        assert "<strong>7</strong> max class size" in body
