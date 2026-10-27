"""Admin Tools > Spotlight at /manage/spotlight/ (#708).

One page: the Discord pings Waiting for Review (#943, queued by ``queue_discord_pings``), the
Spotlight messages (#889, hub.announcement_admin_views) that crawl across the top of Home (#935),
and their Crawl speed slider (#942), saved at once over htmx. The Spotlight text
and meeting were removed in #935, and polls left this page in #890. Admin only
(``@fog_admin_required``). Nothing here sends an email, push, Discord post or bell.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.html_sanitize import ANNOUNCEMENT_MAX_CHARS, rich_html_to_text, sanitize_announcement_html
from core.models import Announcement, QueuedDiscordMessage, QueuedMessageHandledError, SiteConfiguration
from hub.announcement_admin_views import announcement_rows
from hub.spotlight_forms import QueuedLineForm, SpotlightCrawlSpeedForm
from hub.toast import trigger_toast
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context

PING_APPROVED = "Approved. It is live on Home for 7 days."
PING_DISCARDED = "Discarded."
PING_SAVED = "Saved. It is still waiting for review."
CRAWL_SPEED_SAVED = "Crawl speed saved. Members see it the next time Home loads."
# What the preview crawls while no Spotlight message is live.
CRAWL_SPEED_SAMPLE = "This is how fast the Spotlight messages crawl across the top of Home."


@fog_admin_required
def hub_admin_spotlight(request: HttpRequest) -> HttpResponse:
    """The Spotlight admin page: every Spotlight message as a row, in the ticker's order, and the Crawl speed slider."""
    rows = announcement_rows(timezone.now())
    context = {
        **_get_hub_context(request),
        "announcement_rows": rows,
        # Discord pings waiting for review (#943), above the messages; the section is absent when none wait.
        "waiting_pings": list(QueuedDiscordMessage.objects.waiting()),
        "crawl_speed_form": SpotlightCrawlSpeedForm(instance=SiteConfiguration.load()),
        # The preview crawls what Home's ticker would show now: the live rows, already in the ticker's order.
        "crawl_speed_preview_items": [row.announcement for row in rows if row.status == Announcement.Status.LIVE],
        "crawl_speed_sample": CRAWL_SPEED_SAMPLE,
    }
    return render(request, "hub/admin/spotlight.html", context)


@fog_admin_required
@require_POST
def hub_admin_spotlight_crawl_speed(request: HttpRequest) -> HttpResponse:
    """Save the Crawl speed as the slider is let go. Over htmx, a 204 and a toast; a refusal is a 400 and an error toast."""
    form = SpotlightCrawlSpeedForm(request.POST, instance=SiteConfiguration.load())
    is_htmx = request.headers.get("HX-Request") == "true"
    if not form.is_valid():
        if not is_htmx:
            messages.error(request, form.refusal)
            return redirect(f"{reverse('hub_admin_spotlight')}#announcements")
        response = HttpResponse(status=400)
        trigger_toast(response, form.refusal, "error")
        return response
    form.save()
    if not is_htmx:
        messages.success(request, CRAWL_SPEED_SAVED)
        return redirect(f"{reverse('hub_admin_spotlight')}#announcements")
    response = HttpResponse(status=204)
    trigger_toast(response, CRAWL_SPEED_SAVED)
    return response


def _queue_url() -> str:
    return f"{reverse('hub_admin_spotlight')}#announcements"


@fog_admin_required
def hub_admin_ping_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit a waiting Discord message's line in the Spotlight editor; saving keeps it waiting (#943)."""
    ping = get_object_or_404(QueuedDiscordMessage, pk=pk)
    if request.method != "POST" and ping.state != QueuedDiscordMessage.State.WAITING:
        raise Http404
    form = QueuedLineForm(request.POST if request.method == "POST" else None, initial={"body": ping.line})
    if request.method == "POST" and form.is_valid():
        try:
            QueuedDiscordMessage.save_line(pk, form.cleaned_data["body"])
        except QueuedMessageHandledError as exc:
            messages.error(request, str(exc))
            return redirect(_queue_url())
        messages.success(request, PING_SAVED)
        return redirect(_queue_url())
    body = str(form["body"].value() or "")
    context = {
        **_get_hub_context(request),
        "form": form,
        "queued": ping,
        "max_chars": ANNOUNCEMENT_MAX_CHARS,
        "char_count": len(rich_html_to_text(sanitize_announcement_html(body))),
        "section_url": _queue_url(),
    }
    # 200 on a refusal too: hub_boot.js turns a boosted 4xx into a fresh GET, which would drop what was typed.
    return render(request, "hub/admin/announcement_form.html", context)


@fog_admin_required
@require_POST
def hub_admin_ping_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Approve: live on Home for 7 days and on each pinged guild's page, sending nothing."""
    get_object_or_404(QueuedDiscordMessage, pk=pk)
    try:
        QueuedDiscordMessage.approve(pk, by=request.user)  # type: ignore[arg-type]  # @fog_admin_required: a User
    except QueuedMessageHandledError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, PING_APPROVED)
    return redirect(_queue_url())


@fog_admin_required
@require_POST
def hub_admin_ping_discard(request: HttpRequest, pk: int) -> HttpResponse:
    """Discard, from the confirm modal: it never shows anywhere and is never queued again."""
    get_object_or_404(QueuedDiscordMessage, pk=pk)
    try:
        QueuedDiscordMessage.discard(pk)
    except QueuedMessageHandledError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, PING_DISCARDED)
    return redirect(_queue_url())
