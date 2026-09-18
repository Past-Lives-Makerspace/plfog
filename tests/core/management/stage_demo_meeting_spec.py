"""BDD specs for the demo guild type that stage_demo_meeting owns.

Only the category lifecycle is covered here. Staging itself writes the four
production personas and is exercised by hand before a live walkthrough.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import Category
from core.management.commands.stage_demo_meeting import CATEGORY_NAME, CATEGORY_SLUG

pytestmark = pytest.mark.django_db


def describe_stage_demo_meeting():
    def describe_remove():
        def it_drops_the_demo_guild_type_when_nothing_is_filed_under_it():
            CategoryFactory(slug=CATEGORY_SLUG, name=CATEGORY_NAME)
            call_command("stage_demo_meeting", "--remove")
            assert not Category.objects.filter(slug=CATEGORY_SLUG).exists()

        def it_keeps_the_demo_guild_type_when_a_class_is_still_filed_under_it():
            category = CategoryFactory(slug=CATEGORY_SLUG, name=CATEGORY_NAME)
            ClassOfferingFactory(category=category, slug="not-a-staged-class")
            call_command("stage_demo_meeting", "--remove")
            assert Category.objects.filter(slug=CATEGORY_SLUG).exists()

        def it_says_nothing_when_the_demo_guild_type_is_already_gone():
            call_command("stage_demo_meeting", "--remove")
            assert not Category.objects.filter(slug=CATEGORY_SLUG).exists()

        def describe_dry_run():
            def it_leaves_the_demo_guild_type_alone():
                CategoryFactory(slug=CATEGORY_SLUG, name=CATEGORY_NAME)
                call_command("stage_demo_meeting", "--remove", "--dry-run")
                assert Category.objects.filter(slug=CATEGORY_SLUG).exists()


def describe_upserting_a_persona_registration():
    """``_upsert_registration`` has to survive the rows migration 0065 leaves behind.

    That migration cancels a duplicate signup and keeps the earlier one. Default
    ordering is newest-first, so the cancelled row is the one a naive ``.first()``
    hands back, and confirming it would collide with the row that kept the seat.
    This command is run against production, so it must not crash mid-run.
    """

    def _command():
        from core.management.commands.stage_demo_meeting import Command

        return Command()

    def it_updates_the_live_row_not_the_cancelled_duplicate(db):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        offering = ClassOfferingFactory(slug="persona-upsert")
        email = "counciltreasurer+member@pastlives.space"
        kept = RegistrationFactory(
            class_offering=offering, email=email, status=Registration.Status.PENDING, first_name="Old"
        )
        cancelled = RegistrationFactory(
            class_offering=offering,
            email=email,
            status=Registration.Status.CANCELLED,
            cancellation_reason="Duplicate signup for this class, cancelled automatically by migration classes.0065.",
        )

        row = _command()._upsert_registration(
            offering,
            local="member",
            first="New",
            last="Persona",
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=0,
        )

        assert row.pk == kept.pk
        kept.refresh_from_db()
        cancelled.refresh_from_db()
        assert kept.status == Registration.Status.CONFIRMED
        assert kept.first_name == "New"
        assert cancelled.status == Registration.Status.CANCELLED  # left where the migration put it
        assert Registration.objects.filter(class_offering=offering, email=email).count() == 2
