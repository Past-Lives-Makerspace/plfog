"""Models for the education app — classes and orientations."""

from __future__ import annotations

from django.db import models


class MakerClass(models.Model):
    """A class offered by a guild."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    guild = models.ForeignKey(
        "membership.Guild",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maker_classes",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Maker Class"
        verbose_name_plural = "Maker Classes"

    def __str__(self) -> str:
        return self.name


class ClassSession(models.Model):
    """A scheduled session for a MakerClass."""

    maker_class = models.ForeignKey(
        MakerClass,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    capacity = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]
        verbose_name = "Class Session"
        verbose_name_plural = "Class Sessions"

    def __str__(self) -> str:
        return f"{self.maker_class} — {self.starts_at}"


class Orientation(models.Model):
    """An orientation program for a guild."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    guild = models.ForeignKey(
        "membership.Guild",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orientations",
    )
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Orientation"
        verbose_name_plural = "Orientations"

    def __str__(self) -> str:
        return self.name


class ScheduledOrientation(models.Model):
    """A scheduled occurrence of an Orientation."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    orientation = models.ForeignKey(
        Orientation,
        on_delete=models.CASCADE,
        related_name="scheduled_orientations",
    )
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_at"]
        verbose_name = "Scheduled Orientation"
        verbose_name_plural = "Scheduled Orientations"

    def __str__(self) -> str:
        return f"{self.orientation} — {self.scheduled_at}"
