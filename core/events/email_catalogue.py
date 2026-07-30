"""The admin Emails-tab catalogue — every email the app can send, with cadence + routing.

This is the model/service layer for the Site Settings → Emails tab (CLAUDE.md: logic
out of views). The tab is read-and-route: it lists every emailing event, says who gets
it, whether it's automatic (scheduler-driven) or triggered (by an action), and links to
the two places a parameter is actually adjusted — the copy editor for wording, and
either the Voting settings page or the Automations tab for schedule/toggle.

It does NOT own wording or schedule state; it composes the event registry, the copy
catalogue's audience descriptions, and live ``VotingSettings`` into presentation rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import reverse

from core.events import copy as copy_module
from core.events.registry import Channel, EventType, all_events

# Human labels for the channels an event can fan out to, in display order. Kept here (not
# imported from the notification catalogue view) so the service has no view dependency.
_CHANNEL_LABELS: dict[Channel, str] = {
    Channel.EMAIL: "Email",
    Channel.SCHEDULED_EMAIL: "Scheduled email",
    Channel.IN_APP: "In-app",
    Channel.PUSH: "Push",
    Channel.DISCORD: "Discord",
    Channel.DISCORD_DM: "Discord DM",
    Channel.DIGEST: "Digest",
}

# The scheduler-driven emails (fired by a cron source, not an action). Everything else in
# the catalogue is transactional — sent the moment something happens. Membership here is
# harmless if a key ever stops declaring an email channel: it just won't appear.
_VOTING_AUTOMATIC = {"voting.closing_soon", "voting.vote_soon", "voting.officers_closing_soon"}
_OTHER_AUTOMATIC = {
    "class_reminder": "Sent automatically before each class starts.",
    "event.reminder": "Sent automatically before a community event starts.",
    "event.happening_now": "Sent automatically when a community event is starting.",
    "lease_expiring": "Sent automatically as a space lease nears its end date.",
}
_AUTOMATIC_EMAILS = _VOTING_AUTOMATIC | set(_OTHER_AUTOMATIC)


@dataclass(frozen=True)
class EmailRow:
    """One email's presentation row for the Emails tab."""

    key: str
    label: str
    description: str
    audience: str
    channels: list[str]
    is_automatic: bool
    schedule_note: str
    adjust_label: str
    adjust_url: str

    @property
    def kind_label(self) -> str:
        return "Automatic" if self.is_automatic else "Triggered"

    @property
    def edit_url(self) -> str:
        """Deep link to the existing copy editor for this email's EMAIL wording."""
        return reverse("hub_admin_notification_edit", args=[self.key, Channel.EMAIL.value])


def _channels(event: EventType) -> list[str]:
    """Human labels of every channel this event fans out to, email-first."""
    return [_CHANNEL_LABELS[spec.channel] for spec in event.channels if spec.channel in _CHANNEL_LABELS]


def _lower_first(text: str) -> str:
    return text[0].lower() + text[1:] if text else text


def _schedule_note(event: EventType) -> str:
    """Plain-language cadence (automatic) or trigger (transactional) for one email."""
    if event.key in _VOTING_AUTOMATIC:
        from membership.models import VotingSettings

        lead = VotingSettings.load().reminder_lead_days
        days = "day" if lead == 1 else "days"
        return f"Sent {lead} {days} before the monthly guild vote closes."
    if event.key in _OTHER_AUTOMATIC:
        return _OTHER_AUTOMATIC[event.key]
    return f"Sent when {_lower_first(event.description).rstrip('.')}."


def _adjust(event: EventType) -> tuple[str, str]:
    """(label, url) for where this email's schedule/toggle is adjusted, or ("", "")."""
    if event.key in _VOTING_AUTOMATIC:
        return "Adjust timing in Voting settings", reverse("hub_admin_voting_settings")
    if event.key in _OTHER_AUTOMATIC:
        return "Turn on/off in Automations", f"{reverse('hub_admin_site_settings')}?tab=automations"
    return "", ""


def _row(event: EventType) -> EmailRow:
    adjust_label, adjust_url = _adjust(event)
    return EmailRow(
        key=event.key,
        label=event.label,
        description=event.description,
        audience=copy_module.audience_description(event),
        channels=_channels(event),
        is_automatic=event.key in _AUTOMATIC_EMAILS,
        schedule_note=_schedule_note(event),
        adjust_label=adjust_label,
        adjust_url=adjust_url,
    )


def _sends_email(event: EventType) -> bool:
    return event.has_channel(Channel.EMAIL) or event.has_channel(Channel.SCHEDULED_EMAIL)


def build_email_catalogue() -> list[tuple[str, list[EmailRow]]]:
    """Every email the app can send, grouped by category in registry order.

    Only events that actually declare an email channel are included — an in-app/push/
    Discord-only event is not an email. Categories preserve the registry's order.
    """
    groups: dict[str, list[EmailRow]] = {}
    order: list[str] = []
    for event in all_events():
        if not _sends_email(event):
            continue
        if event.category not in groups:
            groups[event.category] = []
            order.append(event.category)
        groups[event.category].append(_row(event))
    return [(category, groups[category]) for category in order]
