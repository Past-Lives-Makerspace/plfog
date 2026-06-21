"""SEO title/description generation on ClassOffering."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from django.utils.html import escape

from classes.factories import ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassOffering


def describe_ClassOffering():
    def describe_seo_title():
        def it_is_unique_across_same_titled_offerings_on_different_dates(db):
            inst = InstructorFactory(full_legal_name="Glen Smith")
            o1 = ClassOfferingFactory(title="Blacksmithing 101", slug="blacksmithing-101-a", instructor=inst)
            o2 = ClassOfferingFactory(title="Blacksmithing 101", slug="blacksmithing-101-b", instructor=inst)
            ClassSessionFactory(class_offering=o1, starts_at=timezone.now() + timedelta(days=3))
            ClassSessionFactory(class_offering=o2, starts_at=timezone.now() + timedelta(days=30))
            assert o1.seo_title != o2.seo_title

        def it_stays_within_sixty_chars(db):
            o = ClassOfferingFactory(
                title="An Extremely Long Introductory Workshop About Hand Forging Knives",
                instructor=InstructorFactory(full_legal_name="Alexandra Montgomery"),
            )
            ClassSessionFactory(class_offering=o, starts_at=timezone.now() + timedelta(days=5))
            assert 0 < len(o.seo_title) <= 60

        def it_never_cuts_a_word_in_half(db):
            o = ClassOfferingFactory(
                title="Introduction to Lampworking Borosilicate Glass Beadmaking",
                instructor=None,
            )
            trimmed = o.seo_title.rstrip("…").strip()
            base_words = o.title.split()
            assert all(word in base_words for word in trimmed.split())

        def describe_with_no_instructor_and_no_sessions():
            def it_falls_back_to_just_the_clean_title(db):
                o = ClassOfferingFactory(title="Open Studio - 6/5/26", instructor=None)
                assert o.seo_title == "Open Studio"

        def describe_with_only_a_past_session():
            def it_still_includes_the_historical_date(db):
                o = ClassOfferingFactory(title="Welding Basics", instructor=None)
                ClassSessionFactory(class_offering=o, starts_at=timezone.now() - timedelta(days=400))
                assert "Welding Basics" in o.seo_title
                assert any(ch.isdigit() for ch in o.seo_title)

        def describe_when_the_title_already_names_the_instructor():
            def it_does_not_repeat_the_instructor(db):
                o = ClassOfferingFactory(
                    title="Blacksmithing 101 with Glen",
                    slug="blacksmithing-101-with-glen",
                    instructor=InstructorFactory(full_legal_name="Glen Morris"),
                )
                title = o.seo_title
                assert title.lower().count("with glen") == 1
                assert "with Glen Morris" not in title

        def describe_when_only_the_date_fits():
            def it_prefers_the_date_over_the_instructor(db):
                o = ClassOfferingFactory(
                    title="An Extremely Long Introductory Workshop About Hand Forging Knives",
                    instructor=InstructorFactory(full_legal_name="Alexandra Montgomery"),
                )
                ClassSessionFactory(class_offering=o, starts_at=timezone.now() + timedelta(days=5))
                assert "with Alexandra Montgomery" not in o.seo_title

        def it_is_html_safe_when_escaped_in_a_template(db):
            o = ClassOfferingFactory(title="Mom & Me: <Clay>", instructor=None)
            assert "&amp;" in escape(o.seo_title)
            assert "&lt;Clay&gt;" in escape(o.seo_title)

    def describe_seo_description():
        def it_never_exceeds_one_hundred_sixty_chars(db):
            o = ClassOfferingFactory(description="Forge a knife from raw steel. " * 20)
            assert 0 < len(o.seo_description) <= 160

        def it_strips_html_and_newlines_from_the_source(db):
            o = ClassOfferingFactory(description="Line one\n\nLine two <b>bold</b> & more")
            d = o.seo_description
            assert "\n" not in d
            assert "<b>" not in d

        def describe_with_a_blank_description():
            def it_falls_back_to_a_category_aware_default(db):
                o = ClassOfferingFactory(description="")
                assert len(o.seo_description) >= 40
                assert o.category.name in o.seo_description

        def it_does_not_cut_a_word_in_half(db):
            o = ClassOfferingFactory(description="Supercalifragilistic " * 30)
            trimmed = o.seo_description.rstrip("…").strip()
            assert all(word == "Supercalifragilistic" for word in trimmed.split())

        def it_is_html_safe_when_escaped_in_a_template(db):
            o = ClassOfferingFactory(description="Bring tongs & gloves <script>x</script>")
            assert "<" not in o.seo_description
            assert "&amp;" in escape(o.seo_description)

    def describe_persistence_guard():
        def it_keeps_archived_offerings_queryable_for_seo(db):
            o = ClassOfferingFactory(status=ClassOffering.Status.ARCHIVED)
            ClassSessionFactory(class_offering=o, starts_at=timezone.now() - timedelta(days=500))
            assert ClassOffering.objects.filter(pk=o.pk).exists()
            assert o.seo_title
