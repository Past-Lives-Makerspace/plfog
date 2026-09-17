"""Specs for classes/management/commands/backfill_override_decisions.py."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from classes.factories import ClassApprovalFactory, ClassOfferingFactory
from classes.models import ADMIN_OVERRIDE_NOTE, ClassApproval, ClassOffering

pytestmark = pytest.mark.django_db

Decision = ClassApproval.Decision
Role = ClassApproval.Role


def _auto_closed_row(**kwargs) -> ClassApproval:
    """A guild-lead row shaped exactly as the pre-marker override path left it."""
    defaults = {
        "class_offering": ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="Forged Bowls"),
        "role": Role.GUILD_LEAD,
        "decision": Decision.APPROVED,
        "notes": ADMIN_OVERRIDE_NOTE,
    }
    return ClassApprovalFactory(**{**defaults, **kwargs})


def describe_backfill_override_decisions():
    def it_names_the_database_before_anything_else():
        out = StringIO()
        call_command("backfill_override_decisions", stdout=out)
        assert out.getvalue().splitlines()[0].startswith("Database host: ")

    def it_says_so_when_there_is_nothing_to_correct():
        out = StringIO()
        call_command("backfill_override_decisions", stdout=out)
        assert "No auto-closed guild-lead rows to correct." in out.getvalue()

    def it_corrects_the_auto_closed_row_and_names_it():
        row = _auto_closed_row()
        out = StringIO()
        call_command("backfill_override_decisions", stdout=out)
        row.refresh_from_db()
        assert row.decision == Decision.OVERRIDDEN_BY_ADMIN
        assert row.notes == ADMIN_OVERRIDE_NOTE
        assert f"#{row.pk}" in out.getvalue()
        assert "Forged Bowls" in out.getvalue()
        assert "Corrected 1 row(s)." in out.getvalue()

    def describe_dry_run():
        def it_writes_nothing():
            row = _auto_closed_row()
            out = StringIO()
            call_command("backfill_override_decisions", "--dry-run", stdout=out)
            row.refresh_from_db()
            assert row.decision == Decision.APPROVED
            assert "[dry-run] would correct 1 row(s); nothing written" in out.getvalue()

    def it_leaves_a_real_guild_lead_approval_alone():
        """The note literal is the whole discriminator: a hand-typed approval is untouched."""
        row = _auto_closed_row(notes="Room is free that week.")
        call_command("backfill_override_decisions", stdout=StringIO())
        row.refresh_from_db()
        assert row.decision == Decision.APPROVED

    def it_leaves_an_admin_row_alone():
        row = _auto_closed_row(role=Role.ADMIN)
        call_command("backfill_override_decisions", stdout=StringIO())
        row.refresh_from_db()
        assert row.decision == Decision.APPROVED

    def it_is_safe_to_run_twice():
        row = _auto_closed_row()
        call_command("backfill_override_decisions", stdout=StringIO())
        out = StringIO()
        call_command("backfill_override_decisions", stdout=out)
        row.refresh_from_db()
        assert row.decision == Decision.OVERRIDDEN_BY_ADMIN
        assert "No auto-closed guild-lead rows to correct." in out.getvalue()
