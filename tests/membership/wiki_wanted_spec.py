"""Specs for the wanted-pages model: the ask, the dedupe, the claim, and closing a row."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from membership.models import WikiError, WikiWantedPage
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    WikiPageFactory,
    WikiWantedPageFactory,
)

pytestmark = pytest.mark.django_db


def describe_request():
    def it_creates_the_first_ask(db):
        guild = GuildFactory()
        member = MemberFactory()
        row, created = WikiWantedPage.objects.request(title="Sharpening jigs", guild=guild, member=member)
        assert created is True
        assert row.request_count == 1
        assert row.created_by_id == member.pk

    def it_bumps_the_count_on_a_second_identical_ask(db):
        guild = GuildFactory()
        WikiWantedPage.objects.request(title="Sharpening jigs", guild=guild, member=MemberFactory())
        row, created = WikiWantedPage.objects.request(title="Sharpening jigs", guild=guild, member=MemberFactory())
        assert created is False
        assert row.request_count == 2
        assert WikiWantedPage.objects.count() == 1

    def it_matches_across_case_and_extra_whitespace(db):
        guild = GuildFactory()
        WikiWantedPage.objects.request(title="Sharpening Jigs", guild=guild, member=MemberFactory())
        row, created = WikiWantedPage.objects.request(title="  sharpening   jigs ", guild=guild, member=MemberFactory())
        assert created is False
        assert row.request_count == 2

    def describe_with_bump_false():
        def it_folds_into_the_row_without_counting_a_person(db):
            """A lead filing off the failed-search panel is not a fourth person asking, and
            the panel's headline number is supposed to count people."""
            guild = GuildFactory()
            WikiWantedPage.objects.request(title="Epoxy cure time", guild=guild, member=MemberFactory())
            row, created = WikiWantedPage.objects.request(
                title="Epoxy cure time", guild=guild, member=MemberFactory(), bump=False
            )
            assert created is False
            assert row.request_count == 1

    def it_scopes_the_dedupe_to_the_guild(db):
        first, second = GuildFactory(), GuildFactory()
        WikiWantedPage.objects.request(title="Finishing walnut", guild=first, member=MemberFactory())
        _row, created = WikiWantedPage.objects.request(title="Finishing walnut", guild=second, member=MemberFactory())
        assert created is True

    def it_dedupes_space_wide_rows_too(db):
        """The partial unique constraint cannot: Postgres NULLs never collide, so the
        manager's own lookup is the real dedupe on every path."""
        WikiWantedPage.objects.request(title="Where the keys live", guild=None, member=MemberFactory())
        _row, created = WikiWantedPage.objects.request(title="Where the keys live", guild=None, member=MemberFactory())
        assert created is False

    def describe_when_an_identical_row_is_already_written():
        def it_opens_a_fresh_ask(db):
            guild = GuildFactory()
            done = WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
            done.fulfil(WikiPageFactory(guild=guild))
            _row, created = WikiWantedPage.objects.request(title="Sharpening jigs", guild=guild, member=MemberFactory())
            assert created is True


def describe_open_titles_for_guild():
    def it_returns_only_the_open_rows_normalized_titles(db):
        guild = GuildFactory()
        WikiWantedPageFactory(guild=guild, title="Sharpening Jigs")
        closed = WikiWantedPageFactory(guild=guild, title="Table saw fence")
        closed.fulfil(WikiPageFactory(guild=guild))
        WikiWantedPageFactory(guild=GuildFactory(), title="Other guild ask")
        assert WikiWantedPage.objects.open_titles_for_guild(guild) == {"sharpening jigs"}


def describe_claim():
    def it_records_who_and_when(db):
        row = WikiWantedPageFactory()
        member = MemberFactory()
        row.claim(member)
        row.refresh_from_db()
        assert row.claimed_by_id == member.pk
        assert row.claimed_at is not None
        assert row.state == "claimed"

    def describe_on_a_written_row():
        def it_raises(db):
            row = WikiWantedPageFactory()
            row.fulfil(WikiPageFactory())
            with pytest.raises(ValueError):
                row.claim(MemberFactory())


def describe_release():
    def it_clears_the_claim_and_leaves_the_row_open(db):
        row = WikiWantedPageFactory(stale_claim=True)
        row.release()
        row.refresh_from_db()
        assert row.claimed_by_id is None
        assert row.claimed_at is None
        assert row.state == "open"

    def describe_on_a_written_row():
        def it_raises(db):
            row = WikiWantedPageFactory()
            row.fulfil(WikiPageFactory())
            with pytest.raises(ValueError):
                row.release()


def describe_fulfil():
    def it_links_the_page_and_closes_the_row(db):
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild)
        page = WikiPageFactory(guild=guild)
        assert row.fulfil(page) is True
        row.refresh_from_db()
        assert row.fulfilled_page_id == page.pk
        assert row.state == "done"

    def it_credits_the_author_when_nobody_had_claimed_it(db):
        """So the Already Written section reads "written by Sam" with no ceremony."""
        author = MemberFactory()
        row = WikiWantedPageFactory()
        row.fulfil(WikiPageFactory(created_by=author))
        row.refresh_from_db()
        assert row.claimed_by_id == author.pk

    def it_keeps_an_existing_claimer(db):
        claimer = MemberFactory()
        row = WikiWantedPageFactory(claimed_by=claimer, claimed_at=timezone.now())
        row.fulfil(WikiPageFactory(created_by=MemberFactory()))
        row.refresh_from_db()
        assert row.claimed_by_id == claimer.pk

    def describe_on_an_already_written_row():
        def it_answers_False_rather_than_raising(db):
            """Spec A's create view follows a possibly month-old ?wanted=<pk> link and must
            never turn a closed row into an error screen; B's view turns the False into a
            400 with the "already closed" toast."""
            row = WikiWantedPageFactory()
            first = WikiPageFactory()
            assert row.fulfil(first) is True
            assert row.fulfil(WikiPageFactory()) is False
            row.refresh_from_db()
            assert row.fulfilled_page_id == first.pk

    def describe_with_an_archived_page():
        def it_raises(db):
            row = WikiWantedPageFactory()
            with pytest.raises(WikiError):
                row.fulfil(WikiPageFactory(archived=True))


def describe_is_claim_stale():
    def it_is_false_for_a_fresh_claim(db):
        row = WikiWantedPageFactory(claimed_by=MemberFactory(), claimed_at=timezone.now())
        assert row.is_claim_stale is False

    def it_is_false_with_no_claim(db):
        assert WikiWantedPageFactory().is_claim_stale is False

    def it_flips_past_thirty_days(db):
        row = WikiWantedPageFactory(claimed_by=MemberFactory(), claimed_at=timezone.now() - timedelta(days=31))
        assert row.is_claim_stale is True

    def it_is_false_once_the_page_exists(db):
        row = WikiWantedPageFactory(stale_claim=True)
        row.fulfil(WikiPageFactory())
        assert row.is_claim_stale is False


def describe_str():
    def it_names_the_scope(db):
        guild = GuildFactory(name="Woodworking")
        assert str(WikiWantedPageFactory(guild=guild, title="Jigs")) == "Jigs (Woodworking)"
        assert str(WikiWantedPageFactory(guild=None, title="Keys")) == "Keys (Space-wide)"
