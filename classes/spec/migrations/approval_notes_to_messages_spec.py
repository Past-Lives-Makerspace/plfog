"""Migration spec for classes 0096: every review decision's note moves into its class's thread (#975).

Each non blank note becomes a reviewer's message, by the decider when recorded, dated at the
decision, oldest first; the admin's auto-close system note and blank notes add nothing. The
notes stay on the approvals. The reverse removes exactly the carried messages. Uses Django's
``MigrationExecutor`` (the pattern of guild_class_types_spec); each test restores head in a
``finally``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from classes.factories import ClassOfferingFactory, UserFactory
from classes.models import ClassApproval, ClassMessage

_APP = "classes"
_BEFORE = "0095_class_messages"
_AFTER = "0096_approval_notes_to_messages"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]


def _migrate(target: str) -> None:
    MigrationExecutor(connection).migrate([(_APP, target)])


def _approval(offering, *, notes: str, decided_at=None, decided_by=None, role=ClassApproval.Role.ADMIN):
    return ClassApproval.objects.create(
        class_offering=offering,
        role=role,
        decision=ClassApproval.Decision.CHANGES_REQUESTED if notes else ClassApproval.Decision.APPROVED,
        notes=notes,
        decided_at=decided_at,
        decided_by=decided_by,
    )


@pytest.mark.django_db(transaction=True)
def describe_migration_0096_approval_notes_to_messages():
    def it_carries_each_note_into_its_class_thread_oldest_first():
        try:
            _migrate(_BEFORE)
            lead = UserFactory(username="mig-lead@example.com")
            offering = ClassOfferingFactory(title="Zz migrate mugs")
            other = ClassOfferingFactory(title="Zz migrate lamps")
            now = timezone.now()
            later = _approval(offering, notes="  Zzsecond round  ", decided_at=now, decided_by=lead)
            earlier = _approval(offering, notes="Zzfirst round", decided_at=now - timedelta(days=3))
            undated = _approval(other, notes="Zzno date", role=ClassApproval.Role.GUILD_LEAD)
            _approval(offering, notes="")
            _approval(offering, notes=" \n ")
            _approval(offering, notes="Approved automatically when an admin gave final approval.", decided_at=now)

            _migrate(_AFTER)

            carried = list(ClassMessage.objects.filter(class_offering=offering).order_by("created_at", "pk"))
            assert [(m.body, m.author_id, m.created_at) for m in carried] == [
                ("Zzfirst round", None, earlier.decided_at),
                ("Zzsecond round", lead.pk, later.decided_at),
            ]
            [lone] = ClassMessage.objects.filter(class_offering=other)
            assert (lone.body, lone.created_at) == ("Zzno date", undated.created_at)
            assert ClassApproval.objects.get(pk=later.pk).notes == "  Zzsecond round  "
        finally:
            _migrate(_HEAD)

    def it_removes_only_the_carried_messages_on_the_way_back():
        try:
            _migrate(_BEFORE)
            offering = ClassOfferingFactory(title="Zz reverse mugs")
            _approval(offering, notes="Zzcarried", decided_at=timezone.now())
            _migrate(_AFTER)
            ClassMessage.objects.create(class_offering=offering, body="Zzwritten since")

            _migrate(_BEFORE)

            assert list(ClassMessage.objects.values_list("body", flat=True)) == ["Zzwritten since"]
            assert ClassApproval.objects.get(class_offering=offering).notes == "Zzcarried"
        finally:
            _migrate(_HEAD)
