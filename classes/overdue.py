"""Reviews that have waited too long (#817): one list feeds the Discord alert, the admin queue and Home.

A review is overdue when a PENDING class has had an open review gate for
:data:`OVERDUE_AFTER_DAYS` days or more, counted from that gate's creation. It reads open
gates, never delivery rows, so a review whose request was never sent is caught the same way.

Class review is sequential (STANDARDS.md §11), but an ``ADMIN`` row can appear early when an
admin opens the review page while the guild lead still holds the class. So each class yields
one row: its open guild-lead gate when there is one (that is who it waits on), else its open
admin gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from classes.models import ClassApproval, ClassOffering

OVERDUE_AFTER_DAYS = 3


@dataclass(frozen=True)
class OverdueReview:
    """One class whose review has waited :data:`OVERDUE_AFTER_DAYS` days or more."""

    offering: ClassOffering
    gate: ClassApproval
    days_waiting: int
    days_until_start: int | None
    dates_passed: bool = False

    @property
    def waiting_on(self) -> str:
        """Who holds it: the guild lead by name, or admins."""
        if self.gate.role == ClassApproval.Role.GUILD_LEAD:
            guild = self.offering.category.guild if self.offering.category_id else None
            if guild is not None and guild.guild_lead is not None:
                return guild.guild_lead.display_name
            return "the guild lead"
        return "admins"

    @property
    def marker(self) -> str:
        """The red line the admin queue and Home show: 'Waiting 4 days · starts in 12 days'."""
        waited = f"Waiting {self.days_waiting} day{'s' if self.days_waiting != 1 else ''}"
        if self.dates_passed:
            return f"{waited} · its dates have passed"
        if self.days_until_start is None:
            return f"{waited} · no dates yet"
        if self.days_until_start <= 0:
            return f"{waited} · starts today"
        return f"{waited} · starts in {self.days_until_start} day{'s' if self.days_until_start != 1 else ''}"


def waiting_since(gate: ClassApproval) -> datetime:
    """When ``gate``'s reviewers were really handed the class.

    A guild lead gate starts when it is opened. An admin gate can be opened early, when an
    admin opens the review page while the guild lead still holds the class, so it starts at
    the later of its creation and the guild lead's approval. Reads ``class_offering.approvals``,
    so a caller that prefetched them pays no query.
    """
    if gate.role != ClassApproval.Role.ADMIN:
        return gate.created_at
    lead_approved = [
        row.decided_at
        for row in gate.class_offering.approvals.all()
        if row.role == ClassApproval.Role.GUILD_LEAD
        and row.decision == ClassApproval.Decision.APPROVED
        and row.decided_at is not None
    ]
    return max([gate.created_at, *lead_approved])


def overdue_reviews(now: datetime | None = None) -> list[OverdueReview]:
    """Every overdue review, soonest first session first, then longest waiting.

    Two queries plus the prefetches, whatever the length of the list.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(days=OVERDUE_AFTER_DAYS)
    gates = (
        ClassApproval.objects.filter(decision="", class_offering__status=ClassOffering.Status.PENDING)
        .select_related("class_offering__category__guild__guild_lead")
        .prefetch_related("class_offering__sessions", "class_offering__approvals", "class_offering__instructor_rows")
        .order_by("created_at")
    )
    by_offering: dict[int, ClassApproval] = {}
    for gate in gates:
        held = by_offering.get(gate.class_offering_id)
        if held is None or (gate.role == ClassApproval.Role.GUILD_LEAD and held.role != gate.role):
            by_offering[gate.class_offering_id] = gate
    rows: list[OverdueReview] = []
    for gate in by_offering.values():
        since = waiting_since(gate)
        if since > cutoff:
            continue
        offering = gate.class_offering
        sessions = [s.starts_at for s in offering.sessions.all()]
        starts = [when for when in sessions if when >= now]
        days_until = (timezone.localdate(min(starts)) - timezone.localdate(now)).days if starts else None
        rows.append(OverdueReview(offering, gate, (now - since).days, days_until, bool(sessions) and not starts))
    rows.sort(key=lambda r: (r.days_until_start is None, r.days_until_start or 0, -r.days_waiting))
    return rows
