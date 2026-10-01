"""Clear the way for the one-seat-per-person constraint added in 0066.

A double-clicked registration used to create a second row, and each row ate a
seat. Those rows cannot coexist with ``uq_registration_seat_email``, so this
migration cancels the extras first.

Nothing is deleted. Each extra row is flipped to CANCELLED with an exact marker
in ``cancellation_reason`` naming this migration and the status it held, which is
what makes the reverse genuine: it reads the marker back and restores exactly the
status it took away.

Both updates go through a raw queryset ``.update()``. ``Registration.save()``
logs activity and dispatches member notifications on a status change, and a
migration must not email anyone.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.db import migrations
from django.utils import timezone

# Frozen copies of the statuses that hold a seat, as of this migration. A
# migration is a historical record: it must not follow later edits to
# ``classes.models.SEAT_HOLDING_REGISTRATION_STATUSES``.
SEAT_HOLDING = ("confirmed", "pending", "waitlisted")
CANCELLED = "cancelled"

# Which row of a duplicate group keeps the seat. Lowest rank first, then earliest
# ``registered_at``, then lowest pk. The spec's rule is earliest-wins; the rank in
# front of it means a paid row never loses its seat to an unpaid one.
STATUS_RANK = {"confirmed": 0, "pending": 1, "waitlisted": 2}

MARKER = "Duplicate signup for this class, cancelled automatically by migration classes.0065."


def carries_real_money(row: dict[str, Any]) -> bool:
    """Whether cancelling this row would strand a payment nobody is told about.

    ``stripe_payment_id`` is the signal. It is written only by the webhook, on a charge
    Stripe actually took.

    ``amount_paid_cents`` is **not** a payment signal by itself. The register view
    stamps it at session-mint time, before anyone has paid ("provisional; webhook is
    canonical"), so every PENDING duplicate on a paid class carries a non-zero amount
    while being entirely unpaid — which is precisely the shape this migration exists to
    clean up. Reading it as money would stop the migration on rows that owe nothing, and
    a migration that raises is a failed deploy.

    It does mean something once a row is CONFIRMED: that is the only state in which
    ``Registration.mark_paid`` can settle a seat in cash, leaving real money behind with
    no Stripe id to show for it.
    """
    if row["stripe_payment_id"]:
        return True
    return row["status"] == "confirmed" and bool(row["amount_paid_cents"])


def reason_for(status: str) -> str:
    """The exact ``cancellation_reason`` stamped on a row cancelled from ``status``."""
    return f"{MARKER} Prior status: {status}."


def cancel_duplicate_signups(apps: Any, schema_editor: Any) -> None:
    """Cancel every seat-holding row beyond the first for each (class, email).

    Raises:
        RuntimeError: When a row that would be cancelled carries money. Two paid rows
            for one person in one class is what the worst version of the double-click
            produced: two sessions, both charged. Cancelling one of those silently
            would strand a real payment with no refund and no email, so the migration
            stops and names the rows for a human. Production carried no such rows when
            this was written (both violating groups were unpaid and pending), so the
            loud path costs nothing there and protects QA and any restore.
    """
    Registration = apps.get_model("classes", "Registration")
    rows = Registration.objects.filter(status__in=SEAT_HOLDING).values(
        "pk", "class_offering_id", "email", "status", "registered_at", "amount_paid_cents", "stripe_payment_id"
    )

    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["class_offering_id"], row["email"])].append(row)

    losers: dict[str, list[int]] = defaultdict(list)
    paid_losers: list[dict[str, Any]] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda row: (STATUS_RANK[row["status"]], row["registered_at"], row["pk"]))
        for row in group[1:]:
            if carries_real_money(row):
                paid_losers.append(row)
                continue
            losers[row["status"]].append(row["pk"])

    if paid_losers:
        detail = "; ".join(
            f"pk={row['pk']} class={row['class_offering_id']} email={row['email']} "
            f"status={row['status']} paid={row['amount_paid_cents']} "
            f"payment={row['stripe_payment_id'] or 'none'}"
            for row in sorted(paid_losers, key=lambda row: row["pk"])
        )
        raise RuntimeError(
            "Refusing to cancel a duplicate registration that carries a recorded payment. Cancelling "
            "one of these would strand money: the row is a duplicate, so it loses its seat, and this "
            "migration writes with a raw update() that sends no email and logs no activity. Rows: "
            f"{detail}. Decide each one by hand (refund it and cancel it, or keep it and cancel its "
            "twin) and run this migration again."
        )

    now = timezone.now()
    for status, pks in losers.items():
        Registration.objects.filter(pk__in=pks).update(
            status=CANCELLED,
            cancellation_reason=reason_for(status),
            cancelled_at=now,
        )


def restore_duplicate_signups(apps: Any, schema_editor: Any) -> None:
    """Put every row this migration cancelled back exactly as it was."""
    Registration = apps.get_model("classes", "Registration")
    for status in SEAT_HOLDING:
        Registration.objects.filter(cancellation_reason=reason_for(status)).update(
            status=status,
            cancellation_reason="",
            cancelled_at=None,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0064_video_provider_help_text"),
    ]

    operations = [
        migrations.RunPython(cancel_duplicate_signups, restore_duplicate_signups),
    ]
