"""Outreach app models."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        TOURED = "toured", "Toured"
        CONVERTED = "converted", "Converted"
        LOST = "lost", "Lost"

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    interests = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    greenlighted_for_membership = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Lead"
        verbose_name_plural = "Leads"

    def __str__(self) -> str:
        return self.name


class Tour(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        CLAIMED = "claimed", "Claimed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No Show"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="tours")
    scheduled_at = models.DateTimeField()
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="claimed_tours"
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scheduled_at"]
        verbose_name = "Tour"
        verbose_name_plural = "Tours"

    def __str__(self) -> str:
        return f"Tour for {self.lead.name} ({self.status})"


class Event(models.Model):
    guild = models.ForeignKey(
        "membership.Guild", null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    is_recurring = models.BooleanField(default=False)
    recurrence_rule = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_events"
    )
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]
        verbose_name = "Event"
        verbose_name_plural = "Events"

    def __str__(self) -> str:
        return self.name


class Buyable(models.Model):
    guild = models.ForeignKey(
        "membership.Guild", null=True, blank=True, on_delete=models.SET_NULL, related_name="buyables"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="buyables/", blank=True)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    revenue_split = models.ForeignKey(
        "billing.RevenueSplit", null=True, blank=True, on_delete=models.SET_NULL, related_name="buyables"
    )
    total_quantity_sold = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Buyable"
        verbose_name_plural = "Buyables"

    def __str__(self) -> str:
        return self.name

    @property
    def formatted_price(self) -> str:
        return f"${self.unit_price:.2f}"


class BuyablePurchase(models.Model):
    buyable = models.ForeignKey(Buyable, on_delete=models.CASCADE, related_name="purchases")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="buyable_purchases")
    quantity = models.PositiveIntegerField(default=1)
    order = models.ForeignKey(
        "billing.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="buyable_purchases"
    )
    purchased_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-purchased_at"]
        verbose_name = "Buyable Purchase"
        verbose_name_plural = "Buyable Purchases"

    def __str__(self) -> str:
        return f"{self.buyable.name} x{self.quantity} - {self.user}"

    @property
    def total_cost(self) -> Decimal:
        return self.buyable.unit_price * self.quantity
