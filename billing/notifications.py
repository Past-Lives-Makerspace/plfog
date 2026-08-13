"""Email notifications for billing events.

Both senders run on the ``core.events.emit`` spine (Phase 4): one event each, the
preserved email shell handed in as the EMAIL-channel override while the in-app row
renders from the explicit title/body. This eliminates the old double-send (a
dedicated ``core.email.send`` paired with an un-suppressed ``notifications.dispatch``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from billing.models import TabCharge

logger = logging.getLogger(__name__)


def _member_url(path: str) -> str:
    """Turn a relative hub path into an absolute URL using the member-site base."""
    return f"{settings.MEMBER_BASE_URL.rstrip('/')}{path}"


def send_receipt(charge: TabCharge) -> None:
    """Send an itemized receipt email to the member after a successful charge.

    Emits the ``tab_charged`` event: the receipt email (preserved shell) goes to the
    member's ``primary_email`` via ``email_to`` — a guest/alias-safe explicit address,
    decoupled from the resolver — while the resolver's in-app row goes to the member's
    linked user. This collapses the old dedicated send + un-suppressed ``dispatch``
    into one event, eliminating the double-send the audit found.
    """
    member = charge.tab.member
    if not member.primary_email:
        logger.warning("Cannot send receipt for charge %s: member has no email.", charge.pk)
        return

    entries = charge.entries.all().order_by("created_at")
    template_context = {
        "member": member,
        "charge": charge,
        "entries": entries,
        "charged_at": charge.charged_at or timezone.now(),
        "billing_history_url": _member_url(reverse("hub_tab_history")),
    }

    from core.events.senders import emit_with_email_shell

    emit_with_email_shell(
        "tab_charged",
        actor=member.user,
        target=charge,
        context={"member": member},
        subject=f"Past Lives Makerspace — Receipt for ${charge.amount}",
        text_template="billing/email/receipt.txt",
        html_template="billing/email/receipt.html",
        template_context=template_context,
        in_app_title="Tab charged",
        in_app_body=f"${charge.amount} was charged to your tab.",
        url="/tab/",
        email_to=member.primary_email,
        period=f"charge:{charge.pk}",
    )

    charge.receipt_sent_at = timezone.now()
    charge.save(update_fields=["receipt_sent_at"])


def notify_admin_charge_failed(charge: TabCharge) -> None:
    """Notify the member (in-app) and the Billing Administrators (email + in-app) on a failure.

    Two events on the spine:

    * ``tab_charge_failed`` (member-facing) writes the member's bell row — their own
      charge-failed email is opt-in/default-off — and logs the single TAB_CHARGE_FAILED
      SiteActivity.
    * ``billing.charge_failed_admin`` (admin-facing) carries the preserved admin email
      shell to the Billing Administrators via the BILLING_APPROVERS resolver (capability
      holders by default; other admins only if they opt in), replacing the old static
      all-admin ``email_to`` blast. It logs no activity, so exactly one activity row is
      written across the pair.
    """
    from core.events.emit import emit
    from core.events.senders import emit_with_email_shell

    member = charge.tab.member

    emit(
        "tab_charge_failed",
        actor=member.user,
        target=charge,
        context={"member": member},
        title="Tab charge failed",
        body="A charge to your tab failed — please update your payment method.",
        url="/tab/",
        period=f"charge:{charge.pk}",
    )

    template_context = {
        "member": member,
        "charge": charge,
        "dashboard_url": _member_url(reverse("billing_admin_dashboard")),
    }
    emit_with_email_shell(
        "billing.charge_failed_admin",
        actor=member.user,
        target=charge,
        context={},
        subject=f"[Billing] Failed charge for {member.display_name} — ${charge.amount}",
        text_template="billing/email/charge_failed_admin.txt",
        html_template="billing/email/charge_failed_admin.html",
        template_context=template_context,
        in_app_title="Tab charge failed",
        in_app_body=f"A tab charge for {member.display_name} failed — follow up in the billing dashboard.",
        url="/tab/",
        email_trigger_kind="tab_charge_failed",
        period=f"charge:{charge.pk}:admin",
    )
