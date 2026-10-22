"""Migration spec for hq 0013: the named members become the Team, and nobody loses access.

0013 renames ``HubCard.named_members`` to ``team`` in place (the table keeps its name, so no row moves) and adds an
empty ``can_open`` list. Forward, every person named on a card is on its Team; reverse, they are its named members
again. Uses Django's ``MigrationExecutor`` (the leadership tabs spec's approach) and restores the head in a
``finally``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "hq"
_BEFORE = "0012_ledger_tally"
_AFTER = "0013_hubcard_team_can_open"


def _migrate(target: str) -> Any:
    """Migrate the hq app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _head() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def _member(apps: Any, name: str) -> Any:
    plan_model = apps.get_model("membership", "MembershipPlan")
    plan, _ = plan_model.objects.get_or_create(name="Std", defaults={"monthly_price": Decimal("10")})
    return apps.get_model("membership", "Member").objects.create(membership_plan=plan, full_legal_name=name)


def _card(apps: Any, kind: str) -> Any:
    hub, _ = apps.get_model(_APP, "EventHub").objects.get_or_create(slug="hb-spec", defaults={"title": "HB"})
    return apps.get_model(_APP, "HubCard").objects.create(hub=hub, kind=kind, title=kind)


@pytest.mark.django_db(transaction=True)
def describe_migration_0013_hubcard_team_can_open():
    def it_puts_every_named_member_on_the_cards_team_and_leaves_can_open_empty():
        try:
            apps = _migrate(_BEFORE)
            lee, kate = _member(apps, "Lee Lumen"), _member(apps, "Kate Kiln")
            ledger, wine = _card(apps, "ledger"), _card(apps, "wine_wall")
            ledger.named_members.set([lee, kate])
            wine.named_members.set([kate])

            apps = _migrate(_AFTER)
            card_model = apps.get_model(_APP, "HubCard")
            assert set(card_model.objects.get(pk=ledger.pk).team.values_list("pk", flat=True)) == {lee.pk, kate.pk}
            assert list(card_model.objects.get(pk=wine.pk).team.values_list("pk", flat=True)) == [kate.pk]
            assert not card_model.can_open.through.objects.exists()
        finally:
            _head()

    def it_gives_the_team_back_as_named_members_on_the_way_down():
        try:
            apps = _migrate(_AFTER)
            lee = _member(apps, "Lee Lumen")
            card = _card(apps, "sponsorship")
            card.team.set([lee])

            apps = _migrate(_BEFORE)
            card = apps.get_model(_APP, "HubCard").objects.get(pk=card.pk)
            assert list(card.named_members.values_list("pk", flat=True)) == [lee.pk]
        finally:
            _head()
