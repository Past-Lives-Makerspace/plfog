"""Who still has to finish an Eventbrite booking on plfog, and the email that asks them to (#652).

Kept apart from :mod:`classes.eventbrite_orders` so the scheduled retry in ``core`` can reach it
without importing the forms and questions modules.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from django.db.models import CharField, Exists, OuterRef, QuerySet, Value
from django.db.models.functions import Cast, Concat
from django.utils import timezone

from classes.emails import eventbrite_finish_subject, send_eventbrite_finish_registration
from classes.models import (
    CAPACITY_CONSUMING_REGISTRATION_STATUSES,
    ClassOffering,
    Registration,
    RegistrationQuestion,
    Waiver,
)
from core.models import EventDelivery, TransactionalEmailLog

_SEAT_ALIAS = re.compile(r"(?P<local>.+)\+seat\d+@(?P<domain>[^@]+)")

FINISH_EVENT = "classes.eventbrite_finish_registration"
MAX_FINISH_ATTEMPTS = 3

logger = logging.getLogger(__name__)


def unfinished_registrations() -> QuerySet[Registration]:
    """Eventbrite seats in a class still going ahead whose liability waiver nobody has signed.

    The one rule for who still has finishing to do: the registration page shows the form to
    these and the retry resends their email. A cancelled class leaves its rows CONFIRMED, so
    it is excluded here; there is nothing to sign for a class that will not run.
    """
    return (
        Registration.objects.filter(
            source=Registration.Source.EVENTBRITE, status__in=CAPACITY_CONSUMING_REGISTRATION_STATUSES
        )
        .exclude(class_offering__status=ClassOffering.Status.CANCELLED)
        .exclude(waivers__kind=Waiver.Kind.LIABILITY)
    )


def needs_finishing(registration: Registration) -> bool:
    """Whether the registration page should show this registration the finish form."""
    return unfinished_registrations().filter(pk=registration.pk).exists()


def send_finish_email(registration: Registration, *, to: str) -> None:
    """Send the finish email for ``registration`` to ``to``; emit's ledger makes a repeat a no-op."""
    send_eventbrite_finish_registration(
        registration,
        to=to,
        offers_account=eventbrite_offers_account(registration),
        # The same filter as ``classes.questions.active_questions``.
        has_questions=RegistrationQuestion.objects.filter(is_active=True).exists(),
    )


@dataclass(frozen=True)
class ResendResult:
    """What one retry pass did: emails tried, and tickets that just reached the attempt cap."""

    tried: int
    capped: int


def resend_unsent_finish_emails(limit: int) -> ResendResult:
    """Resend the finish email to unfinished seats in upcoming classes that never got it.

    The email is best effort: a failed send gives its ``EventDelivery`` slot back, and a
    redelivered order seats nothing, so nothing else would try again. The slot doubles as
    the sent marker, and emit claims it before sending, so a seat whose email went out is
    never sent another.

    Each address is tried at most ``MAX_FINISH_ATTEMPTS`` times per class, counted from the
    FAILED rows on the email log (its address, the event key and the class's subject; the log
    has no registration link, so a buyer's seats in one class share the count). A SUPPRESSED
    row means the environment withheld it (staging), and retrying cannot change that. Newest
    seats go first and capped ones are skipped before they count, so they never crowd out a
    new seat. Reaching the cap logs a warning, once.
    """
    sent = EventDelivery.objects.filter(
        event_key=FINISH_EVENT,
        period=Concat(Value("reg:"), Cast(OuterRef("pk"), output_field=CharField()), Value(":eventbrite-finish")),
    )
    rows = (
        unfinished_registrations()
        .filter(class_offering__sessions__starts_at__gte=timezone.now())
        .exclude(Exists(sent))
        .select_related("class_offering")
        .distinct()
        .order_by("-pk")
    )
    tried = capped = 0
    for registration in rows:
        if tried >= limit:
            break
        to = seat_owner_email(registration.email)
        log = TransactionalEmailLog.objects.filter(
            trigger_kind=FINISH_EVENT, to_email=to, subject=eventbrite_finish_subject(registration.class_offering)
        )
        if log.filter(status=TransactionalEmailLog.Status.SUPPRESSED).exists():
            continue
        failures = log.filter(status=TransactionalEmailLog.Status.FAILED)
        if failures.count() >= MAX_FINISH_ATTEMPTS:
            continue
        send_finish_email(registration, to=to)
        tried += 1
        if failures.count() >= MAX_FINISH_ATTEMPTS:
            capped += 1
            logger.warning(
                "Gave up on the finish registering email for registration %s (%s) after %s failed sends.",
                registration.pk,
                to,
                MAX_FINISH_ATTEMPTS,
            )
    return ResendResult(tried=tried, capped=capped)


def seat_owner_email(email: str) -> str:
    """The buyer's own address behind a ``local+seatN@domain`` seat, or ``email`` itself."""
    match = _SEAT_ALIAS.fullmatch(email)
    return f"{match['local']}@{match['domain']}" if match else email


def eventbrite_offers_account(registration: Registration) -> bool:
    """Whether the finish page offers this Eventbrite ticket an account: the one place that decides.

    Felix decided (2026-10-07) that an Eventbrite buyer gets a Guest account even while the
    site is invite only, since the ticket is already paid for; bookings on the site itself
    keep the invite only guard. A ``+seatN`` seat is never offered one: the address is an
    alias nobody owns, and the account would be minted on it.
    """
    return _SEAT_ALIAS.fullmatch(registration.email) is None
