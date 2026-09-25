"""BDD specs for the readiness checklist and the submit guard that reads it."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from classes.factories import BRACKETED_DESCRIPTION, READY_DESCRIPTION, ClassOfferingFactory, ClassSessionFactory
from classes.models import (
    READINESS_MIN_DESCRIPTION_CHARS,
    ClassNotReadyError,
    ClassOffering,
    CmsActivity,
    description_length,
)

# The wording is pinned here, in full and once; every other spec imports READINESS_DESCRIPTION_HINT.
DESCRIPTION_HINT = f"Write at least {READINESS_MIN_DESCRIPTION_CHARS} characters about the class."
JS_PATH = Path(__file__).resolve().parents[3] / "static" / "js" / "composer_description_count.js"


def _items(offering: ClassOffering) -> dict[str, bool]:
    return {item.label: item.ok for item in offering.readiness()}


def describe_readiness():
    def it_passes_every_item_for_a_ready_class(db):
        offering = ClassOfferingFactory(ready=True)
        assert all(_items(offering).values())
        assert offering.is_ready is True
        assert [item.anchor for item in offering.readiness()] == [
            "hero-preview",
            "gallery-manager",
            "id_description",
            "class-dates",
            "id_capacity",
        ]

    def it_fails_the_hero_photo_without_an_own_hero(db):
        offering = ClassOfferingFactory(ready=True, image="")
        assert _items(offering)["Hero photo"] is False
        assert [i.hint for i in offering.readiness() if not i.ok] == ["Add a hero photo."]

    def it_ticks_the_hero_photo_for_a_photo_imported_from_the_legacy_site(db):
        """An imported photo is the class's own photo, so the checklist and the tab mark tick."""
        offering = ClassOfferingFactory(
            ready=True, image="", legacy_image_url="https://classes.pastlives.space/sites/default/files/glen.jpg"
        )
        assert _items(offering)["Hero photo"] is True
        assert offering.is_ready is True

    def it_fails_the_gallery_without_a_gallery_photo(db):
        offering = ClassOfferingFactory(ready=True, gallery=0)
        assert _items(offering)["Gallery photo"] is False

    def it_fails_the_description_one_character_under_the_minimum(db):
        offering = ClassOfferingFactory(description="x" * (READINESS_MIN_DESCRIPTION_CHARS - 1))
        assert _items(offering)["Description"] is False

    def it_passes_the_description_at_the_minimum(db):
        offering = ClassOfferingFactory(description="x" * READINESS_MIN_DESCRIPTION_CHARS)
        assert _items(offering)["Description"] is True

    def it_counts_the_bracketed_words_a_member_reads(db):
        # Reproduces #425. The class page shows all 63 of these characters (the description renders
        # escaped), and strip_tags took the two bracketed phrases for markup and counted 27, so a
        # description that was long enough was refused as too short.
        assert len(BRACKETED_DESCRIPTION) == 63
        offering = ClassOfferingFactory(description=BRACKETED_DESCRIPTION)
        assert _items(offering)["Description"] is True

    def it_still_ignores_padding_the_page_never_shows(db):
        # Blank lines and runs of spaces render as one break or one space, so they never count:
        # 22 characters padded to 42 with newlines is still 22, under the old rule and this one.
        offering = ClassOfferingFactory(description="Learn to forge a hook." + "\n" * 20)
        assert _items(offering)["Description"] is False

    def it_names_the_minimum_in_the_description_hint(db):
        hint = {i.label: i for i in ClassOfferingFactory(description="Short").readiness()}["Description"].hint
        assert hint == DESCRIPTION_HINT
        assert str(READINESS_MIN_DESCRIPTION_CHARS) in hint

    def it_fails_the_dates_when_every_session_is_in_the_past(db):
        offering = ClassOfferingFactory(description=READY_DESCRIPTION)
        start = timezone.now() - timedelta(days=1)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=1))
        items = {i.label: i for i in offering.readiness()}
        assert items["Dates"].ok is False
        assert items["Dates"].hint == "Add at least one date."

    def it_passes_a_flexible_class_with_a_note_and_fails_one_without(db):
        with_note = ClassOfferingFactory(
            description=READY_DESCRIPTION,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_note="Email me to pick a time.",
        )
        without = ClassOfferingFactory(
            description=READY_DESCRIPTION, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, flexible_note="  "
        )
        assert with_note.is_ready is True
        dates = {i.label: i for i in without.readiness()}["Dates"]
        assert dates.ok is False
        assert dates.hint == "Say how students pick a time."

    def it_fails_capacity_under_one(db):
        offering = ClassOfferingFactory(ready=True, capacity=0)
        assert _items(offering)["Capacity"] is False

    def it_lists_every_failing_label_in_the_error(db):
        offering = ClassOfferingFactory(description="Short", image="", gallery=0, capacity=0)
        assert offering.readiness_error("submit") == (
            "Not ready to submit: Add a hero photo. Add one gallery photo. "
            f"{DESCRIPTION_HINT} Add at least one date. Set how many can attend."
        )


def describe_submit_for_review_guard():
    def it_raises_listing_every_failing_item_and_changes_nothing(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, description="Short")
        with pytest.raises(ValidationError) as excinfo:
            offering.submit_for_review()
        assert excinfo.value.messages == [f"Not ready to submit: {DESCRIPTION_HINT} Add at least one date."]
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT
        assert offering.approvals.count() == 0
        assert not CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_SUBMITTED, class_offering=offering).exists()
        assert mail.outbox == []

    def it_no_longer_uses_the_images_only_message(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, image="", gallery=0)
        with pytest.raises(ValidationError) as excinfo:
            offering.submit_for_review()
        assert "Add photos before submitting" not in excinfo.value.messages[0]
        assert excinfo.value.messages[0].startswith("Not ready to submit: Add a hero photo. Add one gallery photo.")

    def it_submits_a_ready_class(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, ready=True)
        (row,) = offering.submit_for_review()
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING
        assert row.role == "admin"

    def it_submits_a_ready_flexible_class(db):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.DRAFT,
            description=READY_DESCRIPTION,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_note="We will find a time together.",
        )
        offering.submit_for_review()
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING


def describe_submit_blocker():
    def it_is_empty_for_a_ready_class(db):
        assert ClassOfferingFactory(ready=True).submit_blocker() == ""

    def it_is_the_readiness_error_for_an_unready_class(db):
        offering = ClassOfferingFactory(ready=True, image="", gallery=0)
        assert offering.submit_blocker() == "Not ready to submit: Add a hero photo. Add one gallery photo."


def describe_with_readiness_inputs():
    def it_reads_the_gallery_and_the_dates_off_the_annotations_with_no_per_row_queries(db):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        ready = ClassOfferingFactory(ready=True, title="Ready")
        no_gallery = ClassOfferingFactory(ready=True, gallery=0, title="No Gallery")
        no_dates = ClassOfferingFactory(description=READY_DESCRIPTION, title="No Dates")
        rows = {
            row.title: row
            for row in ClassOffering.objects.with_readiness_inputs().filter(
                pk__in=[ready.pk, no_gallery.pk, no_dates.pk]
            )
        }
        with CaptureQueriesContext(connection) as ctx:
            blockers = {title: row.submit_blocker() for title, row in rows.items()}
        assert len(ctx) == 0
        assert blockers == {
            "Ready": "",
            "No Gallery": "Not ready to submit: Add one gallery photo.",
            "No Dates": "Not ready to submit: Add at least one date.",
        }

    def it_ignores_a_session_that_has_already_started(db):
        offering = ClassOfferingFactory(description=READY_DESCRIPTION)
        start = timezone.now() - timedelta(hours=1)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        row = ClassOffering.objects.with_readiness_inputs().get(pk=offering.pk)
        assert row.has_future_session is False
        assert row.is_ready is False

    def it_returns_an_unchanged_clone_when_already_annotated(db):
        qs = ClassOffering.objects.with_readiness_inputs()
        again = qs.with_readiness_inputs()
        assert again is not qs
        assert str(again.query) == str(qs.query)

    def it_still_queries_the_relations_for_a_row_that_was_not_annotated(db):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        offering = ClassOfferingFactory(ready=True)
        with CaptureQueriesContext(connection) as ctx:
            assert offering.is_ready is True
        assert len(ctx) == 2


def describe_a_refused_submit_or_publish():
    def it_raises_a_class_not_ready_error_carrying_the_checklist(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, ready=True, gallery=0)
        with pytest.raises(ClassNotReadyError) as excinfo:
            offering.submit_for_review()
        assert isinstance(excinfo.value, ValidationError)
        assert excinfo.value.messages == ["Not ready to submit: Add one gallery photo."]
        assert [item.label for item in excinfo.value.items if not item.ok] == ["Gallery photo"]
        assert len(excinfo.value.items) == 5

    def it_raises_the_same_error_from_publish(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, ready=True, image="")
        with pytest.raises(ClassNotReadyError) as excinfo:
            offering.publish(None)
        assert excinfo.value.messages == ["Not ready to publish: Add a hero photo."]
        assert [item.hint for item in excinfo.value.items if not item.ok] == ["Add a hero photo."]


def describe_description_length():
    def it_counts_the_typed_text_with_whitespace_collapsed():
        # (what was typed, what counts). Only whitespace runs shrink; brackets, tags and a less than
        # sign count as typed, because the class page shows them as typed. One emoji is one.
        table = [
            ("", 0),
            ("   ", 0),
            ("Forge a coat hook.", 18),
            ("  Forge   a\n\ncoat\thook.  ", 18),
            (BRACKETED_DESCRIPTION, 63),
            ("<p>Short   words</p>", 18),
            ("Kids <16 need a guardian.", 25),
            ("Forge a hook \U0001f525 and take it home.", 32),
            ("Learn to forge a hook." + "\n" * 20, 22),
        ]
        assert [(text, description_length(text)) for text, _ in table] == table

    def it_is_mirrored_by_the_composer_count_script():
        # The browser paints the count the rule will apply, so the two must normalise alike: the
        # script carries the same expression, counts code points the way len() does (.length would
        # count one emoji as two), and reads the minimum and the box off the markup rather than
        # naming a number or a field itself.
        js = JS_PATH.read_text(encoding="utf-8")
        assert 'Array.from(text.trim().split(/\\s+/).filter(Boolean).join(" ")).length' in js
        assert '"data-description-min"' in js and '"data-description-for"' in js
        assert re.search(rf"\b{READINESS_MIN_DESCRIPTION_CHARS}\b", js) is None
        assert "id_description" not in js
        # A body script under hx-boost: one document listener, a boot() per arrival (FRONTEND.md).
        assert "if (window.plDescriptionCount) {" in js and "window.plDescriptionCount.boot();" in js
        assert "Alpine.data(" not in js
