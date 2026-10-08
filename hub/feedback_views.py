"""The admin Feedback inbox at /manage/feedback/ (#693).

Every request sent from the Feedback page, filterable by category and status, and a page per
request where an admin sets its status, writes a note to the sender and keeps the GitHub issue
link. All admin only (``@fog_admin_required``); saves are plain POSTs that redirect back with a
message, which the hub shows as a toast. The inbox's query string rides along to the request
page and back, so Back to inbox keeps the filters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from classes.table import prepare_table
from core.models import FeedbackRequest
from hub.forms import FeedbackRequestAdminForm
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context

if TYPE_CHECKING:
    from django.contrib.auth.models import User

_SORTABLE = frozenset({"created_at", "subject", "category", "status"})


def _saved_message(feedback_request: FeedbackRequest, notified: bool) -> str:
    """The toast after a save: who heard about it, if anyone."""
    if notified:
        return f"Saved. {feedback_request.sender_label} was notified."
    return "Saved."


def _request_url(feedback_request: FeedbackRequest, query: str) -> str:
    """This request's admin page, keeping the inbox's filters in the query string."""
    url = reverse("hub_admin_feedback_request", args=[feedback_request.pk])
    return f"{url}?{query}" if query else url


@fog_admin_required
def hub_admin_feedback_inbox(request: HttpRequest) -> HttpResponse:
    """Every feedback request, newest first, filterable by category and status."""
    category = request.GET.get("category", "")
    status = request.GET.get("status", "")
    rows = FeedbackRequest.objects.select_related("user__member")
    if category in FeedbackRequest.Category.values:
        rows = rows.filter(category=category)
    if status in FeedbackRequest.Status.values:
        rows = rows.filter(status=status)
    table = prepare_table(
        request,
        rows,
        search_fields=[],
        default_sort="created_at",
        default_dir="desc",
        sortable=_SORTABLE,
    )
    return render(
        request,
        "hub/admin/feedback_inbox.html",
        {
            **_get_hub_context(request),
            "page": table["page"],
            "sort": table["sort"],
            "sort_dir": table["sort_dir"],
            "base_params": table["base_params"],
            "filters": {"category": category, "status": status},
            "categories": FeedbackRequest.Category.choices,
            "statuses": FeedbackRequest.Status.choices,
            "is_filtered": bool(category or status),
            "inbox_query": request.GET.urlencode(),
        },
    )


@fog_admin_required
def hub_admin_feedback_request(request: HttpRequest, pk: int) -> HttpResponse:
    """One request: its message, photos and sender, and the admin's status, note and link."""
    feedback_request = get_object_or_404(
        FeedbackRequest.objects.select_related("user__member").prefetch_related("photos"), pk=pk
    )
    query = request.GET.urlencode()
    form = FeedbackRequestAdminForm.for_request(feedback_request, request.POST or None)
    if request.method == "POST" and form.is_valid():
        notified = feedback_request.apply_admin_update(
            status=form.cleaned_data["status"],
            staff_note=form.cleaned_data["staff_note"],
            github_issue_url=form.cleaned_data["github_issue_url"],
            actor=cast("User", request.user),
        )
        messages.success(request, _saved_message(feedback_request, notified))
        return redirect(_request_url(feedback_request, query))
    return render(
        request,
        "hub/admin/feedback_request.html",
        {
            **_get_hub_context(request),
            "feedback_request": feedback_request,
            "sender_member": getattr(feedback_request.user, "member", None),
            "form": form,
            "inbox_query": query,
        },
    )


@fog_admin_required
@require_POST
def hub_admin_feedback_mark_live(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark a request Live (Fixed for a bug) in one click, for anything shipped without a fragment."""
    feedback_request = get_object_or_404(FeedbackRequest.objects.select_related("user__member"), pk=pk)
    notified = feedback_request.mark_live(actor=cast("User", request.user))
    messages.success(request, _saved_message(feedback_request, notified))
    return redirect(_request_url(feedback_request, request.GET.urlencode()))
