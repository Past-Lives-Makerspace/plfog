"""Migration spec for classes 0099: every current instructor gets one personal discount code (#1065).

Uses Django's ``MigrationExecutor`` (the pattern of backfill_class_instructors_spec). Rows are
built at head with factories that never grant through ``Member.save`` (a member created already
able to teach), so no code exists until the migration runs; stepping back to 0098 runs the
reverse and stepping forward runs the backfill. Each test restores the schema to head in a
``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

import itertools
from datetime import timedelta
from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from classes.factories import DiscountCodeFactory
from classes.models import DiscountCode
from core.models import SiteConfiguration
from tests.membership.factories import MemberFactory

_APP = "classes"
_BEFORE = "0098_discountcode_is_personal"
_AFTER = "0099_backfill_personal_discount_codes"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]

_migration = import_module(f"classes.migrations.{_AFTER}")


def _migrate(target: str):
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _teacher(name: str, **kwargs):
    return MemberFactory(
        full_legal_name=name, instructor_oriented_at=timezone.now(), **{"preferred_name": "", **kwargs}
    )


def _codes(member) -> list[tuple[str, bool]]:
    return sorted(DiscountCode.objects.filter(instructor=member).values_list("code", "is_personal"))


@pytest.mark.django_db(transaction=True)
def describe_migration_0099_backfill_personal_discount_codes():
    def it_makes_one_code_for_every_instructor_at_the_site_percent():
        try:
            config = SiteConfiguration.load()
            config.instructor_code_discount_pct = 25
            config.save(update_fields=["instructor_code_discount_pct"])
            alex = _teacher("Alex Rivera")
            patty = _teacher("Patricia Lane", preferred_name="Patty")
            locked = MemberFactory(full_legal_name="Locked Out", instructor_oriented_at=None)
            _migrate(_BEFORE)
            apps = _migrate(_AFTER)
            assert _codes(alex) == [("ALEX25", True)]
            assert _codes(patty) == [("PATTY25", True)]
            assert _codes(locked) == []
            # The historical model: the live one names columns later migrations add (#1072).
            made = apps.get_model(_APP, "DiscountCode").objects.get(instructor_id=alex.pk)
            assert (made.discount_pct, made.is_approved, made.is_active, made.auto_apply) == (25, True, True, False)
            assert (made.max_uses, made.valid_from, made.valid_until, made.class_offering_id) == (
                None,
                None,
                None,
                None,
            )
            assert made.description == _migration.BACKFILL_DESCRIPTION
        finally:
            _migrate(_HEAD)

    def it_keeps_an_existing_instructor_code_as_the_personal_one():
        try:
            jacqueline = _teacher("Jacqueline Sowell")
            DiscountCodeFactory(code="JACQUELINE30", discount_pct=30, class_offering=None, instructor=jacqueline)
            two = _teacher("Morgan Two")
            older = DiscountCodeFactory(code="MORGANOLD", class_offering=None, instructor=two)
            DiscountCodeFactory(code="MORGANNEW", class_offering=None, instructor=two)
            DiscountCode.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=30))
            _migrate(_BEFORE)
            _migrate(_AFTER)
            assert _codes(jacqueline) == [("JACQUELINE30", True)]
            assert _codes(two) == [("MORGANNEW", False), ("MORGANOLD", True)]
        finally:
            _migrate(_HEAD)

    def it_adopts_only_a_live_code_and_leaves_the_others_alone():
        try:
            dana = _teacher("Dana Live")
            off = DiscountCodeFactory(code="DANAOFF", class_offering=None, instructor=dana, is_active=False)
            live = DiscountCodeFactory(code="DANALIVE", class_offering=None, instructor=dana)
            DiscountCode.objects.filter(pk=off.pk).update(created_at=timezone.now() - timedelta(days=30))
            kit = _teacher("Kit Pending")
            DiscountCodeFactory(code="KITPENDING", class_offering=None, instructor=kit, is_approved=False)
            _migrate(_BEFORE)
            apps = _migrate(_AFTER)
            assert _codes(dana) == [("DANALIVE", True), ("DANAOFF", False)]
            codes_then = apps.get_model(_APP, "DiscountCode").objects
            assert live.pk == codes_then.get(instructor_id=dana.pk, is_personal=True).pk
            assert _codes(kit) == [("KIT10", True), ("KITPENDING", False)]
            pending = codes_then.get(code="KITPENDING")
            assert (pending.is_approved, pending.is_active) == (False, True)
        finally:
            _migrate(_HEAD)

    def it_adds_last_name_letters_then_a_b_c_on_a_collision():
        try:
            DiscountCodeFactory(code="SAM10")
            sam = _teacher("Sam Ortiz")
            robin_a = _teacher("Robin Avery")
            robin_b = _teacher("Robin Banks")
            finger_1 = _teacher("Jacqueline Finger")
            finger_2 = _teacher("Jacqueline Finger")
            kai_1 = _teacher("Kai")
            kai_2 = _teacher("Kai")
            nameless = _teacher("李 小龙")
            _migrate(_BEFORE)
            _migrate(_AFTER)
            # Members are taken in pk order, so the first of each name keeps the plain code.
            assert _codes(sam) == [("SAMO10", True)]
            assert (_codes(robin_a), _codes(robin_b)) == ([("ROBIN10", True)], [("ROBINB10", True)])
            assert (_codes(finger_1), _codes(finger_2)) == ([("JACQUELINE10", True)], [("JACQUELINEF10", True)])
            assert (_codes(kai_1), _codes(kai_2)) == ([("KAI10", True)], [("KAIA10", True)])
            assert _codes(nameless) == [("TEACH10", True)]
        finally:
            _migrate(_HEAD)

    def it_names_the_same_codes_as_the_app():
        from classes.models import personal_code_candidates

        for display_name, legal in (
            ("Jacqueline Finger", "Jacqueline Finger"),
            ("Jackie", "Jacqueline Sowell"),
            ("Kai", "Kai"),
        ):
            ours = list(itertools.islice(_migration._candidates(display_name, legal, 25), 40))
            assert ours == list(itertools.islice(personal_code_candidates(display_name, legal, 25), 40))

    def it_makes_nothing_new_when_run_again():
        try:
            _teacher("Alex Rivera")
            _teacher("Jacqueline Sowell")
            apps = None
            _migrate(_BEFORE)
            apps = _migrate(_AFTER)
            before = DiscountCode.objects.count()
            _migration.backfill(apps, None)
            assert DiscountCode.objects.count() == before
        finally:
            _migrate(_HEAD)

    def it_reverses_by_deleting_only_the_unused_codes_it_made():
        try:
            alex = _teacher("Alex Rivera")
            used = _teacher("Robin Avery")
            jacqueline = _teacher("Jacqueline Sowell")
            DiscountCodeFactory(code="JACQUELINE30", class_offering=None, instructor=jacqueline)
            _migrate(_BEFORE)
            _migrate(_AFTER)
            DiscountCode.objects.filter(instructor=used).update(use_count=1)
            _migrate(_BEFORE)
            assert not DiscountCode.objects.filter(instructor=alex).exists()
            assert list(DiscountCode.objects.filter(instructor=used).values_list("code", flat=True)) == ["ROBIN10"]
            assert list(DiscountCode.objects.filter(instructor=jacqueline).values_list("code", flat=True)) == [
                "JACQUELINE30"
            ]
        finally:
            _migrate(_HEAD)

    def it_uses_the_default_percent_with_no_settings_row():
        try:
            alex = _teacher("Alex Rivera")
            SiteConfiguration.objects.all().delete()
            _migrate(_BEFORE)
            _migrate(_AFTER)
            assert _codes(alex) == [("ALEX10", True)]
        finally:
            _migrate(_HEAD)
