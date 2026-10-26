"""Forms for Admin Tools > Spotlight (#708): the Spotlight messages (#889) that crawl across the top of Home."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.utils import timezone

from core.html_sanitize import ANNOUNCEMENT_MAX_CHARS, limit_rich_text, rich_html_to_text, sanitize_announcement_html
from core.models import Announcement
from core.widgets import InlineRichTextWidget
from hub.forms import _meeting_time_label, half_hour_time_choices

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import QueryDict

ANNOUNCEMENT_EMPTY = "Write the Spotlight message first."
ANNOUNCEMENT_ENDS_FIRST = "Show until must be after Show from."
ANNOUNCEMENT_TIME_NEEDS_DATE = "Pick a date for this time."
ANNOUNCEMENT_ORDER_UNREADABLE = "That order could not be read. Reload the page and try again."
# A blank time is the start of the day for Show from and its end for Show until.
_FROM_TIME_CHOICES = [("", "Start of day"), *half_hour_time_choices(required=True)]
_UNTIL_TIME_CHOICES = [("", "End of day"), *half_hour_time_choices(required=True)]


def announcement_too_long(count: int) -> str:
    """The refusal for an announcement over the limit, naming its length."""
    return f"Keep it to {ANNOUNCEMENT_MAX_CHARS} characters (this one is {count})."


def _date_input() -> forms.DateInput:
    # The whole box opens the picker (FRONTEND rule 14), and the class inverts its icon on the dark theme.
    return forms.DateInput(
        format="%Y-%m-%d",
        attrs={
            "type": "date",
            "class": "pl-ann-admin__date",
            "@click": "(() => { try { $el.showPicker() } catch (e) {} })()",
        },
    )


class AnnouncementForm(forms.ModelForm):
    """Add or edit a Spotlight message (#889): the text, optional Show from and Show until, and Active.

    Each date is a date box and a half hour select (FRONTEND rule 20). A blank time starts Show
    from at the start of its day and ends Show until at the end of its day. The text is sanitized
    to bold, italic and links, then held to :data:`ANNOUNCEMENT_MAX_CHARS` visible characters.
    """

    show_from_date = forms.DateField(required=False, label="Show from", widget=_date_input())
    show_from_time = forms.ChoiceField(required=False, label="Time", choices=_FROM_TIME_CHOICES)
    show_until_date = forms.DateField(required=False, label="Show until", widget=_date_input())
    show_until_time = forms.ChoiceField(required=False, label="Time", choices=_UNTIL_TIME_CHOICES)

    class Meta:
        model = Announcement
        fields = ["body", "is_active"]
        widgets = {"body": InlineRichTextWidget()}
        labels = {"body": "Spotlight message", "is_active": "Active"}
        help_texts = {
            "body": f"Up to {ANNOUNCEMENT_MAX_CHARS} characters. Bold, italic and links.",
            "is_active": "Off hides it from Home whatever its dates say.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._seed("show_from", self.instance.show_from, time.min)
        self._seed("show_until", self.instance.show_until, time.max)

    def _seed(self, name: str, value: datetime | None, blank_time: time) -> None:
        """Open a saved date and time in its two boxes, keeping a time off the half hour grid as its own option."""
        if value is None:
            return
        local = timezone.localtime(value)
        self.initial[f"{name}_date"] = local.date()
        moment = local.time()
        if moment == blank_time:
            return
        key = moment.strftime("%H:%M")
        field = cast(forms.ChoiceField, self.fields[f"{name}_time"])
        choices = cast("list[tuple[str, str]]", field.choices)
        if key not in {value for value, _ in choices}:
            field.choices = [*choices, (key, _meeting_time_label(moment.hour, moment.minute))]
        self.initial[f"{name}_time"] = key

    def clean_body(self) -> str:
        html = sanitize_announcement_html(limit_rich_text(self.cleaned_data["body"]))
        count = len(rich_html_to_text(html))
        if not count:
            raise forms.ValidationError(ANNOUNCEMENT_EMPTY)
        if count > ANNOUNCEMENT_MAX_CHARS:
            raise forms.ValidationError(announcement_too_long(count))
        return html

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        show_from = self._moment(cleaned, "show_from", time.min)
        show_until = self._moment(cleaned, "show_until", time.max)
        if show_from is not None and show_until is not None and show_until <= show_from:
            self.add_error("show_until_date", ANNOUNCEMENT_ENDS_FIRST)
        cleaned["show_from"] = show_from
        cleaned["show_until"] = show_until
        return cleaned

    def _moment(self, cleaned: dict[str, Any], name: str, blank_time: time) -> datetime | None:
        """One date and time as an aware Portland datetime, None with no date, or an error for a time alone."""
        # ``get``: a box that failed its own check is absent from cleaned_data and already carries its error.
        day: date | None = cleaned.get(f"{name}_date")
        picked: str = cleaned.get(f"{name}_time", "")
        if day is None:
            if picked and f"{name}_date" not in self.errors:
                self.add_error(f"{name}_date", ANNOUNCEMENT_TIME_NEEDS_DATE)
            return None
        moment = time.fromisoformat(picked) if picked else blank_time
        return timezone.make_aware(datetime.combine(day, moment))

    def save_by(self, user: User) -> Announcement:
        """Save, with the dates, naming ``user`` as the author of a new one."""
        announcement: Announcement = self.save(commit=False)
        announcement.show_from = self.cleaned_data["show_from"]
        announcement.show_until = self.cleaned_data["show_until"]
        if announcement.pk is None:
            announcement.created_by = user
        announcement.save()
        return announcement


class AnnouncementActiveForm(forms.Form):
    """One row's on and off switch, saved as soon as it is flipped. Prefixed by the row, so ids stay unique."""

    is_active = forms.BooleanField(required=False, label="Show on Home")

    @classmethod
    def for_row(cls, announcement: Announcement, data: QueryDict | None = None) -> AnnouncementActiveForm:
        """The switch for ``announcement``, showing its saved state, or bound to ``data``."""
        return cls(data, prefix=f"announcement-{announcement.pk}", initial={"is_active": announcement.is_active})


class AnnouncementOrderForm(forms.Form):
    """The new order of every announcement, as ids in a comma separated list."""

    order = forms.CharField(error_messages={"required": ANNOUNCEMENT_ORDER_UNREADABLE})

    def clean_order(self) -> list[int]:
        try:
            return [int(pk) for pk in self.cleaned_data["order"].split(",")]
        except ValueError as exc:
            raise forms.ValidationError(ANNOUNCEMENT_ORDER_UNREADABLE) from exc
