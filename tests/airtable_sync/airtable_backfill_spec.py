"""The Airtable backfill pushes members, never guest accounts (#654)."""

from __future__ import annotations

from typing import Any

import pytest

from airtable_sync.management.commands.airtable_backfill import Command
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


class _RecordingTable:
    """Stands in for the Airtable members table and records what was created."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create(self, fields: dict[str, Any]) -> dict[str, str]:
        self.created.append(fields)
        return {"id": f"rec{len(self.created)}"}

    def update(self, record_id: str, fields: dict[str, Any]) -> None:
        raise AssertionError(f"no member here has an Airtable record, so {record_id} cannot be updated")


def describe_push_members():
    def it_pushes_members_and_skips_guest_accounts():
        MemberFactory(full_legal_name="Mira Member", status=Member.Status.ACTIVE)
        MemberFactory(full_legal_name="Gail Guest", status=Member.Status.GUEST)
        table = _RecordingTable()
        results = {"created": 0, "updated": 0}

        Command()._push_members(Member, table, False, results)

        assert results == {"created": 1, "updated": 0}
        assert [fields["Status"] for fields in table.created] == ["Active"]
