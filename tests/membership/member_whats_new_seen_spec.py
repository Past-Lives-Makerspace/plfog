"""BDD specs for ``Member.whats_new_seen`` and ``Member.whats_new_opened`` (#1007): the two lists and their database defaults.

STANDARDS §10: a migration applies while the previous release still serves, and that release's
``INSERT`` omits the column, as does every historical ``Member`` a migration spec builds. Both land
on the database default, so the column must carry one, the way ``name_at_deletion`` does (0255).
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

#: Each list column with the membership migration right before it arrived: its ``Member`` writes without the column.
COLUMNS = [
    ("whats_new_seen", "0255_member_name_at_deletion_db_default"),
    ("whats_new_opened", "0256_member_whats_new_seen"),
]


def describe_the_whats_new_lists():
    @pytest.mark.parametrize(("column", "before"), COLUMNS)
    def it_starts_empty(column, before):
        assert getattr(MemberFactory(), column) == []
        assert Member._meta.get_field(column).db_default == []

    @pytest.mark.parametrize(("column", "before"), COLUMNS)
    def it_reads_as_an_empty_list_on_a_row_written_without_the_column(column, before):
        # A historical model from before the column, as a migration spec or the old release would insert.
        plan = MembershipPlanFactory()
        old_member = (
            MigrationExecutor(connection)
            .loader.project_state([("membership", before)])
            .apps.get_model("membership", "Member")
        )
        assert column not in {field.name for field in old_member._meta.get_fields()}

        row = old_member.objects.create(membership_plan_id=plan.pk, full_legal_name="Zorblax Historical")

        assert getattr(Member.objects.get(pk=row.pk), column) == []
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {column} FROM membership_member WHERE id = %s", [row.pk])  # noqa: S608 - a column name from this file
            assert cursor.fetchone()[0] in ("[]", [])
