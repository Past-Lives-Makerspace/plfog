"""Instructor and orientor payouts through Stripe Connect Express (#662).

Part 1 is the setup: who is a payee, which state their Payouts tab shows, and whether a
nudge points them at it. ``BillingSettings.connect_enabled`` is the one switch; while it is
off nothing here shows. No charge path reads that switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from billing.models import BillingSettings, PayoutAccount

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Member


def payouts_on() -> bool:
    """True when an admin has turned payouts on in Payments, Stripe tab."""
    return BillingSettings.load().connect_enabled


def has_earning(member: Member) -> bool:
    """True when anyone has paid for a class ``member`` teaches or an orientation they ran."""
    from classes.models import Registration
    from membership.models import OrientationBooking

    return (
        Registration.objects.filter(class_offering__instructor=member, amount_paid_cents__gt=0).exists()
        or OrientationBooking.objects.filter(oriented_by=member, amount_paid_cents__gt=0).exists()
    )


def is_payee(request: HttpRequest, member: Member) -> bool:
    """Whether ``member`` gets a Payouts tab: they can teach classes, can run orientations, or have an earning."""
    from membership.permissions import manages_orientations

    return member.can_create_classes or manages_orientations(request) or has_earning(member)


@dataclass(frozen=True)
class PayoutsTab:
    """What the Settings, Payouts tab renders: the payee's account in the current mode, if any."""

    account: PayoutAccount | None

    @property
    def state(self) -> str:
        """``not_set_up`` or one of ``PayoutAccount.Status``'s values, the key the template branches on."""
        return "not_set_up" if self.account is None else self.account.status


def settings_tab(request: HttpRequest, member: Member | None) -> PayoutsTab | None:
    """The Payouts tab for this request, or None when it should not show at all."""
    if member is None or not payouts_on() or not is_payee(request, member):
        return None
    return PayoutsTab(account=PayoutAccount.for_member(member))


def needs_nudge(member: Member) -> bool:
    """True while payouts are on and ``member`` has not finished Stripe signup.

    The caller decides the audience (the teaching portal is for teachers, the orientations
    nudge for members who run orientations); this only answers "connected yet?".
    """
    if not payouts_on():
        return False
    account = PayoutAccount.for_member(member)
    return account is None or not account.is_connected
