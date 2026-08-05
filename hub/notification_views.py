"""Admin notification copy catalogue views (design §2.3 + §2.4, Decision 6).

The copy team's home for the event-driven notification system. All views are gated
to actual admins via :func:`hub.view_as.fog_admin_required`. Thin per CLAUDE.md —
business logic (versioning, revert, audience/lock description) lives on the models
and in :mod:`core.events`; validation lives in :mod:`hub.notification_forms`.

Surfaces:

* :func:`catalogue` — every event grouped by category, with lock status, audience,
  channels, the Discord route, and per-channel copy.
* :func:`edit_copy` — edit one (event, channel) row with a live preview + version
  history + revert.
* :func:`preview_copy` — HTMX partial: render the typed-in copy against the sample
  context (the live preview).
* :func:`revert_copy` — restore a historical version.
* :func:`edit_discord_route` — edit one event's Discord webhook routing.

URL home: ``/manage/notifications/`` (name ``hub_admin_notifications``).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.events import copy as copy_module
from core.events import discord as discord_module
from core.events.registry import Channel, ChannelDefault, EventType, all_events, get_event
from core.events.rendering import render_copy
from core.events.templates import wrap_email_html
from core.models import DiscordWebhookRoute, NotificationTemplate, NotificationTemplateVersion
from hub.notification_forms import DiscordRouteForm, NotificationTemplateForm
from hub.toast import trigger_toast
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context


# --- View-model helpers (presentation shaping, no business logic) -------------


@dataclass(frozen=True)
class ChannelView:
    """One channel's presentation row for the catalogue / edit page."""

    channel: Channel
    label: str
    lock_label: str
    is_forced: bool
    has_copy: bool


_CHANNEL_LABELS: dict[Channel, str] = {
    Channel.IN_APP: "In-app",
    Channel.EMAIL: "Email",
    Channel.PUSH: "Push",
    Channel.SCHEDULED_EMAIL: "Scheduled email",
    Channel.DIGEST: "Digest",
    Channel.DISCORD: "Discord",
    Channel.DISCORD_DM: "Discord DM",
}

_LOCK_LABELS: dict[ChannelDefault, str] = {
    ChannelDefault.FORCED: "Forced (members can't opt out)",
    ChannelDefault.ON: "On by default (opt-out)",
    ChannelDefault.OFF: "Off by default (opt-in)",
}


def _channel_label(channel: Channel) -> str:
    return _CHANNEL_LABELS[channel]


def _has_authored_copy(event_key: str) -> set[str]:
    """The set of channel keys with a NotificationTemplate row for ``event_key``."""
    return set(NotificationTemplate.objects.filter(event_key=event_key).values_list("channel", flat=True))


def _channel_views(event: EventType) -> list[ChannelView]:
    authored = _has_authored_copy(event.key)
    out: list[ChannelView] = []
    for spec in event.channels:
        out.append(
            ChannelView(
                channel=spec.channel,
                label=_channel_label(spec.channel),
                lock_label=_LOCK_LABELS[spec.default],
                is_forced=spec.is_forced,
                has_copy=spec.channel.value in authored,
            )
        )
    return out


@dataclass(frozen=True)
class EventView:
    """One event's full presentation row for the catalogue."""

    event: EventType
    audience: str
    is_locked: bool
    channels: list[ChannelView]
    discord_webhook_configured: bool


def _is_locked(event: EventType) -> bool:
    """An event is 'locked' when any of its channels is forced (can't be opted out)."""
    return any(spec.is_forced for spec in event.channels)


def _event_view(event: EventType) -> EventView:
    webhook = discord_module.webhook_for_event(event.key)
    return EventView(
        event=event,
        audience=copy_module.audience_description(event),
        is_locked=_is_locked(event),
        channels=_channel_views(event),
        # Whether Discord would post for this event at all (NEVER expose the URL).
        discord_webhook_configured=bool(webhook),
    )


def _grouped_events() -> list[tuple[str, list[EventView]]]:
    """Every event as an :class:`EventView`, grouped by category in registry order."""
    groups: dict[str, list[EventView]] = {}
    order: list[str] = []
    for event in all_events():
        if event.category not in groups:
            groups[event.category] = []
            order.append(event.category)
        groups[event.category].append(_event_view(event))
    return [(category, groups[category]) for category in order]


def _get_template(event_key: str, channel: Channel) -> NotificationTemplate:
    """Fetch (or lazily seed) the copy row for one event/channel.

    A catalogue that has never been seeded still edits cleanly: if no row exists we
    create one from the code default so the edit page always has a row to version.
    ``get_or_create`` is atomic against the ``(event_key, channel)`` unique constraint,
    so two concurrent first-edit GETs can't both create and trip an IntegrityError.
    """
    default = copy_module.default_copy_for(event_key, channel)
    row, _ = NotificationTemplate.objects.get_or_create(
        event_key=event_key,
        channel=channel.value,
        defaults={
            "subject": default.subject,
            "body_text": default.body_text,
            "body_html": default.body_html,
            "is_overridden": False,
        },
    )
    return row


def _editor(request: HttpRequest) -> "User | None":
    user = request.user
    if isinstance(user, User) and user.pk:
        return user
    return None


# --- Views --------------------------------------------------------------------


@fog_admin_required
def catalogue(request: HttpRequest) -> HttpResponse:
    """The copy catalogue — every event grouped by category (design §2.3)."""
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/admin/notifications/catalogue.html",
        {**ctx, "groups": _grouped_events()},
    )


@fog_admin_required
def edit_copy(request: HttpRequest, event_key: str, channel: str) -> HttpResponse:
    """Edit one (event, channel) copy row — live preview + history + revert."""
    event = get_event(event_key)  # raises KeyError → 500 on a bad key (fails loudly)
    channel_enum = Channel(channel)
    template = _get_template(event_key, channel_enum)

    if request.method == "POST":
        form = NotificationTemplateForm(request.POST, event_key=event_key)
        if form.is_valid():
            template.apply_edit(
                subject=form.cleaned_data["subject"],
                body_text=form.cleaned_data["body_text"],
                body_html=form.cleaned_data["body_html"],
                editor=_editor(request),
            )
            response = redirect(f"{reverse('hub_admin_notifications')}#{event_key}")
            trigger_toast(response, "Notification copy saved.", "success")
            return response
    else:
        form = NotificationTemplateForm.from_template(template)

    versions = list(template.versions.select_related("edited_by")[:25])
    channel_spec = event.channel(channel_enum)
    return render(
        request,
        "hub/admin/notifications/edit_copy.html",
        {
            **_get_hub_context(request),
            "event": event,
            "channel": channel_enum,
            "channel_label": _channel_label(channel_enum),
            "audience": copy_module.audience_description(event),
            "is_forced": channel_spec.is_forced if channel_spec is not None else False,
            "form": form,
            "template": template,
            "versions": versions,
            "placeholders": copy_module.placeholders_for(event_key),
            "sample_context": copy_module.sample_context_for(event_key),
        },
    )


@fog_admin_required
@require_POST
def preview_copy(request: HttpRequest, event_key: str, channel: str) -> HttpResponse:
    """HTMX live preview — render the posted copy against the event's sample context.

    Renders through the SAME constrained, safe substitution the send path uses
    (:func:`core.events.rendering.render_copy`), so the preview is faithful: unknown
    merge fields show as ``[missing: …]`` markers and HTML values are autoescaped.
    """
    get_event(event_key)  # validate the key
    channel_enum = Channel(channel)
    context = copy_module.sample_context_for(event_key)
    rendered = render_copy(
        subject=request.POST.get("subject", ""),
        body_text=request.POST.get("body_text", ""),
        body_html=request.POST.get("body_html", ""),
        context=context,
    )
    # For an email channel, also show the fragment wrapped in the branded shell (the
    # document that actually arrives in the inbox) so the copy team isn't editing
    # blind to the brand. Rendered into an <iframe srcdoc> by the partial.
    wrapped_html = (
        wrap_email_html(rendered.body_html, shell=get_event(event_key).email_shell)
        if rendered.body_html and channel_enum in (Channel.EMAIL, Channel.SCHEDULED_EMAIL)
        else ""
    )
    return render(
        request,
        "hub/admin/notifications/_preview.html",
        {
            "rendered": rendered,
            "channel": channel_enum,
            "wrapped_html": wrapped_html,
        },
    )


@fog_admin_required
def preview_email_visual(request: HttpRequest, event_key: str) -> HttpResponse:
    """HTMX partial — the branded EMAIL as it arrives, for the Emails-tab preview modal.

    Renders the *resolved* copy (the admin-edited DB row if any, else the seeded default)
    against the event's sample context through the same send-path shell, so an admin can
    eyeball the real email from the Emails tab without opening the editor. Read-only.
    """
    from core.events.templates import resolved_copy

    event = get_event(event_key)
    subject, body_text, body_html = resolved_copy(event_key, Channel.EMAIL)
    rendered = render_copy(
        subject=subject, body_text=body_text, body_html=body_html, context=copy_module.sample_context_for(event_key)
    )
    wrapped_html = wrap_email_html(rendered.body_html, shell=event.email_shell) if rendered.body_html else ""
    return render(
        request,
        "hub/admin/notifications/_visual_preview.html",
        {"label": event.label, "subject": rendered.subject, "wrapped_html": wrapped_html},
    )


@fog_admin_required
@require_POST
def revert_copy(request: HttpRequest, event_key: str, channel: str, version_id: int) -> HttpResponse:
    """Revert a copy row to a historical version (snapshots current first)."""
    channel_enum = Channel(channel)
    template = _get_template(event_key, channel_enum)
    version = NotificationTemplateVersion.objects.filter(pk=version_id, template=template).first()
    if version is None:
        response = redirect(reverse("hub_admin_notification_edit", args=[event_key, channel]))
        trigger_toast(response, "That version no longer exists.", "error")
        return response
    template.revert_to(version, editor=_editor(request))
    response = redirect(reverse("hub_admin_notification_edit", args=[event_key, channel]))
    trigger_toast(response, "Reverted to the selected version.", "success")
    return response


@fog_admin_required
def edit_discord_route(request: HttpRequest, event_key: str) -> HttpResponse:
    """Edit one event's Discord webhook routing (DB-backed override, §2.4)."""
    event = get_event(event_key)
    route = DiscordWebhookRoute.objects.filter(event_key=event_key).first()

    if request.method == "POST":
        form = DiscordRouteForm(request.POST)
        if form.is_valid():
            DiscordWebhookRoute.save_routing(
                event_key=event_key,
                submitted_url=form.cleaned_data["webhook_url"],
                is_enabled=form.cleaned_data["is_enabled"],
                editor=_editor(request),
            )
            response = redirect(f"{reverse('hub_admin_notifications')}#{event_key}")
            trigger_toast(response, "Discord routing saved.", "success")
            return response
    else:
        form = DiscordRouteForm.from_route(route)

    return render(
        request,
        "hub/admin/notifications/edit_discord.html",
        {
            **_get_hub_context(request),
            "event": event,
            "form": form,
            "route": route,
            # Whether Discord would fire for this event right now (NEVER the URL).
            "discord_active": bool(discord_module.webhook_for_event(event_key)),
        },
    )
