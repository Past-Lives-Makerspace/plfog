"""Data-migration spec for 0154 — blanking the old 'prefer not to share' pronouns sentinel.

Uses Django's ``MigrationExecutor`` (same approach as the 0132 spec) so fixtures are
built against the 0153 state, right after pronouns became free text but before the
sentinel cleanup. Each test restores the schema to head in a ``finally`` so the rest
of the suite sees the current DB.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "membership"
_BEFORE = "0153_alter_member_pronouns"
_AFTER = "0154_blank_prefer_not_to_share_pronouns"
_HEAD = "0154_blank_prefer_not_to_share_pronouns"


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
def describe_migration_0154_blank_prefer_not_to_share_pronouns():
    def it_blanks_the_sentinel_and_keeps_real_pronouns():
        try:
            apps = _migrate(_BEFORE)
            quiet = _make_member(apps, full_legal_name="Quiet Q", pronouns="prefer not to share")
            loud = _make_member(apps, full_legal_name="Loud L", pronouns="she/him")
            blank = _make_member(apps, full_legal_name="Blank B", pronouns="")

            apps = _migrate(_AFTER)
            Member = apps.get_model(_APP, "Member")
            assert Member.objects.get(pk=quiet.pk).pronouns == ""
            assert Member.objects.get(pk=loud.pk).pronouns == "she/him"
            assert Member.objects.get(pk=blank.pk).pronouns == ""
        finally:
            _migrate(_HEAD)

    def it_reverse_leaves_blanked_pronouns_blank():
        # The reverse deliberately restores nothing: the blanked rows are
        # indistinguishable from members who were already blank, and blank is a
        # valid value under the 0153 schema either way.
        try:
            apps = _migrate(_AFTER)
            member = _make_member(apps, full_legal_name="Rev R", pronouns="")
            kept = _make_member(apps, full_legal_name="Kept K", pronouns="they/them")

            apps = _migrate(_BEFORE)
            Member = apps.get_model(_APP, "Member")
            assert Member.objects.get(pk=member.pk).pronouns == ""
            assert Member.objects.get(pk=kept.pk).pronouns == "they/them"
        finally:
            _migrate(_HEAD)
