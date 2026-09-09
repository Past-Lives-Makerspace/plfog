"""Specs for the failed-search log: what gets written, what does not, and what is purged."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from membership.models import WikiSearchMiss
from membership.wiki_guild import MISS_PANEL_DAYS, purge_old_search_misses
from tests.membership.factories import GuildFactory, MemberFactory, WikiSearchMissFactory

pytestmark = pytest.mark.django_db


def _age(row: WikiSearchMiss, *, days: int) -> None:
    """Backdate a row past ``auto_now_add``."""
    WikiSearchMiss.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=days))


def describe_record():
    def it_writes_one_row(db):
        guild = GuildFactory()
        member = MemberFactory()
        row = WikiSearchMiss.objects.record(query="Epoxy Cure Time", guild=guild, member=member)
        assert row is not None
        assert row.query == "Epoxy Cure Time"
        assert row.query_normalized == "epoxy cure time"
        assert row.guild_id == guild.pk

    def it_declines_an_anonymous_search(db):
        assert WikiSearchMiss.objects.record(query="epoxy", guild=None, member=None) is None
        assert WikiSearchMiss.objects.count() == 0

    def it_declines_a_two_character_query(db):
        assert WikiSearchMiss.objects.record(query="ep", guild=None, member=MemberFactory()) is None

    def it_declines_a_pasted_wall_of_text(db):
        assert WikiSearchMiss.objects.record(query="x" * 200, guild=None, member=MemberFactory()) is None

    def describe_the_same_member_searching_the_same_thing_twice_in_a_day():
        def it_writes_one_row(db):
            member = MemberFactory()
            WikiSearchMiss.objects.record(query="epoxy cure time", guild=None, member=member)
            second = WikiSearchMiss.objects.record(query="  Epoxy   Cure  Time ", guild=None, member=member)
            assert second is None
            assert WikiSearchMiss.objects.count() == 1

        def it_still_counts_a_different_member(db):
            WikiSearchMiss.objects.record(query="epoxy cure time", guild=None, member=MemberFactory())
            WikiSearchMiss.objects.record(query="epoxy cure time", guild=None, member=MemberFactory())
            assert WikiSearchMiss.objects.count() == 2


def describe_top_for_guild():
    def it_groups_case_insensitively_and_counts_people(db):
        guild = GuildFactory()
        WikiSearchMissFactory(guild=guild, query="Epoxy cure time")
        WikiSearchMissFactory(guild=guild, query="epoxy CURE time")
        WikiSearchMissFactory(guild=guild, query="bandsaw blade")
        rows = WikiSearchMiss.objects.top_for_guild(guild, since=timezone.now() - timedelta(days=30))
        assert [row["people"] for row in rows] == [2, 1]
        assert rows[0]["query_normalized"] == "epoxy cure time"

    def it_ignores_another_guilds_misses(db):
        guild = GuildFactory()
        WikiSearchMissFactory(guild=GuildFactory(), query="somebody else's problem")
        assert WikiSearchMiss.objects.top_for_guild(guild, since=timezone.now() - timedelta(days=30)) == []

    def it_honours_the_limit(db):
        guild = GuildFactory()
        for index in range(4):
            WikiSearchMissFactory(guild=guild, query=f"thing {index}")
        rows = WikiSearchMiss.objects.top_for_guild(guild, since=timezone.now() - timedelta(days=30), limit=2)
        assert len(rows) == 2

    def it_honours_an_upper_bound(db):
        """The digest reports a month; a --force run mid-month must not pull this month in."""
        guild = GuildFactory()
        inside = WikiSearchMissFactory(guild=guild, query="last month")
        _age(inside, days=40)
        WikiSearchMissFactory(guild=guild, query="this month")
        rows = WikiSearchMiss.objects.top_for_guild(
            guild,
            since=timezone.now() - timedelta(days=60),
            until=timezone.now() - timedelta(days=20),
        )
        assert [row["query"] for row in rows] == ["last month"]

    def describe_the_panels_rolling_window():
        def it_still_shows_a_miss_from_three_days_ago_on_the_first_of_a_month(db):
            """A calendar-month window shows a false all-clear on the 1st — the exact day
            the digest lands and a lead opens the panel."""
            guild = GuildFactory()
            row = WikiSearchMissFactory(guild=guild, query="epoxy cure time")
            _age(row, days=3)
            rows = WikiSearchMiss.objects.top_for_guild(guild, since=timezone.now() - timedelta(days=MISS_PANEL_DAYS))
            assert [row["query"] for row in rows] == ["epoxy cure time"]


def describe_purge_old_search_misses():
    def it_deletes_rows_past_the_retention_window_and_keeps_the_rest(db):
        guild = GuildFactory()
        old = WikiSearchMissFactory(guild=guild, query="ancient history")
        _age(old, days=120)
        WikiSearchMissFactory(guild=guild, query="recent history")
        assert purge_old_search_misses() == 1
        assert [row.query for row in WikiSearchMiss.objects.all()] == ["recent history"]


def describe_str():
    def it_says_what_found_nothing(db):
        row = WikiSearchMissFactory(query="epoxy cure time")
        assert str(row).startswith("'epoxy cure time' found nothing (")
