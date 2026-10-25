"""Schema spec for polls 0003 (#925 part 3): written answers add and roll back cleanly.

Forward adds ``Poll.allows_write_in`` (off), ``PollVote.write_in`` and ``anonymous``, makes
``choice`` nullable and adds the exactly one of constraint; every existing vote keeps its picked
answer. The reverse first deletes written answers, so ``choice`` can be required again, and keeps
every picked vote. Migrates the polls app with ``MigrationExecutor`` and restores head in a ``finally``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

_APP = "polls"
_BEFORE = "0002_poll_guild"
_AFTER = "0003_poll_write_in"


def _migrate(target: str):
    """Migrate the polls app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, table)}


def _member(apps, name: str):
    plan, _ = apps.get_model("membership", "MembershipPlan").objects.get_or_create(
        name="Std", defaults={"monthly_price": Decimal("10")}
    )
    return apps.get_model("membership", "Member").objects.create(membership_plan=plan, full_legal_name=name)


def _poll_with_choice(apps, question: str):
    now = timezone.now()
    poll = apps.get_model(_APP, "Poll").objects.create(
        question=question, opens_at=now, closes_at=now + timedelta(days=7)
    )
    choice = apps.get_model(_APP, "PollChoice").objects.create(poll=poll, text="Laser", position=0)
    return poll, choice


@pytest.mark.django_db(transaction=True)
def describe_migration_0003_poll_write_in():
    def it_adds_the_fields_and_keeps_every_picked_vote():
        try:
            apps = _migrate(_BEFORE)
            assert "write_in" not in _columns("polls_pollvote")
            poll, choice = _poll_with_choice(apps, "Zorblax before?")
            apps.get_model(_APP, "PollVote").objects.create(poll=poll, choice=choice, member=_member(apps, "Zorblax A"))

            apps = _migrate(_AFTER)

            assert {"write_in", "anonymous"} <= _columns("polls_pollvote")
            assert "allows_write_in" in _columns("polls_poll")
            vote = apps.get_model(_APP, "PollVote").objects.get()
            assert (vote.choice_id, vote.write_in, vote.anonymous) == (choice.pk, "", True)
            assert apps.get_model(_APP, "Poll").objects.get().allows_write_in is False
        finally:
            _migrate(_AFTER)

    def it_reverses_by_deleting_written_answers_and_keeping_picked_votes():
        try:
            apps = _migrate(_AFTER)
            poll, choice = _poll_with_choice(apps, "Zorblax after?")
            PollVote = apps.get_model(_APP, "PollVote")
            PollVote.objects.create(poll=poll, choice=choice, member=_member(apps, "Zorblax Picker"))
            PollVote.objects.create(poll=poll, write_in="A bandsaw", member=_member(apps, "Zorblax Writer"))

            apps = _migrate(_BEFORE)

            assert "write_in" not in _columns("polls_pollvote")
            assert list(apps.get_model(_APP, "PollVote").objects.values_list("choice_id", flat=True)) == [choice.pk]
        finally:
            _migrate(_AFTER)
