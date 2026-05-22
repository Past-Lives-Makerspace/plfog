"""Read-only queries for the /account/ dashboard.

These do not mutate; they join Registration to ClassSession via
ClassOffering so the dashboard can show what a user is signed up for.
The user→registration link uses three paths in order: an explicit
Member.user link, a verified EmailAddress on the user, or the user's
own primary email — so guests who later sign up still find their old
bookings.
"""

from __future__ import annotations

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.db.models.functions import Lower
from django.utils import timezone

from classes.models import ClassSession, Registration


def _emails_for(user) -> list[str]:
    """Every verified email plus the user's primary login email, lowercased."""
    emails: set[str] = set()
    if user.email:
        emails.add(user.email.lower())
    if hasattr(user, "emailaddress_set"):
        for ea in user.emailaddress_set.filter(verified=True):
            emails.add(ea.email.lower())
    return sorted(emails)


def _registrations_for(user) -> QuerySet[Registration]:
    """All registrations linked to this user via Member FK or by case-insensitive email match."""
    emails = _emails_for(user)
    qs = Registration.objects.annotate(_email_lower=Lower("email")).select_related(
        "class_offering", "class_offering__instructor"
    )
    filters = Q()
    member = getattr(user, "member", None)
    if member is not None:
        filters |= Q(member=member)
    if emails:
        filters |= Q(_email_lower__in=emails)
    if not filters.children:
        return qs.none()
    return qs.filter(filters)


def upcoming_registrations(user) -> QuerySet[Registration]:
    """Registrations on offerings whose schedule still has at least one future session.

    Cancelled and refunded statuses are excluded — confirmed, pending, and
    waitlisted are visible.
    """
    now = timezone.now()
    future_sessions = ClassSession.objects.filter(
        class_offering=OuterRef("class_offering"),
        starts_at__gte=now,
    )
    statuses_visible = [
        Registration.Status.CONFIRMED,
        Registration.Status.PENDING,
        Registration.Status.WAITLISTED,
    ]
    return (
        _registrations_for(user)
        .filter(status__in=statuses_visible)
        .annotate(_has_future=Exists(future_sessions))
        .filter(_has_future=True)
        .order_by("class_offering__sessions__starts_at")
        .distinct()
    )


def past_registrations(user) -> QuerySet[Registration]:
    """Registrations whose last session is now in the past.

    Cancelled and refunded are excluded — only classes the user actually attended.
    """
    now = timezone.now()
    future_sessions = ClassSession.objects.filter(
        class_offering=OuterRef("class_offering"),
        starts_at__gte=now,
    )
    statuses_visible = [Registration.Status.CONFIRMED, Registration.Status.WAITLISTED]
    return (
        _registrations_for(user)
        .filter(status__in=statuses_visible)
        .annotate(_has_future=Exists(future_sessions))
        .filter(_has_future=False)
        .order_by("-class_offering__sessions__starts_at")
        .distinct()
    )


def paid_registrations(user) -> QuerySet[Registration]:
    """Registrations with a recorded Stripe payment — for the receipts tab.

    Free-class registrations (no Stripe payment) are excluded by design;
    the design has a dedicated empty state for users who only booked free classes.
    """
    return _registrations_for(user).exclude(stripe_payment_id="").order_by("-confirmed_at").distinct()
