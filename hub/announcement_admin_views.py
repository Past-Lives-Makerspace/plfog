"""The Spotlight Messages section of Admin Tools > Spotlight (#889, #920): the notices in Home's banner.

Every view is admin only (``@fog_admin_required``), the Spotlight page's own gate. Add and Edit
are one dedicated page (``hub/admin/announcement_form.html``); the on and off switch and the
drag to reorder save at once over htmx and answer with a toast; Delete goes through the confirm
modal. Every full page save redirects back to the section with a message, shown as a toast.
Nothing here sends anything: an announcement is shown on Home, never sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.html_sanitize import ANNOUNCEMENT_MAX_CHARS, rich_html_to_text, sanitize_announcement_html
from core.models import Announcement, AnnouncementOrderChangedError
from hub.forms import _meeting_time_label
from hub.spotlight_forms import AnnouncementActiveForm, AnnouncementForm, AnnouncementOrderForm
from hub.toast import trigger_toast
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.auth.models import User

SAVED_NEW = "Spotlight message added. It shows on Home while it is live."
SAVED_EDIT = "Spotlight message saved."
DELETED = "Spotlight message deleted. Members no longer see it."
ORDER_SAVED = "Order saved."
TURNED_ON = "Spotlight message on."
TURNED_OFF = "Spotlight message off. Members no longer see it."


# The status chip's hub-pill tone.
_TONES: dict[str, str] = {
    Announcement.Status.LIVE: "ok",
    Announcement.Status.SCHEDULED: "primary",
    Announcement.Status.ENDED: "neutral",
    Announcement.Status.OFF: "neutral",
}


def _when(value: datetime) -> str:
    """A moment as the row shows it: "Oct 24, 9:00 AM", in Portland time."""
    local = timezone.localtime(value)
    return f"{local:%b} {local.day}, {_meeting_time_label(local.hour, local.minute)}"


def _dates(announcement: Announcement) -> str:
    """The row's dates line, from whichever of Show from and Show until are set."""
    start, end = announcement.show_from, announcement.show_until
    if start is not None and end is not None:
        return f"{_when(start)} to {_when(end)}"
    if start is not None:
        return f"From {_when(start)}"
    if end is not None:
        return f"Until {_when(end)}"
    return "No dates. Shows until turned off."


@dataclass(frozen=True)
class AnnouncementRow:
    """One row of the Announcements section: the announcement, its status now, its dates and its switch."""

    announcement: Announcement
    status: Announcement.Status
    tone: str
    dates: str
    active_form: AnnouncementActiveForm

    @classmethod
    def build(cls, announcement: Announcement, now: datetime) -> AnnouncementRow:
        status = announcement.status(now)
        return cls(
            announcement, status, _TONES[status], _dates(announcement), AnnouncementActiveForm.for_row(announcement)
        )


def announcement_rows(now: datetime) -> list[AnnouncementRow]:
    """Every announcement in the banner's order, each with its status at ``now``. One query."""
    return [AnnouncementRow.build(announcement, now) for announcement in Announcement.objects.in_order()]


def _section_url() -> str:
    return f"{reverse('hub_admin_spotlight')}#announcements"


def _is_htmx(request: HttpRequest) -> bool:
    # POST only, so a history restore can never reach these (FRONTEND.md, wants_fragment).
    return request.headers.get("HX-Request") == "true"


def _render_form(request: HttpRequest, form: AnnouncementForm) -> HttpResponse:
    return render(
        request,
        "hub/admin/announcement_form.html",
        {
            **_get_hub_context(request),
            "form": form,
            "max_chars": ANNOUNCEMENT_MAX_CHARS,
            # The counter's first number, before the editor's own count takes over.
            "char_count": len(rich_html_to_text(sanitize_announcement_html(str(form["body"].value() or "")))),
            "section_url": _section_url(),
        },
    )


def _save(request: HttpRequest, form: AnnouncementForm, message: str) -> HttpResponse:
    if not form.is_valid():
        # 200, not 400: hub_boot.js turns a boosted 4xx into a fresh GET, which would drop what was typed.
        return _render_form(request, form)
    user: User = request.user  # type: ignore[assignment]  # @fog_admin_required guarantees a User
    form.save_by(user)
    messages.success(request, message)
    return redirect(_section_url())


@fog_admin_required
def hub_admin_announcement_new(request: HttpRequest) -> HttpResponse:
    """+ Add announcement: the empty form, and its save."""
    if request.method == "POST":
        return _save(request, AnnouncementForm(request.POST), SAVED_NEW)
    return _render_form(request, AnnouncementForm())


@fog_admin_required
def hub_admin_announcement_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit: one announcement's form, and its save."""
    announcement = get_object_or_404(Announcement, pk=pk)
    if request.method == "POST":
        return _save(request, AnnouncementForm(request.POST, instance=announcement), SAVED_EDIT)
    return _render_form(request, AnnouncementForm(instance=announcement))


@fog_admin_required
@require_POST
def hub_admin_announcement_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    """The row's switch: on or off at once. Over htmx, the row again with its new status, and a toast."""
    announcement = get_object_or_404(Announcement, pk=pk)
    form = AnnouncementActiveForm.for_row(announcement, request.POST)
    form.is_valid()  # A lone optional BooleanField: always valid, absent reads as off.
    announcement.is_active = form.cleaned_data["is_active"]
    announcement.save(update_fields=["is_active", "updated_at"])
    message = TURNED_ON if announcement.is_active else TURNED_OFF
    if not _is_htmx(request):
        messages.success(request, message)
        return redirect(_section_url())
    response = render(
        request,
        "hub/admin/_announcement_row.html",
        {"row": AnnouncementRow.build(announcement, timezone.now())},
    )
    trigger_toast(response, message)
    return response


@fog_admin_required
@require_POST
def hub_admin_announcement_reorder(request: HttpRequest) -> HttpResponse:
    """Save a new order at once, from a drag or the up and down buttons."""
    form = AnnouncementOrderForm(request.POST)
    try:
        if not form.is_valid():
            raise AnnouncementOrderChangedError(form.errors["order"][0])
        Announcement.reorder(form.cleaned_data["order"])
    except AnnouncementOrderChangedError as exc:
        if not _is_htmx(request):
            messages.error(request, str(exc))
            return redirect(_section_url())
        response = HttpResponse(status=409)
        trigger_toast(response, str(exc), "error")
        return response
    if not _is_htmx(request):
        messages.success(request, ORDER_SAVED)
        return redirect(_section_url())
    response = HttpResponse(status=204)
    trigger_toast(response, ORDER_SAVED)
    return response


@fog_admin_required
@require_POST
def hub_admin_announcement_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete, from the confirm modal. Members stop seeing it at once."""
    get_object_or_404(Announcement, pk=pk).delete()
    messages.success(request, DELETED)
    return redirect(_section_url())
