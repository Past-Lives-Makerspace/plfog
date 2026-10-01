"""Data-migration spec for 0190: the Leadership Directory's first two tabs (#564).

Forward makes a People tab from the page's team heading and intro and the Guild Leads tab
from its guilds heading and intro, in that order, and puts every listing (listed or not) on
the People tab. Reverse folds the tabs' wording back onto the page, keeps one listing per
member so 0189's reverse can restore the one-card-per-member rule, and drops the tabs.

Uses Django's ``MigrationExecutor`` (the 0132 spec's approach) so fixtures are built against
the state before or after; each test restores the head in a ``finally``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

_APP = "membership"
_BEFORE = "0188_member_teaching_contact"
_AFTER = "0190_leadership_tabs_data"


def _migrate(target: str) -> Any:
    """Migrate the membership app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _member(apps: Any, name: str) -> Any:
    plan_model = apps.get_model(_APP, "MembershipPlan")
    plan, _ = plan_model.objects.get_or_create(name="Std", defaults={"monthly_price": Decimal("10")})
    return apps.get_model(_APP, "Member").objects.create(membership_plan=plan, full_legal_name=name)


@pytest.mark.django_db(transaction=True)
def describe_migration_0190_leadership_tabs_data():
    def it_makes_the_people_tab_then_guild_leads_from_the_page_wording_and_moves_every_listing():
        try:
            apps = _migrate(_BEFORE)
            apps.get_model(_APP, "LeadershipPage").objects.update_or_create(
                pk=1,
                defaults={
                    "team_heading": "The Crew",
                    "team_intro": "Who keeps the lights on.",
                    "guilds_heading": "Shop Leads",
                    "guilds_intro": "",
                },
            )
            listing_model = apps.get_model(_APP, "LeadershipListing")
            listed = listing_model.objects.create(member=_member(apps, "Ada Aldous"), is_listed=True, sort_order=0)
            hidden = listing_model.objects.create(member=_member(apps, "Hidden Hank"), is_listed=False, sort_order=1)

            apps = _migrate(_AFTER)
            tab_model = apps.get_model(_APP, "LeadershipTab")
            assert list(tab_model.objects.order_by("sort_order").values_list("title", "intro", "kind")) == [
                ("The Crew", "Who keeps the lights on.", "people"),
                ("Shop Leads", "", "guild_leads"),
            ]
            people = tab_model.objects.get(kind="people")
            listing_model = apps.get_model(_APP, "LeadershipListing")
            assert set(listing_model.objects.values_list("pk", "tab_id")) == {
                (listed.pk, people.pk),
                (hidden.pk, people.pk),
            }
            assert listing_model.objects.get(pk=hidden.pk).is_listed is False
        finally:
            _migrate(_AFTER)

    def it_uses_the_default_wording_when_the_page_was_never_saved():
        try:
            apps = _migrate(_BEFORE)
            apps.get_model(_APP, "LeadershipPage").objects.all().delete()

            apps = _migrate(_AFTER)
            tab_model = apps.get_model(_APP, "LeadershipTab")
            assert list(tab_model.objects.order_by("sort_order").values_list("title", "kind")) == [
                ("Leadership & Admin Team", "people"),
                ("Guild Leaders", "guild_leads"),
            ]
            assert apps.get_model(_APP, "LeadershipPage").objects.count() == 0  # forward reads, never writes it
        finally:
            _migrate(_AFTER)

    def it_reverses_to_one_listing_per_member_with_the_wording_back_on_the_page():
        try:
            apps = _migrate(_AFTER)
            tab_model = apps.get_model(_APP, "LeadershipTab")
            listing_model = apps.get_model(_APP, "LeadershipListing")
            role_model = apps.get_model(_APP, "LeadershipRole")
            tab_model.objects.all().delete()
            board = tab_model.objects.create(title="Board", intro="Advisors.", kind="people", sort_order=0)
            council = tab_model.objects.create(title="Council", intro="", kind="people", sort_order=1)
            tab_model.objects.create(title="Guild Leads", intro="From each guild.", kind="guild_leads", sort_order=2)
            morlock = _member(apps, "Morlock")
            # Hidden on the first tab, shown on the second: the card members could see is the one kept.
            hidden_board = listing_model.objects.create(tab=board, member=morlock, is_listed=False)
            shown_council = listing_model.objects.create(tab=council, member=morlock, is_listed=True)
            role_model.objects.create(listing=hidden_board, title="Board Advisor")
            role_model.objects.create(listing=shown_council, title="Guild Executor")
            ada = _member(apps, "Ada Aldous")
            ada_board = listing_model.objects.create(tab=board, member=ada, is_listed=True)
            ada_council = listing_model.objects.create(tab=council, member=ada, is_listed=True)

            apps = _migrate(_BEFORE)
            page = apps.get_model(_APP, "LeadershipPage").objects.get(pk=1)
            assert (page.team_heading, page.team_intro) == ("Board", "Advisors.")
            assert (page.guilds_heading, page.guilds_intro) == ("Guild Leads", "From each guild.")
            listing_model = apps.get_model(_APP, "LeadershipListing")
            assert set(listing_model.objects.values_list("pk", flat=True)) == {shown_council.pk, ada_board.pk}
            assert not listing_model.objects.filter(pk=ada_council.pk).exists()
            role_model = apps.get_model(_APP, "LeadershipRole")
            assert list(role_model.objects.values_list("title", flat=True)) == ["Guild Executor"]
            # The one-card-per-member rule is back.
            with pytest.raises(IntegrityError), transaction.atomic():
                listing_model.objects.create(member=listing_model.objects.get(pk=ada_board.pk).member)
        finally:
            _migrate(_AFTER)

    def it_reverses_with_no_tabs_left_and_keeps_the_page_wording():
        try:
            apps = _migrate(_AFTER)
            apps.get_model(_APP, "LeadershipTab").objects.all().delete()
            apps.get_model(_APP, "LeadershipPage").objects.update_or_create(
                pk=1, defaults={"team_heading": "Kept Heading", "guilds_heading": "Kept Guilds"}
            )

            apps = _migrate(_BEFORE)
            page = apps.get_model(_APP, "LeadershipPage").objects.get(pk=1)
            assert (page.team_heading, page.guilds_heading) == ("Kept Heading", "Kept Guilds")
        finally:
            _migrate(_AFTER)
