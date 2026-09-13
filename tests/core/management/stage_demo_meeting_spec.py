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
