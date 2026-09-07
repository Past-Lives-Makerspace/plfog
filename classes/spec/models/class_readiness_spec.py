"""BDD specs for the readiness checklist and the submit guard that reads it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, READY_DESCRIPTION
from classes.models import ClassOffering, CmsActivity


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

    def it_fails_the_gallery_without_a_gallery_photo(db):
        offering = ClassOfferingFactory(ready=True, gallery=0)
        assert _items(offering)["Gallery photo"] is False

    def it_fails_the_description_under_forty_characters(db):
        offering = ClassOfferingFactory(description="<p>Short   words</p>")
        assert _items(offering)["Description"] is False

    def it_passes_the_description_at_forty_characters_of_plain_text(db):
        offering = ClassOfferingFactory(description="<p>" + "x" * 40 + "</p>")
        assert _items(offering)["Description"] is True

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
            "Write a short description. Add at least one date. Set how many can attend."
        )


def describe_submit_for_review_guard():
    def it_raises_listing_every_failing_item_and_changes_nothing(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, description="Short")
        with pytest.raises(ValidationError) as excinfo:
            offering.submit_for_review()
        assert excinfo.value.messages == ["Not ready to submit: Write a short description. Add at least one date."]
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
