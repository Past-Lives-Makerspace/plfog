"""Schema spec for polls 0002 (#925 part 2): the nullable guild link adds and removes cleanly.

Additive and nullable, so the previous release keeps working while it applies, and a real reverse
that first deletes every guild poll, so a rollback never turns one into a site wide poll; site wide
polls survive it.
Migrates the polls app with ``MigrationExecutor`` and restores head in a ``finally``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

_APP = "polls"
_BEFORE = "0001_initial"
_AFTER = "0002_poll_guild"


def _migrate(target: str):
    """Migrate the polls app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _poll_columns() -> set[str]:
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, "polls_poll")}


@pytest.mark.django_db(transaction=True)
def describe_migration_0002_poll_guild():
    def it_adds_a_blank_guild_link_and_keeps_every_existing_poll_site_wide():
        try:
            apps = _migrate(_BEFORE)
            assert "guild_id" not in _poll_columns()
            Poll = apps.get_model(_APP, "Poll")
            now = timezone.now()
            Poll.objects.create(question="Zorblax before?", opens_at=now, closes_at=now + timedelta(days=7))

            apps = _migrate(_AFTER)

            assert "guild_id" in _poll_columns()
            poll = apps.get_model(_APP, "Poll").objects.get()
            assert poll.question == "Zorblax before?"
            assert poll.guild_id is None
        finally:
            _migrate(_AFTER)

    def it_reverses_by_deleting_guild_polls_and_keeping_site_wide_ones():
        try:
            apps = _migrate(_AFTER)
            Guild = apps.get_model("membership", "Guild")
            Poll = apps.get_model(_APP, "Poll")
            PollChoice = apps.get_model(_APP, "PollChoice")
            now = timezone.now()
            guild = Guild.objects.create(name="Zorblax Guild", slug="zorblax-guild")
            guild_poll = Poll.objects.create(
                question="Zorblax guild?", opens_at=now, closes_at=now + timedelta(days=7), guild=guild
            )
            PollChoice.objects.create(poll=guild_poll, text="Zorblax answer", position=0)
            Poll.objects.create(question="Zorblax site?", opens_at=now, closes_at=now + timedelta(days=7))

            apps = _migrate(_BEFORE)

            assert "guild_id" not in _poll_columns()
            assert list(apps.get_model(_APP, "Poll").objects.values_list("question", flat=True)) == ["Zorblax site?"]
            assert not apps.get_model(_APP, "PollChoice").objects.exists()
        finally:
            _migrate(_AFTER)
