"""BDD specs for a class's Search title and Search description (#1261, part 1 of 3).

Both live on the class (``ClassRecord``), so every run of it shares them, and they are edited
from any run's editor: the composer's Review step and the live class edit page. Set, they replace
the generated title and description in the page's ``<title>``, meta description, Open Graph and
Course JSON-LD; blank falls back to the generated text. Assertions anchor on markup or factory
strings no changelog line could carry (STANDARDS.md section 8).
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory, UserFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm
from classes.models import ClassOffering, ClassRecord
from classes.spec.live_edit import live_facts
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

TITLE = "Qzx Search Title Sentinel"
DESCRIPTION = "Qzx search description sentinel for the meta tag."


def _admin() -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="search-admin", email="search-admin@example.com")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _instructor() -> Member:
    return InstructorFactory(user=UserFactory(username="search-teacher@example.com"), instructor_slug="search-t")


def _composer_data(**overrides: str) -> dict[str, object]:
    data: dict[str, object] = {
        "title": "Forge a Leaf Dish",
        "category": str(CategoryFactory().pk),
        "instructors": [str(InstructorFactory().pk)],
        "description": "Hands-on intro.",
        "price_cents": "100.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
    }
    data.update(overrides)
    return data


def _published_payload(offering: ClassOffering, **overrides: str) -> dict[str, object]:
    payload: dict[str, object] = {
        **live_facts(offering),
        "description": "A hands-on class.",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    payload.update(overrides)
    return payload


def _live_class(**traits: object) -> ClassOffering:
    offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, **traits)
    starts = timezone.now() + timezone.timedelta(days=10)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timezone.timedelta(hours=2))
    offering.refresh_from_db()
    return offering


def _page(client: Client, offering: ClassOffering) -> str:
    record = offering.class_record
    response = client.get(reverse("classes:public_class_detail", kwargs={"slug": record.slug}))
    assert response.status_code == 200
    return response.content.decode()


def _json_ld(html: str) -> dict[str, object]:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def describe_the_class_fields():
    def it_starts_blank():
        record = ClassOfferingFactory().class_record
        record.refresh_from_db()
        assert (record.search_title, record.search_description) == ("", "")

    def it_falls_back_to_the_generated_text_when_blank():
        offering = ClassOfferingFactory()
        record = offering.class_record
        assert record.page_title(offering) == offering.seo_title
        assert record.page_description(offering) == offering.seo_description

    def it_uses_the_set_text_trimmed():
        offering = ClassOfferingFactory()
        record = offering.class_record
        record.set_search_text(search_title=f"  {TITLE} ", search_description=DESCRIPTION)
        record.refresh_from_db()
        assert record.page_title(offering) == TITLE
        assert record.page_description(offering) == DESCRIPTION

    def it_writes_nothing_when_given_nothing():
        record = ClassOfferingFactory().class_record
        before = ClassRecord.objects.get(pk=record.pk).updated_at
        record.set_search_text()
        assert ClassRecord.objects.get(pk=record.pk).updated_at == before


def describe_the_editor_forms():
    @pytest.mark.parametrize("form_class", [ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm])
    def it_caps_the_title_at_60_and_the_description_at_160(form_class):
        ok = form_class(data={"search_title": "x" * 60, "search_description": "y" * 160})
        too_long = form_class(data={"search_title": "x" * 61, "search_description": "y" * 161})
        assert "search_title" not in ok.errors and "search_description" not in ok.errors
        assert "search_title" in too_long.errors and "search_description" in too_long.errors

    @pytest.mark.parametrize("form_class", [ClassOfferingForm, TeachClassOfferingForm], ids=["admin", "teach"])
    def it_saves_to_the_new_class_from_each_composer_form(form_class):
        form = form_class(data=_composer_data(search_title=TITLE, search_description=DESCRIPTION))
        assert form.is_valid(), form.errors
        offering = form.save()
        record = ClassRecord.objects.get(pk=offering.class_record_id)
        assert (record.search_title, record.search_description) == (TITLE, DESCRIPTION)

    def it_prefills_the_class_text_and_shows_the_generated_text_as_placeholder():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        offering.class_record.set_search_text(search_title=TITLE)
        form = TeachPublishedClassForm(instance=offering)
        assert form["search_title"].initial == TITLE
        assert form.fields["search_title"].widget.attrs["placeholder"] == offering.seo_title
        assert form.fields["search_description"].widget.attrs["placeholder"] == offering.seo_description

    def it_has_no_placeholder_on_a_new_class():
        form = TeachClassOfferingForm()
        assert form.fields["search_title"].widget.attrs["placeholder"] == ""

    def it_saves_from_a_live_run_to_the_class_every_run_shares():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        sibling = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, category=offering.category)
        ClassOffering.objects.filter(pk=sibling.pk).update(class_record=offering.class_record)
        form = TeachPublishedClassForm(data=_published_payload(offering, search_title=TITLE), instance=offering)
        assert form.is_valid(), form.errors
        form.save()
        sibling.refresh_from_db()
        assert sibling.class_record.search_title == TITLE

    def it_leaves_the_class_text_alone_when_the_post_carries_no_field():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        offering.class_record.set_search_text(search_title=TITLE, search_description=DESCRIPTION)
        form = TeachPublishedClassForm(data=_published_payload(offering), instance=offering)
        assert form.is_valid(), form.errors
        form.save()
        record = ClassRecord.objects.get(pk=offering.class_record_id)
        assert (record.search_title, record.search_description) == (TITLE, DESCRIPTION)

    def it_clears_a_field_posted_empty():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        offering.class_record.set_search_text(search_title=TITLE, search_description=DESCRIPTION)
        form = TeachPublishedClassForm(data=_published_payload(offering, search_title=""), instance=offering)
        assert form.is_valid(), form.errors
        form.save()
        record = ClassRecord.objects.get(pk=offering.class_record_id)
        assert (record.search_title, record.search_description) == ("", DESCRIPTION)


def describe_the_editor_pages():
    def it_puts_both_fields_on_the_review_step_for_an_instructor(client: Client):
        client.force_login(_instructor().user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        review = html[html.index('data-composer-step="6"') :]
        section = review[review.index("data-search-text") :]
        assert 'name="search_title"' in section and 'maxlength="60"' in section
        assert 'name="search_description"' in section and 'maxlength="160"' in section

    def it_saves_from_the_admin_composer(client: Client):
        client.force_login(_admin())
        response = client.post(
            reverse("classes:admin_class_create"),
            {
                **_composer_data(search_title=TITLE),
                "sessions-TOTAL_FORMS": "0",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        assert ClassRecord.objects.get(title="Forge a Leaf Dish").search_title == TITLE

    def it_shows_both_fields_on_the_live_class_edit_page(client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        offering.class_record.set_search_text(search_description=DESCRIPTION)
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert 'name="search_title"' in html
        assert f"{DESCRIPTION}</textarea>" in html


def describe_the_class_page():
    def it_uses_the_search_text_in_the_title_description_open_graph_and_json_ld(client: Client):
        offering = _live_class()
        offering.class_record.set_search_text(search_title=TITLE, search_description=DESCRIPTION)
        html = _page(client, offering)
        assert re.search(rf"<title>\s*{TITLE}", html)
        assert f'<meta name="description" content="{DESCRIPTION}">' in html
        assert f'<meta property="og:title" content="{TITLE}">' in html
        assert f'<meta property="og:description" content="{DESCRIPTION}">' in html
        data = _json_ld(html)
        assert (data["name"], data["description"]) == (TITLE, DESCRIPTION)

    def it_falls_back_to_the_generated_text_when_blank(client: Client):
        offering = _live_class(title="Qzx Generated Pour")
        html = _page(client, offering)
        assert f'<meta property="og:title" content="{offering.seo_title}">' in html
        assert f'<meta property="og:description" content="{offering.seo_description}">' in html
        data = _json_ld(html)
        assert (data["name"], data["description"]) == ("Qzx Generated Pour", offering.seo_description)

    def it_shows_the_search_text_in_the_preview(client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor)
        offering.class_record.set_search_text(search_title=TITLE)
        client.force_login(instructor.user)
        html = client.get(reverse("classes:class_preview", kwargs={"pk": offering.pk})).content.decode()
        assert f'<meta property="og:title" content="{TITLE}">' in html
