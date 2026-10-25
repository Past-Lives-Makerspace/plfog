"""Factories for polls (#708)."""

from __future__ import annotations

from datetime import timedelta

import factory
from django.utils import timezone

from polls.models import Poll, PollChoice, PollVote
from tests.membership.factories import MemberFactory


class PollFactory(factory.django.DjangoModelFactory):
    """An open poll by default: opened an hour ago, closing in a week."""

    class Meta:
        model = Poll

    question = factory.Sequence(lambda n: f"Zorblax question {n}?")
    opens_at = factory.LazyFunction(lambda: timezone.now() - timedelta(hours=1))
    closes_at = factory.LazyAttribute(lambda o: o.opens_at + timedelta(days=7))


class PollChoiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PollChoice

    poll = factory.SubFactory(PollFactory)
    text = factory.Sequence(lambda n: f"Zorblax answer {n}")
    position = factory.Sequence(lambda n: n)


class PollVoteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PollVote

    choice = factory.SubFactory(PollChoiceFactory)
    poll = factory.LazyAttribute(lambda o: o.choice.poll)
    member = factory.SubFactory(MemberFactory)


def poll_with(*answers: str, votes: tuple[int, ...] = (), **kwargs: object) -> Poll:
    """A poll with these answers in order, and ``votes[i]`` votes on answer ``i``."""
    poll = PollFactory(**kwargs)
    for index, text in enumerate(answers):
        choice = PollChoiceFactory(poll=poll, text=text, position=index)
        for _ in range(votes[index] if index < len(votes) else 0):
            PollVoteFactory(choice=choice)
    return poll
