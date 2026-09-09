"""BDD specs for the catalog card partials: the media strip, its object-position, and every surface that renders it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory, UserFactory
from classes.grouping import CatalogGroup
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


def _published(**kwargs) -> ClassOffering:
    offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, **kwargs)
    start = timezone.now() + timedelta(days=5)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _media(offering: ClassOffering, **params) -> str:
    return render_to_string("classes/public/_class_card_media.html", {"offering": offering, **params})


def describe_class_card_media():
    def it_links_to_the_class_by_default():
        offering = _published(slug="linked-card")
        html = _media(offering)
        assert (
            f'<a class="cls-media" href="{reverse("classes:public_class_detail", kwargs={"slug": "linked-card"})}"'
            in html
        )
        assert "</a>" in html
        assert 'aria-label="View ' in html

    def it_renders_a_span_with_no_href_in_preview_mode():
        offering = _published(slug="preview-card")
        html = _media(offering, preview=True)
        assert '<span class="cls-media">' in html
        assert "href=" not in html
        assert "</span>" in html

    def it_always_emits_the_object_position():
        # No crop, no focus: still the centre. The old {% if hero_crop_w %} guard is gone.
        offering = _published()
        assert 'style="object-position: 50% 50%;"' in _media(offering)

    def it_applies_the_banner_focal_point_set_by_the_adjust_sliders():
        # The regression this fixes: w=0 (focal point mode) used to be dropped by the card.
        offering = _published(hero_crop_x=10, hero_crop_y=90)
        assert offering.hero_crop_w is None
        assert 'style="object-position: 10% 90%;"' in _media(offering)

    def it_prefers_the_card_focus_over_the_banner():
        offering = _published(hero_crop_x=10, hero_crop_y=90, card_focus_x=25, card_focus_y=75)
        assert 'style="object-position: 25% 75%;"' in _media(offering)

    def it_binds_a_live_position_when_asked():
        offering = _published()
        html = _media(offering, preview=True, live_position="objectPosition")
        assert ":style=\"'object-position: ' + objectPosition\"" in html
        assert 'style="object-position: 50% 50%;"' in html

    def it_does_not_bind_a_live_position_by_default():
        assert ":style=" not in _media(_published())

    def it_falls_back_to_the_placeholder_logo_without_a_photo():
        category = CategoryFactory(name="No Logo Here")
        offering = _published(image="", category=category)
        html = _media(offering)
        assert "cls-img-ph--logo" in html
        assert "img/favicon.png" in html
        assert "object-position" not in html


def describe_class_card():
    def it_renders_the_media_strip_with_the_card_position():
        offering = _published(slug="grouped-card", card_focus_x=5, card_focus_y=95)
        html = render_to_string("classes/public/_class_card.html", {"group": CatalogGroup(offering)})
        assert 'class="cls-card"' in html
        assert 'style="object-position: 5% 95%;"' in html
        assert f'href="{reverse("classes:public_class_detail", kwargs={"slug": "grouped-card"})}"' in html


def describe_catalog_and_detail_surfaces():
    def it_positions_the_card_in_the_catalog(client):
        _published(slug="catalog-card", hero_crop_x=10, hero_crop_y=90)
        html = client.get(reverse("classes:public_list")).content.decode()
        assert 'style="object-position: 10% 90%;"' in html

    def it_positions_the_related_cards_on_the_detail_page(client):
        category = CategoryFactory(name="Forge", slug="forge")
        current = _published(slug="current-forge", category=category)
        _published(slug="related-forge", category=category, card_focus_x=15, card_focus_y=85)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": current.slug})).content.decode()
        related_start = html.index("cp-detail__related")
        related = html[related_start:]
        assert 'style="object-position: 15% 85%;"' in related
        # The whole related card is the link, so the media strip renders as a span, not a nested anchor.
        assert '<span class="cls-media">' in related


def describe_composer_preview_frames():
    @pytest.fixture
    def instructor_fixture(db):
        user = UserFactory(username="frames-teacher@example.com")
        return InstructorFactory(user=user, full_legal_name="Teacher F", instructor_slug="teacher-f")

    def it_renders_both_frames_from_the_real_media_partial(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, card_focus_x=33, card_focus_y=66)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        step_two = html[html.index("Your Photo, Two Shapes") : html.index("Dates, Seats And Price")]
        assert step_two.count('<span class="cls-media">') == 2
        assert step_two.count('style="object-position: 33% 66%;"') == 2
        assert step_two.count(":style=\"'object-position: ' + objectPosition\"") == 2
        assert "pl-card-focus__frame--laptop" in step_two and "pl-card-focus__frame--phone" in step_two
        assert "cardFocus({ initial: '33% 66%', banner: '50% 50%' })" in step_two
        assert 'name="card_focus"' in step_two and 'value="{&quot;x&quot;: 33, &quot;y&quot;: 66}"' in step_two

    def it_mirrors_the_position_onto_the_review_step_card(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, card_focus_x=33, card_focus_y=66)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        review = html[html.index("How Your Card Looks") :]
        assert ":style=\"'object-position: ' + cardPosition\"" in review
        assert "cardPosition: '33% 66%'" in html

    def it_shows_the_empty_state_when_the_class_has_no_photo(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, image="")
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert html.count("Upload a photo and your card shows up here.") == 2
        assert (
            ":style=\"'object-position: ' + objectPosition\""
            not in html.split("How Your Card Looks")[0]
            .split("Your Photo, Two Shapes")[1]
            .split('<template x-if="localSrc">')[0]
        )
