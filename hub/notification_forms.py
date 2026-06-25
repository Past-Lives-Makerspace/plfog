"""Forms for the admin notification copy catalogue (design §2.3, Decision 6).

Validation lives here (CLAUDE.md: validation in forms, not views):

* :class:`NotificationTemplateForm` edits one ``(event, channel)`` copy row and
  rejects any merge field the event does not document (via
  :func:`core.events.rendering.validate_copy`) so the copy team gets a clear error
  before they save copy that would render as a ``[missing: …]`` marker.
* :class:`DiscordRouteForm` edits one event's Discord webhook routing row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms

from core.events import copy as copy_module
from core.events.rendering import validate_copy

if TYPE_CHECKING:
    from core.models import DiscordWebhookRoute, NotificationTemplate


class NotificationTemplateForm(forms.Form):
    """Edit the subject + text/HTML body for one event/channel copy row.

    The form is constructed for a specific ``event_key`` so :meth:`clean` can check
    every documented placeholder against that event's vocabulary. It does NOT bind to
    a ModelForm because the save path is the model's :meth:`NotificationTemplate.apply_edit`
    (which snapshots a version first) — the form only validates + carries the values.
    """

    subject = forms.CharField(max_length=255, required=False, label="Subject / title")
    body_text = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), required=False, label="Text body")
    body_html = forms.CharField(widget=forms.Textarea(attrs={"rows": 8}), required=False, label="HTML body")

    def __init__(self, *args: Any, event_key: str, **kwargs: Any) -> None:
        self.event_key = event_key
        self.allowed_placeholders = copy_module.placeholders_for(event_key)
        super().__init__(*args, **kwargs)

    def clean(self) -> dict[str, Any]:
        """Reject any merge field not documented for this event.

        A placeholder the event does not provide would render as a visible
        ``[missing: …]`` marker at send time — surface it as a validation error now
        so the copy team fixes it before saving.
        """
        cleaned = super().clean() or {}
        bad = validate_copy(
            subject=cleaned.get("subject", ""),
            body_text=cleaned.get("body_text", ""),
            body_html=cleaned.get("body_html", ""),
            allowed=self.allowed_placeholders,
        )
        if bad:
            allowed = ", ".join(f"{{{{ {name} }}}}" for name in self.allowed_placeholders) or "(none)"
            unknown = ", ".join(f"{{{{ {name} }}}}" for name in bad)
            raise forms.ValidationError(f"Unknown merge field(s): {unknown}. This event only provides: {allowed}.")
        return cleaned

    @classmethod
    def from_template(cls, template: "NotificationTemplate") -> "NotificationTemplateForm":
        """Build an unbound form pre-filled from an existing template row."""
        return cls(
            event_key=template.event_key,
            initial={
                "subject": template.subject,
                "body_text": template.body_text,
                "body_html": template.body_html,
            },
        )


class DiscordRouteForm(forms.Form):
    """Edit one event's Discord webhook routing (DB-backed override, §2.4).

    A blank ``webhook_url`` with ``is_enabled`` on is a deliberate *disable* of
    Discord for the event (overrides the global webhook with silence). Disabling the
    route falls back to the global webhook. The view never echoes the stored URL into
    a place it would be logged.
    """

    webhook_url = forms.URLField(
        max_length=500,
        required=False,
        assume_scheme="https",
        label="Discord webhook URL",
        help_text=(
            "Where this event posts. Write-only — for security the stored URL is never shown back. "
            "Leave blank to keep the current URL; blank + enabled silences Discord only when no URL is stored."
        ),
    )
    is_enabled = forms.BooleanField(
        required=False,
        initial=True,
        label="Route enabled",
        help_text="Off = ignore this route and fall back to the global webhook.",
    )

    @classmethod
    def from_route(cls, route: "DiscordWebhookRoute | None") -> "DiscordRouteForm":
        """Build an unbound form from an existing route (or defaults when none).

        The webhook URL field is deliberately left **blank** — it is write-only and
        the stored secret is never echoed into the page. Only the enabled flag is
        pre-filled so the toggle reflects the current state.
        """
        if route is None:
            return cls(initial={"webhook_url": "", "is_enabled": True})
        return cls(initial={"webhook_url": "", "is_enabled": route.is_enabled})
