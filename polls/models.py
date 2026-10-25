"""Polls (#708, back in #925): someone asks one question, members pick one answer, and it closes on a date.

A poll is site wide (``guild`` blank, posted by an admin, shown on Home) or a guild's (posted by
that guild's leads, staff, officers or an admin, shown on the guild's Polls tab). A poll is open
exactly while ``opens_at <= now < closes_at``, so it closes on its own and nothing has to run for
that; Close now moves ``closes_at`` to now. Only one poll is open at a time in each scope (site
wide, or one guild), which :meth:`Poll.post` keeps true (a time range cannot be a unique index):
posting while one is open there is refused. A member votes once per poll, held by a database
constraint: by picking an answer, or (#925 part 3, where the poll allows it) by writing their own.
A picked answer is a count and never shown by name; a written one is listed under the counts with
the member's name or "Anonymous", as they chose. Nothing here notifies anyone.
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


class RunLength(models.IntegerChoices):
    """How long a poll runs, as the New Poll composer's Runs for chips offer it (#925)."""

    ONE_DAY = 1, "1 day"
    THREE_DAYS = 3, "3 days"
    ONE_WEEK = 7, "1 week"
    TWO_WEEKS = 14, "2 weeks"
    THIRTY_DAYS = 30, "30 days"


def closes_label(moment: datetime) -> str:
    """A closing time in Portland: ``Tue, Oct 13 at 2:11 PM``. The composer's live line writes the same."""
    local = timezone.localtime(moment)
    return f"{local:%a}, {local:%b} {local.day} at {local.hour % 12 or 12}:{local.minute:02d} {local:%p}"


QUESTION_MAX_LENGTH = 200
ANSWER_MAX_LENGTH = 120
#: What results show in place of the name of a member who posted a written answer anonymously.
ANONYMOUS = "Anonymous"


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


class InvalidAnswerError(VoteRefusedError):
    """Neither or both of a picked and a written answer, a written one this poll does not take, or one too long."""


@dataclass(frozen=True)
class WrittenAnswer:
    """One written answer as results show it (#925 part 3): its text and who wrote it, or "Anonymous".

    Built only by :meth:`from_vote`, which reads the member's name only when the vote is not
    anonymous, so an anonymous author's name never reaches a template, attribute or response.
    """

    text: str
    author: str
    is_mine: bool

    @classmethod
    def from_vote(cls, vote: PollVote, viewer: Member | None) -> WrittenAnswer:
        """The answer for ``viewer``, marked as theirs when they wrote it."""
        author = ANONYMOUS if vote.anonymous else vote.member.display_name
        return cls(text=vote.write_in, author=author, is_mine=viewer is not None and vote.member_id == viewer.pk)


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
    allows_write_in = models.BooleanField(
        default=False,
        help_text="Members may write their own answer instead of picking one (#925 part 3). Set when posted.",
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
        cls,
        *,
        question: str,
        choices: list[str],
        days: int,
        by: User,
        now: datetime,
        guild: Guild | None = None,
        allows_write_in: bool = False,
    ) -> Poll:
        """Open a new poll now, site wide or for ``guild``, unless one is already open there.

        ``allows_write_in`` lets members write their own answer instead of picking one.

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
                allows_write_in=allows_write_in,
            )
            PollChoice.objects.bulk_create(
                PollChoice(poll=poll, text=text, position=index) for index, text in enumerate(answers)
            )
        return poll

    @property
    def closes_label(self) -> str:
        """When voting ends, in Portland time: ``Tue, Oct 13 at 2:11 PM``."""
        return closes_label(self.closes_at)

    def is_open(self, now: datetime) -> bool:
        """Whether members can vote at ``now``."""
        return self.opens_at <= now < self.closes_at

    def close(self, now: datetime) -> None:
        """End voting at ``now`` (Close now). A poll already closed keeps its closing time."""
        if self.is_open(now):
            self.closes_at = now
            self.save(update_fields=["closes_at"])

    def results(self) -> list[ChoiceResult]:
        """Every answer in order with its votes and percent of all votes (written ones too), in two queries."""
        written = self.votes.filter(choice__isnull=True).count()
        return tally(self.choices.annotate(vote_count=Count("votes")).order_by("position"), written=written)

    def written_answers(self, viewer: Member | None = None) -> list[WrittenAnswer]:
        """The written answers, oldest first, each with its author or "Anonymous" (one query, none without write-ins)."""
        if not self.allows_write_in:
            return []
        return [WrittenAnswer.from_vote(vote, viewer) for vote in self.votes.written().select_related("member")]

    def vote(
        self,
        *,
        member: Member | None,
        now: datetime,
        choice_pk: int | None = None,
        write_in: str = "",
        anonymous: bool = True,
    ) -> PollVote:
        """Record ``member``'s one vote: the single way a vote is cast.

        Exactly one of ``choice_pk`` (a picked answer) and ``write_in`` (their own words, where
        the poll allows it; ``anonymous`` decides whether results show their name). Goes through
        ``PollVote.save`` (never a bulk insert), so the cross-poll guard runs, and turns the
        one-vote constraint's race into :class:`AlreadyVotedError`, not a 500.

        Raises:
            NotAVoterError: No member, a guest or a former member.
            PollClosedError: The poll is not open at ``now``.
            InvalidAnswerError: Neither or both answers, a written answer this poll does not take,
                or one longer than ``ANSWER_MAX_LENGTH``.
            AlreadyVotedError: This member already voted here.
            PollChoice.DoesNotExist: ``choice_pk`` is not an answer to this poll.
        """
        if not can_vote(member):
            raise NotAVoterError("Polls are for members.")
        assert member is not None  # can_vote refused None
        if not self.is_open(now):
            raise PollClosedError("This poll has closed.")
        written = write_in.strip()
        if (choice_pk is None) == (not written):
            raise InvalidAnswerError("Pick an answer or write your own.")
        if written and not self.allows_write_in:
            raise InvalidAnswerError("This poll takes picked answers only.")
        if len(written) > ANSWER_MAX_LENGTH:
            raise InvalidAnswerError(f"Keep your answer to {ANSWER_MAX_LENGTH} characters or fewer.")
        vote = PollVote(poll=self, member=member, write_in=written, anonymous=anonymous)
        if choice_pk is not None:
            vote.choice = self.choices.get(pk=choice_pk)
        try:
            with transaction.atomic():
                vote.save()
        except IntegrityError as error:
            raise AlreadyVotedError("You already voted in this poll.") from error
        return vote


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


class PollVoteQuerySet(models.QuerySet["PollVote"]):
    def written(self) -> PollVoteQuerySet:
        """Written answers (#925 part 3), oldest first."""
        return self.filter(choice__isnull=True).order_by("created_at", "pk")


class PollVote(models.Model):
    """A member's one answer to one poll: a picked answer, counted and never shown by name, or a written one.

    A written answer (#925 part 3) is listed under the counts with the member's name, or
    "Anonymous" when ``anonymous`` is set. A vote has exactly one of ``choice`` and ``write_in``.
    """

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="votes", help_text="The poll voted in.")
    choice = models.ForeignKey(
        PollChoice,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="votes",
        help_text="The answer picked. Blank for a written answer.",
    )
    write_in = models.CharField(
        max_length=ANSWER_MAX_LENGTH,
        blank=True,
        default="",
        help_text="The member's own answer, where the poll allows it. Blank for a picked answer.",
    )
    anonymous = models.BooleanField(
        default=True,
        help_text="Show a written answer as Anonymous rather than with the member's name.",
    )
    member = models.ForeignKey(
        "membership.Member", on_delete=models.CASCADE, related_name="poll_votes", help_text="Who voted."
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the vote was cast.")

    objects = PollVoteQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["poll", "member"], name="pollvote_one_per_member"),
            models.CheckConstraint(
                condition=(Q(choice__isnull=False) & Q(write_in="")) | (Q(choice__isnull=True) & ~Q(write_in="")),
                name="pollvote_choice_xor_write_in",
            ),
        ]

    def __str__(self) -> str:
        return f"Vote in poll {self.poll_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Take the poll from the choice, refusing a choice from another poll.

        ``poll`` is kept beside ``choice`` because the one-vote-per-member constraint needs it,
        so the two must never disagree. A written answer has no choice and keeps its poll.

        Raises:
            ValueError: ``poll`` is set and is not the choice's poll.
        """
        choice = self.choice
        if choice is not None:
            if self.poll_id is None:
                self.poll_id = choice.poll_id
            elif self.poll_id != choice.poll_id:
                raise ValueError(f"Answer {choice.pk} belongs to poll {choice.poll_id}, not poll {self.poll_id}.")
        super().save(*args, **kwargs)


def tally(choices: models.QuerySet[PollChoice] | list[PollChoice], written: int = 0) -> list[ChoiceResult]:
    """Turn choices annotated with ``vote_count`` into results whose percents use the poll's total.

    The total counts ``written`` answers too, so a picked answer's percent is its share of every vote.
    """
    rows = list(choices)
    total = sum(row.vote_count for row in rows) + written  # type: ignore[attr-defined]
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
    """One poll as one member sees it: the tally, the written answers, their own answer, and whether to offer choices.

    Built for a page of polls at once by :meth:`for_polls`, so a page costs two queries however
    many polls it shows, and a third when any of them takes written answers. The same card
    renders on Home, a guild's Polls tab and Past Polls.
    """

    poll: Poll
    results: list[ChoiceResult]
    my_choice_pk: int | None
    is_open: bool
    can_vote: bool
    has_voted: bool = False
    written: tuple[WrittenAnswer, ...] = ()

    @property
    def total_votes(self) -> int:
        """Votes cast in this poll, written answers included."""
        return sum(result.votes for result in self.results) + len(self.written)

    @property
    def shows_choices(self) -> bool:
        """Offer the answers as buttons: open, a voter, and not voted yet. Otherwise show results."""
        return self.is_open and self.can_vote and not self.has_voted

    @property
    def offers_write_in(self) -> bool:
        """Offer the write your own box beside the answers, where the poll allows it."""
        return self.shows_choices and self.poll.allows_write_in

    @classmethod
    def for_polls(cls, polls: list[Poll], member: Member | None, now: datetime) -> list[PollCard]:
        """Cards for ``polls`` in their order.

        One query for every tally, one for this member's votes, and one for the written answers
        of the polls that take them (none when no poll does).
        """
        ids = [poll.pk for poll in polls]
        rows = (
            PollChoice.objects.filter(poll_id__in=ids).annotate(vote_count=Count("votes")).order_by("poll", "position")
        )
        by_poll: dict[int, list[PollChoice]] = {pk: [] for pk in ids}
        for row in rows:
            by_poll[row.poll_id].append(row)
        mine: dict[int, int | None] = {}
        if member is not None:
            mine = dict(PollVote.objects.filter(member=member, poll_id__in=ids).values_list("poll_id", "choice_id"))
        written: dict[int, list[WrittenAnswer]] = {pk: [] for pk in ids}
        write_in_ids = [poll.pk for poll in polls if poll.allows_write_in]
        if write_in_ids:
            for vote in PollVote.objects.filter(poll_id__in=write_in_ids).written().select_related("member"):
                written[vote.poll_id].append(WrittenAnswer.from_vote(vote, member))
        voter = can_vote(member)
        return [
            cls(
                poll=poll,
                results=tally(by_poll[poll.pk], written=len(written[poll.pk])),
                my_choice_pk=mine.get(poll.pk),
                is_open=poll.is_open(now),
                can_vote=voter,
                has_voted=poll.pk in mine,
                written=tuple(written[poll.pk]),
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
