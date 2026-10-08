"""Polls (#708): the open window, Close now, posting, one open poll, one vote each, the tally."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext

from polls.models import ChoiceResult, Poll, PollAlreadyOpenError, PollVote, open_poll_with_results
from tests.membership.factories import MemberFactory
from tests.polls.factories import PollChoiceFactory, PollFactory, PollVoteFactory, poll_with

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=PORTLAND)


def _admin() -> User:
    user, _created = User.objects.get_or_create(username="polladmin", defaults={"email": "polladmin@example.com"})
    return user


def _post(now: datetime = NOW, close_current: bool = False, **overrides: object) -> Poll:
    fields: dict = {"question": "Zorblax next?", "choices": ["Laser", "Lathe"], "days": 7}
    fields.update(overrides)
    return Poll.post(by=_admin(), now=now, close_current=close_current, **fields)


def describe_the_open_window():
    def it_is_open_from_the_moment_it_opens():
        poll = PollFactory(opens_at=NOW, closes_at=NOW + timedelta(days=7))

        assert poll.is_open(NOW) is True
        assert list(Poll.objects.open_at(NOW)) == [poll]

    def it_is_not_open_a_moment_before_it_opens():
        poll = PollFactory(opens_at=NOW, closes_at=NOW + timedelta(days=7))

        assert poll.is_open(NOW - timedelta(seconds=1)) is False

    def it_is_open_until_just_before_it_closes():
        poll = PollFactory(opens_at=NOW, closes_at=NOW + timedelta(days=7))

        assert poll.is_open(NOW + timedelta(days=7) - timedelta(seconds=1)) is True

    def it_is_closed_at_its_closing_time_with_nothing_run():
        poll = PollFactory(opens_at=NOW, closes_at=NOW + timedelta(days=7))
        closing = NOW + timedelta(days=7)

        assert poll.is_open(closing) is False
        assert list(Poll.objects.open_at(closing)) == []
        assert list(Poll.objects.closed_at(closing)) == [poll]


def describe_close():
    def it_ends_voting_now():
        poll = PollFactory(opens_at=NOW - timedelta(days=1), closes_at=NOW + timedelta(days=6))

        poll.close(NOW)

        poll.refresh_from_db()
        assert poll.closes_at == NOW
        assert poll.is_open(NOW) is False

    def it_keeps_a_closed_polls_closing_time():
        ended = NOW - timedelta(days=2)
        poll = PollFactory(opens_at=NOW - timedelta(days=9), closes_at=ended)

        poll.close(NOW)

        poll.refresh_from_db()
        assert poll.closes_at == ended


def describe_post():
    def it_opens_now_and_closes_after_the_days():
        poll = _post(days=3)

        assert poll.opens_at == NOW
        assert poll.closes_at == NOW + timedelta(days=3)
        assert [choice.text for choice in poll.choices.all()] == ["Laser", "Lathe"]
        assert poll.created_by == User.objects.get()

    def it_trims_the_question_and_answers():
        poll = _post(question="  Zorblax?  ", choices=["  Laser ", "Lathe  "])

        assert poll.question == "Zorblax?"
        assert [choice.text for choice in poll.choices.all()] == ["Laser", "Lathe"]

    def it_refuses_while_a_poll_is_open():
        first = _post()

        with pytest.raises(PollAlreadyOpenError):
            _post(now=NOW + timedelta(hours=1), question="Zorblax again?")

        assert list(Poll.objects.open_at(NOW + timedelta(hours=1))) == [first]

    def it_closes_the_open_poll_first_when_asked():
        first = _post()
        later = NOW + timedelta(hours=1)

        second = _post(now=later, close_current=True, question="Zorblax again?")

        first.refresh_from_db()
        assert first.closes_at == later
        assert list(Poll.objects.open_at(later)) == [second]

    def it_posts_freely_once_the_last_one_expired():
        _post(days=1)
        later = NOW + timedelta(days=2)

        assert _post(now=later, question="Zorblax again?").is_open(later)

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"question": "   "}, "question"),
            ({"choices": ["Only one"]}, "2 to 6"),
            ({"choices": [f"Answer {n}" for n in range(7)]}, "2 to 6"),
            ({"choices": ["Laser", "  "]}, "none blank"),
            ({"days": 0}, "1 to 60"),
            ({"days": 61}, "1 to 60"),
            ({"question": "Z" * 201}, "at most 200 characters"),
            ({"choices": ["Laser", "L" * 121]}, "at most 120 characters"),
        ],
    )
    def it_refuses_a_poll_out_of_bounds(overrides: dict, message: str):
        with pytest.raises(ValueError, match=message):
            _post(**overrides)

        assert not Poll.objects.exists()


def describe_post_lengths():
    def it_accepts_answers_and_a_question_at_their_limits():
        poll = _post(question="Z" * 200, choices=["L" * 120, "Lathe"])

        assert len(poll.question) == 200
        assert len(poll.choices.first().text) == 120


def describe_votes():
    def it_allows_one_vote_per_member_per_poll():
        poll = poll_with("Laser", "Lathe")
        member = MemberFactory()
        laser, lathe = poll.choices.all()
        PollVoteFactory(choice=laser, member=member)

        with pytest.raises(IntegrityError), transaction.atomic():
            PollVoteFactory(choice=lathe, member=member)

    def it_takes_the_poll_from_the_choice():
        choice = PollChoiceFactory()

        vote = PollVote(choice=choice, member=MemberFactory())
        vote.save()

        assert vote.poll == choice.poll

    def it_refuses_a_choice_from_another_poll():
        choice = PollChoiceFactory()
        other_poll = PollFactory(question="Zorblax elsewhere?")

        with pytest.raises(ValueError, match="belongs to poll"):
            PollVote(poll=other_poll, choice=choice, member=MemberFactory()).save()

        assert not PollVote.objects.exists()

    def it_lets_the_same_member_vote_in_another_poll():
        member = MemberFactory()
        PollVoteFactory(choice=PollChoiceFactory(), member=member)

        PollVoteFactory(choice=PollChoiceFactory(), member=member)

        assert member.poll_votes.count() == 2


def describe_results():
    def it_counts_each_answer_with_its_percent_in_order():
        poll = poll_with("Laser", "Lathe", "Kiln", votes=(2, 1, 0))

        assert poll.results() == [
            ChoiceResult(pk=poll.choices.get(text="Laser").pk, text="Laser", votes=2, percent=67),
            ChoiceResult(pk=poll.choices.get(text="Lathe").pk, text="Lathe", votes=1, percent=33),
            ChoiceResult(pk=poll.choices.get(text="Kiln").pk, text="Kiln", votes=0, percent=0),
        ]

    def it_shows_zero_percent_with_no_votes():
        assert [result.percent for result in poll_with("Laser", "Lathe").results()] == [0, 0]


def describe_with_totals():
    def it_adds_the_total_and_the_top_answer():
        poll_with("Laser", "Lathe", votes=(1, 3))

        poll = Poll.objects.with_totals().get()

        assert poll.total_votes == 4
        assert poll.top_answer == "Lathe"

    def it_breaks_a_tie_toward_the_first_answer():
        poll_with("Laser", "Lathe", votes=(2, 2))

        assert Poll.objects.with_totals().get().top_answer == "Laser"

    def it_has_no_top_answer_without_votes():
        poll_with("Laser", "Lathe")

        poll = Poll.objects.with_totals().get()
        assert poll.total_votes == 0
        assert poll.top_answer is None


def describe_open_poll_with_results():
    def it_reads_the_open_poll_and_its_tally_in_one_query():
        poll = poll_with("Laser", "Lathe", votes=(1, 0))
        poll_with("Old", "Older", opens_at=NOW - timedelta(days=30), closes_at=NOW - timedelta(days=20))

        with CaptureQueriesContext(connection) as queries:
            found, results = open_poll_with_results(poll.opens_at + timedelta(minutes=1))

        assert len(queries) == 1
        assert found == poll
        assert [(result.text, result.votes) for result in results] == [("Laser", 1), ("Lathe", 0)]

    def it_finds_nothing_with_no_open_poll():
        poll_with("Old", "Older", opens_at=NOW - timedelta(days=30), closes_at=NOW - timedelta(days=20))

        assert open_poll_with_results(NOW) == (None, [])
