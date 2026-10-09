"""Admin Tools > Spotlight at /manage/spotlight/ (#708).

One page: the Spotlight text and meeting with a live preview, the open poll with Close now,
a New poll form and the Past polls table. Every view is admin only (``@fog_admin_required``);
saves are plain POSTs that redirect back with a message, shown as a toast. Nothing here sends
an email, push, Discord post or bell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from classes.table import prepare_table
from core.models import SiteConfiguration
from hub.spotlight import SECOND_LINE_DEFAULT, Spotlight, SpotlightMeeting
from hub.spotlight_forms import NewPollForm, SpotlightTextForm
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context
from polls.models import ANSWER_MAX_LENGTH, MAX_CHOICES, Poll, PollAlreadyOpenError

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.auth.models import User

_SORTABLE = frozenset({"question", "opens_at", "closes_at", "total_votes", "top_answer"})


def _meeting_previews(form: SpotlightTextForm, now: datetime) -> dict[str, dict[str, str | bool] | None]:
    """Each offered meeting's next date, keyed by pk, so the preview follows the select."""
    field = form.fields["spotlight_meeting_event"]
    previews: dict[str, dict[str, str | bool] | None] = {}
    for event in field.queryset:  # type: ignore[attr-defined]
        meeting = SpotlightMeeting.next_for(event, now)
        previews[str(event.pk)] = (
            None
            if meeting is None
            else {
                "title": meeting.title,
                "when": meeting.when,
                "pill": meeting.pill,
                "virtual": bool(meeting.video_url),
            }
        )
    return previews


def _render_page(
    request: HttpRequest,
    *,
    text_form: SpotlightTextForm | None = None,
    poll_form: NewPollForm | None = None,
    status: int = 200,
) -> HttpResponse:
    now = timezone.now()
    # Read as nobody for the preview and the poll card. It must not be called "spotlight":
    # that key is the context processor's Spotlight for the signed-in admin (#709).
    admin_spotlight = Spotlight.load(None, now)
    text_form = text_form or SpotlightTextForm(instance=SiteConfiguration.load())
    table = prepare_table(
        request,
        Poll.objects.closed_at(now).with_totals(),
        search_fields=[],
        default_sort="opens_at",
        default_dir="desc",
        sortable=_SORTABLE,
    )
    context: dict[str, Any] = {
        **_get_hub_context(request),
        "admin_spotlight": admin_spotlight,
        "text_form": text_form,
        "poll_form": poll_form or NewPollForm(),
        "max_choices": MAX_CHOICES,
        "answer_max_length": ANSWER_MAX_LENGTH,
        "second_line_default": SECOND_LINE_DEFAULT,
        "spotlight_preview": {
            "first": text_form["spotlight_first_line"].value() or "",
            "second": text_form["spotlight_second_line"].value() or "",
            "meeting": str(text_form["spotlight_meeting_event"].value() or ""),
            "meetings": _meeting_previews(text_form, now),
            "pollQuestion": admin_spotlight.first_line_fallback,
            "secondDefault": SECOND_LINE_DEFAULT,
            "showWhenEmpty": bool(text_form["spotlight_show_when_empty"].value()),
            "hasPoll": admin_spotlight.poll is not None,
        },
        "page": table["page"],
        "sort": table["sort"],
        "sort_dir": table["sort_dir"],
        "base_params": table["base_params"],
    }
    return render(request, "hub/admin/spotlight.html", context, status=status)


@fog_admin_required
def hub_admin_spotlight(request: HttpRequest) -> HttpResponse:
    """The Spotlight admin page."""
    return _render_page(request)


@fog_admin_required
@require_POST
def hub_admin_spotlight_text(request: HttpRequest) -> HttpResponse:
    """Save the meeting and the two minimized lines."""
    form = SpotlightTextForm(request.POST, instance=SiteConfiguration.load())
    if not form.is_valid():
        return _render_page(request, text_form=form, status=400)
    form.save_at(timezone.now())
    messages.success(request, "Spotlight saved.")
    return redirect("hub_admin_spotlight")


@fog_admin_required
@require_POST
def hub_admin_spotlight_poll_new(request: HttpRequest) -> HttpResponse:
    """Open a new poll now, closing the open one only when the confirm modal said so."""
    form = NewPollForm(request.POST)
    if not form.is_valid():
        return _render_page(request, poll_form=form, status=400)
    user: User = request.user  # type: ignore[assignment]  # @fog_admin_required guarantees a User
    try:
        form.post(by=user, now=timezone.now())
    except PollAlreadyOpenError:
        form.add_error(None, "A poll is already open. Close it first, or post again and confirm.")
        return _render_page(request, poll_form=form, status=409)
    messages.success(request, "Poll posted. It is open now.")
    return redirect("hub_admin_spotlight")


@fog_admin_required
@require_POST
def hub_admin_spotlight_poll_close(request: HttpRequest, pk: int) -> HttpResponse:
    """Close now: end voting on this poll at once."""
    poll = get_object_or_404(Poll, pk=pk)
    poll.close(timezone.now())
    messages.success(request, "Poll closed.")
    return redirect("hub_admin_spotlight")


@fog_admin_required
def hub_admin_spotlight_poll(request: HttpRequest, pk: int) -> HttpResponse:
    """One poll's results: counts and percents only, never who voted."""
    poll = get_object_or_404(Poll.objects.with_totals(), pk=pk)
    return render(
        request,
        "hub/admin/spotlight_poll.html",
        {**_get_hub_context(request), "poll": poll, "results": poll.results(), "is_open": poll.is_open(timezone.now())},
    )
