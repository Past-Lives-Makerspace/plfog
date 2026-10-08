"""Casting a vote (#708 part 2): Poll.vote, who may vote, and the per-member PollCard."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from membership.models import Member
from polls.models import (
    AlreadyVotedError,
    NotAVoterError,
    PollCard,
    PollChoice,
    PollClosedError,
    PollVote,
    can_vote,
)
from tests.membership.factories import MemberFactory
from tests.polls.factories import poll_with

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=PORTLAND)


def _open_poll(*answers: str, votes: tuple[int, ...] = ()):
    return poll_with(
        *(answers or ("Laser", "Lathe")),
        votes=votes,
        opens_at=NOW - timedelta(hours=1),
        closes_at=NOW + timedelta(days=6),
    )


def describe_vote():
    def it_records_one_vote_for_the_answer():
        poll = _open_poll()
        member = MemberFactory()
        lathe = poll.choices.get(text="Lathe")

        vote = poll.vote(member=member, choice_pk=lathe.pk, now=NOW)

        assert (vote.poll, vote.choice, vote.member) == (poll, lathe, member)
        assert PollVote.objects.count() == 1

    def it_refuses_a_second_vote_without_changing_the_first():
        poll = _open_poll()
        member = MemberFactory()
        laser, lathe = poll.choices.all()
        poll.vote(member=member, choice_pk=laser.pk, now=NOW)

        with pytest.raises(AlreadyVotedError, match="already voted"):
            poll.vote(member=member, choice_pk=lathe.pk, now=NOW)

        assert list(PollVote.objects.values_list("choice_id", flat=True)) == [laser.pk]

    def it_turns_the_unique_constraint_race_into_already_voted():
        # No read-then-write check exists to race past: the second insert hits the constraint.
        poll = _open_poll()
        member = MemberFactory()
        laser = poll.choices.first()
        PollVote.objects.create(poll=poll, choice=laser, member=member)

        with pytest.raises(AlreadyVotedError):
            poll.vote(member=member, choice_pk=laser.pk, now=NOW)

    def it_refuses_once_the_poll_has_closed():
        poll = _open_poll()

        with pytest.raises(PollClosedError):
            poll.vote(member=MemberFactory(), choice_pk=poll.choices.first().pk, now=NOW + timedelta(days=6))

        assert not PollVote.objects.exists()

    def it_refuses_before_the_poll_opens():
        poll = _open_poll()

        with pytest.raises(PollClosedError):
            poll.vote(member=MemberFactory(), choice_pk=poll.choices.first().pk, now=NOW - timedelta(hours=2))

    def it_refuses_an_answer_from_another_poll():
        poll = _open_poll()
        other = _open_poll("Kiln", "Wheel")

        with pytest.raises(PollChoice.DoesNotExist):
            poll.vote(member=MemberFactory(), choice_pk=other.choices.first().pk, now=NOW)

    @pytest.mark.parametrize("status", [Member.Status.GUEST, Member.Status.FORMER])
    def it_refuses_guests_and_former_members(status: str):
        poll = _open_poll()

        with pytest.raises(NotAVoterError):
            poll.vote(member=MemberFactory(status=status), choice_pk=poll.choices.first().pk, now=NOW)

    def it_refuses_an_account_with_no_member():
        poll = _open_poll()

        with pytest.raises(NotAVoterError):
            poll.vote(member=None, choice_pk=poll.choices.first().pk, now=NOW)


def describe_closes_label():
    def it_reads_the_closing_time_in_portland():
        poll = poll_with("Laser", "Lathe", opens_at=NOW, closes_at=datetime(2026, 10, 13, 14, 11, tzinfo=PORTLAND))

        assert poll.closes_label == "Tue, Oct 13 at 2:11 PM"

    def it_converts_a_utc_closing_time_to_portland():
        utc_close = datetime(2026, 10, 14, 1, 5, tzinfo=ZoneInfo("UTC"))  # 6:05 PM Tuesday in Portland
        poll = poll_with("Laser", "Lathe", opens_at=NOW, closes_at=utc_close)

        assert poll.closes_label == "Tue, Oct 13 at 6:05 PM"


def describe_can_vote():
    @pytest.mark.parametrize(
        ("status", "allowed"),
        [
            (Member.Status.ACTIVE, True),
            (Member.Status.SUSPENDED, True),
            (Member.Status.GUEST, False),
            (Member.Status.FORMER, False),
        ],
    )
    def it_lets_members_vote_but_not_guests_or_former_members(status: str, allowed: bool):
        assert can_vote(MemberFactory(status=status)) is allowed


def describe_PollCard():
    def it_offers_the_answers_on_an_open_poll_not_yet_voted():
        card = PollCard.for_poll(_open_poll(), MemberFactory(), NOW)

        assert card.shows_choices is True
        assert card.my_choice_pk is None

    def it_shows_results_with_the_members_answer_once_voted():
        poll = _open_poll(votes=(2, 0))
        member = MemberFactory()
        lathe = poll.choices.get(text="Lathe")
        poll.vote(member=member, choice_pk=lathe.pk, now=NOW)

        card = PollCard.for_poll(poll, member, NOW)

        assert card.shows_choices is False
        assert card.my_choice_pk == lathe.pk
        assert card.total_votes == 3

    def it_shows_results_on_a_closed_poll_to_someone_who_never_voted():
        poll = _open_poll()

        card = PollCard.for_poll(poll, MemberFactory(), NOW + timedelta(days=7))

        assert card.shows_choices is False
        assert card.is_open is False

    def it_never_offers_answers_to_a_guest():
        assert PollCard.for_poll(_open_poll(), MemberFactory(status=Member.Status.GUEST), NOW).shows_choices is False

    def it_builds_a_page_of_cards_in_two_queries():
        polls = [_open_poll(votes=(1, 1))] + [
            poll_with(
                "A", "B", votes=(1, 0), opens_at=NOW - timedelta(days=30 + n), closes_at=NOW - timedelta(days=20 + n)
            )
            for n in range(5)
        ]
        member = MemberFactory()

        with CaptureQueriesContext(connection) as queries:
            cards = PollCard.for_polls(polls, member, NOW)

        assert len(queries) == 2
        assert [card.poll for card in cards] == polls
        assert [card.total_votes for card in cards] == [2, 1, 1, 1, 1, 1]
