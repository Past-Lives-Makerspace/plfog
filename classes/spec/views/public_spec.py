"""BDD specs for public classes portal — list, detail, category, instructor."""

from __future__ import annotations

import re
from datetime import date, timedelta
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
)
from classes.models import Category, ClassOffering
from membership.models import Member
from tests.membership.factories import MemberContactFactory


@pytest.fixture
def published_class(db):
    category = CategoryFactory(name="Ceramics", slug="ceramics")
    instructor = InstructorFactory(full_legal_name="Deenie", instructor_slug="deenie")
    offering = ClassOfferingFactory(
        title="Intro to Wheel Throwing",
        slug="intro-to-wheel-throwing",
        category=category,
        instructor=instructor,
        status=ClassOffering.Status.PUBLISHED,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    return offering


@pytest.fixture
def windowed_classes(db):
    """Three published, dated classes at now+10d / +60d / +200d for timeframe tests."""
    category = CategoryFactory(name="Woodshop", slug="woodshop")
    instructor = InstructorFactory(full_legal_name="Marlo", instructor_slug="marlo")

    def _make(title, slug, days):
        offering = ClassOfferingFactory(
            title=title,
            slug=slug,
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        start = timezone.now() + timedelta(days=days)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        return offering

    _make("Soon Class", "soon-class", 10)
    _make("Mid Class", "mid-class", 60)
    _make("Far Class", "far-class", 200)


def describe_coerce_dollars_to_cents():
    def it_returns_zero_for_invalid_string():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents("abc") == 0

    def it_returns_zero_for_none():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents(None) == 0

    def it_converts_valid_dollar_amount():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents("12.50") == 1250


def describe_public_list():
    def it_renders_hero_and_published_classes(published_class, client):
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert b"Classes" in response.content
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Deenie" in response.content

    def it_renders_the_class_card_count_in_the_hero(published_class, client):
        # published_class collapses to exactly one bookable card. The hero headline
        # now counts visible cards (paginator.count), labeled "Class(es)" — the old
        # "Upcoming Session(s)" tile is gone.
        response = client.get(reverse("classes:public_list"))
        body = response.content.decode()
        assert "Upcoming Session" not in body
        assert '<div class="hs-n">1</div><div class="hs-l">Class</div>' in body

    def it_labels_the_grouping_as_guilds_not_categories(published_class, client):
        # The "Categories → Class Types" relabel: hero stat label and filter copy read "Class Type(s)".
        response = client.get(reverse("classes:public_list"))
        body = response.content.decode()
        assert '<div class="hs-l">Class Types</div>' in body
        assert "All Class Types" in body
        assert '<div class="hs-l">Categories</div>' not in body
        assert "All categories" not in body

    def it_counts_grouped_cards_not_sessions(db, client):
        # One series offered on three future dates collapses to ONE catalog card.
        # The hero now counts CARDS, so it reads 1 — and agrees with the summary,
        # which is the whole point of the reconciliation (was: hero said 3).
        offering = ClassOfferingFactory(
            title="Three Week Forge",
            slug="three-week-forge",
            scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE,
            status=ClassOffering.Status.PUBLISHED,
        )
        for week in range(3):
            start = timezone.now() + timedelta(days=7 * (week + 1))
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        response = client.get(reverse("classes:public_list"))
        body = response.content.decode()
        # Hero headline and results summary both count cards: 1 class.
        assert '<div class="hs-n">1</div><div class="hs-l">Class</div>' in body
        assert "of <strong>1</strong> class" in body

    def it_counts_a_session_less_flexible_class_as_one(db, client):
        # A published class whose only session is in the past (dropped by bookable),
        # plus a flexible session-less class (always bookable). Semantic flip from
        # the old session-count: the flexible class is now ONE card, so the hero
        # reads "1 Class," not "0."
        past_offering = ClassOfferingFactory(
            title="Yesterday's Class",
            slug="yesterdays-class",
            status=ClassOffering.Status.PUBLISHED,
        )
        past = timezone.now() - timedelta(days=3)
        ClassSessionFactory(class_offering=past_offering, starts_at=past, ends_at=past + timedelta(hours=2))
        ClassOfferingFactory(
            title="Arrange Anytime",
            slug="arrange-anytime",
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            status=ClassOffering.Status.PUBLISHED,
        )
        response = client.get(reverse("classes:public_list"))
        body = response.content.decode()
        assert '<div class="hs-n">1</div><div class="hs-l">Class</div>' in body

    def describe_within_timeframe_filter():
        def it_limits_to_the_next_30_days(windowed_classes, client):
            response = client.get(reverse("classes:public_list") + "?within=30")
            assert b"Soon Class" in response.content
            assert b"Mid Class" not in response.content
            assert b"Far Class" not in response.content

        def it_widens_to_90_and_180_days(windowed_classes, client):
            at_90 = client.get(reverse("classes:public_list") + "?within=90")
            assert b"Soon Class" in at_90.content
            assert b"Mid Class" in at_90.content
            assert b"Far Class" not in at_90.content

            at_180 = client.get(reverse("classes:public_list") + "?within=180")
            assert b"Far Class" not in at_180.content

            all_upcoming = client.get(reverse("classes:public_list") + "?within=all")
            assert b"Soon Class" in all_upcoming.content
            assert b"Mid Class" in all_upcoming.content
            assert b"Far Class" in all_upcoming.content

            no_param = client.get(reverse("classes:public_list"))
            assert b"Far Class" in no_param.content

        def it_keeps_flexible_classes_in_every_window(db, client):
            ClassOfferingFactory(
                title="Anytime Class",
                slug="anytime-workshop",
                status=ClassOffering.Status.PUBLISHED,
                scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            )
            response = client.get(reverse("classes:public_list") + "?within=30")
            assert b"Anytime Class" in response.content

        def it_ignores_an_unknown_within_value(windowed_classes, client):
            response = client.get(reverse("classes:public_list") + "?within=abc")
            assert response.status_code == 200
            assert b"Soon Class" in response.content
            assert b"Mid Class" in response.content
            assert b"Far Class" in response.content

    def describe_hero_count_reconciliation():
        def it_shows_the_grouped_card_count_as_the_hero_number(windowed_classes, client):
            response = client.get(reverse("classes:public_list"))
            body = response.content.decode()
            # Hero headline, results summary, and rendered cards all agree at 3.
            assert '<div class="hs-n">3</div><div class="hs-l">Classes</div>' in body
            assert "of <strong>3</strong> class" in body
            assert body.count('class="cls-card"') == 3

        def it_returns_an_oob_hero_count_on_htmx_requests(windowed_classes, client):
            htmx = client.get(reverse("classes:public_list") + "?within=30", HTTP_HX_REQUEST="true")
            htmx_body = htmx.content.decode()
            # The partial carries the OOB hero tile so the hero tracks the filtered
            # count (within=30 → only "Soon Class" → 1 card).
            assert 'id="hero-classes-stat"' in htmx_body
            assert 'hx-swap-oob="true"' in htmx_body
            assert '<div class="hs-n">1</div><div class="hs-l">Class</div>' in htmx_body

            # A full page load carries exactly one hero tile (in the hero, never a
            # stray duplicate inside the embedded results grid).
            full = client.get(reverse("classes:public_list"))
            full_body = full.content.decode()
            assert full_body.count('id="hero-classes-stat"') == 1
            assert "hx-swap-oob" not in full_body

        def it_counts_a_grouped_class_once(db, client):
            category = CategoryFactory(name="Metals", slug="metals")
            instructor = InstructorFactory(full_legal_name="Reese", instructor_slug="reese")
            # Same title + category → same grouping_key → one card across two dates.
            for idx, days in enumerate((5, 12)):
                offering = ClassOfferingFactory(
                    title="Repeated Class",
                    slug=f"repeated-class-{idx}",
                    category=category,
                    instructor=instructor,
                    status=ClassOffering.Status.PUBLISHED,
                )
                start = timezone.now() + timedelta(days=days)
                ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
            response = client.get(reverse("classes:public_list") + "?within=30")
            body = response.content.decode()
            assert '<div class="hs-n">1</div><div class="hs-l">Class</div>' in body
            assert "of <strong>1</strong> class" in body

    def describe_empty_timeframe_state():
        def it_offers_a_wider_range_when_the_timeframe_is_empty(db, client):
            offering = ClassOfferingFactory(
                title="Far Off Class",
                slug="far-off-class",
                status=ClassOffering.Status.PUBLISHED,
            )
            start = timezone.now() + timedelta(days=200)
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
            response = client.get(reverse("classes:public_list") + "?within=30")
            assert response.status_code == 200
            body = response.content.decode()
            assert "next 30 days" in body
            assert "Show all upcoming" in body
            # The escape link drops 'within' entirely — with no other filters, its
            # hx-get is the bare catalog URL, and no querystring carries 'within='.
            assert f'hx-get="{reverse("classes:public_list")}"' in body
            assert "within=" not in body

        def it_preserves_other_filters_in_show_all_upcoming(db, client):
            category = CategoryFactory(name="Ceramics", slug="ceramics")
            offering = ClassOfferingFactory(
                title="Distant Ceramics",
                slug="distant-ceramics",
                category=category,
                status=ClassOffering.Status.PUBLISHED,
            )
            start = timezone.now() + timedelta(days=200)
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
            response = client.get(reverse("classes:public_list") + "?within=30&category=ceramics")
            body = response.content.decode()
            assert "Show all upcoming" in body
            # 'within' is dropped from the escape link; 'category' is kept.
            escape = f'hx-get="{reverse("classes:public_list")}?category=ceramics"'
            assert escape in body
            assert "within=" not in body

    def describe_pagination_carry_through():
        def it_keeps_within_across_pages(db, client):
            category = CategoryFactory(name="Fibers", slug="fibers")
            instructor = InstructorFactory(full_legal_name="Sable", instructor_slug="sable")
            # 26 distinct cards within 90 days → two pages (25/page).
            for idx in range(26):
                offering = ClassOfferingFactory(
                    title=f"Fiber Class {idx}",
                    slug=f"fiber-class-{idx}",
                    category=category,
                    instructor=instructor,
                    status=ClassOffering.Status.PUBLISHED,
                )
                start = timezone.now() + timedelta(days=20)
                ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
            response = client.get(reverse("classes:public_list") + "?within=90")
            # The next-page link carries the timeframe forward.
            assert b"within=90" in response.content
            assert b"page=2" in response.content

    def it_hides_draft_and_pending_classes(published_class, client):
        ClassOfferingFactory(
            title="Secret Draft",
            slug="secret-draft",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.DRAFT,
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Secret Draft" not in response.content

    def it_hides_private_classes(published_class, client):
        private_offering = ClassOfferingFactory(
            title="Private Lesson",
            slug="private-lesson",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
            is_private=True,
        )
        ClassSessionFactory(
            class_offering=private_offering,
            starts_at=timezone.now() + timedelta(days=4),
            ends_at=timezone.now() + timedelta(days=4, hours=2),
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Private Lesson" not in response.content

    def it_hides_published_classes_with_no_upcoming_sessions(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        ClassOfferingFactory(
            title="Brand New Class",
            slug="brand-new-class",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Brand New Class" not in response.content

    def it_hides_published_classes_whose_only_sessions_are_past(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        stale = ClassOfferingFactory(
            title="Past Class",
            slug="past-class",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        past_start = timezone.now() - timedelta(days=10)
        ClassSessionFactory(
            class_offering=stale,
            starts_at=past_start,
            ends_at=past_start + timedelta(hours=2),
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Past Class" not in response.content

    def it_includes_flexible_classes_even_without_sessions(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        ClassOfferingFactory(
            title="Flexible Class",
            slug="flexible-workshop",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        )
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert b"Flexible Class" in response.content

    def it_filters_to_selected_category(published_class, client):
        other_cat = CategoryFactory(name="Blacksmithing", slug="blacksmithing")
        other_offering = ClassOfferingFactory(
            title="Intro to Forging",
            slug="intro-to-forging",
            category=other_cat,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other_offering,
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?category=ceramics")
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Intro to Forging" not in response.content

    def it_filters_by_instructor_slug(published_class, client):
        other_instructor = InstructorFactory(full_legal_name="Newcomer", instructor_slug="newcomer")
        other = ClassOfferingFactory(
            title="Newcomer Class",
            slug="newcomer-class",
            category=published_class.category,
            instructor=other_instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?instructor=deenie")
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Newcomer Class" not in response.content

    def it_filters_by_min_and_max_price(published_class, client):
        cheap = ClassOfferingFactory(
            title="Cheap Class",
            slug="cheap-class",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=500,
        )
        ClassSessionFactory(
            class_offering=cheap,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        # published_class has price_cents=5000 ($50); filter max=$4 to exclude it and include the $5 class
        response = client.get(reverse("classes:public_list") + "?min_price=1&max_price=9")
        assert b"Intro to Wheel Throwing" not in response.content
        assert b"Cheap Class" in response.content

    def it_ignores_the_retired_members_only_filter(db, client):
        # The automatic member discount is gone, so a bookmarked ``?members_only=1`` narrows
        # nothing: every browsable class lists, and the form has no control for it.
        cat = CategoryFactory()
        inst = InstructorFactory()
        for title, slug, days in (("Anvil Class", "anvil-class", 1), ("Bellows Class", "bellows-class", 2)):
            offering = ClassOfferingFactory(
                title=title, slug=slug, category=cat, instructor=inst, status=ClassOffering.Status.PUBLISHED
            )
            ClassSessionFactory(
                class_offering=offering,
                starts_at=timezone.now() + timedelta(days=days),
                ends_at=timezone.now() + timedelta(days=days, hours=2),
            )
        response = client.get(reverse("classes:public_list") + "?members_only=1")
        assert b"Anvil Class" in response.content
        assert b"Bellows Class" in response.content
        assert b'name="members_only"' not in response.content

    def it_ignores_the_retired_free_filter(db, client):
        # #389: every class has a price now, so ``?free=1`` filters nothing and the
        # "Free classes" control is gone. A legacy $0 row still lists like any other.
        cat = CategoryFactory()
        inst = InstructorFactory()
        legacy_free = ClassOfferingFactory(
            title="Free Class",
            slug="free-workshop",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=0,
        )
        paid = ClassOfferingFactory(
            title="Paid Class",
            slug="paid-workshop",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=2000,
        )
        ClassSessionFactory(
            class_offering=legacy_free,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        ClassSessionFactory(
            class_offering=paid,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?free=1")
        assert b"Free Class" in response.content
        assert b"Paid Class" in response.content
        # The changelog modal (rendered on every page) mentions "Free classes" in old
        # entries, so the assertions target the control's markup, not the bare phrase.
        assert b'name="free"' not in response.content
        assert b"<span>Free classes</span>" not in response.content

    def it_filters_upcoming_classes(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        upcoming = ClassOfferingFactory(
            title="Upcoming Class",
            slug="upcoming-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassOfferingFactory(
            title="No Session Class",
            slug="no-session-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=upcoming,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?upcoming=1")
        assert b"Upcoming Class" in response.content
        assert b"No Session Class" not in response.content

    def it_returns_partial_html_for_htmx_requests(published_class, client):
        response = client.get(
            reverse("classes:public_list"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"cp-results__summary" in response.content
        # Partial doesn't include the full page hero
        assert b"<html" not in response.content


def describe_public_category():
    def it_404s_unknown_category(db, client):
        response = client.get(reverse("classes:public_category", kwargs={"slug": "no-such-cat"}))
        assert response.status_code == 404

    def it_renders_only_classes_in_the_category(published_class, client):
        other_cat = CategoryFactory(name="Woodworking", slug="woodworking")
        other_offering = ClassOfferingFactory(
            title="Intro to Chisels",
            slug="intro-to-chisels",
            category=other_cat,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other_offering,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_category", kwargs={"slug": "ceramics"}))
        assert response.status_code == 200
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Intro to Chisels" not in response.content


def describe_public_class_detail():
    def it_renders_the_detail_page(published_class, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        assert response.status_code == 200
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Deenie" in response.content
        assert b"Schedule" in response.content
        assert b"2808 SE 9th Ave" in response.content

    def it_404s_on_draft_classes(db, client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, slug="secret")
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 404

    def it_404s_on_private_classes(db, client):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            is_private=True,
            slug="private-one",
        )
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 404

    def it_offers_register_now_even_for_a_legacy_free_row(db, client):
        # #389: the "Register — Free" label is retired with the free option; a $0 row
        # left over from before the $1.00 floor gets the same button as every class.
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, slug="legacy-free", price_cents=0)
        # Three days out: the default 48 hour registration cutoff would close a nearer class.
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=2),
        )
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 200
        assert b"Register now" in response.content
        assert "Register — Free".encode() not in response.content

    def it_shows_sold_out_when_no_spots_remain(published_class, client):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        for _ in range(published_class.capacity):
            RegistrationFactory(class_offering=published_class, status=Registration.Status.CONFIRMED)
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        assert response.status_code == 200
        assert b"Sold out" in response.content

    def it_shows_the_disabled_cta_and_note_when_class_registration_off(published_class, client):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.class_registration_enabled = False
        config.class_registration_disabled_note = "Email the studio to reserve a seat."
        config.save()

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        assert response.status_code == 200
        assert b"cp-detail__cta--disabled" in response.content
        assert b"Registration unavailable" in response.content
        assert b"Email the studio to reserve a seat." in response.content
        # The live Register link must not be offered.
        assert b"Register now" not in response.content
        assert reverse("classes:register", kwargs={"slug": published_class.slug}).encode() not in response.content

    def it_renders_a_unique_seo_title_and_meta_description(published_class, client):
        from django.utils.html import escape

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        html = response.content.decode()
        assert response.status_code == 200
        assert escape(published_class.seo_title) in html
        assert 'name="description"' in html
        assert escape(published_class.seo_description[:30]) in html

    def _anvil_class(instructor) -> ClassOffering:
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED, slug="anvil-hours", instructor=instructor
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=4),
            ends_at=timezone.now() + timedelta(days=4, hours=3),
        )
        return offering

    def it_shows_the_teaching_bio_in_the_instructor_card_not_the_directory_bio(db, client):
        # #526: the card read `about_me`, the member-directory blurb, so what an instructor wrote
        # under "About me as an instructor" never reached their class pages. Same rule as the
        # instructor page (``it_renders_the_instructor_bio_not_the_directory_bio`` below).
        instructor = InstructorFactory(
            full_legal_name="Sadie",
            instructor_slug="sadie",
            about_me="Member directory blurb",
            instructor_bio="Twenty years at the anvil.",
        )
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": _anvil_class(instructor).slug}))
        assert response.status_code == 200
        assert b"Twenty years at the anvil." in response.content
        assert b"Member directory blurb" not in response.content

    def it_shows_no_bio_at_all_when_the_teaching_bio_is_blank(db, client):
        # No fallback to the directory blurb: it carries a directory-only privacy toggle that a
        # public class page cannot honour, so it stays off the page rather than leaking.
        instructor = InstructorFactory(
            full_legal_name="Sadie", instructor_slug="sadie", about_me="Member directory blurb", instructor_bio=""
        )
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": _anvil_class(instructor).slug}))
        assert response.status_code == 200
        assert b"Member directory blurb" not in response.content
        assert b"Sadie" in response.content

    def describe_related_classes():
        def it_recommends_upcoming_classes_in_the_same_category(published_class, client):
            soon = timezone.now() + timedelta(days=14)
            upcoming = ClassOfferingFactory(
                title="Advanced Glazing",
                slug="advanced-glazing",
                category=published_class.category,
                status=ClassOffering.Status.PUBLISHED,
            )
            ClassSessionFactory(class_offering=upcoming, starts_at=soon, ends_at=soon + timedelta(hours=2))

            response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
            assert upcoming in list(response.context["related_offerings"])
            assert b"Advanced Glazing" in response.content

        def it_never_recommends_a_class_whose_dates_have_passed(published_class, client):
            past_start = timezone.now() - timedelta(days=30)
            past = ClassOfferingFactory(
                title="Holiday Ornaments",
                slug="holiday-ornaments",
                category=published_class.category,
                status=ClassOffering.Status.PUBLISHED,
            )
            ClassSessionFactory(class_offering=past, starts_at=past_start, ends_at=past_start + timedelta(hours=2))

            response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
            assert past not in list(response.context["related_offerings"])
            assert b"Holiday Ornaments" not in response.content


def describe_catalog_grouping():
    def _publish(title, slug, category, instructor, days_out, capacity=6):
        offering = ClassOfferingFactory(
            title=title,
            slug=slug,
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
            capacity=capacity,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=days_out),
            ends_at=timezone.now() + timedelta(days=days_out, hours=2),
        )
        return offering

    def it_shows_a_repeated_class_once_with_a_pick_a_date_list(db, client):
        cat = CategoryFactory(name="Smithing", slug="smithing")
        inst = InstructorFactory(full_legal_name="Glen", instructor_slug="glen")
        for i in range(3):
            _publish("Blacksmithing 101 with Glen", f"bs-{i}", cat, inst, days_out=i + 1)

        response = client.get(reverse("classes:public_list"))

        # One card renders even though the class is offered on three dates. We
        # count the card element rather than the title text, since the title now
        # also appears in the media link's aria-label.
        assert response.content.count(b'class="cls-card"') == 1
        assert b"Pick a date" in response.content
        assert b"3 dates" in response.content

    def it_truncates_to_four_dates_with_a_more_indicator(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        for i in range(6):
            _publish("Big Series with Glen", f"big-{i}", cat, inst, days_out=i + 1)

        response = client.get(reverse("classes:public_list"))

        assert b"+2 more dates" in response.content

    def it_shows_per_date_seat_counts(db, client):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        cat = CategoryFactory()
        inst = InstructorFactory()
        # Both past the default 48 hour registration cutoff, so each row shows its seats, not Closed.
        full_date = _publish("Repeat Class", "rep-a", cat, inst, days_out=3)
        _publish("Repeat Class", "rep-b", cat, inst, days_out=4)
        for _ in range(full_date.capacity):
            RegistrationFactory(class_offering=full_date, status=Registration.Status.CONFIRMED)

        response = client.get(reverse("classes:public_list"))

        # One date is full while the other still has seats — both shown per-date.
        assert b"Full" in response.content
        assert b"6 spots" in response.content

    def it_renders_one_card_per_group_not_per_dated_offering(db, client):
        cat = CategoryFactory(name="Forge", slug="forge")
        inst = InstructorFactory()
        for i in range(4):
            _publish("Anvil Time with Glen", f"anvil-{i}", cat, inst, days_out=i + 1)

        response = client.get(reverse("classes:public_list"))

        # Four dated offerings collapse to a single browsable card (one cls-title each).
        assert response.content.count(b"cls-title") == 1

    def it_lists_other_dates_on_the_detail_page(db, client):
        cat = CategoryFactory(name="Smithing", slug="smithing")
        inst = InstructorFactory(full_legal_name="Glen", instructor_slug="glen")
        first = _publish("Forge Night with Glen", "forge-a", cat, inst, days_out=2)
        _publish("Forge Night with Glen", "forge-b", cat, inst, days_out=9)

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": first.slug}))

        assert b"Other Dates for This Class" in response.content
        assert b"forge-b" in response.content

    def it_omits_other_dates_when_a_class_stands_alone(published_class, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))

        assert b"Other Dates for This Class" not in response.content

    def it_skips_sibling_lookup_when_the_grouping_key_is_blank(db, client):
        # A title-less offering yields an empty grouping key and must stand alone
        # rather than collapsing with every other keyless row.
        cat = CategoryFactory()
        inst = InstructorFactory()
        offering = ClassOfferingFactory(
            title="",
            slug="blank-title",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
        )
        assert offering.grouping_key == ""
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))

        assert response.status_code == 200
        assert b"Other Dates for This Class" not in response.content


def describe_public_instructor():
    def it_404s_inactive_instructor(db, client):
        instructor = InstructorFactory(instructor_slug="retired", status=Member.Status.FORMER)
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug}))
        assert response.status_code == 404

    def it_renders_profile_with_current_classes(published_class, client):
        response = client.get(
            reverse("classes:public_instructor", kwargs={"slug": published_class.instructor.instructor_slug})
        )
        assert response.status_code == 200
        assert b"Deenie" in response.content
        assert b"Intro to Wheel Throwing" in response.content

    def it_renders_the_instructor_bio_not_the_directory_bio(db, client):
        instructor = InstructorFactory(
            full_legal_name="Sadie",
            instructor_slug="sadie",
            about_me="Member directory blurb",
            instructor_bio="I teach glass blowing.",
        )
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug}))
        assert b"I teach glass blowing." in response.content
        assert b"Member directory blurb" not in response.content

    def it_renders_only_contacts_flagged_for_the_instructor_page(db, client):
        instructor = InstructorFactory(full_legal_name="Wes", instructor_slug="wes")
        MemberContactFactory(
            member=instructor,
            label="Portfolio",
            value="https://wes.example",
            show_on_instructor_page=True,
            show_in_directory=False,
        )
        MemberContactFactory(
            member=instructor,
            label="PrivateNote",
            value="secret@example.com",
            show_on_instructor_page=False,
            show_in_directory=True,
        )
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug}))
        content = response.content.decode()
        assert "Portfolio" in content
        assert 'href="https://wes.example"' in content
        assert "PrivateNote" not in content

    def it_renders_a_social_contact_with_its_platform_icon(db, client):
        instructor = InstructorFactory(full_legal_name="Ida", instructor_slug="ida")
        MemberContactFactory(
            member=instructor,
            label="Instagram",
            value="https://instagram.example/ida",
            kind="social",
            show_on_instructor_page=True,
        )
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug}))
        content = response.content.decode()
        assert "pl-social-icon" in content
        assert 'href="https://instagram.example/ida"' in content

    def it_keeps_the_plain_label_style_for_a_non_social_contact(db, client):
        instructor = InstructorFactory(full_legal_name="Ola", instructor_slug="ola")
        MemberContactFactory(
            member=instructor,
            label="Booking",
            value="book@ola.example",
            kind="other",
            show_on_instructor_page=True,
        )
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug}))
        content = response.content.decode()
        assert "Booking:" in content
        assert "pl-social-icon" not in content


def describe_google_analytics_gate():
    def it_omits_ga_tag_when_id_not_set(published_class, client):
        response = client.get(reverse("classes:public_list"))
        assert b"googletagmanager.com" not in response.content

    def it_injects_ga_tag_when_id_is_configured(published_class, client):
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        site.google_analytics_measurement_id = "G-TEST123"
        site.save()
        response = client.get(reverse("classes:public_list"))
        assert b"googletagmanager.com" in response.content
        assert b"G-TEST123" in response.content


def describe_hero_management_buttons():
    def it_crosses_to_the_members_host_on_the_public_surface(admin_user, published_class, client):
        client.force_login(admin_user)
        with override_settings(PUBLIC_HOSTS=["testserver"], MEMBER_BASE_URL="https://members.example"):
            response = client.get(reverse("classes:public_list"))
        assert b'href="https://members.example/classes/admin/"' in response.content

    def it_stays_relative_on_the_members_surface(admin_user, published_class, client):
        client.force_login(admin_user)
        response = client.get(reverse("classes:public_list"))
        assert b'href="/classes/admin/"' in response.content
        assert b"members.example" not in response.content


def describe_public_topbar_member_chrome():
    @pytest.fixture
    def member_persona_user(db):
        from django.contrib.auth import get_user_model
        from membership.models import Member, MembershipPlan

        plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
        user, _ = get_user_model().objects.get_or_create(
            username="fog@example.com", defaults={"email": "fog@example.com"}
        )
        Member.objects.update_or_create(
            user=user,
            defaults={
                "full_legal_name": "Fog Member",
                "fog_role": Member.FogRole.MEMBER,
                "membership_plan": plan,
                "status": Member.Status.ACTIVE,
                "airtable_record_id": "recFOG123",
            },
        )
        return user

    def it_shows_the_fog_cluster_on_the_public_surface(member_persona_user, published_class, client):
        client.force_login(member_persona_user)
        with override_settings(PUBLIC_HOSTS=["testserver"]):
            response = client.get(reverse("classes:public_list"))
        assert b"cp-topbar__account-item--ext" in response.content
        assert b"cp-topbar__account-pill" in response.content

    def it_hides_the_fog_cluster_on_the_members_surface(member_persona_user, published_class, client):
        client.force_login(member_persona_user)
        response = client.get(reverse("classes:public_list"))
        assert b"cp-topbar__account-item--ext" not in response.content


def describe_card_image_fallback():
    def it_shows_the_category_color_logo_when_class_has_no_image(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            image="",
            category__name="Woodworking",
        )
        resp = client.get(reverse("classes:public_list"))
        assert resp.status_code == 200
        assert "img/guild_logos/woodworking_color.svg" in resp.content.decode()

    def it_shows_the_past_lives_mark_when_category_has_no_logo(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            image="",
            category__name="Creative Business",
        )
        resp = client.get(reverse("classes:public_list"))
        assert resp.status_code == 200
        assert "cls-img-ph--logo" in resp.content.decode()
        assert "img/favicon.png" in resp.content.decode()


def describe_detail_hero_fallback():
    def it_shows_the_category_color_logo_when_no_images(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            image="",
            category__name="Glass",
        )
        resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert resp.status_code == 200
        assert "img/guild_logos/glass_color.svg" in resp.content.decode()

    def it_shows_the_past_lives_mark_when_category_has_no_logo(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            image="",
            category__name="Education",
        )
        resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert resp.status_code == 200
        assert "cp-detail__hero-logo" in resp.content.decode()
        assert "img/favicon.png" in resp.content.decode()


def _banner_tag(body: str) -> str:
    """The opening tag of the class page banner, the one img.cp-detail__hero-img."""
    tag = re.search(r'<img class="cp-detail__hero-img"[^>]*>', body)
    assert tag is not None, "no banner rendered"
    return tag.group(0)


def _banner_src(body: str) -> str:
    """The src of the class page banner."""
    src = re.search(r'src="([^"]*)"', _banner_tag(body))
    assert src is not None, _banner_tag(body)
    return src.group(1)


def describe_detail_hero_crop():
    """Issue #547: the banner shows the copy cut to the composer's crop box, else the upload."""

    def it_shows_the_cropped_copy_on_the_banner(client, db):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            image__width=1000,
            image__height=600,
            hero_crop_x=100,
            hero_crop_y=50,
            hero_crop_w=400,
            hero_crop_h=225,
        )
        assert "hero-crops/" in offering.hero_cropped.url
        body = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert _banner_src(body) == offering.hero_cropped.url
        # The related classes strip and the card share the accessor; the original is nowhere.
        assert offering.image.url not in body

    def it_shows_the_upload_on_the_banner_without_a_box(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, image__width=1000, image__height=600)
        assert not offering.hero_cropped
        body = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert _banner_src(body) == offering.image.url

    def it_shows_the_cropped_copy_on_a_related_class_card(client, db):
        shared = CategoryFactory(name="Crop Related")
        ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, slug="crop-host", category=shared)
        related = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            slug="crop-related",
            category=shared,
            image__width=1000,
            image__height=600,
            hero_crop_x=0,
            hero_crop_y=0,
            hero_crop_w=400,
            hero_crop_h=225,
        )
        start = timezone.now() + timedelta(days=7)
        ClassSessionFactory(class_offering=related, starts_at=start, ends_at=start + timedelta(hours=2))
        body = client.get(reverse("classes:public_class_detail", kwargs={"slug": "crop-host"})).content.decode()
        assert f'src="{related.hero_cropped.url}"' in body
        assert related.image.url not in body


# Smallest valid GIF, enough for a category hero ImageField to accept and store.
_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _category_with_hero() -> Category:
    """A category carrying a hero image, for the fallback branch of the class page banner."""
    category = CategoryFactory()
    category.hero_image = SimpleUploadedFile("hero.gif", _GIF, content_type="image/gif")
    category.save()
    return category


def _hero_tag(body: str) -> str:
    """The opening tag of the class page banner frame, header.cp-detail__hero."""
    tag = re.search(r'<header class="cp-detail__hero[^>]*>', body)
    assert tag is not None, "no hero rendered"
    return tag.group(0)


def describe_detail_hero_shape():
    """The banner shows the whole class photo in a frame of the photo's shape; Adjust is for the category hero only."""

    def it_frames_the_cropped_copy_in_its_own_shape_with_a_backdrop_and_no_adjust(admin_user, client):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            image__width=1000,
            image__height=600,
            hero_crop_x=100,
            hero_crop_y=50,
            hero_crop_w=400,
            hero_crop_h=225,
        )
        client.force_login(admin_user)
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.context["can_edit_offering"] is True
        body = response.content.decode()
        hero = _hero_tag(body)
        assert "cp-detail__hero--photo" in hero
        assert 'style="--cp-hero-ratio: 400 / 225;"' in hero
        assert (
            f"""<div class="cp-detail__hero-backdrop" style="background-image: url('{offering.hero_cropped.url}');">"""
            in body
        )
        assert _banner_src(body) == offering.hero_cropped.url
        # Nothing is clipped, so nothing is adjustable: no component, no sliders, no bound style.
        assert "heroPlacement(" not in body
        assert 'title="Adjust Placement"' not in body
        assert "data-hero-img" not in body
        assert "object-position" not in _banner_tag(body)
        # The Edit link stays, and no longer leans on the component's state.
        assert f'href="{response.context["edit_url"]}"' in body
        assert "isAdjusting" not in body

    def it_frames_the_upload_in_its_own_shape_without_a_box(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, image__width=1000, image__height=600)
        body = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert 'style="--cp-hero-ratio: 1000 / 600;"' in _hero_tag(body)
        assert (
            f"""<div class="cp-detail__hero-backdrop" style="background-image: url('{offering.image.url}');">""" in body
        )

    def it_frames_an_imported_photo_whole_in_the_crop_boxs_shape(admin_user, client):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            image="",
            legacy_image_url="https://classes.pastlives.space/sites/default/files/glen.jpg",
        )
        client.force_login(admin_user)
        body = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert 'style="--cp-hero-ratio: 16 / 9;"' in _hero_tag(body)
        proxied = offering.hero_image_url
        assert "_legacy-image" in proxied
        assert f"""<div class="cp-detail__hero-backdrop" style="background-image: url('{proxied}');">""" in body
        assert _banner_src(body) == proxied
        assert "heroPlacement(" not in body
        assert 'title="Adjust Placement"' not in body

    def it_keeps_the_cover_fit_and_the_adjust_tool_for_a_category_hero(admin_user, client):
        from django.contrib.contenttypes.models import ContentType

        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, image="", category=_category_with_hero())
        client.force_login(admin_user)
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.context["can_edit_category"] is True
        body = response.content.decode()
        hero = _hero_tag(body)
        assert "cp-detail__hero--photo" not in hero
        assert "--cp-hero-ratio" not in hero
        assert "cp-detail__hero-backdrop" not in body
        banner = re.search(r'<img class="cp-detail__hero-img cp-detail__hero-img--cover"[^>]*>', body)
        assert banner is not None, "no category banner rendered"
        assert f'src="{offering.category.hero_image.url}"' in banner.group(0)
        assert "data-hero-img" in banner.group(0)
        assert "heroPlacement({" in body
        assert f"contentTypeId: {ContentType.objects.get_for_model(Category).pk}," in body
        assert f"objectId: {offering.category.pk}," in body
        assert 'title="Adjust Placement"' in body
        assert 'x-show="!isAdjusting" title="Edit Class"' in body

    def it_shows_a_guest_the_category_hero_positioned_without_the_tool(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, image="", category=_category_with_hero())
        body = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        banner = re.search(r'<img class="cp-detail__hero-img cp-detail__hero-img--cover"[^>]*>', body)
        assert banner is not None, "no category banner rendered"
        assert 'style="object-position: 50% 50%;"' in banner.group(0)
        assert "heroPlacement(" not in body
        assert 'title="Adjust Placement"' not in body


def describe_all_guild_types_show():
    """Every class type (Category) appears in the catalog, even with zero bookable classes."""

    def it_lists_a_guild_type_with_no_bookable_classes(db, client):
        stocked = CategoryFactory(name="Ceramics", slug="ceramics")
        empty = CategoryFactory(name="Leatherwork", slug="leatherwork")
        offering = ClassOfferingFactory(
            title="Wheel Throwing",
            slug="wheel-throwing",
            category=stocked,
            status=ClassOffering.Status.PUBLISHED,
        )
        start = timezone.now() + timedelta(days=7)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))

        response = client.get(reverse("classes:public_list"))

        cats = {c.slug: c for c in response.context["categories"]}
        assert {"ceramics", "leatherwork"} <= set(cats)
        assert cats["ceramics"].class_count == 1
        assert cats["leatherwork"].class_count == 0
        assert response.context["total_categories"] == 2  # exactly the two we created
        assert empty.slug in cats

    def it_counts_every_guild_type_in_the_hero_stat(db, client):
        for i in range(3):
            CategoryFactory(name=f"Guild {i}", slug=f"guild-{i}")

        response = client.get(reverse("classes:public_list"))

        assert response.context["total_categories"] == 3

    def it_hides_demo_guild_types_when_demo_is_off(db, client):
        # Listing all categories must not undo the demo gate: [DEMO] class types stay
        # hidden on prod when display_demo_classes is off.
        from core.models import SiteConfiguration

        CategoryFactory(name="[DEMO] Lamp Working", slug="demo-lamp-working")
        CategoryFactory(name="Ceramics", slug="ceramics")
        config = SiteConfiguration.load()
        config.display_demo_classes = False
        config.save()

        response = client.get(reverse("classes:public_list"))

        slugs = {c.slug for c in response.context["categories"]}
        assert "ceramics" in slugs
        assert "demo-lamp-working" not in slugs

    def it_shows_demo_guild_types_when_demo_is_on(db, client):
        from core.models import SiteConfiguration

        CategoryFactory(name="[DEMO] Lamp Working", slug="demo-lamp-working")
        config = SiteConfiguration.load()
        config.display_demo_classes = True
        config.save()

        response = client.get(reverse("classes:public_list"))

        slugs = {c.slug for c in response.context["categories"]}
        assert "demo-lamp-working" in slugs


def describe_the_description_on_the_class_page():
    def it_renders_editor_html_formatted_and_sanitized(published_class, client):
        published_class.description = (
            "<p>Make a <strong>coat hook</strong>.</p><ul><li>Bring gloves</li></ul><script>evil()</script>"
        )
        published_class.save(update_fields=["description"])

        html = client.get(
            reverse("classes:public_class_detail", kwargs={"slug": published_class.slug})
        ).content.decode()

        prose = html.split('class="cp-detail__prose"', 1)[1].split("</section>", 1)[0]
        assert "<strong>coat hook</strong>" in prose
        assert "<ul><li>Bring gloves</li></ul>" in prose
        assert "<script" not in prose

    def it_renders_a_pre_editor_description_as_escaped_paragraphs(published_class, client):
        published_class.description = "Wear <closed toe shoes>.\n\nTake it home."
        published_class.save(update_fields=["description"])

        html = client.get(
            reverse("classes:public_class_detail", kwargs={"slug": published_class.slug})
        ).content.decode()

        prose = html.split('class="cp-detail__prose"', 1)[1].split("</section>", 1)[0]
        assert "<p>Wear &lt;closed toe shoes&gt;.</p><p>Take it home.</p>" in prose

    def it_keeps_the_meta_description_plain(published_class, client):
        published_class.description = "<p>Make a <strong>coat hook</strong> &amp; hanger.</p>"
        published_class.save(update_fields=["description"])

        assert published_class.seo_description.startswith("Make a coat hook & hanger.")


def describe_detail_guild_card():
    """The guild's About text is plain text; its paragraphs and line breaks must survive on the class page."""

    def it_keeps_the_guild_about_paragraphs_and_line_breaks(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering
        from membership.models import Guild

        guild = Guild.objects.create(
            name="Metalworkers Guild",
            slug="metalworkers-guild",
            about="Welcome to the metal shop.\n\n• Two gas forges\n• MIG and TIG welding",
        )
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            category__name="Metalworking",
            category__guild=guild,
        )
        resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "<p>Welcome to the metal shop.</p>" in body
        assert "• Two gas forges<br>• MIG and TIG welding" in body

    def it_falls_back_to_the_partnership_line_when_about_is_empty(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering
        from membership.models import Guild

        guild = Guild.objects.create(name="Glass Guild", slug="glass-guild", about="")
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            category__name="Glass",
            category__guild=guild,
        )
        resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert "offered in partnership with the Glass Guild" in resp.content.decode()


def describe_a_flexible_class_page():
    """A flexible class explains itself (#545): the window, how booking works, never a Schedule.

    Positive anchors are markup (data-flexible-section, the pill class) and factory strings
    (the instructor's name, the window dates); negative ones are the Schedule's own markup.
    """

    def _flexible(**traits) -> ClassOffering:
        # One Billy per spec: the slug is unique, and several specs build more than one class.
        billy = Member.objects.filter(instructor_slug="billy").first()
        traits.setdefault(
            "instructor", billy or InstructorFactory(full_legal_name="Billy Anvil", instructor_slug="billy")
        )
        return ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, **traits
        )

    def _page(client, offering: ClassOffering) -> str:
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 200
        return response.content.decode()

    def _section(html: str) -> str:
        return html.split('<section class="cp-detail__section" data-flexible-section>')[1].split("</section>")[0]

    def it_shows_the_window_in_bold_and_the_booking_sentence_naming_the_instructor(db, client):
        offering = _flexible(flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))
        section = _section(_page(client, offering))
        assert '<h2 class="cp-detail__h2">Flexible Date Range</h2>' in section
        assert "<strong>Nov 2 to Dec 1, 2026</strong>" in section
        assert "with Billy Anvil and pick a day inside this window that works for both of you." in section

    def it_renders_each_window_shape(db, client):
        shapes = [
            ({"flexible_starts_on": date(2026, 11, 2)}, "From Nov 2, 2026"),
            ({"flexible_ends_on": date(2026, 12, 1)}, "Through Dec 1, 2026"),
            (
                {"flexible_starts_on": date(2026, 12, 20), "flexible_ends_on": date(2027, 1, 10)},
                "Dec 20, 2026 to Jan 10, 2027",
            ),
        ]
        for traits, label in shapes:
            section = _section(_page(client, _flexible(**traits)))
            assert f"<strong>{label}</strong>" in section, traits
            assert "Flexible Date Range" in section, traits
            assert "inside this window" in section, traits

    def it_reads_flexible_scheduling_with_no_window_and_no_window_words(db, client):
        section = _section(_page(client, _flexible()))
        assert '<h2 class="cp-detail__h2">Flexible Scheduling</h2>' in section
        assert "cp-detail__flex-window" not in section
        assert "with Billy Anvil and pick a day that works for both of you." in section
        assert "inside this window" not in section

    def it_shows_the_instructors_note_after_the_sentence_when_there_is_one(db, client):
        section = _section(_page(client, _flexible(flexible_note="Weekday mornings only.")))
        assert "cp-detail__flex-instructor-note" in section
        assert section.index("Billy Anvil") < section.index("Weekday mornings only.")
        assert "cp-detail__flex-instructor-note" not in _section(_page(client, _flexible(slug="quiet")))

    def it_shows_the_instructors_own_booking_text_in_place_of_the_standard_line(db, client):
        offering = _flexible(flexible_booking_text="Email me and we will pick a Saturday.\nMornings are best.")
        section = _section(_page(client, offering))
        how = section.split('<p class="cp-detail__flex-how">')[1].split("</p>")[0]
        assert how == "Email me and we will pick a Saturday.<br>Mornings are best."
        assert "book your session directly" not in section

    def it_never_renders_the_schedule_for_a_class_still_carrying_a_month_long_session(db, client):
        # The shape of production class 665: a flexible class with one 703 hour session row.
        offering = _flexible(slug="billy-november")
        start = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=703))
        html = _page(client, offering)
        assert "data-flexible-section" in html
        assert "cp-detail__sessions" not in html
        assert "cp-detail__h2-sub" not in html
        assert "h total" not in html
        assert 'cp-detail__next-pill">Next session' not in html
        assert 'cp-detail__next-pill cp-detail__next-pill--flex">Flexible scheduling' in html
        assert "<strong>1</strong> session" not in html

    def it_offers_register_now_through_the_last_day(db, client):
        today = timezone.localdate()
        html = _page(client, _flexible(flexible_starts_on=today - timedelta(days=10), flexible_ends_on=today))
        assert 'data-help-key="class.register"' in html
        assert "data-flexible-closed" not in html
        assert 'cp-detail__spots--full">Registration closed' not in html

    def it_closes_registration_the_day_after_the_last_day_naming_the_date(db, client):
        offering = _flexible(flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))
        with mock.patch("classes.models.timezone.localdate", return_value=date(2026, 12, 2)):
            html = _page(client, offering)
        assert 'cp-detail__spots--full">Registration closed' in html
        assert "data-flexible-closed>This class's booking window ended Dec 1, 2026.</div>" in html
        assert 'data-help-key="class.register"' not in html
        assert "has already started" not in html.split("data-flexible-closed")[1].split("</div>")[0]

    def it_shows_the_window_under_the_flex_line_on_the_catalog_card(db, client):
        _flexible(
            title="Open Forge",
            slug="open-forge",
            flexible_starts_on=date(2026, 11, 2),
            flexible_ends_on=date(2026, 12, 1),
        )
        _flexible(title="Any Time Forge", slug="any-time-forge")
        html = client.get(reverse("classes:public_list")).content.decode()
        assert html.count('<div class="cls-schedule__flex">Flexible: schedule with the instructor</div>') == 2
        assert html.count('<div class="cls-schedule__window">') == 1
        assert '<div class="cls-schedule__window">Nov 2 to Dec 1, 2026</div>' in html

    def it_leaves_the_catalog_the_day_after_the_last_day(db, client):
        _flexible(title="Open Forge", slug="open-forge", flexible_ends_on=date(2026, 12, 1))
        with mock.patch("classes.models.timezone.localdate", return_value=date(2026, 12, 1)):
            assert b"Open Forge" in client.get(reverse("classes:public_list")).content
        with mock.patch("classes.models.timezone.localdate", return_value=date(2026, 12, 2)):
            assert b"Open Forge" not in client.get(reverse("classes:public_list")).content


def describe_the_rail_and_the_card_of_a_flexible_class():
    """No seat cap (#545): no count, no max class size, no waitlist; Register now however many hold a seat."""

    def _flexible(**traits) -> ClassOffering:
        return ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, **traits
        )

    def _rail(html: str) -> str:
        return html.split('<div class="cp-detail__rail-card">')[1].split("</ul>")[0]

    def it_offers_register_now_past_the_stored_capacity_with_no_seat_math(db, client):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        offering = _flexible(slug="open-forge", capacity=1)
        for _ in range(3):
            RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        rail = _rail(html)
        assert 'data-help-key="class.register"' in rail
        assert "cp-detail__spots" not in rail
        assert "max class size" not in rail
        assert "?waitlist=1" not in rail
        assert "cp-detail__cta--waitlist" not in rail
        assert "<strong>1</strong> session" not in rail

    def it_keeps_the_seat_math_on_a_fixed_class(published_class, client):
        html = client.get(
            reverse("classes:public_class_detail", kwargs={"slug": published_class.slug})
        ).content.decode()
        rail = _rail(html)
        assert "cp-detail__spots--ok" in rail
        assert f"<strong>{published_class.capacity}</strong> max class size" in rail

    def it_shows_no_seat_pill_on_the_catalog_card(db, client):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        offering = _flexible(title="Open Forge", slug="open-forge", capacity=1)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        fixed = ClassOfferingFactory(title="Fixed Forge", slug="fixed-forge", status=ClassOffering.Status.PUBLISHED)
        ClassSessionFactory(class_offering=fixed, starts_at=timezone.now() + timedelta(days=3))
        html = client.get(reverse("classes:public_list")).content.decode()
        cards = html.split('<div class="cls-card"')
        flexible_card = next(card for card in cards if "Open Forge" in card)
        fixed_card = next(card for card in cards if "Fixed Forge" in card)
        assert 'class="cls-spots' not in flexible_card
        assert "Sold out" not in flexible_card
        assert 'class="cls-spots ok"' in fixed_card

    def it_shows_no_seat_pill_on_a_flexible_date_option_row(db, client):
        # Two runs of one class, grouped on the card: the flexible option row carries no pill.
        instructor = InstructorFactory(full_legal_name="Group Lead", instructor_slug="group-lead")
        category = CategoryFactory(name="Forge", slug="forge")
        fixed = ClassOfferingFactory(
            title="Grouped Forge",
            slug="grouped-forge-1",
            status=ClassOffering.Status.PUBLISHED,
            instructor=instructor,
            category=category,
        )
        ClassSessionFactory(class_offering=fixed, starts_at=timezone.now() + timedelta(days=3))
        _flexible(title="Grouped Forge", slug="grouped-forge-2", instructor=instructor, category=category, capacity=1)
        html = client.get(reverse("classes:public_list")).content.decode()
        # Each row clipped at its own closing tag, so the last one never swallows the card footer's pill.
        rows = [row.split("</a>")[0] for row in html.split('<a class="cls-schedule__row cls-schedule__row--pick"')]
        assert len(rows) == 3
        flexible_row = next(row for row in rows[1:] if "grouped-forge-2" in row)
        fixed_row = next(row for row in rows[1:] if "grouped-forge-1" in row)
        assert 'class="cls-spots' not in flexible_row
        assert 'class="cls-spots ok"' in fixed_row
        # The flexible row dates nothing: the flex line stands in for the session date.
        assert '<span class="cls-schedule__date">Flexible: schedule with the instructor</span>' in flexible_row
        assert "cls-schedule__time" not in flexible_row

    def it_shows_the_flex_line_and_no_seat_pill_on_a_flexible_other_date_row(db, client):
        # The detail page's "Other Dates for This Class" rows are the card rows' twin: a flexible
        # sibling of a fixed class shows the flex line with its window and no pill, never "None spots".
        instructor = InstructorFactory(full_legal_name="Group Lead", instructor_slug="group-lead")
        category = CategoryFactory(name="Forge", slug="forge")
        fixed = ClassOfferingFactory(
            title="Grouped Forge",
            slug="grouped-forge-1",
            status=ClassOffering.Status.PUBLISHED,
            instructor=instructor,
            category=category,
        )
        ClassSessionFactory(class_offering=fixed, starts_at=timezone.now() + timedelta(days=3))
        _flexible(
            title="Grouped Forge",
            slug="grouped-forge-2",
            instructor=instructor,
            category=category,
            capacity=1,
            flexible_starts_on=date(2026, 11, 2),
            flexible_ends_on=date(2026, 12, 1),
        )
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": fixed.slug})).content.decode()
        rows = html.split('<li class="cp-detail__other-date">')[1:]
        assert len(rows) == 1
        row = rows[0].split("</li>")[0]
        assert "grouped-forge-2" in row
        assert "Flexible: schedule with the instructor · Nov 2 to Dec 1, 2026" in row
        assert "cls-spots" not in row
        assert "None" not in row


def describe_sale_markup():
    """The Sale feature's public markup: badge and struck price on the card, banner and struck rail price."""

    @pytest.fixture
    def sale_class(published_class):
        published_class.price_cents = 5000
        published_class.sale_enabled = True
        published_class.sale_kind = ClassOffering.SaleKind.PERCENT
        published_class.sale_percent = 20  # $50 -> $40
        published_class.sale_banner_text = "Summer blowout!"
        published_class.save()
        return published_class

    def it_renders_the_sale_badge_and_struck_price_on_the_catalog_card(sale_class, client):
        body = client.get(reverse("classes:public_list")).content.decode()
        assert '<span class="badge sale">Sale</span>' in body
        assert '<span class="cls-price--was">$50</span>' in body
        assert '<span class="cls-price">$40</span>' in body

    def it_renders_the_sale_banner_and_struck_rail_price_on_the_detail_page(sale_class, client):
        url = reverse("classes:public_class_detail", kwargs={"slug": sale_class.slug})
        body = client.get(url).content.decode()
        assert 'class="cp-detail__sale-banner"' in body
        assert "Summer blowout!" in body
        assert "20% off" in body
        assert '<div class="cp-detail__price--was">$50</div>' in body
        assert '<div class="cp-detail__price">$40</div>' in body

    def it_renders_the_struck_original_in_the_register_summary(sale_class, client):
        body = client.get(reverse("classes:register", kwargs={"slug": sale_class.slug})).content.decode()
        assert '<span class="reg-was">$50</span> $40' in body

    def it_hides_all_sale_markup_when_no_sale_is_active(published_class, client):
        body = client.get(reverse("classes:public_list")).content.decode()
        assert "badge sale" not in body
        assert "cls-price--was" not in body
        url = reverse("classes:public_class_detail", kwargs={"slug": published_class.slug})
        detail = client.get(url).content.decode()
        assert "cp-detail__sale-banner" not in detail
