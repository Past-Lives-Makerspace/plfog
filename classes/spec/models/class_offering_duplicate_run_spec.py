"""BDD specs for ClassOffering.duplicate_as_new_run — spinning off another date-set."""

from __future__ import annotations

from classes.factories import ClassOfferingFactory, SeriesClassOfferingFactory
from classes.models import ClassOffering


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
