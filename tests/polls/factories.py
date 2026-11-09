"""Factories for polls (#708)."""

from __future__ import annotations

from datetime import datetime, timedelta

import factory
from django.utils import timezone

from membership.models import AnnouncementDraft
from polls.models import EmailPoll, Poll, PollChoice, PollVote, QuestionKind, QuestionSpec
from tests.membership.factories import AnnouncementDraftFactory, MemberFactory


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


class EmailPollFactory(factory.django.DjangoModelFactory):
    """A draft email poll (#1142) on a site wide Poll row, with no questions until given some."""

    class Meta:
        model = EmailPoll

    draft = factory.SubFactory(AnnouncementDraftFactory, kind=AnnouncementDraft.Kind.POLL)


def sent_email_poll(*specs: QuestionSpec, now: datetime, **kwargs: object) -> EmailPoll:
    """An email poll with these questions (one Pick one by default), opened at ``now``."""
    poll = EmailPollFactory(**kwargs)
    poll.replace_questions(list(specs) or [QuestionSpec("Which shop?", QuestionKind.PICK_ONE, ("Wood", "Metal"))])
    poll.open(now)
    return poll
