from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import segno
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Buyable, Guild, GuildMembership, Member, Order
from .stripe_utils import create_checkout_session

if TYPE_CHECKING:
    from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# Guild pages (public)
# ---------------------------------------------------------------------------


def guild_list(request: HttpRequest) -> HttpResponse:
    guilds = Guild.objects.filter(is_active=True).annotate(member_count=Count("memberships")).order_by("name")
    return render(request, "membership/guild_list.html", {"guilds": guilds})


def guild_detail(request: HttpRequest, slug: str) -> HttpResponse:
    guild = get_object_or_404(Guild, slug=slug, is_active=True)

    context: dict = {
        "guild": guild,
        "member_count": guild.memberships.count(),
        "guild_lead": guild.guild_lead,
        "wishlist_items": guild.wishlist_items.filter(is_fulfilled=False),
        "buyables": guild.buyables.filter(is_active=True),
        "links": guild.links or [],
    }

    if request.user.is_authenticated:
        context["members"] = guild.memberships.select_related("user").order_by("-is_lead", "user__username")
        context["is_member"] = guild.memberships.filter(user=request.user).exists()
        context["is_lead"] = guild.memberships.filter(user=request.user, is_lead=True).exists()
    else:
        context["members"] = None
        context["is_member"] = False
        context["is_lead"] = False

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
    members = Member.objects.active().select_related("user", "membership_plan").order_by("full_legal_name")
    return render(request, "membership/member_directory.html", {"members": members})


def _get_lead_guild(request: HttpRequest, slug: str) -> Guild:
    """Return guild if the authenticated user is a lead or staff. Raise 403 otherwise."""
    from django.core.exceptions import PermissionDenied

    user = cast("User", request.user)
    guild = get_object_or_404(Guild, slug=slug)
    is_lead = GuildMembership.objects.filter(guild=guild, user=user, is_lead=True).exists()
    if not is_lead and not user.is_staff:
        raise PermissionDenied
    return guild
