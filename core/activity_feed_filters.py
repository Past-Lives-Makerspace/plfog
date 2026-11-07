"""The Activity Feed tab's filters under Manage > Site Activity (#1114).

The sibling of ``core.email_log_filters`` (#1112): the form reads the tab's GET params, narrows
the feed, and words the count line and the empty line for what was searched, so
``core.views.site_activity`` only wires it up.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from django import forms
from django.db.models import Q, QuerySet

from core.email_log_filters import start_of_pacific_day
from core.models import SITE_ACTIVITY_CATEGORIES, SiteActivity

EventGroup = tuple[str, list[tuple[str, str]]]


class ActivityFeedFilterForm(forms.Form):
    """The Activity Feed's filter row: category, event, person and a Pacific date range.

    Every field is optional and judged on its own: a malformed date typed into the URL drops
    that one bound rather than the whole search. ``actor`` is the person box's old name, still
    read so links made before #1114 keep working.
    """

    category = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All categories"),
            *((slug, label) for slug, (label, _kinds) in SITE_ACTIVITY_CATEGORIES.items()),
        ],
    )
    # No max_length on either: an over-long value would fail validation and be dropped,
    # showing the whole feed as if it were the answer. A long value simply matches nothing.
    kind = forms.CharField(required=False)
    person = forms.CharField(required=False)
    # Not date_from/date_to: the Email Log's form reads those from the same page's GET, and
    # every tab renders at once, so a shared name would let one tab's dates empty the other.
    feed_from = forms.DateField(required=False)
    feed_to = forms.DateField(required=False)

    def __init__(self, data: Mapping[str, Any]) -> None:
        # items() gives a QueryDict's last value per key, as reading one key would.
        values = dict(data.items())
        if "person" not in values and "actor" in values:
            values["person"] = values["actor"]
        super().__init__(values)
        # Cleaned at once: the view only ever reads the result, and a field that fails
        # validation is simply absent from cleaned_data, so it filters nothing.
        self.is_valid()

    def _text(self, name: str) -> str:
        return str(self.cleaned_data.get(name) or "")

    def _date(self, name: str) -> date | None:
        value = self.cleaned_data.get(name)
        return value if isinstance(value, date) else None

    @property
    def category_value(self) -> str:
        """The chosen category's slug, or blank."""
        return self._text("category")

    @property
    def kind_value(self) -> str:
        """The chosen event, or blank when none was chosen or it lies outside the chosen category."""
        kind = self._text("kind")
        if category := self.category_value:
            _label, kinds = SITE_ACTIVITY_CATEGORIES[category]
            if kind not in kinds:
                return ""
        return kind

    @property
    def person_value(self) -> str:
        """The person searched for, stripped, or blank."""
        return self._text("person")

    @property
    def has_other_filters(self) -> bool:
        """Whether a category, event or date bound narrows the feed besides the person."""
        return bool(self.category_value or self.kind_value or self._date("feed_from") or self._date("feed_to"))

    def event_groups(self) -> list[EventGroup]:
        """The Event select's options under their category: only the chosen category's, when there is one."""
        return [
            (label, [(kind.value, kind.label) for kind in kinds])
            for slug, (label, kinds) in SITE_ACTIVITY_CATEGORIES.items()
            if self.category_value in ("", slug)
        ]

    def filter(self, activities: QuerySet[SiteActivity]) -> QuerySet[SiteActivity]:
        """Narrow ``activities`` to the rows every filled field allows.

        The person matches the actor's email or their member's preferred or legal name. The
        dates bound the row's time by Pacific calendar day, both ends inclusive.
        """
        if category := self.category_value:
            _label, kinds = SITE_ACTIVITY_CATEGORIES[category]
            activities = activities.filter(kind__in=kinds)
        if kind := self.kind_value:
            activities = activities.filter(kind=kind)
        if person := self.person_value:
            activities = activities.filter(
                Q(actor__email__icontains=person)
                | Q(actor__member__preferred_name__icontains=person)
                | Q(actor__member__full_legal_name__icontains=person)
            )
        if feed_from := self._date("feed_from"):
            activities = activities.filter(created_at__gte=start_of_pacific_day(feed_from))
        # The last representable day has no next midnight; every row is before it anyway.
        if (feed_to := self._date("feed_to")) and feed_to < date.max:
            activities = activities.filter(created_at__lt=start_of_pacific_day(feed_to + timedelta(days=1)))
        return activities

    def empty_message(self) -> str:
        """The line shown when the filtered feed has no rows, naming the person searched."""
        if self.person_value and self.has_other_filters:
            return f"No activity by {self.person_value} matches these filters."
        if self.person_value:
            return f"No activity by {self.person_value} yet."
        if self.has_other_filters:
            return "No activity matches these filters."
        return "No activity yet."


def count_line(total: int) -> str:
    """Count the filtered feed: "124 events". Blank when empty, since the empty line already says so."""
    if not total:
        return ""
    return f"{total} event" if total == 1 else f"{total} events"
