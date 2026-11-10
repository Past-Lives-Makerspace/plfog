"""The New Poll form (#708, back in #925): a question, two to six answers and how many days it runs.

One form for Admin Tools > Polls (site wide) and a guild's Polls tab. Also the email poll answer
form (#1142): one field per question of a poll sent from Email Members.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.utils import timezone

from polls.models import (
    ANSWER_MAX_LENGTH,
    DEFAULT_DAYS,
    MAX_CHOICES,
    MIN_CHOICES,
    OPEN_ANSWER_MAX_LENGTH,
    QUESTION_MAX_LENGTH,
    Given,
    Poll,
    QuestionKind,
    RunLength,
    closes_label,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import QueryDict

    from membership.models import Guild
    from polls.models import EmailPollOption, EmailPollQuestion, SavedAnswers


class NewPollForm(forms.Form):
    """A question, two to six answers and how long it runs: the New Poll composer (#925).

    The answers arrive as repeated ``choice`` inputs (the composer's answer pills), so they are
    read from the raw data rather than declared as fields. How long it runs is one of the
    ``RunLength`` chips; ``Poll.post`` still holds every poll to 1 to 60 days.
    """

    question = forms.CharField(
        max_length=QUESTION_MAX_LENGTH,
        label="Question",
        widget=forms.Textarea(
            attrs={
                "rows": 1,
                "class": "pl-poll-compose__question",
                "placeholder": "Ask a question",
                "aria-label": "Question",
            }
        ),
    )
    days = forms.TypedChoiceField(
        choices=RunLength.choices,
        coerce=int,
        initial=DEFAULT_DAYS,
        label="Runs for",
        widget=forms.RadioSelect,
    )
    allows_write_in = forms.BooleanField(
        required=False,
        initial=False,
        label="Let members write their own answer",
        help_text="It counts as their one vote.",
        widget=forms.CheckboxInput(attrs={"x-model": "writeIn"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        data = cast("QueryDict", self.data)
        self.choices: list[str] = [text.strip() for text in data.getlist("choice")] if self.is_bound else []

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if any(not text for text in self.choices):
            self.add_error(None, "Fill in every answer, or remove the empty one.")
        elif len(self.choices) < MIN_CHOICES:
            self.add_error(None, f"A poll needs at least {MIN_CHOICES} answers.")
        elif len(self.choices) > MAX_CHOICES:
            self.add_error(None, f"A poll can have at most {MAX_CHOICES} answers.")
        elif any(len(text) > ANSWER_MAX_LENGTH for text in self.choices):
            self.add_error(None, f"Keep each answer to {ANSWER_MAX_LENGTH} characters or fewer.")
        elif len({text.casefold() for text in self.choices}) < len(self.choices):
            self.add_error(None, "Two answers are the same. Make each one different.")
        return cleaned

    def post(self, *, by: User, now: datetime, guild: Guild | None = None) -> Poll:
        """Open the poll, site wide or for ``guild``; raises ``PollAlreadyOpenError`` when one is open there."""
        return Poll.post(
            question=self.cleaned_data["question"],
            choices=self.choices,
            days=self.cleaned_data["days"],
            by=by,
            now=now,
            guild=guild,
            allows_write_in=self.cleaned_data["allows_write_in"],
        )

    @property
    def selected_days(self) -> int:
        """The chip to show as chosen: what was posted when it is one of the lengths, else a week."""
        value = str(self["days"].value())
        lengths = {str(length.value): length.value for length in RunLength}
        return lengths[value] if value in lengths else DEFAULT_DAYS

    @property
    def lengths(self) -> list[tuple[int, str]]:
        """The Runs for chips: each length in days with its label."""
        return [(length.value, length.label) for length in RunLength]

    @property
    def closes_preview(self) -> str:
        """The draft's "Closes ..." line before any script runs: now plus the chosen length."""
        return closes_label(timezone.now() + timedelta(days=self.selected_days))

    @property
    def rows(self) -> list[str]:
        """The answer boxes to show: what was typed, or two empty ones on a fresh form."""
        return self.choices or [""] * MIN_CHOICES


class WriteInForm(forms.Form):
    """A member's own answer to one poll (#925 part 3), with Post anonymously on by default.

    Prefixed per poll (``poll-<pk>``), so two cards on a page never share an id. ``shown`` is a
    hidden marker the rendered form always carries: a post without it (a hand made request, an
    old page) is read as anonymous, so the member's name is only ever shown when they turned off
    the switch on the form itself.
    """

    write_in = forms.CharField(
        max_length=ANSWER_MAX_LENGTH,
        label="Or write your own answer",
        help_text=f"Up to {ANSWER_MAX_LENGTH} characters. This counts as your one vote.",
    )
    anonymous = forms.BooleanField(required=False, initial=True, label="Post anonymously")
    shown = forms.BooleanField(required=False, initial=True, widget=forms.HiddenInput)

    @classmethod
    def for_poll(cls, poll: Poll, data: QueryDict | None = None) -> WriteInForm:
        """The form for ``poll``, bound to ``data`` when given."""
        return cls(data, prefix=f"poll-{poll.pk}")

    @property
    def is_anonymous(self) -> bool:
        """Post anonymously, unless the member turned the switch off on the rendered form."""
        return self.cleaned_data["anonymous"] or not self.cleaned_data["shown"]


@dataclass(frozen=True)
class ChoiceRow:
    """One option row as the answer page draws it: the value posted, its text, and whether it is ticked."""

    value: str
    text: str
    checked: bool
    is_write_in: bool = False


@dataclass(frozen=True)
class AnswerRow:
    """One question card on the answer page: the question, its field, and its option rows (pick kinds)."""

    number: int
    question: EmailPollQuestion
    field: forms.BoundField
    choices: tuple[ChoiceRow, ...]
    other: forms.BoundField | None = None

    @property
    def other_open(self) -> bool:
        """Whether the write-in's text box starts open: its option is ticked."""
        return any(choice.checked for choice in self.choices if choice.is_write_in)

    @property
    def hint(self) -> str:
        """What the card asks for under the question."""
        return {
            QuestionKind.PICK_ONE: "Pick one.",
            QuestionKind.PICK_ANY: "Pick any that apply.",
            QuestionKind.OPEN: "Optional.",
        }[QuestionKind(self.question.kind)]


class EmailPollAnswerForm(forms.Form):
    """The answer page's form (#1142): one field per question, named ``q<pk>``.

    Pick one is a required choice, Pick any a required multiple choice, Open-ended an optional
    text. The form only reads what was posted; :meth:`polls.models.EmailPoll.answer` decides
    whether it may be saved.
    """

    def __init__(
        self,
        *args: Any,
        questions: list[EmailPollQuestion],
        saved: SavedAnswers | None = None,
        **kwargs: Any,
    ) -> None:
        """Build the fields for ``questions`` (options prefetched); ``saved`` fills in an earlier response.

        A question with a write-in option (#1142 AC13) also gets ``q<pk>_other``, the text typed for it.
        """
        super().__init__(*args, **kwargs)
        self.questions = questions
        for question in questions:
            name = self.field_name(question)
            choices = [(str(option.pk), option.text) for option in question.options.all()]
            if question.kind == QuestionKind.PICK_ONE:
                self.fields[name] = forms.ChoiceField(
                    choices=choices,
                    error_messages={"required": "Pick an answer.", "invalid_choice": "Pick one of the choices."},
                )
            elif question.kind == QuestionKind.PICK_ANY:
                self.fields[name] = forms.MultipleChoiceField(
                    choices=choices,
                    error_messages={"required": "Pick at least one.", "invalid_choice": "Pick from the choices."},
                )
            else:
                self.fields[name] = forms.CharField(
                    required=False, max_length=OPEN_ANSWER_MAX_LENGTH, widget=forms.Textarea(attrs={"rows": 4})
                )
            if saved is not None and question.pk in saved.answers:
                self.initial[name] = _initial(question, saved.answers[question.pk])
            if self._write_in_of(question) is not None:
                self.fields[self.other_name(question)] = forms.CharField(required=False, max_length=ANSWER_MAX_LENGTH)
                if saved is not None and question.pk in saved.write_ins:
                    self.initial[self.other_name(question)] = saved.write_ins[question.pk]

    @staticmethod
    def _write_in_of(question: EmailPollQuestion) -> EmailPollOption | None:
        """The question's write-in option, or None (options prefetched, so no query)."""
        return next((option for option in question.options.all() if option.is_write_in), None)

    @staticmethod
    def other_name(question: EmailPollQuestion) -> str:
        """The posted name of the text typed for ``question``'s write-in option."""
        return f"q{question.pk}_other"

    @property
    def write_ins(self) -> dict[int, str]:
        """The text typed for each write-in, by question pk, as :meth:`polls.models.EmailPoll.answer` takes it."""
        return {
            question.pk: self.cleaned_data[self.other_name(question)]
            for question in self.questions
            if self.other_name(question) in self.fields
        }

    @staticmethod
    def field_name(question: EmailPollQuestion) -> str:
        """The posted name of ``question``'s answer."""
        return f"q{question.pk}"

    @property
    def answers(self) -> dict[int, Given]:
        """The cleaned answers by question pk, as :meth:`polls.models.EmailPoll.answer` takes them."""
        return {question.pk: self.cleaned_data[self.field_name(question)] for question in self.questions}

    @property
    def rows(self) -> list[AnswerRow]:
        """One card per question, with each option row ticked from what was posted or saved."""
        rows = []
        for number, question in enumerate(self.questions, start=1):
            field = self[self.field_name(question)]
            value = field.value()
            picked = {str(item) for item in value} if isinstance(value, list) else {str(value)}
            choices = tuple(
                ChoiceRow(
                    value=str(option.pk),
                    text=option.text,
                    checked=str(option.pk) in picked,
                    is_write_in=option.is_write_in,
                )
                for option in question.options.all()
            )
            other = self[self.other_name(question)] if self.other_name(question) in self.fields else None
            rows.append(AnswerRow(number=number, question=question, field=field, choices=choices, other=other))
        return rows


def _initial(question: EmailPollQuestion, saved: str | list[int]) -> str | list[str]:
    """A saved answer as the field shows it: the text, the one pick, or the picks, as strings."""
    if isinstance(saved, str):
        return saved
    if question.kind == QuestionKind.PICK_ONE:
        return str(saved[0])
    return [str(pk) for pk in saved]
