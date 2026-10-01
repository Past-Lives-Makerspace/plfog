"""BDD specs for the class subtitle (#563): an optional plain line under the title on the class page.

Hosts edit it on composer step 1 (admin and instructor alike) and on the live class edit page;
the hero renders it between the title and the byline, and a blank one renders nothing. Every
assertion anchors on markup (an id, a class name) or on a factory string no changelog line could
carry, because the changelog renders on every page (STANDARDS.md section 8).
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.forms import SUBTITLE_HELP_TEXT, ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

SENTINEL = "Qzx Subtitle Sentinel"
HERO_SUBTITLE = f'<p class="cp-detail__subtitle">{SENTINEL}</p>'


def _admin() -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="subtitle-admin", email="subtitle-admin@example.com")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _instructor() -> Member:
    return InstructorFactory(user=UserFactory(username="subtitle-teacher@example.com"), instructor_slug="subtitle-t")


def _composer_data(**overrides: str) -> dict[str, str]:
    """The fields both composer forms require, plus whatever the spec is about."""
    data = {
        "title": "Forge a Leaf Dish",
        "category": str(CategoryFactory().pk),
        "description": "Hands-on intro.",
        "price_cents": "100.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
    }
    data.update(overrides)
    return data


def _published_edit_payload(**overrides: str) -> dict[str, str]:
    """What templates/classes/teach/class_form_published.html posts: its light fields and the FAQ."""
    payload = {
        "description": "A hands-on class.",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    payload.update(overrides)
    return payload


def _between(html: str, start: str, end: str) -> str:
    return html[html.index(start) : html.index(end)]


def _control(html: str, name: str) -> re.Match[str]:
    """The one unprefixed control named ``name``; asserting the count keeps a miss from passing."""
    found = list(re.finditer(rf'<(?:input|select|textarea)\b[^>]*\bname="{name}"[^>]*>', html))
    assert len(found) == 1, f"expected one {name} control, found {len(found)}"
    return found[0]


def describe_the_subtitle_field():
    def it_starts_blank_on_a_new_class():
        assert ClassOffering().subtitle == ""
        offering = ClassOfferingFactory()
        offering.refresh_from_db()
        assert offering.subtitle == ""

    @pytest.mark.parametrize("form_class", [ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm])
    def it_takes_at_most_150_characters(form_class):
        assert "subtitle" not in form_class(data={"subtitle": "x" * 150}).errors
        assert "subtitle" in form_class(data={"subtitle": "x" * 151}).errors


def describe_saving_the_subtitle():
    @pytest.mark.parametrize("form_class", [ClassOfferingForm, TeachClassOfferingForm], ids=["admin", "teach"])
    def it_saves_through_each_composer_form(form_class):
        form = form_class(data=_composer_data(subtitle=SENTINEL))
        assert form.is_valid(), form.errors
        offering = form.save()
        offering.refresh_from_db()
        assert offering.subtitle == SENTINEL

    def it_saves_through_the_live_class_edit_form():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        form = TeachPublishedClassForm(data=_published_edit_payload(subtitle=SENTINEL), instance=offering)
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert offering.subtitle == SENTINEL

    def it_saves_from_the_live_class_edit_page(client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        client.force_login(instructor.user)
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _published_edit_payload(subtitle=SENTINEL),
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.subtitle == SENTINEL

    def it_clears_when_the_host_empties_it():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, subtitle=SENTINEL)
        form = TeachPublishedClassForm(data=_published_edit_payload(subtitle=""), instance=offering)
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert offering.subtitle == ""


def describe_composer_step_one():
    def _assert_subtitle_under_title(html: str) -> None:
        step_one = _between(html, 'data-composer-step="1"', 'data-composer-step="2"')
        title, subtitle, category = (_control(step_one, name) for name in ("title", "subtitle", "category"))
        assert title.start() < subtitle.start() < category.start()
        # The step check (static/js/composer_validation.js) reads maxlength off the control.
        assert 'maxlength="150"' in subtitle.group()
        assert f'<p class="pl-field-hint">{SUBTITLE_HELP_TEXT}</p>' in step_one[subtitle.end() : category.start()]

    def it_puts_the_subtitle_right_under_the_title_for_an_instructor(client: Client):
        client.force_login(_instructor().user)
        _assert_subtitle_under_title(client.get(reverse("classes:teach_class_create")).content.decode())

    def it_puts_the_subtitle_right_under_the_title_for_an_admin(client: Client):
        client.force_login(_admin())
        _assert_subtitle_under_title(client.get(reverse("classes:admin_class_create")).content.decode())

    def it_shows_the_saved_subtitle_when_a_draft_reopens(client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, subtitle=SENTINEL)
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        step_one = _between(html, 'data-composer-step="1"', 'data-composer-step="2"')
        assert f'value="{SENTINEL}"' in _control(step_one, "subtitle").group()


def describe_the_live_class_edit_page():
    def it_shows_the_subtitle_open_and_above_the_description(client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED, subtitle=SENTINEL)
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        subtitle = _control(html, "subtitle")
        assert f'value="{SENTINEL}"' in subtitle.group()
        assert subtitle.start() < _control(html, "description").start()
        # The plain form_field.html wrapper, not the collapsible one the light fields below it use.
        wrapper = html.rfind('class="pl-form-group', 0, subtitle.start())
        assert html.startswith('class="pl-form-group">', wrapper)


def describe_the_class_page_hero():
    def it_renders_the_subtitle_between_the_title_and_the_byline(client: Client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, subtitle=SENTINEL)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        title = html.index('class="cp-detail__title"')
        subtitle = html.index(HERO_SUBTITLE)
        byline = html.index('class="cp-detail__byline"')
        assert title < subtitle < byline
        assert html.count('class="cp-detail__subtitle"') == 1

    def it_renders_no_subtitle_element_when_blank(client: Client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert 'class="cp-detail__title"' in html
        assert "cp-detail__subtitle" not in html

    def it_renders_the_subtitle_as_plain_text(client: Client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, subtitle="<em>Qzx</em>")
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert '<p class="cp-detail__subtitle">&lt;em&gt;Qzx&lt;/em&gt;</p>' in html

    def it_shows_the_subtitle_in_the_review_step_preview(client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, subtitle=SENTINEL)
        client.force_login(instructor.user)
        html = client.get(reverse("classes:class_preview", kwargs={"pk": offering.pk})).content.decode()
        assert HERO_SUBTITLE in html

    def it_loads_the_italic_weight_on_the_one_font_link(client: Client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, subtitle=SENTINEL)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert html.count("fonts.googleapis.com/css2?family=Playfair+Display") == 1
        assert "family=Playfair+Display:ital,wght@0,700;0,800;0,900;1,500&" in html
