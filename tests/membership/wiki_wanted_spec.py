"""Specs for the wanted-pages model: the ask, the dedupe, the claim, and closing a row."""

from __future__ import annotations

from datetime import timedelta

from unittest import mock

import pytest
from django.utils import timezone

from membership.models import (
    WANTED_MAX_PER_MEMBER_PER_DAY,
    WikiError,
    WikiWantedPage,
    WikiWantedPageQuerySet,
)
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

    def it_recovers_from_a_lost_race_inside_a_transaction(db):
        """The create is wrapped in its own savepoint. Without it, Postgres marks the
        transaction needs_rollback on the failed INSERT and the recovery SELECT raises
        TransactionManagementError instead of returning the winner -- and every test using
        the db fixture runs inside exactly such a block, which is what made this branch
        untestable before."""
        guild = GuildFactory()
        winner = WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        # Blind the FIRST lookup only, which is what losing the race looks like: the
        # pre-check misses, the INSERT hits the constraint, and the recovery lookup still
        # has to be able to read. Patched on the QUERYSET class, because the manager method
        # calls QuerySet.first and not the manager's own.
        calls = {"n": 0}
        real_first = WikiWantedPageQuerySet.first

        def blind_once(self):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_first(self)

        with mock.patch.object(WikiWantedPageQuerySet, "first", blind_once):
            row, created = WikiWantedPage.objects.request(title="Sharpening jigs", guild=guild, member=MemberFactory())
        assert calls["n"] >= 2, "the create never raised, so the recovery branch was not exercised"
        assert created is False
        assert row.pk == winner.pk
        assert WikiWantedPage.objects.count() == 1

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

    def describe_on_a_row_somebody_else_holds():
        def it_refuses_to_steal_it(db):
            """The template never offers Claim on a claimed row; the model used to allow
            it anyway, so a crafted POST took somebody's work off them."""
            holder = MemberFactory()
            row = WikiWantedPageFactory(claimed_by=holder, claimed_at=timezone.now())
            with pytest.raises(ValueError):
                row.claim(MemberFactory())
            row.refresh_from_db()
            assert row.claimed_by_id == holder.pk

        def it_lets_the_holder_re_claim_their_own(db):
            holder = MemberFactory()
            row = WikiWantedPageFactory(claimed_by=holder, claimed_at=timezone.now())
            row.claim(holder)
            row.refresh_from_db()
            assert row.claimed_by_id == holder.pk


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

    def describe_with_a_page_from_another_scope():
        def it_answers_False_and_leaves_the_row_open(db):
            """Spec A's ?wanted=<pk> path takes the pk straight off the query string with
            no scope check, so a member could read one guild's pk out of a Start This Page
            href and close its row against an unrelated space-wide page. Refused here, so
            the guard covers every caller, and silently, so A's create view never becomes
            an error screen."""
            row = WikiWantedPageFactory(guild=GuildFactory())
            assert row.fulfil(WikiPageFactory(guild=None)) is False
            row.refresh_from_db()
            assert row.fulfilled_page_id is None

    def describe_with_an_archived_page():
        def it_raises(db):
            row = WikiWantedPageFactory(guild=None)
            with pytest.raises(WikiError):
                row.fulfil(WikiPageFactory(guild=None, archived=True))


def describe_reopen():
    def it_puts_a_wrongly_closed_row_back_with_its_count_intact(db):
        """Delete was the only exit, and it threw away the ask and how many people asked."""
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        WikiWantedPage.objects.filter(pk=row.pk).update(request_count=4)
        row.refresh_from_db()
        row.fulfil(WikiPageFactory(guild=guild))
        row.reopen()
        row.refresh_from_db()
        assert row.state == "open"
        assert row.request_count == 4
        assert row.title == "Sharpening jigs"
        assert row.claimed_by_id is None

    def it_refuses_a_row_that_is_already_open(db):
        """A lead POSTing reopen at an open row used to silently clear a live claim,
        walking past the Release confirm modal and the line naming whose claim it is."""
        holder = MemberFactory()
        row = WikiWantedPageFactory(claimed_by=holder, claimed_at=timezone.now())
        with pytest.raises(ValueError):
            row.reopen()
        row.refresh_from_db()
        assert row.claimed_by_id == holder.pk

    def it_refuses_when_the_same_ask_has_been_filed_again(db):
        """fulfilled_page = None moves the row INTO the partial unique index, so a
        duplicate opened while it sat closed would surface as an uncaught IntegrityError."""
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        row.fulfil(WikiPageFactory(guild=guild))
        WikiWantedPageFactory(guild=guild, title="sharpening JIGS")
        with pytest.raises(ValueError):
            row.reopen()
        row.refresh_from_db()
        assert row.state == "done"


def describe_the_daily_caps():
    def it_refuses_a_member_opening_too_many_rows_in_one_day(db):
        """Nothing capped DISTINCT asks, so a script wrote one row per request."""
        member = MemberFactory()
        for index in range(WANTED_MAX_PER_MEMBER_PER_DAY):
            WikiWantedPage.objects.request(title=f"Ask number {index}", guild=None, member=member)
        with pytest.raises(WikiError):
            WikiWantedPage.objects.request(title="One too many", guild=None, member=member)

    def it_still_lets_them_bump_an_existing_row(db):
        """The cap is on creation; joining an ask somebody already filed is not creation."""
        member = MemberFactory()
        WikiWantedPage.objects.request(title="Popular ask", guild=None, member=MemberFactory())
        for index in range(WANTED_MAX_PER_MEMBER_PER_DAY):
            WikiWantedPage.objects.request(title=f"Ask number {index}", guild=None, member=member)
        row, created = WikiWantedPage.objects.request(title="Popular ask", guild=None, member=member)
        assert created is False
        assert row.request_count == 2


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
