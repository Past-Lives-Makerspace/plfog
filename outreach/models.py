"""Models for the outreach app — public-facing events."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Event(models.Model):
    """A published public event associated with a guild."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True)
    guild = models.ForeignKey(
        "membership.Guild",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]
        verbose_name = "Event"
        verbose_name_plural = "Events"

    def __str__(self) -> str:
        return self.name
