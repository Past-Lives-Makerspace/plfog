"""BDD specs for ClassOffering.duplicate_as_new_run — spinning off another date-set — and for
what BOTH clone paths carry across (#526)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import (
    ClassFaqFactory,
    ClassImageFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    SeriesClassOfferingFactory,
)
from classes.models import LOCKED_CLASS_FAQS, ClassOffering

#: The two ways a class is copied: the admin's Duplicate and Run it again. One helper serves
#: both (``_copy_photos_and_faqs_from``), and these specs run every carry-over rule on each.
CLONE_PATHS = ["duplicate", "duplicate_as_new_run"]


def _clone(offering: ClassOffering, path: str) -> ClassOffering:
    """Run one clone path on a fresh instance, so the spec's own ``offering`` keeps its pk."""
    return getattr(ClassOffering.objects.get(pk=offering.pk), path)()


def describe_duplicate_as_new_run():
    def it_keeps_the_title_so_the_new_run_stays_grouped(db):
        original = SeriesClassOfferingFactory(title="Blacksmithing 101", slug="bs-1", session_count=3)
        original_pk = original.pk
        original_key = original.grouping_key
        run = original.duplicate_as_new_run()
        assert run.pk != original_pk
        assert run.title == "Blacksmithing 101"
        assert run.grouping_key == original_key != ""

    def it_starts_as_a_draft_with_a_unique_slug_and_no_sessions(db):
        original = SeriesClassOfferingFactory(title="Forge", slug="forge-1", session_count=2)
        run = original.duplicate_as_new_run()
        assert run.status == ClassOffering.Status.DRAFT
        assert run.slug == "forge-1-run"
        assert run.sessions.count() == 0
        # The original is untouched and keeps its dates.
        assert ClassOffering.objects.get(slug="forge-1").sessions.count() == 2

    def it_clears_legacy_cms_id_to_satisfy_the_unique_constraint(db):
        original = ClassOfferingFactory(title="Imported", slug="imported", legacy_cms_id="node-xyz")
        run = original.duplicate_as_new_run()
        assert run.legacy_cms_id == ""

    def it_suffixes_the_slug_when_a_run_slug_already_exists(db):
        original = ClassOfferingFactory(title="Anvil", slug="anvil")
        ClassOfferingFactory(title="Anvil run", slug="anvil-run")
        run = original.duplicate_as_new_run()
        assert run.slug == "anvil-run-2"


def describe_what_a_copy_carries():
    """#526: a copy arrived with an empty gallery and the default FAQs, and the Photos step
    then refused to submit it until the same pictures were uploaded again. Both clone paths
    now carry the rows that hang off the class; sessions stay behind by design."""

    def _source() -> ClassOffering:
        offering = ClassOfferingFactory(title="Blacksmithing 101", slug="bs-101", gallery=0, ready=True)
        # Inserted out of order on purpose: "same order" has to mean sort_order, not insertion.
        ClassImageFactory(class_offering=offering, sort_order=2, alt_text="Quench")
        ClassImageFactory(class_offering=offering, sort_order=0, alt_text="Forge")
        ClassImageFactory(class_offering=offering, sort_order=1, alt_text="Anvil")
        ClassFaqFactory(class_offering=offering, question="Gloves?", answer="Provided.", sort_order=1)
        ClassFaqFactory(class_offering=offering, question="Age?", answer="16 and up.", sort_order=0)
        return offering

    @pytest.mark.parametrize("path", CLONE_PATHS)
    def it_carries_the_gallery_in_order_with_alt_text(db, path):
        copy = _clone(_source(), path)
        assert [(image.alt_text, image.sort_order) for image in copy.gallery_images.all()] == [
            ("Forge", 0),
            ("Anvil", 1),
            ("Quench", 2),
        ]

    @pytest.mark.parametrize("path", CLONE_PATHS)
    def it_carries_each_photos_focus(db, path):
        source = ClassOfferingFactory(title="Jewelry 101", slug="jw-101", gallery=0, ready=True)
        ClassImageFactory(class_offering=source, sort_order=0, alt_text="Pendant", focus_x=12, focus_y=88)
        ClassImageFactory(class_offering=source, sort_order=1, alt_text="Bench")
        copy = _clone(source, path)
        assert list(copy.gallery_images.values_list("alt_text", "focus_x", "focus_y")) == [
            ("Pendant", 12, 88),
            ("Bench", None, None),
        ]

    @pytest.mark.parametrize("path", CLONE_PATHS)
    def it_points_the_copied_photos_at_the_same_stored_files(db, path):
        source = _source()
        keys_before = sorted(source.gallery_images.values_list("image", flat=True))
        copy = _clone(source, path)
        # No file is written: the copy's rows carry the source's storage keys, hero included.
        assert sorted(copy.gallery_images.values_list("image", flat=True)) == keys_before
        assert copy.image.name == source.image.name

    @pytest.mark.parametrize("path", CLONE_PATHS)
    def it_carries_the_faq_rows_in_order(db, path):
        copy = _clone(_source(), path)
        assert list(copy.faqs.values_list("question", "answer", "sort_order")) == [
            ("Age?", "16 and up.", 0),
            ("Gloves?", "Provided.", 1),
        ]

    @pytest.mark.parametrize("path", CLONE_PATHS)
    def it_leaves_a_row_asking_a_locked_question_behind(db, path):
        source = _source()
        ClassFaqFactory(class_offering=source, question=" IS THE SPACE ACCESSIBLE? ", answer="Old copy.", sort_order=2)
        copy = _clone(source, path)
        assert list(copy.faqs.values_list("question", flat=True)) == ["Age?", "Gloves?"]
        assert [faq["question"] for faq in copy.display_faqs].count(LOCKED_CLASS_FAQS[1]["question"]) == 1

    @pytest.mark.parametrize("path", CLONE_PATHS)
    def it_passes_the_photo_readiness_checks_without_an_upload(db, path):
        copy = _clone(_source(), path)
        checks = {item.label: item.ok for item in copy.readiness()}
        assert (checks["Hero photo"], checks["Gallery photo"]) == (True, True)

    @pytest.mark.parametrize("path", CLONE_PATHS)
    def it_leaves_the_original_untouched(db, path):
        source = _source()
        start = timezone.now() + timedelta(days=9)
        ClassSessionFactory(class_offering=source, starts_at=start, ends_at=start + timedelta(hours=2))
        _clone(source, path)
        original = ClassOffering.objects.get(pk=source.pk)
        assert (original.gallery_images.count(), original.faqs.count(), original.sessions.count()) == (3, 2, 2)
        assert original.status == ClassOffering.Status.DRAFT
        assert ClassOffering.objects.count() == 2

    @pytest.mark.parametrize("path", CLONE_PATHS)
    def it_copies_nothing_when_the_source_has_no_gallery_or_faqs(db, path):
        copy = _clone(ClassOfferingFactory(gallery=0), path)
        assert (copy.gallery_images.count(), copy.faqs.count()) == (0, 0)
