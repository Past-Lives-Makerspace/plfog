"""Polls (#708, back in #925): someone asks one question, members pick one answer, and it closes on a date.

A poll is site wide (``guild`` blank, posted by an admin, shown on Home) or a guild's (posted by
that guild's leads, staff, officers or an admin, shown on the guild's Polls tab). A poll is open
exactly while ``opens_at <= now < closes_at``, so it closes on its own and nothing has to run for
that; Close now moves ``closes_at`` to now. Only one poll is open at a time in each scope (site
wide, or one guild), which :meth:`Poll.post` keeps true (a time range cannot be a unique index):
posting while one is open there is refused. A member votes once per poll, held by a database constraint. Who voted
for what is never shown to anyone: pages read counts only. Nothing here notifies anyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import Guild, Member

MIN_CHOICES = 2
MAX_CHOICES = 6
MIN_DAYS = 1
MAX_DAYS = 60
DEFAULT_DAYS = 7
QUESTION_MAX_LENGTH = 200
ANSWER_MAX_LENGTH = 120


class PollAlreadyOpenError(Exception):
    """A poll is open, so another cannot be posted until it closes."""


class VoteRefusedError(Exception):
    """A vote that cannot count; the message is written for the member."""


class PollClosedError(VoteRefusedError):
    """The poll is not open: it has closed, or has not opened yet."""


class AlreadyVotedError(VoteRefusedError):
    """This member already voted in this poll; votes cannot change."""


class NotAVoterError(VoteRefusedError):
    """The account has no member, or is a guest (#691) or former member."""


@dataclass(frozen=True)
class ChoiceResult:
    """One answer's tally: its text, its votes and its share of the total, rounded."""

    pk: int
    text: str
    votes: int
    percent: int


class PollQuerySet(models.QuerySet["Poll"]):
    def open_at(self, now: datetime) -> PollQuerySet:
        """Polls open at ``now``: opened, and not yet at their closing time."""
        return self.filter(opens_at__lte=now, closes_at__gt=now)

    def closed_at(self, now: datetime) -> PollQuerySet:
        """Polls whose closing time has passed by ``now``."""
        return self.filter(closes_at__lte=now)

    def site_wide(self) -> PollQuerySet:
        """Polls asked of the whole makerspace: no guild."""
        return self.filter(guild__isnull=True)

    def for_guild(self, guild: Guild) -> PollQuerySet:
        """One guild's polls."""
        return self.filter(guild=guild)

    def open_poll(self, now: datetime) -> Poll | None:
        """The one poll open at ``now``, or None. One query."""
        return self.open_at(now).first()

    def with_totals(self) -> PollQuerySet:
        """Annotate ``total_votes`` and ``top_answer`` (the most chosen answer, blank with no votes).

        A tie goes to the answer listed first. Both are computed in the one query.
        """
        top = (
            PollChoice.objects.filter(poll=OuterRef("pk"))
            .annotate(vote_count=Count("votes"))
            .filter(vote_count__gt=0)
            .order_by("-vote_count", "position")
            .values("text")[:1]
        )
        return self.annotate(total_votes=Count("votes", distinct=True), top_answer=Subquery(top))


class Poll(models.Model):
    """One question with two to six answers, open for a set number of days."""

    question = models.CharField(max_length=QUESTION_MAX_LENGTH, help_text="What members are asked, in plain words.")
    opens_at = models.DateTimeField(help_text="When members can start voting.")
    closes_at = models.DateTimeField(help_text="When voting ends. Close now sets it to the moment the admin closed it.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The admin who posted the poll.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the poll was posted.")
    guild = models.ForeignKey(
        "membership.Guild",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="polls",
        help_text="The guild that asked, shown on its Polls tab. Blank for a site wide poll on Home.",
    )

    objects = PollQuerySet.as_manager()

    class Meta:
        ordering = ["-opens_at"]
        constraints = [
            models.CheckConstraint(condition=Q(closes_at__gte=models.F("opens_at")), name="poll_closes_after_opens"),
        ]
        indexes = [models.Index(fields=["closes_at", "opens_at"], name="poll_window_idx")]

    def __str__(self) -> str:
        return self.question

    @classmethod
    def post(
        cls, *, question: str, choices: list[str], days: int, by: User, now: datetime, guild: Guild | None = None
    ) -> Poll:
        """Open a new poll now, site wide or for ``guild``, unless one is already open there.

        A guild's polls never count against the site wide one, nor against another guild's.
        Posts are serialized on the site settings row (site wide) or the guild's row, so two
        people posting at once cannot both open one.

        Raises:
            PollAlreadyOpenError: A poll is open in that scope at ``now``; its question is the message.
            ValueError: The question is blank or too long, the answers are not 2 to 6 or one is
                too long, or days is not 1 to 60.
        """
        from core.models import SiteConfiguration
        from membership.models import Guild

        answers = [choice.strip() for choice in choices]
        if not question.strip():
            raise ValueError("A poll needs a question.")
        if len(question.strip()) > QUESTION_MAX_LENGTH:
            raise ValueError(f"A question is at most {QUESTION_MAX_LENGTH} characters.")
        if not MIN_CHOICES <= len(answers) <= MAX_CHOICES or not all(answers):
            raise ValueError(f"A poll needs {MIN_CHOICES} to {MAX_CHOICES} answers, none blank.")
        if any(len(answer) > ANSWER_MAX_LENGTH for answer in answers):
            raise ValueError(f"An answer is at most {ANSWER_MAX_LENGTH} characters.")
        if not MIN_DAYS <= days <= MAX_DAYS:
            raise ValueError(f"A poll runs {MIN_DAYS} to {MAX_DAYS} days.")
        with transaction.atomic():
            if guild is None:
                SiteConfiguration.objects.select_for_update().get_or_create(pk=1)
                scope = cls.objects.site_wide()
            else:
                Guild.objects.select_for_update().get(pk=guild.pk)
                scope = cls.objects.for_guild(guild)
            current = scope.open_poll(now)
            if current is not None:
                raise PollAlreadyOpenError(current.question)
            poll = cls.objects.create(
                question=question.strip(),
                opens_at=now,
                closes_at=now + timedelta(days=days),
                created_by=by,
                guild=guild,
            )
            PollChoice.objects.bulk_create(
                PollChoice(poll=poll, text=text, position=index) for index, text in enumerate(answers)
            )
        return poll

    @property
    def closes_label(self) -> str:
        """When voting ends, in Portland time: ``Tue, Oct 13 at 2:11 PM``."""
        local = timezone.localtime(self.closes_at)
        return f"{local:%a}, {local:%b} {local.day} at {local.hour % 12 or 12}:{local.minute:02d} {local:%p}"

    def is_open(self, now: datetime) -> bool:
        """Whether members can vote at ``now``."""
        return self.opens_at <= now < self.closes_at

    def close(self, now: datetime) -> None:
        """End voting at ``now`` (Close now). A poll already closed keeps its closing time."""
        if self.is_open(now):
            self.closes_at = now
            self.save(update_fields=["closes_at"])

    def results(self) -> list[ChoiceResult]:
        """Every answer in order with its votes and percent, in one query."""
        return tally(self.choices.annotate(vote_count=Count("votes")).order_by("position"))

    def vote(self, *, member: Member | None, choice_pk: int, now: datetime) -> PollVote:
        """Record ``member``'s one vote for the answer ``choice_pk``: the single way a vote is cast.

        Goes through ``PollVote.save`` (never a bulk insert), so the cross-poll guard runs, and
        turns the one-vote constraint's race into :class:`AlreadyVotedError`, not a 500.

        Raises:
            NotAVoterError: No member, a guest or a former member.
            PollClosedError: The poll is not open at ``now``.
            AlreadyVotedError: This member already voted here.
            PollChoice.DoesNotExist: ``choice_pk`` is not an answer to this poll.
        """
        if not can_vote(member):
            raise NotAVoterError("Polls are for members.")
        assert member is not None  # can_vote refused None
        if not self.is_open(now):
            raise PollClosedError("This poll has closed.")
        choice = self.choices.get(pk=choice_pk)
        try:
            with transaction.atomic():
                return PollVote.objects.create(poll=self, choice=choice, member=member)
        except IntegrityError as error:
            raise AlreadyVotedError("You already voted in this poll.") from error


class PollChoice(models.Model):
    """One answer to a poll, in the order the admin wrote them."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="choices", help_text="The poll this answers.")
    text = models.CharField(max_length=ANSWER_MAX_LENGTH, help_text="The answer as members see it.")
    position = models.PositiveSmallIntegerField(help_text="Its place in the list, from 0.")

    class Meta:
        ordering = ["poll", "position"]
        constraints = [models.UniqueConstraint(fields=["poll", "position"], name="pollchoice_one_per_position")]

    def __str__(self) -> str:
        return self.text


class PollVote(models.Model):
    """A member's one answer to one poll. Counted, never shown by name."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="votes", help_text="The poll voted in.")
    choice = models.ForeignKey(
        PollChoice, on_delete=models.CASCADE, related_name="votes", help_text="The answer chosen."
    )
    member = models.ForeignKey(
        "membership.Member", on_delete=models.CASCADE, related_name="poll_votes", help_text="Who voted."
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the vote was cast.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["poll", "member"], name="pollvote_one_per_member")]

    def __str__(self) -> str:
        return f"Vote in poll {self.poll_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Take the poll from the choice, refusing a choice from another poll.

        ``poll`` is kept beside ``choice`` because the one-vote-per-member constraint needs it,
        so the two must never disagree.

        Raises:
            ValueError: ``poll`` is set and is not the choice's poll.
        """
        if self.poll_id is None:
            self.poll_id = self.choice.poll_id
        elif self.poll_id != self.choice.poll_id:
            raise ValueError(f"Answer {self.choice_id} belongs to poll {self.choice.poll_id}, not poll {self.poll_id}.")
        super().save(*args, **kwargs)


def tally(choices: models.QuerySet[PollChoice] | list[PollChoice]) -> list[ChoiceResult]:
    """Turn choices annotated with ``vote_count`` into results whose percents use the poll's total."""
    rows = list(choices)
    total = sum(row.vote_count for row in rows)  # type: ignore[attr-defined]
    return [
        ChoiceResult(
            pk=row.pk,
            text=row.text,
            votes=row.vote_count,  # type: ignore[attr-defined]
            percent=round(100 * row.vote_count / total) if total else 0,  # type: ignore[attr-defined]
        )
        for row in rows
    ]


def can_vote(member: Member | None) -> bool:
    """Whether this account may vote: the Spotlight's audience (``hub.spotlight.sees_spotlight``).

    A member record that is not a guest (#691) or a former member. One rule, kept in one place.
    """
    from hub.spotlight import sees_spotlight

    return sees_spotlight(member)


@dataclass(frozen=True)
class PollCard:
    """One poll as one member sees it: the tally, their own answer, and whether to offer choices.

    Built for a page of polls at once by :meth:`for_polls`, so a page costs two queries however
    many polls it shows. The same card renders on Home, a guild's Polls tab and Past Polls.
    """

    poll: Poll
    results: list[ChoiceResult]
    my_choice_pk: int | None
    is_open: bool
    can_vote: bool

    @property
    def total_votes(self) -> int:
        """Votes cast in this poll."""
        return sum(result.votes for result in self.results)

    @property
    def shows_choices(self) -> bool:
        """Offer the answers as buttons: open, a voter, and not voted yet. Otherwise show results."""
        return self.is_open and self.can_vote and self.my_choice_pk is None

    @classmethod
    def for_polls(cls, polls: list[Poll], member: Member | None, now: datetime) -> list[PollCard]:
        """Cards for ``polls`` in their order: one query for every tally, one for this member's votes."""
        ids = [poll.pk for poll in polls]
        rows = (
            PollChoice.objects.filter(poll_id__in=ids).annotate(vote_count=Count("votes")).order_by("poll", "position")
        )
        by_poll: dict[int, list[PollChoice]] = {pk: [] for pk in ids}
        for row in rows:
            by_poll[row.poll_id].append(row)
        mine: dict[int, int] = {}
        if member is not None:
            mine = dict(PollVote.objects.filter(member=member, poll_id__in=ids).values_list("poll_id", "choice_id"))
        voter = can_vote(member)
        return [
            cls(
                poll=poll,
                results=tally(by_poll[poll.pk]),
                my_choice_pk=mine.get(poll.pk),
                is_open=poll.is_open(now),
                can_vote=voter,
            )
            for poll in polls
        ]

    @classmethod
    def for_poll(cls, poll: Poll, member: Member | None, now: datetime) -> PollCard:
        """One poll's card."""
        return cls.for_polls([poll], member, now)[0]

    @classmethod
    def open_for(cls, member: Member | None, now: datetime) -> PollCard | None:
        """The open site wide poll's card for Home, or None (one query, three with one open)."""
        poll = Poll.objects.site_wide().open_poll(now)
        return None if poll is None else cls.for_poll(poll, member, now)


@dataclass(frozen=True)
class GuildPolls:
    """A guild's Polls tab (#925 part 2): its open poll on top, its closed ones below, newest first."""

    open: PollCard | None
    past: list[PollCard]

    def __bool__(self) -> bool:
        """Whether the guild has any poll at all."""
        return self.open is not None or bool(self.past)

    @classmethod
    def load(cls, guild: Guild, member: Member | None, now: datetime) -> GuildPolls:
        """The tab for ``member``: one query for the polls, two for their cards (none with no polls)."""
        polls = list(Poll.objects.for_guild(guild))
        cards = PollCard.for_polls(polls, member, now) if polls else []
        open_cards = [card for card in cards if card.is_open]
        return cls(open=open_cards[0] if open_cards else None, past=[card for card in cards if not card.is_open])
