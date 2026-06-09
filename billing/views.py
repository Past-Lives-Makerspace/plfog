"""Views for billing — payment method setup, Stripe AJAX endpoints, admin actions."""

from __future__ import annotations

import logging
from decimal import Decimal

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
from billing.exceptions import TabLimitExceededError, TabLockedError
from billing.forms import CONTEXT_ADMIN_DASHBOARD, TabItemForm
from billing.models import BillingSettings, Tab, TabCharge, TabEntry
from hub.view_as import fog_admin_required

logger = logging.getLogger(__name__)

# Map Stripe event types to handler functions
_WEBHOOK_HANDLERS = {
    "setup_intent.succeeded": webhook_handlers.handle_setup_intent_succeeded,
    "payment_intent.succeeded": webhook_handlers.handle_payment_intent_succeeded,
    "payment_intent.payment_failed": webhook_handlers.handle_payment_intent_failed,
    "payment_method.detached": webhook_handlers.handle_payment_method_detached,
    "payment_method.updated": webhook_handlers.handle_payment_method_updated,
    "charge.dispute.created": webhook_handlers.handle_charge_dispute_created,
    "checkout.session.completed": classes_webhook_handlers.handle_checkout_session_completed,
}


@login_required
def setup_payment_method(request: HttpRequest) -> HttpResponse:
    """Page with Stripe Elements for adding/replacing a payment method."""
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
            "stripe_publishable_key": BillingSettings.load().connect_platform_publishable_key,
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


_VALID_TABS = {"overview", "open-tabs", "settings", "stripe"}


@fog_admin_required
def admin_tab_dashboard(request: HttpRequest) -> HttpResponse:
    """Admin payments dashboard — five-tab view of billing data."""
    from django.contrib import admin as django_admin
    from billing.forms import BillingSettingsForm
    from billing.models import BillingSettings, Product
    from membership.models import Guild

    active_tab = request.GET.get("tab", "overview")
    if active_tab not in _VALID_TABS:
        active_tab = "overview"

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # --- Overview stats ---
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

    # --- Settings tab ---
    from billing.forms import ConnectPlatformSettingsForm

    settings_obj = BillingSettings.load()
    settings_form = BillingSettingsForm(instance=settings_obj)
    connect_platform_form = ConnectPlatformSettingsForm(instance=settings_obj)

    # --- Stripe tab (Settings tab since v1.5.0 — single platform account) ---
    products = Product.objects.select_related("guild").order_by("guild__name", "name")
    guilds = Guild.objects.filter(is_active=True).order_by("name")

    # --- Add Charge modal form ---
    add_charge_form = TabItemForm(context=CONTEXT_ADMIN_DASHBOARD, user=request.user)

    context = {
        **django_admin.site.each_context(request),
        "active_tab": active_tab,
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
        # Settings
        "settings_form": settings_form,
        "connect_platform_form": connect_platform_form,
        "billing_settings": settings_obj,
        # Stripe (platform settings + product overview)
        "products": products,
        "guilds": guilds,
        # Shared
        "add_charge_form": add_charge_form,
    }

    return render(request, "billing/admin_dashboard.html", context)


@fog_admin_required
def admin_add_tab_entry(request: HttpRequest) -> HttpResponse:
    """Admin quick-add: add a charge to any member's tab.

    Two paths:
      * Product selected — splits come from the product; no formset needed.
      * Custom entry (no product) — ``CustomSplitFormSet`` is required and
        validated before any entry is created.
    """
    from django.contrib import admin

    from typing import Any

    from billing.forms import CustomSplitFormSet
    from membership.models import Guild

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


@fog_admin_required
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


@fog_admin_required
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


@fog_admin_required
def admin_reports(request: HttpRequest) -> HttpResponse:
    """Reports page — filtered entry list + per-guild payout summary.

    When opened without any GET params, defaults to the current month so the
    page always shows data immediately.
    """
    from django.contrib import admin as django_admin

    from billing.reports import (
        CHARGE_TYPE_CHOICES,
        STATUS_CHOICES,
        ReportFilterForm,
        build_report,
    )
    from membership.models import Guild

    # Default to current month when no filters are provided
    now = timezone.now()
    month_start = now.replace(day=1).date()
    default_filters = {"start_date": month_start.isoformat(), "end_date": now.date().isoformat()}
    effective_filters = request.GET if request.GET else default_filters

    filter_form = ReportFilterForm(effective_filters)
    filter_kwargs = filter_form.filter_kwargs()
    rows, payout_summary, admin_total = build_report(**filter_kwargs)

    # Resolve multi-value fields to lists so the template can check membership
    _getlist = effective_filters.getlist if hasattr(effective_filters, "getlist") else lambda k: []
    selected_guilds = _getlist("guilds")
    selected_charge_types = _getlist("charge_type")
    selected_statuses = _getlist("status")

    context = {
        **django_admin.site.each_context(request),
        "filters": effective_filters,
        "selected_guilds": selected_guilds,
        "selected_charge_types": selected_charge_types,
        "selected_statuses": selected_statuses,
        "rows": rows,
        "payout_summary": payout_summary,
        "admin_total": admin_total,
        "guilds": Guild.objects.filter(is_active=True).order_by("name"),
        "charge_type_choices": CHARGE_TYPE_CHOICES,
        "status_choices": STATUS_CHOICES,
        "query_string": request.META.get("QUERY_STRING", ""),
    }
    return render(request, "billing/admin_reports.html", context)


@fog_admin_required
def admin_reports_csv(request: HttpRequest) -> StreamingHttpResponse:
    """Streaming CSV download for the reports page, same filters via GET."""
    from billing.reports import ReportFilterForm, stream_report_csv

    filter_form = ReportFilterForm(request.GET)
    return stream_report_csv(**filter_form.filter_kwargs())


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
