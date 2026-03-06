from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import segno
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import BuyableForm, MemberProfileForm
from .models import Buyable, Guild, Member

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

    active_leases = guild.active_leases.select_related("space", "content_type")
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


def buyable_qr(request: HttpRequest, slug: str, buyable_slug: str) -> HttpResponse:
    guild = get_object_or_404(Guild, slug=slug, is_active=True)
    get_object_or_404(Buyable, guild=guild, slug=buyable_slug, is_active=True)
    url = request.build_absolute_uri(f"/guilds/{slug}/buy/{buyable_slug}/")
    qr = segno.make(url)
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=4)
    return HttpResponse(buf.getvalue(), content_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Member pages (auth + active member only)
# ---------------------------------------------------------------------------


def _get_active_member(request: HttpRequest) -> Member:
    """Return active Member for the authenticated user. Raise 403 otherwise."""
    user = cast("User", request.user)
    try:
        member = Member.objects.select_related("membership_plan").get(user=user)
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
    """Return guild if the authenticated user is a lead or staff. Raise 403 otherwise.

    Callers must apply @login_required to ensure request.user is authenticated.
    """
    guild = get_object_or_404(Guild, slug=slug)
    if not request.user.is_authenticated or not guild.is_managed_by(request.user):
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
