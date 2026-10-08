"""Forms for Admin Tools > Spotlight (#708): the Spotlight text and meeting, and a new poll."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.db.models import Q
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import CommunityEvent
from polls.models import (
    ANSWER_MAX_LENGTH,
    DEFAULT_DAYS,
    MAX_CHOICES,
    MAX_DAYS,
    MIN_CHOICES,
    MIN_DAYS,
    QUESTION_MAX_LENGTH,
    Poll,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import QueryDict

_LINE_FIELDS = ("spotlight_first_line", "spotlight_second_line")


class MeetingEventChoiceField(forms.ModelChoiceField):
    """The meeting select, labelled without ``CommunityEvent.__str__``, which reads each row's guild."""

    def label_from_instance(self, obj: CommunityEvent) -> str:
        """The title, its first date and how it repeats: "Feature Request Meeting (from Sep 8, 2026, every month)"."""
        start = timezone.localtime(obj.starts_at)
        repeats = "" if obj.recurrence == obj.Recurrence.NONE else f", {obj.get_recurrence_display().lower()}"
        return f"{obj.title} (from {start:%b} {start.day}, {start.year}{repeats})"


class SpotlightTextForm(forms.ModelForm):
    """The Feature Request Meeting and the two minimized lines."""

    class Meta:
        model = SiteConfiguration
        fields = ["spotlight_meeting_event", "spotlight_first_line", "spotlight_second_line"]
        field_classes = {"spotlight_meeting_event": MeetingEventChoiceField}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = cast(forms.ModelChoiceField, self.fields["spotlight_meeting_event"])
        upcoming = CommunityEvent.objects.published().upcoming().values("pk")
        picked = Q(pk=self.instance.spotlight_meeting_event_id) if self.instance.spotlight_meeting_event_id else Q()
        field.queryset = CommunityEvent.objects.filter(Q(pk__in=upcoming) | picked).order_by("title", "starts_at")
        field.empty_label = "No meeting"

    def save_at(self, now: datetime) -> SiteConfiguration:
        """Save, stamping the text-changed time only when a line really changed.

        Members see the Spotlight's dot until they open it after that time (#709).
        """
        config: SiteConfiguration = self.save(commit=False)
        fields = ["spotlight_meeting_event", *_LINE_FIELDS]
        if any(name in self.changed_data for name in _LINE_FIELDS):
            config.spotlight_text_changed_at = now
            fields.append("spotlight_text_changed_at")
        # Only these columns: a whole-row save would revert another admin's concurrent edit
        # to any other setting.
        config.save(update_fields=fields)
        return config


class NewPollForm(forms.Form):
    """A question, two to six answers and how many days it stays open.

    The answers arrive as repeated ``choice`` inputs (the page's + Add choice list), so they
    are read from the raw data rather than declared as fields.
    """

    question = forms.CharField(max_length=QUESTION_MAX_LENGTH, label="Question")
    days = forms.IntegerField(
        min_value=MIN_DAYS,
        max_value=MAX_DAYS,
        initial=DEFAULT_DAYS,
        label="Expires after",
        help_text=f"Days, {MIN_DAYS} to {MAX_DAYS}. Posting opens the poll now.",
    )
    close_current = forms.BooleanField(required=False, widget=forms.HiddenInput)

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

    def post(self, *, by: User, now: datetime) -> Poll:
        """Open the poll; raises ``PollAlreadyOpenError`` when one is open and not asked to close."""
        return Poll.post(
            question=self.cleaned_data["question"],
            choices=self.choices,
            days=self.cleaned_data["days"],
            by=by,
            now=now,
            close_current=self.cleaned_data["close_current"],
        )

    @property
    def rows(self) -> list[str]:
        """The answer boxes to show: what was typed, or two empty ones on a fresh form."""
        return self.choices or [""] * MIN_CHOICES
