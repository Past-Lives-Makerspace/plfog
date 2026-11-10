"""Polls (#708, back in #925): someone asks one question, members pick one answer, and it closes on a date.

A poll is site wide (``guild`` blank, posted by an admin, shown on Home) or a guild's (posted by
that guild's leads, staff or an admin, shown on the guild's Polls tab). A poll is open
exactly while ``opens_at <= now < closes_at``, so it closes on its own and nothing has to run for
that; Close now moves ``closes_at`` to now. Only one poll is open at a time in each scope (site
wide, or one guild), which :meth:`Poll.post` keeps true (a time range cannot be a unique index):
posting while one is open there is refused. A member votes once per poll, held by a database
constraint: by picking an answer, or (#925 part 3, where the poll allows it) by writing their own.
A picked answer is a count and never shown by name; a written one is listed under the counts with
the member's name or "Anonymous", as they chose. Nothing here notifies anyone.

Email polls (#1142) are a separate feature at the end of this module: a poll of up to ten
questions sent from Email Members, carried by an ``AnnouncementDraft`` of kind Poll.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, models, transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import AnnouncementDraft, Guild, Member

logger = logging.getLogger(__name__)

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


# Email polls (#1142): a poll of up to ten questions sent from Email Members to any audience it
# emails. It rides on an AnnouncementDraft whose ``kind`` is Poll (the draft holds the title, the
# audience and the send); these tables hold the questions, who it was sent to and their answers.

MAX_QUESTIONS = 10
MIN_OPTIONS = 2
MAX_OPTIONS = 4
INTRO_MAX_LENGTH = 500
OPEN_ANSWER_MAX_LENGTH = 2000
_ANSWER_TOKEN_SALT = "polls.email-poll-answer"
#: What a member outside the audience reads on the plain link of an audience only poll.
DIFFERENT_GROUP = "This poll was sent to a different group."
POLL_CLOSED = "This poll has closed."
#: The event each recipient's go-live email is emitted under (#1142 part 2).
EMAIL_POLL_LIVE = "polls.email_poll_live"
#: The one reminder to recipients who have not answered (#1142 part 4).
EMAIL_POLL_REMINDER = "polls.email_poll_reminder"
#: The member announcement email switch the poll emails follow besides their own.
_ANNOUNCEMENT_EVENT = "site_announcement"
#: What a submission with every question left blank reads: a response always holds an answer.
NO_ANSWERS = "Answer at least one question."
BAD_LINK = "This link is not valid."


#: One question's answer as :meth:`EmailPoll.answer` takes it: written text, or the option picked
#: (an int or a string of digits) or a list of them.
Given = str | int | list[int | str]


class ReminderLead(models.IntegerChoices):
    """How long before closing an email poll's one reminder goes out (#1142)."""

    ONE_DAY = 1, "1 day before closing"
    TWO_DAYS = 2, "2 days before closing"
    THREE_DAYS = 3, "3 days before closing"


class QuestionKind(models.TextChoices):
    """What kind of answer an email poll question takes (#1142)."""

    OPEN = "open", "Open-ended"
    PICK_ONE = "one", "Pick one"
    PICK_ANY = "any", "Pick any"


class BadPollLinkError(Exception):
    """An emailed poll link that is tampered, names no recipient, or whose poll has closed."""


class AnswerRefusedError(Exception):
    """Answers that cannot be saved; the message is written for the member."""


@dataclass(frozen=True)
class QuestionSpec:
    """One question as the composer hands it over: its text, its kind and, for a pick kind, its options."""

    text: str
    kind: str
    options: tuple[str, ...] = ()
    write_in: str = ""

    @property
    def filled_options(self) -> list[str]:
        """The options with text, stripped, in order: a blank option row is not an option."""
        return [text for text in (option.strip() for option in self.options) if text]

    @property
    def write_in_label(self) -> str:
        """The write-in option's name (#1142 AC13), stripped; blank when the question has none."""
        return self.write_in.strip()

    @property
    def option_count(self) -> int:
        """The options members choose from, the write-in included: the number held to 2 to 4."""
        return len(self.filled_options) + (1 if self.write_in_label else 0)


@dataclass(frozen=True)
class PollDraft:
    """A poll as the Email Members composer hands it over (#1142 part 3): its settings and questions."""

    title: str
    intro: str
    run_days: int
    send_email: bool
    remind_days_before: int | None
    audience_only: bool
    questions: list[QuestionSpec]

    @property
    def signature(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """The questions as a repeat is compared on: text, kind and options in order, as they are stored."""
        return [
            question_signature(spec.text, spec.kind, _signed_options(spec.filled_options, spec.write_in_label))
            for spec in self.questions
        ]

    def apply_to(self, draft: AnnouncementDraft) -> None:
        """Make ``draft`` this poll's row, unsaved: kind Poll, its title, and every email only channel off."""
        from membership.models import AnnouncementDraft, GuildAnnouncement

        draft.kind = AnnouncementDraft.Kind.POLL
        draft.title = self.title.strip()
        draft.body = ""
        draft.send_email = self.send_email
        draft.push_enabled = False
        draft.discord_enabled = False
        draft.discord_channel = GuildAnnouncement.DiscordChannel.NONE
        draft.mention = AnnouncementDraft.Mention.NONE
        draft.discord_post_as_me = False
        draft.mark_as_urgent = False
        draft.expires_at = None


def _signed_options(options: list[str], write_in: str) -> list[str]:
    """Options as a signature lists them: the write-in last, marked, so it never matches a plain option."""
    return [*options, f"write-in: {write_in}"] if write_in else options


def question_signature(text: str, kind: str, options: list[str]) -> tuple[str, str, tuple[str, ...]]:
    """One question as two polls are compared (#1142): its text, its kind and its options, open-ended keeping none."""
    return (text.strip(), kind, () if kind == QuestionKind.OPEN else tuple(options))


def validate_questions(specs: list[QuestionSpec]) -> None:
    """Refuse a question list an email poll cannot send.

    Raises:
        ValueError: No questions or more than ``MAX_QUESTIONS``, a blank or too long question, an
            unknown kind, a pick question without 2 to 4 filled options (blank rows are dropped,
            not counted), an option too long, or an open-ended question carrying a filled option.
    """
    if not specs:
        raise ValueError("Add at least one question.")
    if len(specs) > MAX_QUESTIONS:
        raise ValueError(f"A poll has at most {MAX_QUESTIONS} questions.")
    for number, spec in enumerate(specs, start=1):
        _validate_question(number, spec)


def _validate_question(number: int, spec: QuestionSpec) -> None:
    """Refuse one question, named by its ``number`` from 1 (see :func:`validate_questions`).

    Raises:
        ValueError: As :func:`validate_questions` describes, for this question.
    """
    text = spec.text.strip()
    if not text:
        raise ValueError(f"Question {number} needs its text.")
    if len(text) > QUESTION_MAX_LENGTH:
        raise ValueError(f"Question {number} is longer than {QUESTION_MAX_LENGTH} characters.")
    if spec.kind not in QuestionKind.values:
        raise ValueError(f"Question {number} has an unknown kind: {spec.kind!r}.")
    options = spec.filled_options
    if spec.kind == QuestionKind.OPEN:
        if options or spec.write_in_label:
            raise ValueError(f"Question {number} is open-ended and takes no options.")
        return
    if not MIN_OPTIONS <= spec.option_count <= MAX_OPTIONS:
        raise ValueError(
            f"Question {number} needs {MIN_OPTIONS} to {MAX_OPTIONS} filled options, the write-in included."
        )
    if any(len(option) > ANSWER_MAX_LENGTH for option in [*options, spec.write_in_label]):
        raise ValueError(f"An option in question {number} is longer than {ANSWER_MAX_LENGTH} characters.")


class EmailPollQuerySet(models.QuerySet["EmailPoll"]):
    def sent(self) -> EmailPollQuerySet:
        """Polls that went live (Send stamped their window)."""
        return self.filter(opens_at__isnull=False)

    def open_at(self, now: datetime) -> EmailPollQuerySet:
        """Polls open at ``now``."""
        return self.filter(opens_at__lte=now, closes_at__gt=now)

    def sent_to(self, member: Member) -> EmailPollQuerySet:
        """Polls sent to ``member``."""
        return self.filter(recipients__member=member)

    def listed_for(self, member: Member) -> EmailPollQuerySet:
        """The Polls page: every poll sent to ``member``, newest first, each with ``answered`` annotated."""
        answered = EmailPollResponse.objects.filter(poll=OuterRef("pk"), member=member)
        return (
            self.sent_to(member)
            .select_related("draft")
            .annotate(answered=Exists(answered))
            .order_by("-opens_at", "-pk")
        )

    def awaiting_answer(self, member: Member, now: datetime) -> EmailPollQuerySet:
        """Home's Needs Attention (#1142 part 4): open polls sent to ``member`` they have not answered, closing soonest first."""
        answered = EmailPollResponse.objects.filter(poll=OuterRef("pk"), member=member)
        return (
            self.open_at(now)
            .sent_to(member)
            .exclude(Exists(answered))
            .select_related("draft")
            .order_by("closes_at", "pk")
        )

    def with_counts(self) -> EmailPollQuerySet:
        """Annotate ``sent_count`` (recipients) and ``answered_count`` (responses), in the one query."""
        sent = EmailPollRecipient.objects.filter(poll=OuterRef("pk")).values("poll").annotate(n=Count("pk")).values("n")
        answered = (
            EmailPollResponse.objects.filter(poll=OuterRef("pk")).values("poll").annotate(n=Count("pk")).values("n")
        )
        return self.annotate(sent_count=Coalesce(Subquery(sent), 0), answered_count=Coalesce(Subquery(answered), 0))

    def reminders_due(self, now: datetime) -> EmailPollQuerySet:
        """Open polls whose reminder time has come and whose reminder has not gone out."""
        return self.open_at(now).filter(remind_at__lte=now, reminder_sent_at__isnull=True)


class EmailPoll(models.Model):
    """A poll of up to ten questions sent from Email Members (#1142).

    Its title, audience, sender and send belong to its :class:`~membership.models.AnnouncementDraft`
    (``kind`` Poll), so drafts, audiences and the site queue work as they do for an email. It opens
    at Send (:meth:`open`) and closes on its own after ``run_days``; nothing can close it early.
    """

    draft = models.OneToOneField(
        "membership.AnnouncementDraft",
        on_delete=models.CASCADE,
        related_name="poll",
        help_text="The Email Members row that carries this poll's title, audience, sender and send.",
    )
    intro = models.TextField(
        max_length=INTRO_MAX_LENGTH,
        blank=True,
        default="",
        help_text="An optional short note above the questions and in the email.",
    )
    run_days = models.PositiveSmallIntegerField(
        choices=RunLength.choices,
        default=RunLength.ONE_WEEK,
        help_text="How many days the poll stays open after Send.",
    )
    remind_days_before = models.PositiveSmallIntegerField(
        choices=ReminderLead.choices,
        null=True,
        blank=True,
        help_text="Days before closing to remind recipients who have not answered. Blank sends no reminder.",
    )
    audience_only = models.BooleanField(
        default=False,
        help_text="Only the people it was sent to may answer through the plain link.",
    )
    opens_at = models.DateTimeField(null=True, blank=True, help_text="When Send opened it. Blank while a draft.")
    closes_at = models.DateTimeField(null=True, blank=True, help_text="When answering ends. Blank while a draft.")
    remind_at = models.DateTimeField(
        null=True, blank=True, help_text="When the reminder is due, set at Send. Blank with no reminder."
    )
    reminder_sent_at = models.DateTimeField(
        null=True, blank=True, help_text="When the reminder went out. Blank until it does."
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the poll was first saved.")

    objects = EmailPollQuerySet.as_manager()

    class Meta:
        ordering = ["-opens_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(opens_at__isnull=True) | Q(closes_at__gt=models.F("opens_at")),
                name="emailpoll_closes_after_opens",
            ),
            models.CheckConstraint(
                condition=Q(remind_days_before__isnull=True) | Q(remind_days_before__lt=models.F("run_days")),
                name="emailpoll_remind_in_run",
            ),
        ]
        indexes = [
            models.Index(fields=["closes_at", "opens_at"], name="emailpoll_window_idx"),
            models.Index(fields=["remind_at"], name="emailpoll_remind_idx"),
        ]

    def __str__(self) -> str:
        return self.draft.title

    @property
    def is_sent(self) -> bool:
        """Whether Send has opened it."""
        return self.opens_at is not None

    def is_open(self, now: datetime) -> bool:
        """Whether members can answer at ``now``."""
        if self.opens_at is None or self.closes_at is None:
            return False
        return self.opens_at <= now < self.closes_at

    @property
    def closes_label(self) -> str:
        """When answering ends, in Portland time.

        Raises:
            ValueError: The poll has not been sent, so it has no closing time.
        """
        if self.closes_at is None:
            raise ValueError("A poll has no closing time until it is sent.")
        return closes_label(self.closes_at)

    @classmethod
    def save_for_draft(cls, draft: AnnouncementDraft, data: PollDraft) -> EmailPoll:
        """Write ``draft``'s poll from the composer: its settings and questions, creating it the first time.

        Raises:
            ValueError: The draft's poll was sent, or :func:`validate_questions` refuses the questions.
        """
        poll, _created = cls.objects.get_or_create(draft=draft)
        poll.intro = data.intro.strip()
        poll.run_days = data.run_days
        poll.remind_days_before = data.remind_days_before
        poll.audience_only = data.audience_only
        poll.save(update_fields=["intro", "run_days", "remind_days_before", "audience_only"])
        poll.replace_questions(data.questions)
        return poll

    @property
    def signature(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """The saved questions as :attr:`PollDraft.signature` reads a composed one, in two queries."""
        return [
            question_signature(
                question.text,
                question.kind,
                _signed_options(
                    [option.text for option in question.options.all() if not option.is_write_in],
                    next((option.text for option in question.options.all() if option.is_write_in), ""),
                ),
            )
            for question in self.questions.prefetch_related("options")
        ]

    def replace_questions(self, specs: list[QuestionSpec]) -> None:
        """Save ``specs`` as this draft poll's questions, in order, replacing any it had.

        Raises:
            ValueError: The poll was sent (its questions are fixed), or :func:`validate_questions` refuses.
        """
        if self.is_sent:
            raise ValueError("A sent poll's questions cannot change.")
        validate_questions(specs)
        with transaction.atomic():
            self.questions.all().delete()
            questions = EmailPollQuestion.objects.bulk_create(
                EmailPollQuestion(poll=self, position=position, text=spec.text.strip(), kind=spec.kind)
                for position, spec in enumerate(specs)
            )
            EmailPollOption.objects.bulk_create(
                EmailPollOption(question=question, position=position, text=text, is_write_in=is_write_in)
                for question, spec in zip(questions, specs, strict=True)
                for position, (text, is_write_in) in enumerate(_option_rows(spec))
            )

    def open(self, now: datetime) -> None:
        """Open the poll at ``now`` (Send): it closes ``run_days`` later, and the reminder falls due before that.

        Who it goes to is recorded here, in the same transaction (:meth:`record_recipients` from
        ``draft.poll_recipient_ids()``), so from the moment it opens its recipients can answer an
        audience only poll and see it as theirs, even while a queued site send has not emailed
        them yet. Opening a poll already open keeps its first window and recipients, so a
        repeated Send changes nothing.

        Raises:
            ValueError: The poll has no questions.
        """
        if self.is_sent:
            return
        if not self.questions.exists():
            raise ValueError("Add at least one question.")
        self.opens_at = now
        self.closes_at = now + timedelta(days=self.run_days)
        self.remind_at = (
            None if self.remind_days_before is None else self.closes_at - timedelta(days=self.remind_days_before)
        )
        with transaction.atomic():
            self.save(update_fields=["opens_at", "closes_at", "remind_at"])
            self.record_recipients(self.draft.poll_recipient_ids())

    def record_recipients(self, user_ids: set[int]) -> None:
        """Note who this poll was sent to: the member of each account in ``user_ids``.

        An account with no member is left out. Recording someone twice changes nothing, so a
        retried send can record again.
        """
        from membership.models import Member

        member_pks = Member.objects.filter(user_id__in=list(user_ids)).values_list("pk", flat=True)
        EmailPollRecipient.objects.bulk_create(
            (EmailPollRecipient(poll=self, member_id=pk) for pk in member_pks), ignore_conflicts=True
        )

    def is_recipient(self, member: Member) -> bool:
        """Whether this poll was sent to ``member``."""
        return self.recipients.filter(member=member).exists()

    def answer_refusal(self, member: Member, now: datetime) -> str:
        """Why ``member`` may not answer at ``now``, or ``""`` when they may.

        Closed (or never sent) polls refuse everyone; an audience only poll refuses anyone it was not sent to.
        """
        if not self.is_open(now):
            return POLL_CLOSED
        if self.audience_only and not self.is_recipient(member):
            return DIFFERENT_GROUP
        return ""

    def make_answer_token(self, member: Member) -> str:
        """The personal token in ``member``'s email link: it names them and this poll."""
        return signing.dumps({"poll": self.pk, "member": member.pk}, salt=_ANSWER_TOKEN_SALT)

    @staticmethod
    def read_answer_token(token: str, now: datetime) -> tuple[EmailPoll, Member]:
        """The poll and member an emailed link names, while the poll is open.

        Raises:
            BadPollLinkError: The token is tampered or unreadable, names someone the poll was not
                sent to, or its poll has closed (the message says which, for the member).
        """
        try:
            data = signing.loads(token, salt=_ANSWER_TOKEN_SALT)
        except signing.BadSignature as error:
            raise BadPollLinkError(BAD_LINK) from error
        recipient = (
            EmailPollRecipient.objects.select_related("poll__draft", "member")
            .filter(poll_id=data["poll"], member_id=data["member"])
            .first()
        )
        if recipient is None:
            raise BadPollLinkError(BAD_LINK)
        if not recipient.poll.is_open(now):
            raise BadPollLinkError(POLL_CLOSED)
        return recipient.poll, recipient.member

    def answer(
        self,
        *,
        member: Member,
        answers: dict[int, Given],
        now: datetime,
        write_ins: dict[int, str] | None = None,
    ) -> EmailPollResponse:
        """Save ``member``'s answers, replacing any they gave before: the one way an answer is written.

        ``answers`` maps a question's pk to the text written (open-ended) or the option picked: an
        option pk as an int or a string of digits (as a form posts it), or a list of them. Every
        pick question needs an answer (exactly one for Pick one); an open-ended one may be left
        out or blank, and a blank one stores nothing, but a submission must answer something.

        Raises:
            AnswerRefusedError: The member may not answer (:meth:`answer_refusal`), a pick question
                is unanswered, Pick one has more than one pick, a pick is not one of the question's
                options, an answer has the wrong shape, a written answer is too long, or nothing at
                all was answered (``NO_ANSWERS``).
        """
        refusal = self.answer_refusal(member, now)
        if refusal:
            raise AnswerRefusedError(refusal)
        rows = [
            row
            for question in self.questions.prefetch_related("options")
            for row in _answer_rows(question, answers, write_ins or {})
        ]
        if not rows:
            # Only an all open-ended poll gets here; an empty response would count as answered.
            raise AnswerRefusedError(NO_ANSWERS)
        with transaction.atomic():
            response, created = EmailPollResponse.objects.get_or_create(poll=self, member=member)
            response = EmailPollResponse.objects.select_for_update().get(pk=response.pk)
            if not created:
                response.save(update_fields=["updated_at"])
            response.answers.all().delete()
            for row in rows:
                row.response = response
            EmailPollAnswer.objects.bulk_create(rows)
        return response

    def may_see_results(self, member: Member | None, *, is_sender: bool = False) -> bool:
        """Whether ``member`` may see the results: a sender, an admin, or someone who answered.

        A sender is the draft's author, or anyone the caller found may open the poll's row on Email
        Members (``is_sender``: ``hub.views._announcement_rows``, which needs the request), such as
        a guild's co-lead or a class's co-instructor, who can already open, save over and send it.
        """
        if member is None:
            return False
        if is_sender or member.is_fog_admin or (member.user_id is not None and member.user_id == self.draft.author_id):
            return True
        return self.responses.filter(member=member).exists()

    def results(self) -> EmailPollResults:
        """Every question's results and the per person table, in four queries."""
        return EmailPollResults.load(self)

    def answers_of(self, member: Member) -> SavedAnswers:
        """``member``'s saved answers: by question pk, the text written or the option pks picked,
        and the text typed for any write-in they picked. Empty if none. One query."""
        saved: dict[int, str | list[int]] = {}
        write_ins: dict[int, str] = {}
        for answer in EmailPollAnswer.objects.filter(response__poll=self, response__member=member).order_by("pk"):
            if answer.option_id is None:
                saved[answer.question_id] = answer.text
                continue
            picks = saved.setdefault(answer.question_id, [])
            assert isinstance(picks, list)  # one question is never both written and picked
            picks.append(answer.option_id)
            if answer.text:
                write_ins[answer.question_id] = answer.text
        return SavedAnswers(answers=saved, write_ins=write_ins)

    @property
    def answer_path(self) -> str:
        """The plain link: anyone signed in may open it (only recipients may answer an audience only poll)."""
        from django.urls import reverse

        return reverse("polls:email_poll", args=[self.pk])

    def answer_link(self, member: Member) -> str:
        """``member``'s personal link, absolute for an email: it answers as them without signing in."""
        from django.urls import reverse

        from core.urls_util import member_absolute_url

        return member_absolute_url(reverse("polls:email_poll_link", args=[self.make_answer_token(member)]))

    def deliver(self) -> int:
        """Email the go-live notice to everyone recorded at :meth:`open`, each with their personal link.

        Never works out the recipients again: it reads the ones :meth:`open` recorded, so the
        people emailed are exactly the people the poll admits. Sends nothing when the draft's
        Email when it goes live is off. Skips anyone who turned off the makerspace announcement
        email on Settings; the event's own Email switch applies inside ``emit`` as well. Every
        email shares the period ``email_poll:<pk>:live`` and the ledger keys on each person, so a
        retried send (the site queue) emails only those the first run did not reach.

        Returns:
            How many people were emailed.

        Raises:
            ValueError: The poll has not been opened.
        """
        if not self.is_sent:
            raise ValueError("Open the poll before emailing it.")
        if not self.draft.send_email:
            return 0
        return self._email_each(self.recipients.all(), EMAIL_POLL_LIVE, self.email_subject, "live")

    def remind(self) -> int:
        """Email the one reminder to everyone recorded at :meth:`open` who has not answered (#1142 part 4).

        Sent whatever "Email when it goes live" says: the reminder is its own switch. Follows the
        announcement email switch like :meth:`deliver`, under the period
        ``email_poll:<pk>:reminder``, so a retried run reminds nobody twice.

        Returns:
            How many people were emailed.
        """
        answered = EmailPollResponse.objects.filter(poll=self).values("member_id")
        waiting = self.recipients.exclude(member_id__in=answered)
        return self._email_each(waiting, EMAIL_POLL_REMINDER, f"Poll closing soon: {self.draft.title}", "reminder")

    def _email_each(self, recipients: models.QuerySet[EmailPollRecipient], event: str, subject: str, name: str) -> int:
        """Emit ``event`` once per recipient with an account, each with their own link, as ``polls/emails/email_poll_<name>``.

        One emit per person, because each email carries that member's token; every one shares the
        period ``email_poll:<pk>:<name>`` and the ledger keys on the person. Anyone who turned off
        the makerspace announcement email is skipped; the event's own Email switch applies in ``emit``.

        Returns:
            How many people were emailed.
        """
        from core.events.registry import Channel
        from core.events.senders import emit_with_email_shell

        rows = list(recipients.select_related("member__user").filter(member__user__isnull=False))
        muted = _announcement_email_off({cast(int, row.member.user_id) for row in rows})
        question_count = self.questions.count()
        emailed = 0
        for row in rows:
            member = row.member
            if member.user_id in muted:
                continue
            result = emit_with_email_shell(
                event,
                actor=self.draft.author,
                context={"user": member.user},
                subject=subject,
                text_template=f"polls/emails/email_poll_{name}.txt",
                html_template=f"polls/emails/email_poll_{name}.html",
                template_context=self._email_context(member, question_count),
                url=self.answer_path,
                period=f"email_poll:{self.pk}:{name}",
                recipient_user_ids={cast(int, member.user_id)},
                log_activity=False,
            )
            emailed += sum(1 for _user_pk, channel in result.delivered if channel is Channel.EMAIL)
        return emailed

    @property
    def email_subject(self) -> str:
        """The emails' subject: ``Poll: <title>``."""
        return f"Poll: {self.draft.title}"

    def _email_context(self, member: Member, question_count: int) -> dict[str, Any]:
        """What ``polls/emails/email_poll_live.{txt,html}`` read for ``member``."""
        from django.urls import reverse

        from core.urls_util import member_absolute_url

        return {
            "first_name": member.display_name.split(" ")[0],
            "title": self.draft.title,
            "intro": self.intro,
            "question_count": question_count,
            "closes_label": self.closes_label,
            "answer_url": self.answer_link(member),
            "polls_url": member_absolute_url(reverse("polls:mine")),
        }


def _announcement_email_off(user_ids: set[int]) -> set[int]:
    """The accounts among ``user_ids`` that turned the makerspace announcement email off, in one query."""
    from core.models import NotificationPreference

    return set(
        NotificationPreference.objects.filter(
            user_id__in=list(user_ids), event_key=_ANNOUNCEMENT_EVENT, channel="email", enabled=False
        ).values_list("user_id", flat=True)
    )


@dataclass(frozen=True)
class EmailPollPage:
    """One email poll as one member sees its page (#1142 part 2).

    ``outsider`` is a member an audience only poll was not sent to (and who is not its sender
    or an admin): they see "This poll was sent to a different group." and nothing more.
    ``results`` is set only for those :meth:`EmailPoll.may_see_results` allows.
    """

    poll: EmailPoll
    is_open: bool
    outsider: bool
    can_answer: bool
    has_answered: bool
    results: EmailPollResults | None

    @classmethod
    def load(cls, poll: EmailPoll, member: Member, now: datetime, *, is_sender: bool = False) -> EmailPollPage:
        """The page for ``member`` at ``now``; ``is_sender`` as :meth:`EmailPoll.may_see_results` takes it."""
        sees_results = poll.may_see_results(member, is_sender=is_sender)
        outsider = not sees_results and poll.audience_only and not poll.is_recipient(member)
        return cls(
            poll=poll,
            is_open=poll.is_open(now),
            outsider=outsider,
            can_answer=not outsider and poll.answer_refusal(member, now) == "",
            has_answered=poll.responses.filter(member=member).exists(),
            results=poll.results() if sees_results else None,
        )


def _option_rows(spec: QuestionSpec) -> list[tuple[str, bool]]:
    """A question's options to store, in order: ``(text, is_write_in)``, the write-in last."""
    rows = [(text, False) for text in spec.filled_options]
    return [*rows, (spec.write_in_label, True)] if spec.write_in_label else rows


def _answer_rows(
    question: EmailPollQuestion, answers: dict[int, Given], write_ins: dict[int, str]
) -> list[EmailPollAnswer]:
    """The answer rows ``answers`` gives ``question`` (unsaved, no response yet).

    A picked write-in option (#1142 AC13) carries the text the member typed for it, from
    ``write_ins``; text for a write-in nobody picked is dropped.

    Raises:
        AnswerRefusedError: See :meth:`EmailPoll.answer`.
    """
    given = answers.get(question.pk)  # A question left out is unanswered, which only an open one may be.
    if question.kind == QuestionKind.OPEN:
        if given is not None and not isinstance(given, str):
            raise AnswerRefusedError(f"Write your answer to: {question.text}")
        text = (given or "").strip()
        if len(text) > OPEN_ANSWER_MAX_LENGTH:
            raise AnswerRefusedError(f"Keep each written answer to {OPEN_ANSWER_MAX_LENGTH} characters or fewer.")
        return [EmailPollAnswer(question=question, text=text)] if text else []
    picked = _picked_pks(question, given)
    if not picked:
        raise AnswerRefusedError(f"Answer this question: {question.text}")
    if question.kind == QuestionKind.PICK_ONE and len(picked) > 1:
        raise AnswerRefusedError(f"Pick one answer for: {question.text}")
    offered = {option.pk: option for option in question.options.all()}
    if any(pk not in offered for pk in picked):
        raise AnswerRefusedError("That choice is not part of this poll.")
    return [
        EmailPollAnswer(question=question, option_id=pk, text=_write_in_text(offered[pk], question, write_ins))
        for pk in picked
    ]


def _write_in_text(option: EmailPollOption, question: EmailPollQuestion, write_ins: dict[int, str]) -> str:
    """What the member typed for a picked write-in option; blank for any other option.

    Raises:
        AnswerRefusedError: A picked write-in with no text, or text over ``ANSWER_MAX_LENGTH``.
    """
    if not option.is_write_in:
        return ""
    text = (write_ins.get(question.pk) or "").strip()  # Missing text is refused just below.
    if not text:
        raise AnswerRefusedError(f"Write your answer for {option.text}.")
    if len(text) > ANSWER_MAX_LENGTH:
        raise AnswerRefusedError(f"Keep your answer for {option.text} to {ANSWER_MAX_LENGTH} characters or fewer.")
    return text


def _picked_pks(question: EmailPollQuestion, given: Given | None) -> list[int]:
    """The option pks ``given`` names for a pick question, once each, in order.

    Takes an int, a string of digits, or a list or tuple of either; nothing given is no pick.

    Raises:
        AnswerRefusedError: Anything else (a word, a bool, a nested list), so a bad post is a
            message for the member and never a 500.
    """
    if given is None:
        return []
    items = list(given) if isinstance(given, list | tuple) else [given]
    picked: list[int] = []
    for item in items:
        if isinstance(item, int) and not isinstance(item, bool):
            picked.append(item)
        elif isinstance(item, str) and item.strip().isascii() and item.strip().isdigit():
            picked.append(int(item.strip()))
        else:
            raise AnswerRefusedError(f"Pick from the choices for: {question.text}")
    return list(dict.fromkeys(picked))


class EmailPollQuestion(models.Model):
    """One question of an email poll, in the order the sender wrote them."""

    poll = models.ForeignKey(
        EmailPoll, on_delete=models.CASCADE, related_name="questions", help_text="The poll this question is in."
    )
    position = models.PositiveSmallIntegerField(help_text="Its place in the poll, from 0.")
    text = models.CharField(max_length=QUESTION_MAX_LENGTH, help_text="The question as members read it.")
    kind = models.CharField(max_length=4, choices=QuestionKind.choices, help_text="What kind of answer it takes.")

    class Meta:
        ordering = ["poll", "position"]
        constraints = [models.UniqueConstraint(fields=["poll", "position"], name="emailpollq_one_per_position")]

    def __str__(self) -> str:
        return self.text


class EmailPollOption(models.Model):
    """One choice of a Pick one or Pick any question, in the order the sender wrote them."""

    question = models.ForeignKey(
        EmailPollQuestion, on_delete=models.CASCADE, related_name="options", help_text="The question it answers."
    )
    position = models.PositiveSmallIntegerField(help_text="Its place in the list, from 0.")
    text = models.CharField(max_length=ANSWER_MAX_LENGTH, help_text="The choice as members read it.")
    is_write_in = models.BooleanField(
        default=False,
        db_default=False,
        help_text="The question's write-in (#1142): picking it asks the member to type their own answer. Always last.",
    )

    class Meta:
        ordering = ["question", "position"]
        constraints = [
            models.UniqueConstraint(fields=["question", "position"], name="emailpollopt_one_per_position"),
            models.UniqueConstraint(
                fields=["question"], condition=Q(is_write_in=True), name="emailpollopt_one_write_in"
            ),
        ]

    def __str__(self) -> str:
        return self.text


class EmailPollRecipient(models.Model):
    """One member an email poll was sent to, recorded at send."""

    poll = models.ForeignKey(EmailPoll, on_delete=models.CASCADE, related_name="recipients", help_text="The poll sent.")
    member = models.ForeignKey(
        "membership.Member",
        on_delete=models.CASCADE,
        related_name="email_poll_receipts",
        help_text="Who it was sent to.",
    )
    sent_at = models.DateTimeField(auto_now_add=True, help_text="When it was recorded as sent to them.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["poll", "member"], name="emailpollrecipient_once")]

    def __str__(self) -> str:
        return f"Poll {self.poll_id} sent to member {self.member_id}"


class EmailPollResponse(models.Model):
    """One member's answers to one email poll; Update Answers rewrites them in place."""

    poll = models.ForeignKey(
        EmailPoll, on_delete=models.CASCADE, related_name="responses", help_text="The poll answered."
    )
    member = models.ForeignKey(
        "membership.Member",
        on_delete=models.CASCADE,
        related_name="email_poll_responses",
        help_text="Who answered.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When they first answered.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When they last sent or updated their answers.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["poll", "member"], name="emailpollresponse_once")]

    def __str__(self) -> str:
        return f"Member {self.member_id} answered poll {self.poll_id}"


class EmailPollAnswer(models.Model):
    """One answer within a response: a picked option (a Pick any question may have several) or written text."""

    response = models.ForeignKey(
        EmailPollResponse, on_delete=models.CASCADE, related_name="answers", help_text="The response it is part of."
    )
    question = models.ForeignKey(
        EmailPollQuestion, on_delete=models.CASCADE, related_name="answers", help_text="The question it answers."
    )
    option = models.ForeignKey(
        EmailPollOption,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="answers",
        help_text="The option picked. Blank for a written answer.",
    )
    text = models.TextField(
        max_length=OPEN_ANSWER_MAX_LENGTH,
        blank=True,
        default="",
        help_text="The written answer to an open-ended question, or what was typed for a picked write-in option.",
    )

    class Meta:
        constraints = [
            # An open-ended answer has text; an option answer may carry text only for a write-in,
            # which EmailPoll.answer holds (a constraint cannot read the option's is_write_in).
            models.CheckConstraint(
                condition=Q(option__isnull=False) | ~Q(text=""),
                name="emailpollans_open_has_text",
            ),
            models.UniqueConstraint(
                fields=["response", "option"], condition=Q(option__isnull=False), name="emailpollans_one_per_option"
            ),
            models.UniqueConstraint(
                fields=["response", "question"], condition=Q(option__isnull=True), name="emailpollans_one_text"
            ),
        ]

    def __str__(self) -> str:
        return f"Answer to question {self.question_id}"


def _cell_text(answer: EmailPollAnswer, option_text: dict[int, str]) -> str:
    """One answer as a Responses table cell: the text written, the option, or ``Option: what they typed``."""
    if answer.option_id is None:
        return answer.text
    label = option_text[answer.option_id]
    return f"{label}: {answer.text}" if answer.text else label


@dataclass(frozen=True)
class SavedAnswers:
    """A member's saved response as the answer form fills it in: picks or text, and write-in text."""

    answers: dict[int, str | list[int]]
    write_ins: dict[int, str]


@dataclass(frozen=True)
class OptionResult:
    """One option's count and its share of everyone who answered, rounded.

    ``pk`` is the option's, so the shared tally partial (``_poll_results.html``) can compare it
    with ``my_choice_pk`` and never mark every row as the viewer's.
    """

    pk: int
    text: str
    votes: int
    percent: int
    is_write_in: bool = False
    written: tuple[WrittenReply, ...] = ()


@dataclass(frozen=True)
class WrittenReply:
    """One written answer with the name of the member who wrote it."""

    name: str
    text: str


@dataclass(frozen=True)
class QuestionResult:
    """One question's results: option bars for a pick question, written answers for an open one."""

    question: EmailPollQuestion
    options: tuple[OptionResult, ...]
    written: tuple[WrittenReply, ...]


@dataclass(frozen=True)
class ResponseRow:
    """One person's row of the Responses table: their name, then one cell per question in order."""

    name: str
    cells: tuple[str, ...]


@dataclass(frozen=True)
class EmailPollResults:
    """An email poll's results (#1142): each question's tally or written answers, and one row per person."""

    questions: tuple[QuestionResult, ...]
    rows: tuple[ResponseRow, ...]

    @property
    def response_count(self) -> int:
        """How many people answered."""
        return len(self.rows)

    @classmethod
    def load(cls, poll: EmailPoll) -> EmailPollResults:
        """Build the results in four queries: questions, options, responses, answers.

        A percent is the share of everyone who answered the poll, so Pick any shares can add past 100.
        """
        questions = list(poll.questions.prefetch_related("options"))
        responses = list(poll.responses.select_related("member").order_by("created_at", "pk"))
        option_text = {option.pk: option.text for question in questions for option in question.options.all()}
        given: dict[tuple[int, int], list[EmailPollAnswer]] = defaultdict(list)
        for answer in EmailPollAnswer.objects.filter(response__poll=poll).order_by("pk"):
            given[(answer.response_id, answer.question_id)].append(answer)
        votes = Counter(
            answer.option_id for answers in given.values() for answer in answers if answer.option_id is not None
        )
        total = len(responses)
        results = tuple(
            QuestionResult(
                question=question,
                options=tuple(
                    OptionResult(
                        pk=option.pk,
                        text=option.text,
                        votes=votes[option.pk],
                        percent=round(100 * votes[option.pk] / total) if total else 0,
                        is_write_in=option.is_write_in,
                        written=tuple(
                            WrittenReply(name=response.member.display_name, text=answer.text)
                            for response in responses
                            for answer in given[(response.pk, question.pk)]
                            if answer.option_id == option.pk and answer.text
                        ),
                    )
                    for option in question.options.all()
                ),
                written=tuple(
                    WrittenReply(name=response.member.display_name, text=answer.text)
                    for response in responses
                    for answer in given[(response.pk, question.pk)]
                    if answer.option_id is None
                ),
            )
            for question in questions
        )
        rows = tuple(
            ResponseRow(
                name=response.member.display_name,
                cells=tuple(
                    ", ".join(_cell_text(answer, option_text) for answer in given[(response.pk, question.pk)])
                    for question in questions
                ),
            )
            for response in responses
        )
        return cls(questions=results, rows=rows)


@dataclass(frozen=True)
class ReminderRun:
    """One run of ``send_email_poll_reminders``: polls reminded, people emailed, polls that failed."""

    polls: int
    emailed: int
    failed: int


def send_due_poll_reminders(now: datetime) -> ReminderRun:
    """Send every poll's reminder whose time has come, once each: the 15 minute job (#1142 part 4).

    Each due poll is claimed with a conditional update on ``reminder_sent_at``, so two runs at
    once remind it once. A poll whose emails fail gives its claim back for the next run; the
    ledger period ``email_poll:<pk>:reminder`` keeps anyone already reminded from a second one.
    One poll failing never stops the rest.
    """
    polls = emailed = failed = 0
    for pk in list(EmailPoll.objects.reminders_due(now).values_list("pk", flat=True)):
        if not EmailPoll.objects.filter(pk=pk, reminder_sent_at__isnull=True).update(reminder_sent_at=now):
            continue
        poll = EmailPoll.objects.select_related("draft__author").get(pk=pk)
        try:
            emailed += poll.remind()
        except Exception:
            logger.exception("Email poll %s: the reminder failed; the next run tries again.", pk)
            EmailPoll.objects.filter(pk=pk).update(reminder_sent_at=None)
            failed += 1
            continue
        polls += 1
    return ReminderRun(polls=polls, emailed=emailed, failed=failed)
