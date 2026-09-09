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

LEGACY_URL = "https://classes.pastlives.space/sites/default/files/blacksmithing.jpg"


def _proxy_src(offering: ClassOffering) -> str:
    return f'src="{reverse("classes:legacy_image")}?url=https%3A%2F%2Fclasses.pastlives.space%2Fsites%2Fdefault%2Ffiles%2Fblacksmithing.jpg"'


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

    def it_serves_an_imported_photo_through_the_proxy_with_the_position_and_live_binding():
        """A legacy only class is the class's own photo: same position rule, same live binding."""
        offering = _published(image="", legacy_image_url=LEGACY_URL, card_focus_x=10, card_focus_y=20)
        html = _media(offering, preview=True, live_position="objectPosition")
        assert _proxy_src(offering) in html
        assert 'style="object-position: 10% 20%;"' in html
        assert ":style=\"'object-position: ' + objectPosition\"" in html
        assert "cls-img-ph" not in html

    def it_prefers_the_uploaded_photo_over_an_imported_one():
        offering = _published(legacy_image_url=LEGACY_URL)
        html = _media(offering)
        assert "_legacy-image" not in html
        assert f'src="{offering.image.url}"' in html

    def it_renders_a_live_source_in_place_of_the_fallback_when_the_class_has_no_photo():
        """The composer mirrors the hero field's local preview through live_src before a save."""
        category = CategoryFactory(name="No Logo Here")
        offering = _published(image="", category=category)
        html = _media(offering, preview=True, live_src="localSrc", live_position="objectPosition")
        assert '<img class="cls-img" :src="localSrc" alt="" :style="\'object-position: \' + objectPosition">' in html
        assert "cls-img-ph" not in html
        assert "loading=" not in html
        assert '<img class="cls-img" :src="localSrc" alt="">' in _media(offering, preview=True, live_src="localSrc")

    def it_ignores_a_live_source_when_the_class_already_has_a_photo():
        offering = _published()
        html = _media(offering, preview=True, live_src="localSrc")
        assert ":src=" not in html
        assert f'src="{offering.image.url}"' in html


def describe_class_card():
    def it_renders_the_media_strip_with_the_card_position():
        offering = _published(slug="grouped-card", card_focus_x=5, card_focus_y=95)
        html = render_to_string("classes/public/_class_card.html", {"group": CatalogGroup(offering)})
        assert 'class="cls-card"' in html
        assert 'style="object-position: 5% 95%;"' in html
        assert f'href="{reverse("classes:public_class_detail", kwargs={"slug": "grouped-card"})}"' in html
        assert '<a class="cls-title"' in html
        assert 'class="cls-inst"' in html and "<a href=" in html.split('class="cls-inst"')[1]
        assert "x-text=" not in html

    def it_renders_nothing_that_navigates_in_preview_mode():
        instructor = InstructorFactory(full_legal_name="Glen Smith", instructor_slug="glen-smith")
        offering = _published(slug="preview-card", instructor=instructor, title="Blacksmithing 101")
        html = render_to_string(
            "classes/public/_class_card.html",
            {"group": CatalogGroup(offering), "preview": True, "live_position": "objectPosition"},
        )
        assert "href=" not in html
        assert "onclick=" not in html
        assert '<span class="cls-media">' in html
        assert '<span class="cls-title">' in html and "</a>" not in html
        assert "with Glen Smith" in " ".join(html.split())
        assert ":style=\"'object-position: ' + objectPosition\"" in html
        # The body is the real one: instructor, dates, price, spots.
        assert 'class="cls-body"' in html and "cls-price" in html and "cls-spots" in html

    def it_drops_the_more_button_onclick_in_preview_mode():
        offering = _published(slug="many-dates")
        for day in range(6, 12):
            start = timezone.now() + timedelta(days=day)
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        live = render_to_string("classes/public/_class_card.html", {"group": CatalogGroup(offering)})
        preview = render_to_string(
            "classes/public/_class_card.html", {"group": CatalogGroup(offering), "preview": True}
        )
        assert 'class="cls-schedule__more"' in live and "onclick=" in live
        assert 'class="cls-schedule__more"' in preview and "onclick=" not in preview

    def it_binds_the_title_to_a_live_expression_when_asked():
        offering = _published(slug="live-title", title="Glen's Forge")
        html = render_to_string(
            "classes/public/_class_card.html",
            {"group": CatalogGroup(offering), "preview": True, "live_title": "liveTitle"},
        )
        assert "<span x-text=\"liveTitle || 'Glen\\u0027s Forge'\">Glen&#x27;s Forge</span>" in html


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

    def _step_two(html: str) -> str:
        return html[html.index("Your Photo, Two Shapes") : html.index("Dates, Seats And Price")]

    def _frames(step_two: str) -> str:
        """The card frames only: from the focus tool's root to its sliders (or the note when there are none)."""
        pane = step_two.split('class="cp-page pl-card-focus"')[1]
        return pane.split("pl-card-focus__sliders")[0]

    def it_renders_both_frames_as_the_whole_card(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, card_focus_x=33, card_focus_y=66, title="Forge Night", price_cents=4500
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        step_two = _step_two(html)
        assert step_two.count('<span class="cls-media">') == 2
        assert step_two.count('style="object-position: 33% 66%;"') == 2
        assert step_two.count(":style=\"'object-position: ' + objectPosition\"") == 2
        assert "pl-card-focus__frame--laptop" in step_two and "pl-card-focus__frame--phone" in step_two
        assert "cardFocus({ initial: '33% 66%', banner: '50% 50%' })" in step_two
        assert 'name="card_focus"' in step_two and 'value="{&quot;x&quot;: 33, &quot;y&quot;: 66}"' in step_two
        # The whole card, twice: body, title, instructor, dates, price, spots.
        frames = _frames(step_two)
        assert frames.count('class="cls-body"') == 2
        assert frames.count("<span x-text=\"liveTitle || 'Forge Night'\">Forge Night</span>") == 2
        assert frames.count("Teacher F") == 2
        assert frames.count('class="cls-price"') == 2 and frames.count("$45") == 2
        assert frames.count("cls-spots") == 2
        assert frames.count("cls-schedule") >= 2
        assert "href=" not in frames and "onclick=" not in frames
        assert "This is your whole card, at the two widths members see." in step_two
        assert "Your photo, title, dates, price and spots, exactly as members see them." in step_two
        assert "photo only" not in html
        # A saved hero: the sliders show outright, nothing waits on a mirrored photo.
        assert '<div class="pl-card-focus__sliders">' in step_two
        assert 'x-if="localSrc"' not in step_two

    def it_mirrors_the_position_and_title_onto_the_review_step_card(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, card_focus_x=33, card_focus_y=66, title="Forge Night"
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        review = html[html.index("How Your Card Looks") :]
        assert ":style=\"'object-position: ' + cardPosition\"" in review
        assert "cardPosition: '33% 66%'" in html
        assert review.count('class="cls-body"') == 1
        assert "<span x-text=\"liveTitle || 'Forge Night'\">Forge Night</span>" in review
        assert "cls-price" in review
        assert "href=" not in review.split("pl-card-focus__frame--phone")[1].split("</div>")[0]
        assert "liveTitle: ''" in html
        assert "@input=\"if ($event.target.id === 'id_title') liveTitle = $event.target.value\"" in html

    def it_renders_an_imported_photo_in_the_frames_through_the_proxy(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, image="", legacy_image_url=LEGACY_URL, card_focus_x=33, card_focus_y=66
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        step_two = _step_two(html)
        assert step_two.count(_proxy_src(offering)) == 3  # the hero preview plus the two frames
        assert step_two.count('style="object-position: 33% 66%;"') == 2
        assert step_two.count(":style=\"'object-position: ' + objectPosition\"") == 2
        assert "pl-card-focus__sliders" in step_two
        review = html[html.index("How Your Card Looks") :]
        assert _proxy_src(offering) in review
        assert "Add a photo on step 2 and your card shows up here." not in html

    def it_renders_the_placeholder_card_and_waits_for_a_photo_when_the_class_has_none(instructor_fixture, client):
        """Saved, no photo: placeholder frames now, the real card the moment an instant upload lands."""
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, image="", category=CategoryFactory(name="No Logo Here"), title="Forge Night"
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        step_two = _step_two(html)
        pane = step_two.split('class="cp-page pl-card-focus"')[1]
        placeholder, mirrored = pane.split('<template x-if="localSrc">')
        # The placeholder frames until a photo exists.
        assert '<div class="pl-card-focus__frames" x-show="!localSrc">' in placeholder
        assert placeholder.count("cls-img-ph--logo") == 2
        assert placeholder.count('class="cls-body"') == 2
        assert ":src=" not in placeholder
        # The mirrored frames: the real card, its photo bound to the hero field's local preview.
        assert mirrored.count(':src="localSrc"') == 2
        assert mirrored.count(":style=\"'object-position: ' + objectPosition\"") == 2
        assert mirrored.count("<span x-text=\"liveTitle || 'Forge Night'\">Forge Night</span>") == 2
        assert "cls-img-ph" not in mirrored.split("</template>")[0]
        # The notes swap with the photo, and the sliders wait for it instead of a page reload.
        assert '<p class="pl-card-focus__note" x-show="!localSrc">Add a photo above and the sliders appear.</p>' in pane
        assert 'x-show="localSrc" x-cloak>Your photo, title, dates, price and spots' in pane
        assert '<div class="pl-card-focus__sliders" x-show="localSrc" x-cloak>' in pane
        assert "Match the Banner" in pane
        assert "Upload a photo and your card shows up here." not in html
        assert "Add a photo on step 2 and your card shows up here." in html[html.index("How Your Card Looks") :]
