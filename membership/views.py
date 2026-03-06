from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import segno
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import BuyableForm, MemberProfileForm, OrderNoteForm
from .models import Buyable, Guild, Member, Order
from .stripe_utils import create_checkout_session

if TYPE_CHECKING:
    from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# Guild pages (public)
# ---------------------------------------------------------------------------


def guild_list(request: HttpRequest) -> HttpResponse:
    guilds = Guild.objects.filter(is_active=True).order_by("name")
    return render(request, "membership/guild_list.html", {"guilds": guilds})


def guild_detail(request: HttpRequest, slug: str) -> HttpResponse:
    guild = get_object_or_404(Guild, slug=slug, is_active=True)

    context: dict = {
        "guild": guild,
        "guild_lead": guild.guild_lead,
        "wishlist_items": guild.wishlist_items.filter(is_fulfilled=False),
        "buyables": guild.buyables.filter(is_active=True),
        "links": guild.links or [],
        "is_lead": request.user.is_authenticated and guild.is_managed_by(request.user),
    }

    active_leases = guild.active_leases.select_related("space")
    spaces = [lease.space for lease in active_leases]
    if spaces:
        context["spaces"] = spaces

    return render(request, "membership/guild_detail.html", context)


# ---------------------------------------------------------------------------
# Buyable pages (public)
# ---------------------------------------------------------------------------


def buyable_detail(request: HttpRequest, slug: str, buyable_slug: str) -> HttpResponse:
    guild = get_object_or_404(Guild, slug=slug, is_active=True)
    buyable = get_object_or_404(Buyable, guild=guild, slug=buyable_slug, is_active=True)
    return render(
        request,
        "membership/buyable_detail.html",
        {"guild": guild, "buyable": buyable},
    )


def buyable_checkout(request: HttpRequest, slug: str, buyable_slug: str) -> HttpResponse:
    if request.method != "POST":
        return redirect("buyable_detail", slug=slug, buyable_slug=buyable_slug)

    guild = get_object_or_404(Guild, slug=slug, is_active=True)
    buyable = get_object_or_404(Buyable, guild=guild, slug=buyable_slug, is_active=True)
    quantity = int(request.POST.get("quantity", 1))
    if quantity < 1:
        quantity = 1

    success_url = request.build_absolute_uri("/checkout/success/") + "?session_id={CHECKOUT_SESSION_ID}"
    cancel_url = request.build_absolute_uri("/checkout/cancel/")

    session = create_checkout_session(
        buyable=buyable,
        quantity=quantity,
        success_url=success_url,
        cancel_url=cancel_url,
    )

    # Create pending order
    Order.objects.create(
        buyable=buyable,
        user=request.user if request.user.is_authenticated else None,
        quantity=quantity,
        amount=int(buyable.unit_price * 100) * quantity,
        stripe_checkout_session_id=session.id,
    )

    return redirect(cast(str, session.url))


def buyable_qr(request: HttpRequest, slug: str, buyable_slug: str) -> HttpResponse:
    guild = get_object_or_404(Guild, slug=slug, is_active=True)
    get_object_or_404(Buyable, guild=guild, slug=buyable_slug, is_active=True)
    url = request.build_absolute_uri(f"/guilds/{slug}/buy/{buyable_slug}/")
    qr = segno.make(url)
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=4)
    return HttpResponse(buf.getvalue(), content_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Stripe callbacks
# ---------------------------------------------------------------------------


def checkout_success(request: HttpRequest) -> HttpResponse:
    import stripe

    from .stripe_utils import get_stripe_key

    session_id = request.GET.get("session_id", "")
    order = None
    if session_id:
        stripe.api_key = get_stripe_key()
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            order = Order.objects.filter(stripe_checkout_session_id=session_id).first()
            if order and order.status != Order.Status.PAID:
                order.status = Order.Status.PAID
                order.paid_at = timezone.now()
                if session.customer_details and session.customer_details.email:
                    order.email = session.customer_details.email
                order.save()
        except stripe.StripeError:
            pass

    return render(request, "membership/checkout_success.html", {"order": order})


def checkout_cancel(request: HttpRequest) -> HttpResponse:
    return render(request, "membership/checkout_cancel.html")


# ---------------------------------------------------------------------------
# User orders (auth required)
# ---------------------------------------------------------------------------


@login_required
def user_orders(request: HttpRequest) -> HttpResponse:
    user = cast("User", request.user)
    orders = Order.objects.filter(user=user).select_related("buyable__guild").order_by("-created_at")
    return render(request, "membership/user_orders.html", {"orders": orders})


# ---------------------------------------------------------------------------
# Member pages (auth + active member only)
# ---------------------------------------------------------------------------


def _get_active_member(request: HttpRequest) -> Member:
    """Return active Member for the authenticated user. Raise 403 otherwise."""
    from django.core.exceptions import PermissionDenied

    user = cast("User", request.user)
    try:
        member = Member.objects.get(user=user)
    except Member.DoesNotExist:
        raise PermissionDenied
    if member.status != Member.Status.ACTIVE:
        raise PermissionDenied
    return member


@login_required
def member_directory(request: HttpRequest) -> HttpResponse:
    _get_active_member(request)
    members = Member.objects.active().select_related("user", "membership_plan").order_by("full_legal_name")
    return render(request, "membership/member_directory.html", {"members": members})


@login_required
def profile_edit(request: HttpRequest) -> HttpResponse:
    member = _get_active_member(request)
    if request.method == "POST":
        form = MemberProfileForm(request.POST, instance=member)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("profile_edit")
    else:
        form = MemberProfileForm(instance=member)
    return render(request, "membership/profile_edit.html", {"member": member, "form": form})


# ---------------------------------------------------------------------------
# Guild lead management (auth + guild lead only)
# ---------------------------------------------------------------------------


def _get_lead_guild(request: HttpRequest, slug: str) -> Guild:
    """Return guild if the authenticated user is a lead or staff. Raise 403 otherwise."""
    from django.core.exceptions import PermissionDenied

    guild = get_object_or_404(Guild, slug=slug)
    if not guild.is_managed_by(request.user):
        raise PermissionDenied
    return guild


@login_required
def guild_manage(request: HttpRequest, slug: str) -> HttpResponse:
    guild = _get_lead_guild(request, slug)
    buyables = guild.buyables.all()
    return render(request, "membership/guild_manage.html", {"guild": guild, "buyables": buyables})


@login_required
def buyable_add(request: HttpRequest, slug: str) -> HttpResponse:
    guild = _get_lead_guild(request, slug)
    if request.method == "POST":
        form = BuyableForm(request.POST, request.FILES)
        if form.is_valid():
            buyable = form.save(commit=False)
            buyable.guild = guild
            buyable.save()
            messages.success(request, f"Added {buyable.name}.")
            return redirect("guild_manage", slug=slug)
    else:
        form = BuyableForm()
    return render(request, "membership/buyable_form.html", {"guild": guild, "form": form})


@login_required
def buyable_edit(request: HttpRequest, slug: str, buyable_slug: str) -> HttpResponse:
    guild = _get_lead_guild(request, slug)
    buyable = get_object_or_404(Buyable, guild=guild, slug=buyable_slug)
    if request.method == "POST":
        form = BuyableForm(request.POST, request.FILES, instance=buyable)
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated {buyable.name}.")
            return redirect("guild_manage", slug=slug)
    else:
        form = BuyableForm(instance=buyable)
    return render(request, "membership/buyable_form.html", {"guild": guild, "form": form, "buyable": buyable})


@login_required
def guild_orders(request: HttpRequest, slug: str) -> HttpResponse:
    guild = _get_lead_guild(request, slug)
    orders = Order.objects.filter(buyable__guild=guild).select_related("buyable", "user").order_by("-created_at")
    return render(request, "membership/guild_orders.html", {"guild": guild, "orders": orders})


@login_required
def order_detail(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    guild = _get_lead_guild(request, slug)
    order = get_object_or_404(Order, pk=pk, buyable__guild=guild)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "fulfill":
            order.is_fulfilled = True
            order.fulfilled_by = cast("User", request.user)
            order.fulfilled_at = timezone.now()
            order.save()
            messages.success(request, "Order marked as fulfilled.")
        elif action == "notes":
            form = OrderNoteForm(request.POST, instance=order)
            if form.is_valid():  # pragma: no branch
                form.save()
                messages.success(request, "Notes updated.")
        return redirect("order_detail", slug=slug, pk=pk)

    form = OrderNoteForm(instance=order)
    return render(request, "membership/order_detail.html", {"guild": guild, "order": order, "form": form})
