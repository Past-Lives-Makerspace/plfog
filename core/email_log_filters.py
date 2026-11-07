"""The Email Log tab's filters under Manage > Site Activity (#1112).

Kept apart from ``core.views.site_activity`` so the Activity Feed's own filters (#1114)
can sit beside it without the view body growing. The form reads the tab's GET params,
narrows the log, and words the count line and the empty line for what was searched.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from django import forms
from django.db.models import Count, QuerySet
from django.utils import timezone

from core.events.registry import get_event
from core.models import TransactionalEmailLog


def type_label(trigger_kind: str) -> str:
    """The plain words for an email type: the event registry's label, else the raw key.

    Several senders log a ``trigger_kind`` the registry never declared (``billing.receipt``),
    and those have no better name than their key.
    """
    try:
        return get_event(trigger_kind).label
    except KeyError:
        return trigger_kind


def type_choices() -> list[tuple[str, str]]:
    """Every email type that appears in the log, as (key, plain label), sorted by label."""
    kinds = TransactionalEmailLog.objects.order_by().values_list("trigger_kind", flat=True).distinct()
    return sorted(((kind, type_label(kind)) for kind in kinds), key=lambda choice: choice[1].casefold())


def start_of_pacific_day(day: date) -> datetime:
    """Midnight starting ``day`` in the site's time zone (Pacific)."""
    return timezone.make_aware(datetime.combine(day, time.min))


class EmailLogFilterForm(forms.Form):
    """The Email Log's filter row: recipient, type, status and a Pacific date range.

    Every field is optional and judged on its own: a malformed date typed into the URL
    drops that one bound rather than the whole search.
    """

    # No max_length: an over-long value would fail validation and be dropped, showing the
    # whole log as if it were the answer. A long search simply matches nothing.
    recipient = forms.CharField(required=False)
    type = forms.CharField(required=False)
    status = forms.ChoiceField(required=False, choices=[("", "All"), *TransactionalEmailLog.Status.choices])
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)

    def __init__(self, data: Mapping[str, Any]) -> None:
        super().__init__(data)
        # Cleaned at once: the view only ever reads the result, and a field that fails
        # validation is simply absent from cleaned_data, so it filters nothing.
        self.is_valid()

    def _text(self, name: str) -> str:
        return str(self.cleaned_data.get(name) or "")

    def _date(self, name: str) -> date | None:
        value = self.cleaned_data.get(name)
        return value if isinstance(value, date) else None

    @property
    def recipient_value(self) -> str:
        """The recipient searched for, stripped, or blank."""
        return self._text("recipient")

    @property
    def has_other_filters(self) -> bool:
        """Whether a type, status or date bound narrows the log besides the recipient."""
        return bool(self._text("type") or self._text("status") or self._date("date_from") or self._date("date_to"))

    def filter(self, emails: QuerySet[TransactionalEmailLog]) -> QuerySet[TransactionalEmailLog]:
        """Narrow ``emails`` to the rows every filled field allows.

        The dates bound the send time by Pacific calendar day, both ends inclusive.
        """
        if recipient := self.recipient_value:
            emails = emails.filter(to_email__icontains=recipient)
        if trigger_kind := self._text("type"):
            emails = emails.filter(trigger_kind=trigger_kind)
        if status := self._text("status"):
            emails = emails.filter(status=status)
        if date_from := self._date("date_from"):
            emails = emails.filter(created_at__gte=start_of_pacific_day(date_from))
        # The last representable day has no next midnight; every row is before it anyway.
        if (date_to := self._date("date_to")) and date_to < date.max:
            emails = emails.filter(created_at__lt=start_of_pacific_day(date_to + timedelta(days=1)))
        return emails

    def empty_message(self) -> str:
        """The line shown when the filtered log has no rows, naming the address searched."""
        if self.recipient_value and self.has_other_filters:
            return f"No emails to {self.recipient_value} match these filters."
        if self.recipient_value:
            return f"No emails to {self.recipient_value} have been logged."
        if self.has_other_filters:
            return "No emails match these filters."
        return "No emails logged yet."


def count_line(emails: QuerySet[TransactionalEmailLog]) -> str:
    """Count the filtered set by outcome: "12 emails: 11 sent, 1 failed".

    Only the outcomes present are named, in the order the model declares them. Blank when
    the set is empty, since the empty line already says so.
    """
    counts = dict(emails.order_by().values("status").annotate(n=Count("id")).values_list("status", "n"))
    total = sum(counts.values())
    if not total:
        return ""
    parts = [
        f"{counts[status]} {label.lower()}"
        for status, label in TransactionalEmailLog.Status.choices
        if status in counts
    ]
    noun = "email" if total == 1 else "emails"
    return f"{total} {noun}: {', '.join(parts)}"
