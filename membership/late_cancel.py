"""The late cancellation policy: one resolver for the fee, the notice window and lateness (#456).

Site Settings holds the switch, the notice members are told and the grace they are not told;
a guild or a piece of equipment holds only the amount. Nothing else reads those columns or
computes lateness: every surface asks :func:`policy_for` (a booking or a reservation),
:func:`policy_for_type` (a bookable orientation type) or :func:`policy_for_equipment` (a
reservation not yet made) and renders the copy through :func:`booking_sentence` and
:func:`cancel_sentence`. Part 1 ships the policy and the copy; charging (the fee record, the
Stripe Checkout session, the block until paid) is part 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from membership.models import Equipment, EquipmentReservation, Guild, OrientationBooking, OrientationType


@dataclass(frozen=True)
class LateCancelPolicy:
    """The governing fee and window for one booking, reservation, type or equipment.

    Attributes:
        fee_cents: The fee a late self cancel would cost. 0 means no fee applies.
        notice_hours: How far ahead members are told to cancel (the number they see).
        grace_hours: The hidden allowance inside the notice; never shown to members.
    """

    fee_cents: int
    notice_hours: int
    grace_hours: int

    @property
    def applies(self) -> bool:
        """True when a fee is configured and the site switch is on (a positive fee)."""
        return self.fee_cents > 0

    def is_late(self, starts_at: datetime, *, now: datetime | None = None) -> bool:
        """Whether a cancel at ``now`` (default: this moment) is inside the fee window.

        Late means the booking starts in less than ``notice_hours - grace_hours``. Exactly at
        that line is not late, and a booking that has already started is not late (a fee is for
        the seat that could still have gone to someone else).
        """
        moment = now if now is not None else timezone.now()
        if moment >= starts_at:
            return False
        return starts_at - moment < timedelta(hours=self.notice_hours - self.grace_hours)

    @property
    def fee_display(self) -> str:
        """The fee in dollars with cents, e.g. "$15.00"."""
        return f"${Decimal(self.fee_cents) / 100:.2f}"


def _policy(fee_cents: int) -> LateCancelPolicy:
    """Wrap an owner's fee in the site's window; the switch off zeroes the fee everywhere."""
    from core.models import SiteConfiguration

    site = SiteConfiguration.load()
    return LateCancelPolicy(
        fee_cents=fee_cents if site.late_cancel_fees_enabled else 0,
        notice_hours=site.late_cancel_notice_hours,
        grace_hours=site.late_cancel_grace_hours,
    )


def _guild_fee_cents(guild: Guild) -> int:
    """The guild's configured fee; a guild with no settings row has never set one."""
    from membership.models import GuildOrientationSettings

    try:
        return int(guild.orientation_settings.late_cancel_fee_cents)
    except GuildOrientationSettings.DoesNotExist:
        return 0


def policy_for_equipment(equipment: Equipment) -> LateCancelPolicy:
    """The policy for reserving ``equipment`` (or booking an orientation it owns)."""
    return _policy(equipment.late_cancel_fee_cents)


def policy_for_type(orientation_type: OrientationType) -> LateCancelPolicy:
    """The policy for booking ``orientation_type``: its guild's fee, or its equipment's when it owns it."""
    from membership.models import Equipment

    owner = orientation_type.owner
    if isinstance(owner, Equipment):
        return policy_for_equipment(owner)
    return _policy(_guild_fee_cents(owner))


def policy_for(target: OrientationBooking | EquipmentReservation) -> LateCancelPolicy:
    """The policy governing a booking or a reservation that already exists.

    A guild orientation booking follows its guild's fee, an equipment owned booking and an
    equipment reservation follow the equipment's fee. The site switch off means fee 0.
    """
    from membership.models import EquipmentReservation

    if isinstance(target, EquipmentReservation):
        return policy_for_equipment(target.equipment)
    return policy_for_type(target.orientation_type)


def _hours(count: int) -> str:
    return f"{count} hour" if count == 1 else f"{count} hours"


def booking_sentence(policy: LateCancelPolicy) -> str:
    """The line members see before they commit and in their confirmation, or "" with no fee."""
    if not policy.applies:
        return ""
    return (
        f"Cancel at least {_hours(policy.notice_hours)} ahead. Cancelling later costs a {policy.fee_display} late fee."
    )


def cancel_sentence(policy: LateCancelPolicy) -> str:
    """The self cancel modal's line for a cancel inside the window, or "" with no fee.

    Written now for part 2's modals; the notice reads as a compound adjective ("24 hour notice
    window"), so it never pluralises.
    """
    if not policy.applies:
        return ""
    return (
        f"This is inside the {policy.notice_hours} hour notice window, so a {policy.fee_display} "
        "late cancellation fee applies. You'll get a link to pay it."
    )
