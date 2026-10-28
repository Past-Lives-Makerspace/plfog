"""Renting a guild cubby from the guild page's Cubbies tab (#978).

Rent (the sheet an open cubby opens, then Stripe Checkout), the Checkout return and cancel
pages, and the Your Cubby card's Change Amount, Cancel Rental, Keep My Cubby and Manage
Payment. Every change is :mod:`membership.cubby_rentals`; these views parse, call and answer.

A rental's buttons reach only the renter's own rentals, or any for an admin (``view_as`` aware):
anyone else's crafted POST is a 404 (``CubbyRental.objects.for_actor``). A refusal that is not
about the amount (the cubby was just taken, the rental ended) lands back on the tab with a toast.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import stripe
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from hub.cubby_rental_forms import CubbyAmountForm
from hub.views import _get_member
from membership import cubby_rentals
from membership.models import Cubby, CubbyRental, Guild, cubby_dollars

if TYPE_CHECKING:
    from membership.models import Member

logger = logging.getLogger(__name__)


def _member(request: HttpRequest) -> Member:
    member = _get_member(request)
    if member is None:
        raise Http404("No member for this account.")
    return member


def _is_admin(request: HttpRequest) -> bool:
    return bool(getattr(getattr(request, "view_as", None), "is_admin", False))


def _go(request: HttpRequest, url: str) -> HttpResponse:
    """Send the browser to ``url``: ``HX-Redirect`` for htmx (an XHR cannot follow a cross origin 302), else a 302."""
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


def _back_to_tab(request: HttpRequest, guild: Guild) -> HttpResponse:
    return _go(request, cubby_rentals.tab_url(guild))


def _rentable_cubby(cubby_pk: int) -> Cubby:
    return get_object_or_404(Cubby.objects.active().with_renter().select_related("unit__guild"), pk=cubby_pk)


def _unavailable(cubby: Cubby, member: Member) -> str:
    """Why the sheet cannot offer ``cubby`` to ``member`` right now, or ""."""
    reason = cubby_rentals.unavailable_reason(cubby)
    if reason:
        return reason
    if cubby.renter_id == member.pk:
        return f"{cubby.label} is already yours."
    if cubby.renter_id is not None:
        return f"Someone just rented {cubby.label}. Pick another open cubby."
    return ""


def _rent_sheet(request: HttpRequest, cubby: Cubby, member: Member, form: CubbyAmountForm) -> HttpResponse:
    guild = cubby.unit.guild
    return render(
        request,
        "hub/cubbies/_rent_sheet.html",
        {
            "cubby": cubby,
            "guild": guild,
            "form": form,
            "unavailable": _unavailable(cubby, member),
            "pays_guild": guild.cubby_payee_id is not None,
            "charge_day": cubby_rentals.ordinal(timezone.localdate().day),
            "floor_message": cubby_rentals.amount_problem(cubby, 0),
        },
    )


@login_required
@require_GET
def hub_cubby_rent_sheet(request: HttpRequest, cubby_pk: int) -> HttpResponse:
    """The Rent This Cubby sheet for one cubby, loaded into the tab's modal."""
    member = _member(request)
    cubby = _rentable_cubby(cubby_pk)
    return _rent_sheet(request, cubby, member, CubbyAmountForm(cubby=cubby, auto_id="rent-%s"))


@login_required
@require_POST
def hub_cubby_rent(request: HttpRequest, cubby_pk: int) -> HttpResponse:
    """Continue to Payment: hold the cubby and go to Stripe; an amount refusal re-renders the sheet."""
    member = _member(request)
    cubby = _rentable_cubby(cubby_pk)
    form = CubbyAmountForm(request.POST, cubby=cubby, auto_id="rent-%s")
    if not form.is_valid():
        return _rent_sheet(request, cubby, member, form)
    try:
        checkout_url = cubby_rentals.start_rental(cubby, member, form.cleaned_data["amount_cents"])
    except cubby_rentals.AmountError as refusal:
        form.add_error(None, str(refusal))
        return _rent_sheet(request, cubby, member, form)
    except cubby_rentals.CubbyRentalError as refusal:
        messages.error(request, str(refusal))
        return _back_to_tab(request, cubby.unit.guild)
    return _go(request, checkout_url)


def _token_target(request: HttpRequest, token: str) -> tuple[CubbyRental | None, Cubby]:
    """The member's rental a checkout token names (None once released) and its cubby."""
    member = _member(request)
    try:
        rental_pk, cubby_pk = cubby_rentals.read_checkout_token(token)
    except signing.BadSignature as bad:
        raise Http404("That checkout link is not valid.") from bad
    cubby = get_object_or_404(Cubby.objects.select_related("unit__guild"), pk=cubby_pk)
    rental = CubbyRental.objects.filter(pk=rental_pk).first()
    if rental is not None and rental.member_id != member.pk:
        raise Http404("That checkout is someone else's.")
    return rental, cubby


@login_required
@require_GET
def hub_cubby_checkout_return(request: HttpRequest, token: str) -> HttpResponse:
    """Stripe's success page: settle the rental now when Stripe is done, then the tab."""
    rental, cubby = _token_target(request, token)
    outcome = cubby_rentals.finish_checkout(rental.pk) if rental is not None else "gone"
    if outcome == "active":
        messages.success(request, f"{cubby.label} is yours. We emailed you the details.")
    elif outcome == "pending":
        messages.info(request, f"Your payment is finishing up. {cubby.label} will show as yours in a minute.")
    else:
        messages.error(request, "That checkout ended before it was paid. Pick an open cubby to try again.")
    return redirect(cubby_rentals.tab_url(cubby.unit.guild))


@login_required
@require_GET
def hub_cubby_checkout_cancelled(request: HttpRequest, token: str) -> HttpResponse:
    """Stripe's back link: free the cubby unless Stripe says it was paid after all."""
    rental, cubby = _token_target(request, token)
    if rental is not None:
        outcome = cubby_rentals.release_hold_if_unpaid(rental)
        if outcome == "paid":
            messages.success(request, f"Your payment went through, so {cubby.label} is yours.")
        elif outcome == "unknown":
            messages.info(request, f"We couldn't reach Stripe. {cubby.label} is held for you for up to an hour.")
    return redirect(cubby_rentals.tab_url(cubby.unit.guild))


def _rental(request: HttpRequest, rental_pk: int) -> CubbyRental:
    """The rental these buttons act on: the viewer's own, or any for an admin; 404 otherwise."""
    rentals = CubbyRental.objects.for_actor(_member(request), is_admin=_is_admin(request))
    return get_object_or_404(rentals.select_related("cubby__unit__guild", "member"), pk=rental_pk)


def change_form(rental: CubbyRental, data: Any = None) -> CubbyAmountForm:
    """The Change Amount form for ``rental``, opening on what the member pays now."""
    return CubbyAmountForm(
        data, cubby=rental.cubby, current_cents=rental.amount_cents, auto_id=f"change-{rental.pk}-%s"
    )


def _change_sheet(request: HttpRequest, rental: CubbyRental, form: CubbyAmountForm) -> HttpResponse:
    return render(
        request,
        "hub/cubbies/_change_sheet.html",
        {
            "rental": rental,
            "cubby": rental.cubby,
            "form": form,
            "floor_message": cubby_rentals.amount_problem(rental.cubby, 0),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def hub_cubby_rental_amount(request: HttpRequest, rental_pk: int) -> HttpResponse:
    """Change Amount: GET is the sheet; POST saves, and the new amount starts at the next charge."""
    rental = _rental(request, rental_pk)
    if request.method == "GET":
        return _change_sheet(request, rental, change_form(rental))
    form = change_form(rental, request.POST)
    if form.is_valid():
        try:
            changed = cubby_rentals.change_amount(rental, form.cleaned_data["amount_cents"])
        except cubby_rentals.AmountError as refusal:
            form.add_error(None, str(refusal))
        except cubby_rentals.CubbyRentalError as refusal:
            messages.error(request, str(refusal))
            return _back_to_tab(request, rental.cubby.unit.guild)
        else:
            if changed.amount_cents == rental.amount_cents:
                messages.info(request, f"You still pay {cubby_dollars(changed.amount_cents)} a month.")
            else:
                starts = cubby_rentals.date_display(changed.current_period_end)
                messages.success(request, f"New amount starts {starts}.")
            return _back_to_tab(request, rental.cubby.unit.guild)
    return _change_sheet(request, rental, form)


def _set_cancelling(request: HttpRequest, rental_pk: int, *, cancel: bool) -> HttpResponse:
    rental = _rental(request, rental_pk)
    try:
        changed = cubby_rentals.set_cancelling(rental, cancel=cancel)
    except cubby_rentals.CubbyRentalError as refusal:
        messages.error(request, str(refusal))
    else:
        label = changed.cubby.label
        if changed.status == CubbyRental.Status.ENDED:
            # An assigned rental's Cancel Rental ends it today (#979).
            messages.success(request, f"{label} is no longer yours. Thanks for clearing it out.")
        elif cancel:
            until = cubby_rentals.date_display(changed.current_period_end)
            messages.success(request, f"{label} stays yours until {until}. You won't be charged again.")
        else:
            messages.success(request, f"You're keeping {label}.")
    return _back_to_tab(request, rental.cubby.unit.guild)


@login_required
@require_POST
def hub_cubby_rental_cancel(request: HttpRequest, rental_pk: int) -> HttpResponse:
    """Cancel Rental: it stays the member's until the paid month ends."""
    return _set_cancelling(request, rental_pk, cancel=True)


@login_required
@require_POST
def hub_cubby_rental_keep(request: HttpRequest, rental_pk: int) -> HttpResponse:
    """Keep My Cubby: undo a cancel before the month ends."""
    return _set_cancelling(request, rental_pk, cancel=False)


@login_required
@require_GET
def hub_cubby_rental_portal(request: HttpRequest, rental_pk: int) -> HttpResponse:
    """Manage Payment: a Stripe Billing Portal page to update the card, back to the tab after.

    A GET, because it changes nothing here and the payment emails link to it. Links to it carry
    ``hx-boost="false"``: the redirect leaves the site.
    """
    rental = _rental(request, rental_pk)
    try:
        url = cubby_rentals.portal_url(rental)
    except cubby_rentals.CubbyRentalError as refusal:
        messages.error(request, str(refusal))
        return redirect(cubby_rentals.tab_url(rental.cubby.unit.guild))
    except stripe.StripeError:
        logger.exception("Could not open the Billing Portal for cubby rental %s.", rental.pk)
        messages.error(request, "We couldn't open Stripe right now. Try again in a minute.")
        return redirect(cubby_rentals.tab_url(rental.cubby.unit.guild))
    return redirect(url)
