"""Every run belongs to one class (#1260): ClassRecord and the class a run joins on save."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering, ClassRecord
from core.integrations.eventbrite import EventbriteSync

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

    def it_files_a_duplicate_under_a_class_that_exists_after_a_slug_race():
        ClassOfferingFactory(title="Taken", slug="taken")
        source = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")

        # The first save collides on the slug and rolls back, the class it started with it.
        with patch("classes.models._unique_slug", side_effect=["taken", "make-a-cup-copy"]):
            copy = ClassOffering.objects.get(pk=source.pk).duplicate()

        copy.refresh_from_db()
        assert copy.slug == "make-a-cup-copy"
        assert ClassRecord.objects.filter(pk=copy.class_record_id).exists()
        assert copy.class_record_id != source.class_record_id

    def it_stays_on_its_class_when_renamed():
        run = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")
        class_id = run.class_record_id

        run.title = "Make a Mug"
        run.save()

        run.refresh_from_db()
        assert run.class_record_id == class_id


def describe_a_class_whose_runs_go():
    def it_goes_with_its_last_run_and_frees_its_address():
        run = ClassOfferingFactory(title="Pottery", slug="pottery-2026-12-01")

        run.delete()

        assert not ClassRecord.objects.exists()
        assert ClassOfferingFactory(title="Pottery", slug="pottery-2027-01-05").class_record.slug == "pottery"

    def it_stays_while_a_run_remains():
        first = ClassOfferingFactory(title="Pottery", slug="pottery-1")
        second = ClassOfferingFactory(title="Pottery", slug="pottery-2", category=first.category)

        first.delete()

        assert ClassRecord.objects.get().runs.get() == second

    def it_goes_when_its_runs_are_deleted_together():
        first = ClassOfferingFactory(title="Pottery", slug="pottery-1")
        second = ClassOfferingFactory(title="Pottery", slug="pottery-2", category=first.category)

        ClassOffering.objects.filter(pk__in=[first.pk, second.pk]).delete()

        assert not ClassRecord.objects.exists()

    def it_follows_its_newest_run_when_none_is_left_in_its_class_type():
        first = ClassOfferingFactory(title="Pottery", slug="pottery-1")
        moved = ClassOfferingFactory(title="Pottery", slug="pottery-2", category=first.category)
        elsewhere = CategoryFactory()
        ClassOffering.objects.filter(pk=moved.pk).update(category=elsewhere)

        first.delete()

        assert ClassRecord.objects.get().category == elsewhere


def describe_a_class_whose_runs_move_class_type():
    def it_moves_with_its_runs():
        first = ClassOfferingFactory(title="Picture Framing", slug="framing-1")
        second = ClassOfferingFactory(title="Picture Framing", slug="framing-2", category=first.category)
        wood = CategoryFactory()

        first.category = wood
        first.save()

        second.refresh_from_db()
        assert second.category == wood
        assert ClassRecord.objects.get().category == wood

    def it_moves_every_run_when_one_is_renamed_and_moved_at_once():
        first = ClassOfferingFactory(title="Picture Framing", slug="framing-1")
        second = ClassOfferingFactory(title="Picture Framing", slug="framing-2", category=first.category)
        wood = CategoryFactory()

        first.title = "Mat Cutting"
        first.category = wood
        first.save()

        second.refresh_from_db()
        record = ClassRecord.objects.get()
        assert (record.title, record.category) == ("Mat Cutting", wood)
        assert (second.title, second.category, second.grouping_key) == ("Mat Cutting", wood, first.grouping_key)


def describe_a_renamed_class():
    def it_renames_the_class_and_every_run_but_keeps_its_address():
        first = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")
        second = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup-run", category=first.category)

        second.title = "Make a Mug"
        second.save(update_fields=["title"])

        first.refresh_from_db()
        record = ClassRecord.objects.get()
        assert (record.title, record.slug) == ("Make a Mug", "make-a-cup")
        assert first.title == "Make a Mug"
        assert first.grouping_key == second.grouping_key == f"make-a-mug:{first.category_id}"

    def it_queues_a_listed_run_to_send_eventbrite_the_new_name():
        listed = ClassOfferingFactory(
            title="Make a Cup",
            slug="make-a-cup",
            eventbrite_enabled=True,
            eventbrite_sync_state=ClassOffering.EventbriteSyncState.LISTED,
        )
        idle = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup-idle", category=listed.category)
        edited = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup-run", category=listed.category)

        edited.title = "Make a Mug"
        edited.save()

        listed.refresh_from_db()
        idle.refresh_from_db()
        assert listed.eventbrite_sync_state == ClassOffering.EventbriteSyncState.PENDING
        assert listed.eventbrite_sync_error == EventbriteSync.EDIT_SAVED
        assert idle.eventbrite_sync_state == ClassOffering.EventbriteSyncState.IDLE

    def it_leaves_the_class_alone_on_a_save_that_does_not_write_the_title():
        first = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")
        second = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup-run", category=first.category)

        second.title = "Make a Mug"
        second.capacity = 9
        second.save(update_fields=["capacity"])

        first.refresh_from_db()
        assert first.title == "Make a Cup"
        assert ClassRecord.objects.get().title == "Make a Cup"


TAKEN = "Another class in this Class Type is already called Wheel Throwing 101. Pick a different name."


def describe_a_rename_onto_another_classes_title():
    def it_is_refused_on_save():
        ClassOfferingFactory(title="Wheel Throwing 101", slug="wheel-throwing")
        run = ClassOfferingFactory(
            title="Hand Building", slug="hand-building", category=ClassRecord.objects.get().category
        )

        run.title = "wheel throwing 101"
        with pytest.raises(ValidationError) as refused:
            run.save()

        assert refused.value.message_dict == {"title": [TAKEN]}
        run.refresh_from_db()
        assert run.title == "Hand Building"

    def it_is_refused_by_clean():
        taken = ClassOfferingFactory(title="Wheel Throwing 101", slug="wheel-throwing")
        run = ClassOfferingFactory(title="Hand Building", slug="hand-building", category=taken.category)

        run.title = "Wheel Throwing 101"
        with pytest.raises(ValidationError) as refused:
            run.clean()

        assert refused.value.message_dict == {"title": [TAKEN]}

    def it_is_refused_when_a_move_lands_on_it():
        taken = ClassOfferingFactory(title="Wheel Throwing 101", slug="wheel-throwing")
        run = ClassOfferingFactory(title="Wheel Throwing 101", slug="wheel-elsewhere")

        run.category = taken.category
        with pytest.raises(ValidationError):
            run.save(update_fields=["category"])

    def it_allows_a_name_held_only_in_another_class_type():
        ClassOfferingFactory(title="Wheel Throwing 101", slug="wheel-throwing")
        run = ClassOfferingFactory(title="Hand Building", slug="hand-building")

        run.title = "Wheel Throwing 101"
        run.full_clean()
        run.save()

        assert ClassRecord.objects.count() == 2

    def it_allows_a_rename_that_keeps_the_same_name():
        run = ClassOfferingFactory(title="Wheel throwing 101", slug="wheel-throwing")

        run.title = "Wheel Throwing 101!"
        run.full_clean()
        run.save()

        assert ClassRecord.objects.get().title == "Wheel Throwing 101!"


def describe_a_renamed_class_listed_before_the_eventbrite_rules():
    def it_is_not_queued():
        old = ClassOfferingFactory(
            title="Make a Cup",
            slug="make-a-cup",
            eventbrite_sync_state=ClassOffering.EventbriteSyncState.LISTED,
        )
        ClassOffering.objects.filter(pk=old.pk).update(eventbrite_rules_agreed_at=None)
        edited = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup-run", category=old.category)

        edited.title = "Make a Mug"
        edited.save()

        old.refresh_from_db()
        assert old.eventbrite_sync_state == ClassOffering.EventbriteSyncState.LISTED


def describe_a_class_moved_while_a_run_is_in_review():
    def it_re_asks_the_new_guild_for_the_run_in_review():
        from classes.factories import UserFactory
        from classes.models import ClassApproval
        from membership.models import Member
        from tests.membership.factories import GuildFactory

        def lead(email: str) -> Member:
            return Member.objects.get(user=UserFactory(username=email, email=email))

        old_guild = GuildFactory(name="Woodshop", guild_lead=lead("old-lead@example.com"))
        new_guild = GuildFactory(name="Leather", guild_lead=lead("new-lead@example.com"))
        old_type, new_type = CategoryFactory(guild=old_guild), CategoryFactory(guild=new_guild)
        live = ClassOfferingFactory(title="Forge Night", slug="forge-1", category=old_type)
        pending = ClassOfferingFactory(
            title="Forge Night", slug="forge-2", category=old_type, ready=True, status=ClassOffering.Status.DRAFT
        )
        (old_gate,) = pending.submit_for_review()

        live.category = new_type
        live.save()

        assert not ClassApproval.objects.filter(pk=old_gate.pk).exists()
        assert pending.approvals.get(role=ClassApproval.Role.GUILD_LEAD, decision="").opened_for_guild == new_guild
