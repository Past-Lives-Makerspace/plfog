from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import BuyableForm, MemberProfileForm, OrderNoteForm
from .models import Buyable, GuildMembership, Order
from .views import _get_active_member, _get_lead_guild

if TYPE_CHECKING:
    from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    member = _get_active_member(request)
    user = cast("User", request.user)

    active_leases = list(member.active_leases.select_related("space"))
    guild_memberships = GuildMembership.objects.filter(user=user).select_related("guild").order_by("guild__name")
    recent_orders = Order.objects.filter(user=user).select_related("buyable__guild").order_by("-created_at")[:5]

    return render(
        request,
        "membership/hub/dashboard.html",
        {
            "member": member,
            "active_leases": active_leases,
            "guild_memberships": guild_memberships,
            "recent_orders": recent_orders,
            "active_tab": "dashboard",
        },
    )


# ---------------------------------------------------------------------------
# Profile (moved from views.py)
# ---------------------------------------------------------------------------


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
    return render(
        request,
        "membership/profile_edit.html",
        {"member": member, "form": form, "active_tab": "profile"},
    )


# ---------------------------------------------------------------------------
# User orders (moved from views.py)
# ---------------------------------------------------------------------------


@login_required
def user_orders(request: HttpRequest) -> HttpResponse:
    user = cast("User", request.user)
    orders = Order.objects.filter(user=user).select_related("buyable__guild").order_by("-created_at")
    return render(
        request,
        "membership/user_orders.html",
        {"orders": orders, "active_tab": "orders"},
    )


# ---------------------------------------------------------------------------
# My guilds
# ---------------------------------------------------------------------------


@login_required
def my_guilds(request: HttpRequest) -> HttpResponse:
    _get_active_member(request)
    user = cast("User", request.user)
    guild_memberships = GuildMembership.objects.filter(user=user).select_related("guild").order_by("guild__name")
    return render(
        request,
        "membership/hub/guilds.html",
        {"guild_memberships": guild_memberships, "active_tab": "guilds"},
    )


# ---------------------------------------------------------------------------
# My spaces
# ---------------------------------------------------------------------------


@login_required
def my_spaces(request: HttpRequest) -> HttpResponse:
    member = _get_active_member(request)
    active_leases = member.active_leases.select_related("space")
    return render(
        request,
        "membership/hub/spaces.html",
        {"active_leases": active_leases, "member": member, "active_tab": "spaces"},
    )


# ---------------------------------------------------------------------------
# Guild lead management (moved from views.py)
# ---------------------------------------------------------------------------


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
