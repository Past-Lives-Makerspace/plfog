"""Data-migration spec for 0132 — backfilling ``MemberContact.kind`` from freeform labels.

Uses Django's ``MigrationExecutor`` (same approach as the 0086 spec) so fixtures are
built against the 0131 state where every row still carries the plain "other" default.
Each test restores the schema to head in a ``finally`` so the rest of the suite sees
the current DB.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "membership"
_BEFORE = "0131_membercontact_kind"
_AFTER = "0132_backfill_membercontact_kind"
_HEAD = "0132_backfill_membercontact_kind"


def _migrate(target: str):
    """Migrate the membership app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_member(apps, **kwargs):
    MembershipPlan = apps.get_model(_APP, "MembershipPlan")
    plan, _ = MembershipPlan.objects.get_or_create(name="Std", defaults={"monthly_price": Decimal("10")})
    Member = apps.get_model(_APP, "Member")
    return Member.objects.create(membership_plan=plan, **kwargs)


@pytest.mark.django_db(transaction=True)
def describe_migration_0132_backfill_membercontact_kind():
    def it_classifies_labels_into_website_social_and_other():
        try:
            apps = _migrate(_BEFORE)
            MemberContact = apps.get_model(_APP, "MemberContact")
            member = _make_member(apps, full_legal_name="Kinds K")
            website = MemberContact.objects.create(member=member, label="Website", value="https://k.example")
            social = MemberContact.objects.create(member=member, label="Instagram", value="@kinds")
            other = MemberContact.objects.create(member=member, label="Signal", value="@quiet-k")

            apps = _migrate(_AFTER)
            MemberContact = apps.get_model(_APP, "MemberContact")
            assert MemberContact.objects.get(pk=website.pk).kind == "website"
            assert MemberContact.objects.get(pk=social.pk).kind == "social"
            assert MemberContact.objects.get(pk=other.pk).kind == "other"
        finally:
            _migrate(_HEAD)

    def it_matches_keywords_case_insensitively_as_substrings():
        try:
            apps = _migrate(_BEFORE)
            MemberContact = apps.get_model(_APP, "MemberContact")
            member = _make_member(apps, full_legal_name="Substring S")
            shop = MemberContact.objects.create(member=member, label="My Etsy SHOP", value="https://etsy.example")
            tube = MemberContact.objects.create(member=member, label="my youtube channel", value="https://yt.example")

            apps = _migrate(_AFTER)
            MemberContact = apps.get_model(_APP, "MemberContact")
            assert MemberContact.objects.get(pk=shop.pk).kind == "website"
            assert MemberContact.objects.get(pk=tube.pk).kind == "social"
        finally:
            _migrate(_HEAD)

    def it_reverse_resets_every_row_to_other():
        try:
            apps = _migrate(_AFTER)
            MemberContact = apps.get_model(_APP, "MemberContact")
            member = _make_member(apps, full_legal_name="Rev R")
            website = MemberContact.objects.create(
                member=member, label="Website", value="https://r.example", kind="website"
            )
            social = MemberContact.objects.create(member=member, label="Instagram", value="@rev", kind="social")

            apps = _migrate(_BEFORE)
            MemberContact = apps.get_model(_APP, "MemberContact")
            assert MemberContact.objects.get(pk=website.pk).kind == "other"
            assert MemberContact.objects.get(pk=social.pk).kind == "other"
        finally:
            _migrate(_HEAD)
