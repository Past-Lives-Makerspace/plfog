"""Remove seeded demo classes, and everything hanging off them, from a real database (#983).

Demo content is every class whose slug starts with ``demo-`` (whatever seeded it, including
the old uncommitted cart seeder ``demo_data --remove`` never knew about), every ``[DEMO]``
category, and the registrations, sessions, refunds and payouts below them. Demo persona
members and their users stay: deleting them is out of scope.

``plan_demo_scrub`` reads and lists, and writes nothing. ``scrub_demo_data`` re-plans inside
one transaction and deletes exactly that list, or refuses and deletes nothing. A second run
finds nothing to remove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Sized
from typing import TYPE_CHECKING, Any, cast

from django.contrib.admin.utils import NestedObjects
from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models import Q

if TYPE_CHECKING:
    from django.db.models import Model

DEMO_EMAIL_SUFFIX = "@pastlives.demo"


class DemoScrubRefusedError(Exception):
    """The scrub found something it must not delete; nothing was removed."""


@dataclass(frozen=True)
class StripeBackedRegistration:
    """A demo registration with a real Stripe payment behind it: named on its own in the dry run."""

    pk: int
    class_slug: str
    paid_cents: int
    refunded_cents: int


@dataclass(frozen=True)
class DemoScrubPlan:
    """Every row the scrub removes, and every reason it would refuse.

    ``deletes`` maps a model label ("classes.ClassOffering") to the sorted primary keys that
    go. ``cleared_links`` counts rows outside the demo set that only lose a link to it (a
    SET_NULL foreign key). ``blockers`` non-empty means the scrub refuses.
    """

    deletes: dict[str, list[int]]
    cleared_links: dict[str, int]
    stripe_backed: list[StripeBackedRegistration]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        """How many rows the scrub deletes, across every model."""
        return sum(len(pks) for pks in self.deletes.values())

    @property
    def is_empty(self) -> bool:
        """True when there is nothing left to remove (a second run)."""
        return not self.deletes


def _demo_querysets() -> tuple[Any, Any, Any, Any, Any]:
    """The roots of the scrub: payouts, refunds, registrations, classes, categories.

    Payouts and refunds PROTECT a registration and a registration PROTECTs its class, so
    each is collected as a root of its own rather than reached by cascade.
    """
    from billing.models import PaymentRefund, Payout
    from classes.models import DEMO_SLUG_PREFIX, DEMO_TITLE_PREFIX, Category, ClassOffering, Registration

    offerings = ClassOffering.objects.demo()
    registrations = Registration.objects.filter(class_offering__in=offerings)
    payouts = Payout.objects.filter(registration__in=registrations)
    refunds = PaymentRefund.objects.filter(registration__in=registrations)
    categories = Category.objects.filter(Q(slug__startswith=DEMO_SLUG_PREFIX) | Q(name__startswith=DEMO_TITLE_PREFIX))
    return payouts, refunds, registrations, offerings, categories


def _label(obj: Model) -> str:
    return str(obj._meta.label)


def plan_demo_scrub() -> DemoScrubPlan:
    """List every row the scrub would remove, without writing anything.

    Returns:
        The plan. Its ``blockers`` name anything that makes the scrub refuse: a demo payout
        Stripe may already have sent, a demo registrant on a real class, or a real row the
        delete would have to take with it.
    """
    return _collect()[0]


def _collect() -> tuple[DemoScrubPlan, NestedObjects]:
    """Build the plan and the collector holding exactly its rows; the real run deletes through it."""
    from classes.models import ClassOffering, Registration

    payouts, refunds, registrations, offerings, categories = _demo_querysets()
    blockers: list[str] = []
    warnings: list[str] = []

    for payout in payouts.exclude(stripe_transfer_id="", attempted_at__isnull=True):
        blockers.append(
            f"billing.Payout {payout.pk} on registration {payout.registration_id} has a Stripe transfer"
            " (sent or attempted); settle it by hand first."
        )
    strays = (
        Registration.objects.filter(email__iendswith=DEMO_EMAIL_SUFFIX)
        .exclude(class_offering__in=offerings)
        .order_by("pk")
    )
    for reg in strays.select_related("class_offering"):
        blockers.append(
            f"classes.Registration {reg.pk} is a demo registrant on the real class '{reg.class_offering.slug}'."
        )
    listed = offerings.exclude(eventbrite_event_id="").exclude(
        eventbrite_sync_state=ClassOffering.EventbriteSyncState.ENDED
    )
    for offering in listed:
        warnings.append(f"'{offering.slug}' is still listed on Eventbrite; end that listing there.")

    collector = NestedObjects(using=DEFAULT_DB_ALIAS)
    for qs in (payouts, refunds, registrations, offerings, categories):
        collector.collect(list(qs))
    collected: set[Model] = {obj for objs in collector.data.values() for obj in objs}
    for obj in sorted(collector.protected - collected, key=lambda o: (_label(o), o.pk)):
        blockers.append(f"{_label(obj)} {obj.pk} is not demo data but holds on to a demo row.")

    # Collector.add never stores an empty batch, so every model here has rows to remove.
    deletes = {str(model._meta.label): sorted(obj.pk for obj in objs) for model, objs in collector.data.items()}
    cleared: dict[str, int] = {}
    for (fld, _value), batches in collector.field_updates.items():
        # Each batch is a queryset or list of rows; the stubs type it as one model.
        count = sum(len(cast(Sized, batch)) for batch in batches)
        if count:
            key = f"{fld.model._meta.label}.{fld.name}"
            cleared[key] = cleared.get(key, 0) + count

    stripe_backed = [
        StripeBackedRegistration(
            pk=reg.pk,
            class_slug=reg.class_offering.slug,
            paid_cents=reg.amount_paid_cents,
            refunded_cents=reg.amount_refunded_cents,
        )
        for reg in registrations.exclude(stripe_payment_id="")
        .select_related("class_offering")
        .prefetch_related("refunds")
        .order_by("pk")
    ]
    plan = DemoScrubPlan(
        deletes=dict(sorted(deletes.items())),
        cleared_links=dict(sorted(cleared.items())),
        stripe_backed=stripe_backed,
        blockers=blockers,
        warnings=warnings,
    )
    return plan, collector


def scrub_demo_data() -> DemoScrubPlan:
    """Delete exactly the rows ``plan_demo_scrub`` lists, in one transaction.

    The delete runs through the same collector the plan was read from, so it removes what the
    dry run listed and nothing else. Foreign keys are checked at commit, so the order of
    PROTECT-linked rows inside the transaction does not matter.

    Returns:
        The plan that was carried out (empty when there was nothing to remove).

    Raises:
        DemoScrubRefusedError: The plan has blockers; nothing was deleted.
    """
    with transaction.atomic():
        plan, collector = _collect()
        if plan.blockers:
            raise DemoScrubRefusedError("; ".join(plan.blockers))
        if not plan.is_empty:
            collector.delete()
    return plan
