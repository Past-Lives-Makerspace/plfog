"""Email notifications for billing events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

if TYPE_CHECKING:
    from billing.models import TabCharge

logger = logging.getLogger(__name__)


def send_receipt(charge: TabCharge) -> None:
    """Send an itemized receipt email to the member after a successful charge."""
    member = charge.tab.member
    if not member.primary_email:
        logger.warning("Cannot send receipt for charge %s: member has no email.", charge.pk)
        return

    entries = charge.entries.all().order_by("created_at")
    context = {
        "member": member,
        "charge": charge,
        "entries": entries,
        "charged_at": charge.charged_at or timezone.now(),
    }

    text_body = render_to_string("billing/email/receipt.txt", context)
    html_body = render_to_string("billing/email/receipt.html", context)

    from core import email as core_email

    email_log = core_email.send(
        to=member.primary_email,
        subject=f"Past Lives Makerspace — Receipt for ${charge.amount}",
        trigger_kind="billing.receipt",
        text_body=text_body,
        html_body=html_body,
    )
    from core.models import SiteActivity

    SiteActivity.log(
        SiteActivity.Kind.TAB_CHARGED,
        actor=member.user,
        target=charge,
        email_log=email_log,
    )

    if member.user is not None:
        from core import notifications

        notifications.dispatch(
            "tab_charged",
            [member.user],
            title="Tab charged",
            body=f"${charge.amount} was charged to your tab.",
            url="/tab/",
        )

    charge.receipt_sent_at = timezone.now()
    charge.save(update_fields=["receipt_sent_at"])


def notify_admin_charge_failed(charge: TabCharge) -> None:
    """Notify admins when a charge fails."""
    member = charge.tab.member
    context = {
        "member": member,
        "charge": charge,
    }

    text_body = render_to_string("billing/email/charge_failed_admin.txt", context)

    admin_emails = getattr(settings, "BILLING_ADMIN_EMAILS", [settings.DEFAULT_FROM_EMAIL])

    from core import email as core_email

    core_email.send(
        to=admin_emails,
        subject=f"[Billing] Failed charge for {member.display_name} — ${charge.amount}",
        trigger_kind="billing.charge_failed_admin",
        text_body=text_body,
    )
    from core.models import SiteActivity

    SiteActivity.log(SiteActivity.Kind.TAB_CHARGE_FAILED, actor=member.user, target=charge)

    if member.user is not None:
        from core import notifications

        notifications.dispatch(
            "tab_charge_failed",
            [member.user],
            title="Tab charge failed",
            body="A charge to your tab failed — please update your payment method.",
            url="/tab/",
        )
