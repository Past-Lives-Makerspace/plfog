"""BDD specs for ``Member.whats_new_seen`` (#1007): the seen list and its database default.

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

#: The membership migration right before the column arrived: its ``Member`` writes without it.
BEFORE = ("membership", "0255_member_name_at_deletion_db_default")


def describe_whats_new_seen():
    def it_starts_empty():
        assert MemberFactory().whats_new_seen == []
        assert Member._meta.get_field("whats_new_seen").db_default == []

    def it_reads_as_an_empty_list_on_a_row_written_without_the_column():
        # A historical model from before 0256, as a migration spec or the old release would insert.
        plan = MembershipPlanFactory()
        old_member = MigrationExecutor(connection).loader.project_state([BEFORE]).apps.get_model("membership", "Member")
        assert "whats_new_seen" not in {field.name for field in old_member._meta.get_fields()}

        row = old_member.objects.create(membership_plan_id=plan.pk, full_legal_name="Zorblax Historical")

        assert Member.objects.get(pk=row.pk).whats_new_seen == []
        with connection.cursor() as cursor:
            cursor.execute("SELECT whats_new_seen FROM membership_member WHERE id = %s", [row.pk])
            assert cursor.fetchone()[0] in ("[]", [])
