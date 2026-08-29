"""Views for billing — payment method setup, Stripe AJAX endpoints, admin actions."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from billing import stripe_utils, webhook_handlers
from classes import webhook_handlers as classes_webhook_handlers
from membership import webhook_handlers as membership_webhook_handlers
from billing.exceptions import TabLimitExceededError, TabLockedError
from billing.forms import CONTEXT_ADMIN_DASHBOARD, TabItemForm
from billing.models import BillingSettings, Tab, TabCharge, TabEntry
from hub.view_as import billing_admin_access_required, fog_admin_required, refund_authority_required

logger = logging.getLogger(__name__)

# Fan-in lists for Checkout events: each registered handler self-filters on
# ``metadata.kind``, so one Stripe event reaches every app that might own it.
_CHECKOUT_COMPLETED_HANDLERS = [
    classes_webhook_handlers.handle_checkout_session_completed,
    membership_webhook_handlers.handle_checkout_session_completed,
]
_CHECKOUT_EXPIRED_HANDLERS = [
    membership_webhook_handlers.handle_checkout_session_expired,
]


def _dispatch_checkout_completed(event: dict[str, Any]) -> None:
    """Deliver ``checkout.session.completed`` to each registered kind-filtered handler."""
    for handler in _CHECKOUT_COMPLETED_HANDLERS:
        handler(event)


def _dispatch_checkout_expired(event: dict[str, Any]) -> None:
    """Deliver ``checkout.session.expired`` to each registered kind-filtered handler."""
    for handler in _CHECKOUT_EXPIRED_HANDLERS:
        handler(event)


# Map Stripe event types to handler functions
_WEBHOOK_HANDLERS = {
    "setup_intent.succeeded": webhook_handlers.handle_setup_intent_succeeded,
    "payment_intent.succeeded": webhook_handlers.handle_payment_intent_succeeded,
    "payment_intent.payment_failed": webhook_handlers.handle_payment_intent_failed,
    "payment_method.detached": webhook_handlers.handle_payment_method_detached,
    "payment_method.updated": webhook_handlers.handle_payment_method_updated,
    "charge.dispute.created": webhook_handlers.handle_charge_dispute_created,
    "checkout.session.completed": _dispatch_checkout_completed,
    "checkout.session.expired": _dispatch_checkout_expired,
    "charge.refunded": classes_webhook_handlers.handle_charge_refunded,
    "refund.updated": classes_webhook_handlers.handle_refund_updated,
}


@login_required
def setup_payment_method(request: HttpRequest) -> HttpResponse:
    """Page with Stripe Elements for adding/replacing a payment method."""
    from core.models import SiteConfiguration

    if not SiteConfiguration.load().my_tab_enabled:
        django_messages.info(request, "My Tab isn't available right now.")
        return redirect("home")

    from membership.models import Member

    member: Member | None = getattr(request.user, "member", None)
    if member is None:
        return redirect("hub_tab_detail")

    tab, _created = Tab.objects.get_or_create(member=member)

    return render(
        request,
        "billing/setup_payment_method.html",
        {
            "tab": tab,
            "stripe_publishable_key": BillingSettings.load().active_publishable_key,
        },
    )


@login_required
@require_POST
def create_setup_intent_api(request: HttpRequest) -> JsonResponse:
    """AJAX endpoint — creates a Stripe SetupIntent and returns the client_secret."""
    from membership.models import Member

    member: Member | None = getattr(request.user, "member", None)
    if member is None:
        return JsonResponse({"error": "No membership found."}, status=400)

    tab, _created = Tab.objects.get_or_create(member=member)
    customer_id = tab.get_or_create_stripe_customer()
    result = stripe_utils.create_setup_intent(customer_id=customer_id)
    return JsonResponse(result)


@login_required
@require_POST
def confirm_setup(request: HttpRequest) -> HttpResponse:
    """Post-setup callback — updates Tab with the new payment method details."""
    from membership.models import Member

    member: Member | None = getattr(request.user, "member", None)
    if member is None:
        return redirect("hub_tab_detail")

    tab, _created = Tab.objects.get_or_create(member=member)

    payment_method_id = request.POST.get("payment_method_id", "")
    if not payment_method_id:
        return redirect("billing_setup_payment_method")

    tab.set_payment_method(payment_method_id)
    return redirect("hub_tab_detail")


@login_required
@require_POST
def remove_payment_method(request: HttpRequest) -> HttpResponse:
    """Detach the payment method from Stripe and clear Tab fields."""
    from membership.models import Member

    member: Member | None = getattr(request.user, "member", None)
    if member is None:
        return redirect("hub_tab_detail")

    tab, _created = Tab.objects.get_or_create(member=member)
    tab.clear_payment_method()
    return redirect("hub_tab_detail")


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    """Stripe webhook endpoint — verifies signature and dispatches to handlers."""
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        event = stripe_utils.construct_webhook_event(payload=payload, sig_header=sig_header)
    except Exception:
        logger.exception("Webhook signature verification failed.")
        return HttpResponse(status=400)

    event_type = event.type if hasattr(event, "type") else event.get("type", "")
    handler = _WEBHOOK_HANDLERS.get(event_type)

    if handler:
        try:
            event_data = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            handler(event_data)
        except Exception:
            logger.exception("Webhook handler error for event %s", event_type)
            return HttpResponse(status=500)
    else:
        logger.debug("Unhandled webhook event type: %s", event_type)

    return HttpResponse(status=200)


def _payments_panel_context(request: HttpRequest) -> dict[str, object]:
    """Shared context for the Payments tab and its standalone table partial.

    The same filters feed the page render, the ``refund-done`` HTMX refresh, and
    the CSV export, so all three always agree on what "the current view" means.
    """
    from urllib.parse import urlencode

    from billing.payments_panel import build_payments_ledger, parse_window
    from hub.view_as import has_refund_authority

    source = request.GET.get("source", "all")
    status = request.GET.get("status", "all")
    window = parse_window(request.GET.get("start", ""), request.GET.get("end", ""))
    view_as = request.view_as  # type: ignore[attr-defined]
    viewer_is_fog_admin = view_as.has_actual("admin")
    ledger = build_payments_ledger(
        window=window,
        source=source,
        status=status,
        viewer_is_admin=viewer_is_fog_admin,
    )
    payments_query = urlencode(
        {
            "source": source,
            "status": status,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
        }
    )
    return {
        "ledger": ledger,
        "payments_source": source,
        "payments_status": status,
        "payments_start": window.start.isoformat(),
        "payments_end": window.end.isoformat(),
        "payments_query": payments_query,
        "viewer_has_refund_authority": has_refund_authority(request),
        "viewer_is_fog_admin": viewer_is_fog_admin,
    }


@billing_admin_access_required
def admin_tab_dashboard(request: HttpRequest) -> HttpResponse:
    """Admin payments dashboard — tabbed view of billing data.

    The tab set is a function of ``(my_tab_enabled, role)``. With My Tab off, the
    Overview and Open Tabs tabs (100% tab-ledger content) disappear and Payments
    becomes the first and default tab; the Settings and Stripe tabs stay admin-only
    in every state (they configure Stripe, which powers class and orientation
    payments regardless of the flag — config never hides behind its own feature).
    """
    from django.contrib import admin as django_admin

    from billing.forms import BillingSettingsForm, ConnectPlatformSettingsForm, ReconciliationSettingsForm
    from billing.models import BillingSettings, Product
    from core.models import SiteConfiguration
    from membership.models import Guild

    view_as = request.view_as  # type: ignore[attr-defined]
    viewer_is_fog_admin = view_as.has_actual("admin")

    # Tab set as a function of (flag, role). Settings/Stripe stay admin-only in every state.
    tabs_on = SiteConfiguration.load().my_tab_enabled
    default_tab = "overview" if tabs_on else "payments"
    allowed = {"overview", "open-tabs", "payments"} if tabs_on else {"payments"}
    if viewer_is_fog_admin:
        allowed |= {"settings", "stripe", "reconciliation"}

    active_tab = request.GET.get("tab", default_tab)
    # A non-admin probing Settings/Stripe/Reconciliation is an access question → 403
    # (money-disbursement config stays actual-admin only); a feature-hidden tab
    # (?tab=overview with tabs off) is a feature question → fall back.
    if active_tab in {"settings", "stripe", "reconciliation"} and not viewer_is_fog_admin:
        return HttpResponse("Admin access required.", status=403)
    if active_tab not in allowed:
        active_tab = default_tab

    context: dict[str, object] = {
        **django_admin.site.each_context(request),
        "active_tab": active_tab,
        "viewer_is_fog_admin": viewer_is_fog_admin,
    }

    # --- Overview + Open Tabs context (tab-ledger tables — only when My Tab is on) ---
    # No point walking three Tab tables for tabs that cannot render with the flag off; the
    # template never references these keys when tabs_on is false (the guarding {% if %} is false).
    if tabs_on:
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total_outstanding = TabEntry.objects.pending().aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]

        collected_this_month = TabCharge.objects.filter(
            status=TabCharge.Status.SUCCEEDED,
            charged_at__gte=month_start,
        ).aggregate(total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField()))["total"]

        failed_count = TabCharge.objects.filter(status=TabCharge.Status.FAILED).count()
        locked_count = Tab.objects.filter(is_locked=True).count()

        outstanding_tabs = (
            Tab.objects.filter(
                entries__tab_charge__isnull=True,
                entries__voided_at__isnull=True,
            )
            .distinct()
            .select_related("member")
        )

        failed_charges = (
            TabCharge.objects.filter(status=TabCharge.Status.FAILED)
            .select_related("tab__member")
            .order_by("-created_at")[:20]
        )

        # --- Open Tabs tab ---
        # Annotate with pending balance so we can exclude $0 tabs
        _pending_balance = Coalesce(
            Sum(
                "entries__amount",
                filter=Q(entries__tab_charge__isnull=True, entries__voided_at__isnull=True),
            ),
            Value(Decimal("0.00")),
            output_field=DecimalField(),
        )
        tab_filter = request.GET.get("filter", "outstanding")
        if tab_filter == "all":
            open_tabs = Tab.objects.select_related("member").order_by("member__full_legal_name")
        elif tab_filter == "failed":
            open_tabs = Tab.objects.filter(charges__status=TabCharge.Status.FAILED).distinct().select_related("member")
        else:  # outstanding (default)
            open_tabs = (
                Tab.objects.filter(
                    entries__tab_charge__isnull=True,
                    entries__voided_at__isnull=True,
                )
                .distinct()
                .select_related("member")
            )
        open_tabs = open_tabs.annotate(_balance=_pending_balance).exclude(_balance__lte=Decimal("0.00"))

        # --- Add Charge modal form ---
        add_charge_form = TabItemForm(context=CONTEXT_ADMIN_DASHBOARD, user=request.user)

        context.update(
            {
                "tab_filter": tab_filter,
                # Overview
                "total_outstanding": total_outstanding,
                "collected_this_month": collected_this_month,
                "failed_count": failed_count,
                "locked_count": locked_count,
                "outstanding_tabs": outstanding_tabs,
                "failed_charges": failed_charges,
                # Open Tabs
                "open_tabs": open_tabs,
                # Shared
                "add_charge_form": add_charge_form,
            }
        )

    # --- Settings + Stripe tab context (admin config + product overview; flag-independent) ---
    settings_obj = BillingSettings.load()
    context.update(
        {
            "settings_form": BillingSettingsForm(instance=settings_obj),
            "connect_platform_form": ConnectPlatformSettingsForm(instance=settings_obj),
            "reconciliation_settings_form": ReconciliationSettingsForm(instance=settings_obj),
            "billing_settings": settings_obj,
            "products": Product.objects.select_related("guild").order_by("guild__name", "name"),
            "guilds": Guild.objects.filter(is_active=True).order_by("name"),
        }
    )

    # --- Payments tab (built only when active — it walks two money tables) ---
    if active_tab == "payments":
        context.update(_payments_panel_context(request))

    # --- Reconciliation tab (admin-only; walks all three streams + voting) ---
    if active_tab == "reconciliation":
        context.update(_reconciliation_context(request))

    return render(request, "billing/admin_dashboard.html", context)


def _reconciliation_context(request: HttpRequest) -> dict[str, object]:
    """Shared context for the Reconciliation tab, its table partial, and the print/CSV exports."""
    from urllib.parse import urlencode

    from billing.models import ReconciliationSnapshot
    from billing.payments_panel import parse_window
    from billing.reconciliation import build_reconciliation
    from billing.reports import build_report

    window = parse_window(request.GET.get("start", ""), request.GET.get("end", ""))
    view_as = request.view_as  # type: ignore[attr-defined]
    viewer_is_fog_admin = view_as.has_actual("admin")
    result = build_reconciliation(window=window)
    _rows, payout_summary, admin_total = build_report(start_date=window.start, end_date=window.end)
    query = urlencode({"start": window.start.isoformat(), "end": window.end.isoformat()})
    return {
        "reconciliation": result,
        "reconciliation_start": window.start.isoformat(),
        "reconciliation_end": window.end.isoformat(),
        "reconciliation_query": query,
        "reconciliation_date_error": (
            "End date must be on or after the start date." if window.end < window.start else ""
        ),
        "reconciliation_payout_summary": payout_summary,
        "reconciliation_admin_total": admin_total,
        "reconciliation_snapshots": ReconciliationSnapshot.objects.all(),
        "viewer_is_fog_admin": viewer_is_fog_admin,
    }


@fog_admin_required
def reconciliation_table(request: HttpRequest) -> HttpResponse:
    """The reconciliation allocation table alone — the HTMX refresh target."""
    return render(request, "billing/partials/reconciliation_table.html", _reconciliation_context(request))


@fog_admin_required
def admin_reconciliation_csv(request: HttpRequest) -> StreamingHttpResponse:
    """Streaming CSV of the per-recipient allocation for the current window."""
    from billing.payments_panel import parse_window
    from billing.reconciliation import build_reconciliation, stream_reconciliation_csv

    window = parse_window(request.GET.get("start", ""), request.GET.get("end", ""))
    return stream_reconciliation_csv(build_reconciliation(window=window))


@fog_admin_required
def reconciliation_print(request: HttpRequest) -> HttpResponse:
    """Print-optimized allocation table (Print -> Save as PDF from the browser)."""
    from billing.payments_panel import parse_window
    from billing.reconciliation import build_reconciliation

    window = parse_window(request.GET.get("start", ""), request.GET.get("end", ""))
    result = build_reconciliation(window=window)
    return render(
        request,
        "billing/reconciliation_print.html",
        {"reconciliation": result, "generated_at": timezone.now()},
    )


@billing_admin_access_required
def billing_admin_payments_table(request: HttpRequest) -> HttpResponse:
    """The payments ledger table alone — the ``refund-done`` HTMX refresh target."""
    return render(request, "billing/partials/payments_table.html", _payments_panel_context(request))


@billing_admin_access_required
def admin_payments_csv(request: HttpRequest) -> StreamingHttpResponse:
    """Streaming CSV of the payments ledger, same filters via GET."""
    from billing.payments_panel import build_payments_ledger, parse_window, stream_payments_csv

    window = parse_window(request.GET.get("start", ""), request.GET.get("end", ""))
    view_as = request.view_as  # type: ignore[attr-defined]
    ledger = build_payments_ledger(
        window=window,
        source=request.GET.get("source", "all"),
        status=request.GET.get("status", "all"),
        viewer_is_admin=view_as.has_actual("admin"),
    )
    return stream_payments_csv(ledger)


def _render_orientation_refund_form(request: HttpRequest, booking: Any, form: Any) -> HttpResponse:
    """Render the orientation refund modal body — the retry confirm when the latest attempt failed.

    Mirrors the classes refund partial: the FAILED state's only action is Retry
    (the failed row is the anchor); otherwise the editable amount/reason form.
    """
    from billing.models import PaymentRefund

    failed_refund = None
    if booking.refund_state == "failed":
        failed_refund = booking.refunds.filter(status=PaymentRefund.Status.FAILED).first()
    return render(
        request,
        "billing/partials/orientation_refund_form.html",
        {"booking": booking, "form": form, "failed_refund": failed_refund},
    )


@refund_authority_required
def payment_orientation_refund_form(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """GET partial — the orientation refund modal body, loaded via HTMX by the Payments panel."""
    from django.shortcuts import get_object_or_404

    from billing.forms import OrientationRefundForm
    from membership.models import OrientationBooking

    booking = get_object_or_404(OrientationBooking.objects.select_related("slot", "guild", "member"), pk=booking_pk)
    return _render_orientation_refund_form(request, booking, OrientationRefundForm(booking=booking))


@refund_authority_required
@require_POST
def payment_orientation_refund(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """Issue a real Stripe refund for an orientation booking — 204 + toast + ``refund-done``.

    Validation errors re-render the form partial in place. A Stripe rejection is
    loud: an error toast carries Stripe's message and the modal stays open,
    re-rendered in the failed state whose action is Retry.
    """
    from django.shortcuts import get_object_or_404

    from billing.exceptions import RefundError
    from billing.forms import OrientationRefundForm
    from billing.models import PaymentRefund
    from hub.toast import trigger_client_event, trigger_toast
    from membership.models import OrientationBooking

    booking = get_object_or_404(OrientationBooking.objects.select_related("slot", "guild", "member"), pk=booking_pk)
    form = OrientationRefundForm(request.POST, booking=booking)
    if not form.is_valid():
        return _render_orientation_refund_form(request, booking, form)
    try:
        refund = booking.issue_refund(
            amount_cents=form.amount_cents,
            reason=form.cleaned_data["reason"],
            actor=request.user,
        )
    except RefundError as exc:
        booking.refresh_from_db()
        response = _render_orientation_refund_form(request, booking, OrientationRefundForm(booking=booking))
        trigger_toast(response, f"Refund failed: {exc}", "error")
        return response
    response = HttpResponse(status=204)
    if refund.status == PaymentRefund.Status.SUCCEEDED:
        trigger_toast(response, f"Refunded ${form.cleaned_data['amount']:.2f}.", "success")
    else:
        # Stripe accepted the refund but hasn't settled it; refund.updated will.
        trigger_toast(response, "Refund sent. Stripe is processing it.", "success")
    trigger_client_event(response, "refund-done")
    return response


@refund_authority_required
@require_POST
def payment_refund_retry(request: HttpRequest, refund_pk: int) -> HttpResponse:
    """Retry a failed refund from the panel — 204 + toast + ``refund-done`` on success.

    A Stripe rejection is loud: an error toast carries Stripe's message and the
    modal stays open (no ``refund-done``, which would close it) — the row keeps
    its FAILED state with the fresh failure reason for the next attempt.
    """
    from billing import refunds as refunds_service
    from billing.exceptions import RefundError
    from billing.models import PaymentRefund
    from django.shortcuts import get_object_or_404
    from hub.toast import trigger_client_event, trigger_toast

    refund = get_object_or_404(PaymentRefund, pk=refund_pk)
    try:
        result = refunds_service.retry_refund(refund, actor=request.user)
    except RefundError as exc:
        response = HttpResponse(status=204)
        trigger_toast(response, f"Refund failed: {exc}", "error")
        return response
    response = HttpResponse(status=204)
    if result.status == PaymentRefund.Status.SUCCEEDED:
        trigger_toast(response, f"Refunded ${result.amount_cents / 100:.2f}.", "success")
    else:
        # Stripe accepted the retry but hasn't settled it; refund.updated will.
        trigger_toast(response, "Refund sent. Stripe is processing it.", "success")
    trigger_client_event(response, "refund-done")
    return response


@fog_admin_required
def admin_add_tab_entry(request: HttpRequest) -> HttpResponse:
    """Admin quick-add: add a charge to any member's tab.

    Two paths:
      * Product selected — splits come from the product; no formset needed.
      * Custom entry (no product) — ``CustomSplitFormSet`` is required and
        validated before any entry is created.
    """
    from django.contrib import admin

    from billing.forms import CustomSplitFormSet
    from core.models import SiteConfiguration
    from membership.models import Guild

    # New tab charges make no sense with My Tab off — members cannot see or pay them and
    # bill_tabs skips the run. Gate the view server-side so a mid-session flag flip cannot
    # leave an already-open dashboard POSTing entries onto a frozen ledger. Covers both the
    # modal POST and the standalone add-entry page. (Flag on = structural no-op.)
    if not SiteConfiguration.load().my_tab_enabled:
        django_messages.info(request, "My Tab is off, so new tab charges can't be added right now.")
        return redirect("billing_admin_dashboard")

    def _render(form: TabItemForm, splits_formset: Any) -> HttpResponse:
        context = {
            **admin.site.each_context(request),
            "form": form,
            "splits_formset": splits_formset,
            "all_guilds": Guild.objects.filter(is_active=True).order_by("name"),
        }
        return render(request, "billing/admin_add_entry.html", context)

    if request.method == "POST":
        form = TabItemForm(request.POST, context=CONTEXT_ADMIN_DASHBOARD, user=request.user)
        splits_formset = CustomSplitFormSet(data=request.POST, prefix="splits")
        if form.is_valid():
            member = form.cleaned_data["member"]
            tab, _created = Tab.objects.get_or_create(member=member)
            product = form.cleaned_data.get("product")
            try:
                if product is not None:
                    form.save(tab=tab)
                else:
                    if not splits_formset.is_valid():
                        django_messages.error(
                            request,
                            f"Invalid splits: {splits_formset.non_form_errors() or 'see row errors'}",
                        )
                        return _render(form, splits_formset)
                    form.save(tab=tab, splits=splits_formset.to_split_dicts())  # type: ignore[attr-defined]  # formset_factory loses _BaseCustomSplitFormSet methods
            except (TabLockedError, TabLimitExceededError) as exc:
                django_messages.error(request, str(exc))
                return _render(form, splits_formset)
            django_messages.success(request, f"Added ${form.cleaned_data['amount']} to {member.display_name}'s tab.")
            return redirect("billing_admin_dashboard")
    else:
        form = TabItemForm(context=CONTEXT_ADMIN_DASHBOARD, user=request.user)
        splits_formset = CustomSplitFormSet(prefix="splits")

    return _render(form, splits_formset)


@billing_admin_access_required
def billing_admin_tab_detail_api(request: HttpRequest, tab_pk: int) -> JsonResponse:
    """Return JSON tab detail for the tab detail modal."""
    try:
        tab = Tab.objects.select_related("member").get(pk=tab_pk)
    except Tab.DoesNotExist:
        from django.http import Http404

        raise Http404

    pending_entries = list(
        tab.entries.filter(tab_charge__isnull=True, voided_at__isnull=True)
        .select_related("product__guild")
        .order_by("-created_at")
        .values("description", "amount", "created_at")
    )

    charge_history = list(
        tab.charges.exclude(status=TabCharge.Status.PENDING)
        .order_by("-created_at")[:20]
        .values("amount", "status", "charged_at", "stripe_receipt_url")
    )

    payment_method = ""
    if tab.payment_method_brand and tab.payment_method_last4:
        payment_method = f"{tab.payment_method_brand} {tab.payment_method_last4}"

    return JsonResponse(
        {
            "member_name": tab.member.display_name,
            "balance": f"{tab.current_balance:.2f}",
            "limit": f"{tab.effective_tab_limit:.2f}",
            "payment_method": payment_method,
            "is_locked": tab.is_locked,
            "locked_reason": tab.locked_reason,
            "tab_pk": tab.pk,
            "pending_entries": [
                {
                    "description": e["description"],
                    "amount": f"{e['amount']:.2f}",
                    "date": e["created_at"].strftime("%-d %b") if e["created_at"] else "",
                }
                for e in pending_entries
            ],
            "charge_history": [
                {
                    "amount": f"{c['amount']:.2f}",
                    "status": c["status"],
                    "date": c["charged_at"].strftime("%-d %b %Y") if c["charged_at"] else "—",
                    "receipt_url": c["stripe_receipt_url"] or "",
                }
                for c in charge_history
            ],
        }
    )


@fog_admin_required
@require_POST
def billing_admin_save_settings(request: HttpRequest) -> HttpResponse:
    """Save BillingSettings singleton from the Settings tab form."""
    from billing.forms import BillingSettingsForm

    settings_obj = BillingSettings.load()
    form = BillingSettingsForm(request.POST, instance=settings_obj)
    if form.is_valid():
        form.save()
        django_messages.success(request, "Billing settings saved.")
    else:
        django_messages.error(request, "Invalid settings — please check the form.")
    return redirect("/billing/admin/dashboard/?tab=settings")


@billing_admin_access_required
@require_POST
def billing_admin_retry_charge(request: HttpRequest, charge_pk: int) -> JsonResponse:
    """Immediately retry a single failed charge. Returns JSON with new status."""
    import uuid as _uuid

    try:
        charge = TabCharge.objects.select_related("tab").get(pk=charge_pk)
    except TabCharge.DoesNotExist:
        from django.http import Http404

        raise Http404

    idempotency_key = f"admin-retry-{charge.pk}-{_uuid.uuid4()}"
    success = charge.execute_stripe_charge(idempotency_key)
    if success:
        return JsonResponse({"status": "succeeded"})
    logger.exception("Admin retry failed for charge %s.", charge.pk)
    return JsonResponse({"status": "failed"})


@billing_admin_access_required
def admin_reports(request: HttpRequest) -> HttpResponse:
    """Retired: the Reports page folded into the Reconciliation tab (301 to it).

    The per-guild payout summary now renders as a section of the admin-only
    Reconciliation tab. Old links / bookmarks land there instead of 404ing.
    """
    from urllib.parse import urlencode

    from billing.payments_panel import parse_window

    window = parse_window(request.GET.get("start_date", "") or request.GET.get("start", ""), "")
    query = urlencode({"tab": "reconciliation", "start": window.start.isoformat(), "end": window.end.isoformat()})
    return redirect(f"/billing/admin/dashboard/?{query}", permanent=True)


@billing_admin_access_required
def admin_reports_csv(request: HttpRequest) -> HttpResponse:
    """Retired: superseded by the reconciliation CSV (a superset). 301 to it."""
    from urllib.parse import urlencode

    from billing.payments_panel import parse_window

    window = parse_window(request.GET.get("start_date", "") or request.GET.get("start", ""), "")
    query = urlencode({"start": window.start.isoformat(), "end": window.end.isoformat()})
    return redirect(f"/billing/admin/reconciliation/export/csv/?{query}", permanent=True)


@fog_admin_required
@require_POST
def billing_admin_save_reconciliation_settings(request: HttpRequest) -> HttpResponse:
    """Save the reconciliation split percentages from the Settings tab."""
    from billing.forms import ReconciliationSettingsForm

    settings_obj = BillingSettings.load()
    form = ReconciliationSettingsForm(request.POST, instance=settings_obj)
    if form.is_valid():
        form.save()
        django_messages.success(request, "Reconciliation splits saved.")
    else:
        for errors in form.errors.values():
            for error in errors:
                django_messages.error(request, error)
    return redirect("/billing/admin/dashboard/?tab=settings")


_ADJUSTMENT_KINDS = {"tab", "class", "orientation"}


def _reconciliation_adjustment_context(source_kind: str, source_pk: int) -> dict[str, object]:
    """Build the adjust-modal form (existing adjustment prefilled, else the configured split)."""
    from billing.forms import TransactionAdjustmentForm
    from billing.models import BillingSettings, TransactionAdjustment
    from billing.reconciliation import _class_percents, _orientation_percents

    existing = TransactionAdjustment.objects.filter(source_kind=source_kind, source_pk=source_pk).first()
    initial: dict[str, object] = {}
    if existing is not None:
        initial["is_omitted"] = existing.is_omitted
        initial["reason"] = existing.reason
    if source_kind != "tab":
        settings_obj = BillingSettings.load()
        percents = _class_percents(settings_obj) if source_kind == "class" else _orientation_percents(settings_obj)
        if existing is not None and existing.override_percents is not None:
            percents = {k: existing.override_percents.get(k, v) for k, v in percents.items()}
        for key, value in percents.items():
            initial.setdefault(f"percent_{key}", value)
    form = TransactionAdjustmentForm(source_kind=source_kind, source_pk=source_pk, initial=initial)
    return {"form": form, "source_kind": source_kind, "source_pk": source_pk, "has_existing": existing is not None}


@fog_admin_required
def reconciliation_adjust_form(request: HttpRequest, source_kind: str, source_pk: int) -> HttpResponse:
    """GET the adjust-modal body for one transaction."""
    from django.http import Http404

    if source_kind not in _ADJUSTMENT_KINDS:
        raise Http404
    context = _reconciliation_adjustment_context(source_kind, source_pk)
    return render(request, "billing/partials/reconciliation_adjust_form.html", context)


@fog_admin_required
@require_POST
def reconciliation_adjust(request: HttpRequest, source_kind: str, source_pk: int) -> HttpResponse:
    """Save (create or update) a transaction adjustment -> 204 + toast + refresh trigger."""
    from django.http import Http404

    from billing.forms import TransactionAdjustmentForm
    from hub.toast import trigger_client_event, trigger_toast

    if source_kind not in _ADJUSTMENT_KINDS:
        raise Http404
    form = TransactionAdjustmentForm(request.POST, source_kind=source_kind, source_pk=source_pk)
    if not form.is_valid():
        context = {"form": form, "source_kind": source_kind, "source_pk": source_pk, "has_existing": True}
        return render(request, "billing/partials/reconciliation_adjust_form.html", context)
    form.save(actor=request.user)
    response = HttpResponse(status=204)
    trigger_toast(response, "Adjustment saved.", "success")
    trigger_client_event(response, "reconciliation-changed")
    return response


@fog_admin_required
@require_POST
def reconciliation_clear(request: HttpRequest, source_kind: str, source_pk: int) -> HttpResponse:
    """Remove a transaction adjustment (back to the standard split) -> 204 + toast + refresh."""
    from billing.models import TransactionAdjustment
    from hub.toast import trigger_client_event, trigger_toast

    TransactionAdjustment.objects.filter(source_kind=source_kind, source_pk=source_pk).delete()
    response = HttpResponse(status=204)
    trigger_toast(response, "Adjustment removed.", "success")
    trigger_client_event(response, "reconciliation-changed")
    return response


@fog_admin_required
@require_POST
def reconciliation_snapshot_take(request: HttpRequest) -> HttpResponse:
    """Freeze the current window's reconciliation into a snapshot."""
    from billing.models import ReconciliationSnapshot
    from billing.payments_panel import parse_window

    window = parse_window(request.GET.get("start", ""), request.GET.get("end", ""))
    title = request.POST.get("title", "").strip()
    snapshot = ReconciliationSnapshot.take(
        period_start=window.start, period_end=window.end, title=title, actor=request.user
    )
    django_messages.success(request, f"Snapshot taken for {snapshot.period_start:%b %Y}.")
    from urllib.parse import urlencode

    query = urlencode({"tab": "reconciliation", "start": window.start.isoformat(), "end": window.end.isoformat()})
    return redirect(f"/billing/admin/dashboard/?{query}")


@fog_admin_required
def reconciliation_snapshot_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Read-only render of a frozen snapshot's allocation."""
    from django.contrib import admin as django_admin
    from django.shortcuts import get_object_or_404

    from billing.models import ReconciliationSnapshot
    from billing.reconciliation import result_from_snapshot

    snapshot = get_object_or_404(ReconciliationSnapshot, pk=pk)
    context = {
        **django_admin.site.each_context(request),
        "snapshot": snapshot,
        "reconciliation": result_from_snapshot(snapshot),
    }
    return render(request, "billing/reconciliation_snapshot_detail.html", context)


@fog_admin_required
@require_POST
def reconciliation_snapshot_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a frozen snapshot (the record only; no payments change)."""
    from django.shortcuts import get_object_or_404

    from billing.models import ReconciliationSnapshot
    from core.models import SiteActivity

    snapshot = get_object_or_404(ReconciliationSnapshot, pk=pk)
    SiteActivity.log(SiteActivity.Kind.RECONCILIATION_SNAPSHOT_DELETED, actor=request.user)
    snapshot.delete()
    django_messages.success(request, "Snapshot deleted.")
    return redirect("/billing/admin/dashboard/?tab=reconciliation")


@fog_admin_required
@require_POST
def billing_test_platform_connection(request: HttpRequest) -> JsonResponse:
    """AJAX: verify a candidate platform Stripe secret key.

    Used by the "Test connection" button on the Settings tab. Always returns
    200 so the frontend can render results inline.
    """
    secret_key = request.POST.get("secret_key", "").strip()
    if not secret_key:
        return JsonResponse({"ok": False, "error": "Secret key is required."})
    if not (secret_key.startswith("sk_test_") or secret_key.startswith("sk_live_") or secret_key.startswith("rk_")):
        return JsonResponse({"ok": False, "error": "Key must start with sk_test_, sk_live_, or rk_."})
    try:
        result = stripe_utils.verify_platform_credentials(secret_key)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": f"Stripe rejected the key: {exc}"})
    return JsonResponse({"ok": True, **result})


@fog_admin_required
@require_POST
def billing_save_connect_platform(request: HttpRequest) -> HttpResponse:
    """Save the platform Stripe credentials to BillingSettings."""
    from billing.forms import ConnectPlatformSettingsForm

    settings_obj = BillingSettings.load()
    form = ConnectPlatformSettingsForm(request.POST, instance=settings_obj)
    if form.is_valid():
        form.save()
        django_messages.success(request, "Stripe platform settings saved.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                django_messages.error(request, f"{field}: {error}")
    return redirect("/billing/admin/dashboard/?tab=stripe")
