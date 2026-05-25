"""Email notifications for billing events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import send_mail
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

    send_mail(
        subject=f"Past Lives Makerspace — Receipt for ${charge.amount}",
        message=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[member.primary_email],
        html_message=html_body,
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

    send_mail(
        subject=f"[Billing] Failed charge for {member.display_name} — ${charge.amount}",
        message=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=admin_emails,
    )
