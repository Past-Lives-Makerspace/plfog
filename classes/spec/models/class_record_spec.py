"""Every run belongs to one class (#1260): ClassRecord and the class a run joins on save."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering, ClassRecord

pytestmark = pytest.mark.django_db


def describe_a_new_run():
    def it_starts_a_class_named_for_its_title():
        run = ClassOfferingFactory(title="Wheel Throwing 101", slug="wheel-throwing-101-2026-12-01")

        record = run.class_record
        assert record.slug == "wheel-throwing-101"
        assert record.title == "Wheel Throwing 101"
        assert record.category == run.category
        assert str(record) == "Wheel Throwing 101"

    def it_leaves_an_imported_date_out_of_the_address():
        run = ClassOfferingFactory(title="Blacksmithing 101 - 6/5/26", slug="blacksmithing-101-legacy")

        assert run.class_record.slug == "blacksmithing-101"

    def it_gives_an_untitled_run_a_class_of_its_own():
        run = ClassOfferingFactory(title="", slug="untitled")

        assert run.class_record.slug == "class"

    def it_joins_the_class_of_a_run_with_the_same_title_and_class_type():
        first = ClassOfferingFactory(title="Picture Framing", slug="picture-framing-2026-10-11")
        second = ClassOfferingFactory(
            title="Picture Framing", slug="picture-framing-2026-11-08", category=first.category
        )

        assert second.class_record_id == first.class_record_id
        assert ClassRecord.objects.count() == 1

    def it_keeps_the_same_title_in_another_class_type_apart():
        glass = ClassOfferingFactory(title="Intro", slug="intro-glass")
        wood = ClassOfferingFactory(title="Intro", slug="intro-wood")

        assert glass.class_record_id != wood.class_record_id
        assert glass.class_record.slug == "intro"
        assert wood.class_record.slug == f"intro-{wood.category.slug}"

    def it_numbers_the_address_once_the_class_type_form_is_taken_too():
        category = CategoryFactory(slug="glass")
        ClassOfferingFactory(title="Other", slug="intro")
        ClassOfferingFactory(title="Another", slug="intro-glass")

        run = ClassOfferingFactory(title="Intro", slug="intro-2026-12-01", category=category)

        assert run.class_record.slug == "intro-glass-2"

    def it_never_takes_the_old_address_of_a_run_in_another_class():
        ClassOfferingFactory(title="Open Forge Sundays", slug="forge")

        run = ClassOfferingFactory(title="Forge", slug="forge-2026-12-01")

        assert run.class_record.slug == f"forge-{run.category.slug}"


def describe_a_run_without_a_class():
    def it_joins_one_on_its_next_save_even_a_narrow_one():
        run = ClassOfferingFactory(title="Pottery", slug="pottery")
        ClassOffering.objects.filter(pk=run.pk).update(class_record=None)
        ClassRecord.objects.all().delete()
        run.refresh_from_db()

        run.title = "Pottery"
        run.save(update_fields=["title"])

        run.refresh_from_db()
        # Its own old address is free to it: that link simply becomes the class page.
        assert run.class_record.slug == "pottery"


def describe_runs_made_from_a_class():
    def it_keeps_run_it_again_on_the_same_class():
        source = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")
        class_id = source.class_record_id

        run = ClassOffering.objects.get(pk=source.pk).duplicate_as_new_run()

        assert run.pk != source.pk
        assert run.class_record_id == class_id
        assert ClassRecord.objects.count() == 1

    def it_keeps_split_dates_on_the_same_class():
        source = ClassOfferingFactory(title="Sushi Set", slug="sushi-set", status=ClassOffering.Status.PUBLISHED)
        for day in (10, 20):
            ClassSessionFactory(class_offering=source, starts_at=timezone.now() + timedelta(days=day))

        runs = source.split_dates_into_runs(actor=None)

        assert runs
        assert {run.class_record_id for run in runs} == {source.class_record_id}

    def it_starts_a_new_class_for_a_duplicate():
        source = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")

        copy = ClassOffering.objects.get(pk=source.pk).duplicate()

        assert copy.class_record_id != source.class_record_id
        assert copy.class_record.title == "Make a Cup (copy)"

    def it_stays_on_its_class_when_renamed():
        run = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")
        class_id = run.class_record_id

        run.title = "Make a Mug"
        run.save()

        run.refresh_from_db()
        assert run.class_record_id == class_id
