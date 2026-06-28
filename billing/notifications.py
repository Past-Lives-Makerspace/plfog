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
    """Notify admins (email) and the member (in-app) when a charge fails.

    Emits one ``tab_charge_failed`` event covering both audiences: the admin email
    (preserved shell) goes via ``email_to`` to the FOG_ADMINS-resolved addresses —
    the Phase-4 consistency change that collapses the old ``BILLING_ADMIN_EMAILS``
    static list into the role×scope resolver layer — while the resolver's in-app row
    goes to the member's linked user (the member's own ``tab_charge_failed`` email is
    opt-in/default-off, so they keep getting only the bell row as before). One emit
    logs exactly one ``tab_charge_failed`` activity row and eliminates the prior
    un-suppressed ``dispatch`` double-send.
    """
    from core.events import resolvers
    from core.events.registry import Recipients
    from core.events.senders import emit_with_email_shell

    member = charge.tab.member
    template_context = {
        "member": member,
        "charge": charge,
        "dashboard_url": _member_url(reverse("billing_admin_dashboard")),
    }

    admin_emails = [user.email for user, _ in resolvers.resolve(Recipients.FOG_ADMINS, {})]

    emit_with_email_shell(
        "tab_charge_failed",
        actor=member.user,
        target=charge,
        context={"member": member},
        subject=f"[Billing] Failed charge for {member.display_name} — ${charge.amount}",
        text_template="billing/email/charge_failed_admin.txt",
        html_template="billing/email/charge_failed_admin.html",
        template_context=template_context,
        in_app_title="Tab charge failed",
        in_app_body="A charge to your tab failed — please update your payment method.",
        url="/tab/",
        email_to=admin_emails,
        period=f"charge:{charge.pk}",
    )
