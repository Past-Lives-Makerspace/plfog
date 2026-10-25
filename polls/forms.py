"""The New Poll form (#708, back in #925): a question, two to six answers and how many days it runs.

One form for Admin Tools > Polls (site wide) and a guild's Polls tab.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.utils import timezone

from polls.models import (
    ANSWER_MAX_LENGTH,
    DEFAULT_DAYS,
    MAX_CHOICES,
    MIN_CHOICES,
    QUESTION_MAX_LENGTH,
    Poll,
    RunLength,
    closes_label,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import QueryDict

    from membership.models import Guild


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
