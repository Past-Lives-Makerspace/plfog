"""Admin Tools > Spotlight at /manage/spotlight/ (#708).

One page: the announcements (#889, hub.announcement_admin_views), shown on Home and, since #890,
in the Spotlight; and the Spotlight text and meeting with a live preview. Polls were removed in
#890, their Past polls table with them. Every view is admin only
(``@fog_admin_required``); saves are plain POSTs that redirect back with a message, shown as a
toast. Nothing here sends an email, push, Discord post or bell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import SiteConfiguration
from hub.announcement_admin_views import announcement_rows
from hub.spotlight import SECOND_LINE_DEFAULT, SpotlightMeeting
from hub.context_processors import request_spotlight
from hub.spotlight_forms import SpotlightTextForm
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context

if TYPE_CHECKING:
    from datetime import datetime


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


def _render_page(request: HttpRequest, *, text_form: SpotlightTextForm | None = None) -> HttpResponse:
    now = timezone.now()
    # Read for the preview. It must not be called "spotlight": that key is the context
    # processor's, which is falsy for an admin without a member record (#709).
    admin_spotlight = request_spotlight(request)
    text_form = text_form or SpotlightTextForm(instance=SiteConfiguration.load())
    context: dict[str, Any] = {
        **_get_hub_context(request),
        "admin_spotlight": admin_spotlight,
        "announcement_rows": announcement_rows(now),
        "text_form": text_form,
        "second_line_default": SECOND_LINE_DEFAULT,
        "spotlight_preview": {
            "first": text_form["spotlight_first_line"].value() or "",
            "second": text_form["spotlight_second_line"].value() or "",
            "meeting": str(text_form["spotlight_meeting_event"].value() or ""),
            "meetings": _meeting_previews(text_form, now),
            "fallback": admin_spotlight.first_line_fallback,
            "secondDefault": SECOND_LINE_DEFAULT,
            "showWhenEmpty": bool(text_form["spotlight_show_when_empty"].value()),
            "hasAnnouncement": bool(admin_spotlight.announcements),
        },
    }
    return render(request, "hub/admin/spotlight.html", context)


@fog_admin_required
def hub_admin_spotlight(request: HttpRequest) -> HttpResponse:
    """The Spotlight admin page."""
    return _render_page(request)


@fog_admin_required
@require_POST
def hub_admin_spotlight_text(request: HttpRequest) -> HttpResponse:
    """Save the meeting and the two minimized lines.

    An invalid save re-renders at 200 with its errors: the page is boosted, and ``hub_boot.js``
    turns a boosted 4xx into a fresh GET, which would drop what the admin typed (#890, as the
    announcement form does since #889).
    """
    form = SpotlightTextForm(request.POST, instance=SiteConfiguration.load())
    if not form.is_valid():
        return _render_page(request, text_form=form)
    form.save_at(timezone.now())
    messages.success(request, "Spotlight saved.")
    return redirect("hub_admin_spotlight")
