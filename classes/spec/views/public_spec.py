"""BDD specs for public classes portal — list, detail, category, instructor."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
)
from classes.models import ClassOffering
from membership.models import Member


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
        # The "Categories → Guild Types" relabel: hero stat label and filter copy read "Guild Type(s)".
        response = client.get(reverse("classes:public_list"))
        body = response.content.decode()
        assert '<div class="hs-l">Guild Types</div>' in body
        assert "All Guild Types" in body
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
                title="Anytime Workshop",
                slug="anytime-workshop",
                status=ClassOffering.Status.PUBLISHED,
                scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            )
            response = client.get(reverse("classes:public_list") + "?within=30")
            assert b"Anytime Workshop" in response.content

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
            title="Flexible Workshop",
            slug="flexible-workshop",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        )
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert b"Flexible Workshop" in response.content

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

    def it_filters_members_only(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        members_only = ClassOfferingFactory(
            title="Members Class",
            slug="members-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            member_discount_pct=15,
        )
        no_discount = ClassOfferingFactory(
            title="Open Class",
            slug="open-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            member_discount_pct=0,
        )
        ClassSessionFactory(
            class_offering=members_only,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        ClassSessionFactory(
            class_offering=no_discount,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?members_only=1")
        assert b"Members Class" in response.content
        assert b"Open Class" not in response.content

    def it_filters_free_classes(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        free = ClassOfferingFactory(
            title="Free Workshop",
            slug="free-workshop",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=0,
        )
        paid = ClassOfferingFactory(
            title="Paid Workshop",
            slug="paid-workshop",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=2000,
        )
        ClassSessionFactory(
            class_offering=free,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        ClassSessionFactory(
            class_offering=paid,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?free=1")
        assert b"Free Workshop" in response.content
        assert b"Paid Workshop" not in response.content

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
        full_date = _publish("Repeat Class", "rep-a", cat, inst, days_out=1)
        _publish("Repeat Class", "rep-b", cat, inst, days_out=2)
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
