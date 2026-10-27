"""The admin Feedback inbox at /manage/feedback/ (#693).

Every request sent from the Feedback page, filterable by category, status and Member replied,
and a page per request where an admin reads and answers its comment thread, sets its status and
keeps the GitHub issue link. Opening that page marks the request seen, which clears its Member
replied pill. All admin only (``@fog_admin_required``); saves are plain POSTs that redirect back
with a message, which the hub shows as a toast. The inbox's query string rides along to the
request page and back, so Back to inbox keeps the filters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from classes.table import prepare_table
from core.models import FeedbackRequest, FeedbackRequestError
from hub.forms import FeedbackCommentForm, FeedbackRequestAdminForm, FeedbackResolveForm
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context

if TYPE_CHECKING:
    from django.contrib.auth.models import User

_SORTABLE = frozenset({"created_at", "subject", "category", "status"})


def _told(feedback_request: FeedbackRequest, actor: User, notified: bool) -> str:
    """Who heard about what an admin just did, as the toast's second sentence, or nothing.

    The sender, unless the admin is the sender: then their comment went to the other Webmasters.
    """
    if not notified:
        return ""
    if actor.pk == feedback_request.user_id:
        return " The Webmasters were notified."
    return f" {feedback_request.sender_label} was notified."


def _saved_message(feedback_request: FeedbackRequest, actor: User, notified: bool) -> str:
    """The toast after a save: who heard about it, if anyone."""
    return "Saved." + _told(feedback_request, actor, notified)


def _request_url(feedback_request: FeedbackRequest, query: str) -> str:
    """This request's admin page, keeping the inbox's filters in the query string."""
    url = reverse("hub_admin_feedback_request", args=[feedback_request.pk])
    return f"{url}?{query}" if query else url


@fog_admin_required
def hub_admin_feedback_inbox(request: HttpRequest) -> HttpResponse:
    """Every feedback request, newest first, filterable by category and status."""
    category = request.GET.get("category", "")
    status = request.GET.get("status", "")
    replied = request.GET.get("replied", "") == "1"
    rows = FeedbackRequest.objects.select_related("user__member")
    if category in FeedbackRequest.Category.values:
        rows = rows.filter(category=category)
    if status in FeedbackRequest.Status.values:
        rows = rows.filter(status=status)
    elif status == "closed_all":
        rows = rows.closed()
    elif status != "all":
        status = ""  # the default view, and what any unknown value falls back to
        rows = rows.open()
    if replied:
        rows = rows.member_replied()
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
            "filters": {"category": category, "status": status, "replied": replied},
            "categories": FeedbackRequest.Category.choices,
            "statuses": FeedbackRequest.Status.choices,
            "views": [("", "Open"), ("closed_all", "All closed"), ("all", "All statuses")],
            "is_filtered": bool(category or status or replied),
            "inbox_query": request.GET.urlencode(),
        },
    )


def _request_page(
    request: HttpRequest,
    feedback_request: FeedbackRequest,
    *,
    form: FeedbackRequestAdminForm | None = None,
    comment_form: FeedbackCommentForm | None = None,
) -> HttpResponse:
    """Render a request's admin page, with either form bound when its post was refused."""
    return render(
        request,
        "hub/admin/feedback_request.html",
        {
            **_get_hub_context(request),
            "feedback_request": feedback_request,
            "sender_member": getattr(feedback_request.user, "member", None),
            "form": form or FeedbackRequestAdminForm(feedback_request=feedback_request),
            "comment_form": comment_form or FeedbackCommentForm(),
            "not_a_bug_form": FeedbackResolveForm.for_not_a_bug(),
            "close_form": FeedbackResolveForm.for_close(),
            "inbox_query": request.GET.urlencode(),
        },
    )


def _get_request(pk: int) -> FeedbackRequest:
    """The request with its sender and whole thread loaded, or a 404."""
    return get_object_or_404(FeedbackRequest.objects.select_related("user__member").with_thread(), pk=pk)


@fog_admin_required
def hub_admin_feedback_request(request: HttpRequest, pk: int) -> HttpResponse:
    """One request: its sender, thread and comment box, and the admin's status, comment and link.

    Opening it marks the request seen by staff, which clears the inbox's Member replied pill.
    """
    feedback_request = _get_request(pk)
    feedback_request.mark_seen_by_staff()
    if request.method != "POST":
        return _request_page(request, feedback_request)
    form = FeedbackRequestAdminForm(request.POST, feedback_request=feedback_request)
    if not form.is_valid():
        return _request_page(request, feedback_request, form=form)
    notified = feedback_request.apply_admin_update(
        status=form.cleaned_data["status"],
        comment=form.cleaned_data["comment"],
        github_issue_url=form.cleaned_data["github_issue_url"],
        actor=cast("User", request.user),
    )
    messages.success(request, _saved_message(feedback_request, cast("User", request.user), notified))
    return redirect(_request_url(feedback_request, request.GET.urlencode()))


@fog_admin_required
@require_POST
def hub_admin_feedback_comment(request: HttpRequest, pk: int) -> HttpResponse:
    """Post an admin's comment, with photos, on a request's thread; the sender is always told."""
    feedback_request = _get_request(pk)
    comment_form = FeedbackCommentForm(request.POST, request.FILES)
    if not comment_form.is_valid():
        return _request_page(request, feedback_request, comment_form=comment_form)
    author = cast("User", request.user)
    comment = feedback_request.add_comment(
        author=author, body=comment_form.cleaned_data["body"], photos=comment_form.cleaned_data["photos"]
    )
    messages.success(request, "Sent." + _told(feedback_request, author, comment.notified))
    return redirect(_request_url(feedback_request, request.GET.urlencode()))


@fog_admin_required
@require_POST
def hub_admin_feedback_mark_live(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark a request Live (Fixed for a bug) in one click, for anything shipped without a fragment.

    General feedback stays Received, so it is refused with the model's message.
    """
    feedback_request = get_object_or_404(FeedbackRequest.objects.select_related("user__member"), pk=pk)
    try:
        notified = feedback_request.mark_live(actor=cast("User", request.user))
    except FeedbackRequestError as refusal:
        messages.error(request, str(refusal))
    else:
        messages.success(request, _saved_message(feedback_request, cast("User", request.user), notified))
    return redirect(_request_url(feedback_request, request.GET.urlencode()))


def _resolve(request: HttpRequest, pk: int, *, not_a_bug: bool) -> HttpResponse:
    """Post Not a bug or Close with its comment in one save and one notice; a refusal is a message."""
    feedback_request = get_object_or_404(FeedbackRequest.objects.select_related("user__member"), pk=pk)
    actor = cast("User", request.user)
    form = FeedbackResolveForm(request.POST)
    form.is_valid()  # one optional text field: it always cleans
    comment = form.cleaned_data["comment"]
    try:
        if not_a_bug:
            notified = feedback_request.mark_not_a_bug(actor=actor, comment=comment)
        else:
            notified = feedback_request.close(actor=actor, comment=comment)
    except FeedbackRequestError as refusal:
        messages.error(request, str(refusal))
    else:
        messages.success(request, _saved_message(feedback_request, actor, notified))
    return redirect(_request_url(feedback_request, request.GET.urlencode()))


@fog_admin_required
@require_POST
def hub_admin_feedback_not_a_bug(request: HttpRequest, pk: int) -> HttpResponse:
    """Close a bug report as Not a bug with the required comment telling the sender the right way."""
    return _resolve(request, pk, not_a_bug=True)


@fog_admin_required
@require_POST
def hub_admin_feedback_close(request: HttpRequest, pk: int) -> HttpResponse:
    """Close any request, general feedback included, with an optional comment."""
    return _resolve(request, pk, not_a_bug=False)
