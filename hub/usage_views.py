"""The admin Usage page at /manage/usage/ (#1078).

Admin only, and view-as aware like its Admin Tools card and the Growth page: an admin previewing
as a member is refused, as a member would be. A GET page: the range and role are the query string,
so every view of it is a link. Counts only; no part of the page names a person. The rows come
from :class:`core.models.PageView` (kept 90 days, which is why 90 is the longest range) and
:meth:`core.models.SiteActivity.weekly_counts`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from django import forms
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from core.models import ActivityWeeks, PageView, SiteActivity
from hub.views import _get_hub_context, _require_admin

#: The ranges the page offers, in days, with their chip labels. 90 is the raw log's retention.
RANGES: tuple[tuple[str, str], ...] = (("7", "Last 7 Days"), ("30", "Last 30 Days"), ("90", "Last 90 Days"))
DEFAULT_RANGE = "30"

#: The role chips: every role, then each one alone.
ROLE_CHOICES: tuple[tuple[str, str], ...] = (("", "All Roles"), *PageView.Role.choices)

#: How many pages the Pages card lists, most viewed first.
TOP_PAGES_COUNT = 15

#: How many pages the Least Used card lists. It shows only when more pages were visited than the
#: Pages card lists, so the two never repeat each other.
LEAST_USED_COUNT = 8


class UsageForm(forms.Form):
    """The Usage page's GET controls; anything invalid falls back to the last 30 days and every role."""

    range = forms.ChoiceField(choices=RANGES, required=False)  # noqa: A003 (the query string's name)
    role = forms.ChoiceField(choices=ROLE_CHOICES, required=False)

    def selection(self) -> tuple[str, str]:
        """The range key and role to show: the valid values asked for, else the defaults."""
        if not self.is_valid():
            return DEFAULT_RANGE, ""
        return self.cleaned_data["range"] or DEFAULT_RANGE, self.cleaned_data["role"]


@dataclass(frozen=True)
class PageRow:
    """One page's counts on the Usage page, with its readable name and its view name."""

    label: str
    view_name: str
    views: int
    people: int


def _page_rows(rows: list[dict[str, Any]]) -> list[PageRow]:
    return [
        PageRow(
            label=PageView.label_for(row["view_name"]),
            view_name=row["view_name"],
            views=row["views"],
            people=row["people"],
        )
        for row in rows
    ]


@dataclass(frozen=True)
class UsageBoard:
    """Everything the Usage page shows for one range and role."""

    views: int
    people: int
    pages: list[PageRow]
    page_count: int
    least_used: list[PageRow]
    activity: ActivityWeeks


def usage_board(days: int, role: str) -> UsageBoard:
    """The page views and activity counts for the last ``days`` days, page views filtered to ``role``."""
    start = timezone.now() - timedelta(days=days)
    views = PageView.objects.since(start).for_role(role)
    totals = views.aggregate(views=Count("id"), people=Count("user", distinct=True))
    ranked = list(views.by_page())
    least_used = list(reversed(ranked[-LEAST_USED_COUNT:])) if len(ranked) > TOP_PAGES_COUNT else []
    least_used.sort(key=lambda row: (row["views"], row["view_name"]))
    return UsageBoard(
        views=totals["views"],
        people=totals["people"],
        pages=_page_rows(ranked[:TOP_PAGES_COUNT]),
        page_count=len(ranked),
        least_used=_page_rows(least_used),
        activity=SiteActivity.weekly_counts(start),
    )


@login_required
def hub_admin_usage(request: HttpRequest) -> HttpResponse:
    """The Usage page for the range and role the query string asks for."""
    refused = _require_admin(request)
    if refused is not None:
        return refused
    range_key, role = UsageForm(request.GET).selection()
    return render(
        request,
        "hub/admin/usage.html",
        {
            **_get_hub_context(request),
            "board": usage_board(int(range_key), role),
            "range_key": range_key,
            "role": role,
            "range_label": dict(RANGES)[range_key],
            "role_label": dict(ROLE_CHOICES)[role],
            "range_chips": [(key, label, urlencode({"range": key, "role": role})) for key, label in RANGES],
            "role_chips": [(key, label, urlencode({"range": range_key, "role": key})) for key, label in ROLE_CHOICES],
        },
    )
