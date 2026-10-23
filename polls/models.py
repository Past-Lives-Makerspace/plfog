"""The removed polls feature's tables, kept until the drop migration.

Polls (#708, #709) were removed on 2026-10-23 (#890): no page, view, URL, admin or job reads
them. These three models stay, with their fields and constraints exactly as the migrations
built them, only so the tables survive while the previous release still reads them during the
deploy (STANDARDS.md section 10). A follow-up PR deletes these models with a migration that
drops the tables once this release is live. Add nothing here.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q


class Poll(models.Model):
    """One question with two to six answers, open for a set number of days. Removed in #890; table kept."""

    question = models.CharField(max_length=200, help_text="What members are asked, in plain words.")
    opens_at = models.DateTimeField(help_text="When members can start voting.")
    closes_at = models.DateTimeField(help_text="When voting ends. Close now sets it to the moment the admin closed it.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The admin who posted the poll.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the poll was posted.")

    class Meta:
        ordering = ["-opens_at"]
        constraints = [
            models.CheckConstraint(condition=Q(closes_at__gte=models.F("opens_at")), name="poll_closes_after_opens"),
        ]
        indexes = [models.Index(fields=["closes_at", "opens_at"], name="poll_window_idx")]


class PollChoice(models.Model):
    """One answer to a poll. Removed in #890; table kept."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="choices", help_text="The poll this answers.")
    text = models.CharField(max_length=120, help_text="The answer as members see it.")
    position = models.PositiveSmallIntegerField(help_text="Its place in the list, from 0.")

    class Meta:
        ordering = ["poll", "position"]
        constraints = [models.UniqueConstraint(fields=["poll", "position"], name="pollchoice_one_per_position")]


class PollVote(models.Model):
    """A member's one answer to one poll. Removed in #890; table kept."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="votes", help_text="The poll voted in.")
    choice = models.ForeignKey(
        PollChoice, on_delete=models.CASCADE, related_name="votes", help_text="The answer chosen."
    )
    member = models.ForeignKey(
        "membership.Member", on_delete=models.CASCADE, related_name="poll_votes", help_text="Who voted."
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the vote was cast.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["poll", "member"], name="pollvote_one_per_member")]
