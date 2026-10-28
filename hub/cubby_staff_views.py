"""The staff Cubbies manager's Rentals tab (#979): who rents what, Assign a Cubby, End Rental and the CSV.

Mockup views manage-rentals, rentals-phone, rentals-end and assign in ``mockups/guild-cubbies.html``.
Every view reads its guild and answers 404 to anyone :func:`membership.permissions.can_edit_guild`
refuses: a guild's lead and staff reach only their own guild, admins and officers every guild. End
Rental reads the guild from the rental, and Assign checks that the posted cubby is this guild's, so a
crafted POST naming another guild's rental or cubby is a 404 for this guild's staff. The rules and
every change are :mod:`membership.cubby_rentals`; these views parse, call and answer.
"""

from __future__ import annotations

import logging
from typing import Any

import stripe
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from classes.table import prepare_table
from hub.cubby_forms import CubbyAssignForm
from hub.cubby_views import manage_context
from hub.views import _get_member
from membership import cubbies, cubby_rentals
from membership.cubby_exports import stream_rentals_csv
from membership.models import Cubby, CubbyRental, Guild
from membership.permissions import can_edit_guild

logger = logging.getLogger(__name__)

#: Stripe errors that are no answer (a timeout, a dropped connection, a 5xx, a rate limit): the cancel may have landed.
_NO_ANSWER = (stripe.APIConnectionError, stripe.APIError, stripe.RateLimitError)

#: The Rentals table's sortable columns, by ``?sort=``; Cubby is the default.
_SORTABLE = frozenset(
    {"member__full_legal_name", "cubby__label", "amount_cents", "start_date", "current_period_end", "status"}
)


def _staff_guild(request: HttpRequest, pk: int) -> Guild:
    """The guild in the URL, for someone who runs it (or an admin); 404 for anyone else."""
    guild = get_object_or_404(Guild, pk=pk)
    if not can_edit_guild(request, guild):
        raise Http404("No such guild among the ones you run.")
    return guild


def _render_rentals(request: HttpRequest, guild: Guild, *, assign_form: CubbyAssignForm | None = None) -> HttpResponse:
    show = cubbies.rental_filter(request.GET.get("show", ""))
    table = prepare_table(
        request,
        cubbies.staff_rentals(guild, show),
        search_fields=[],
        default_sort="cubby__label",
        sortable=_SORTABLE,
    )
    context: dict[str, Any] = {
        **manage_context(request, guild, "rentals"),
        **table,
        "rows": [cubbies.RentalRow.build(rental) for rental in table["page"].object_list],
        "show": show,
        "summary": cubbies.RentalsSummary.load(guild),
        "assign_form": assign_form or CubbyAssignForm(guild=guild),
        "open_assign_modal": assign_form is not None,
    }
    # A refused Assign renders at 200: a boosted 4xx becomes a fresh GET (hub_boot.js) and loses what was typed.
    return render(request, "hub/cubbies/manage.html", context)


@login_required
@require_GET
def hub_guild_cubbies_rentals(request: HttpRequest, pk: int) -> HttpResponse:
    """The Rentals tab: the tiles, the filter chips, the sortable table (cards on a phone) and Assign a Cubby."""
    return _render_rentals(request, _staff_guild(request, pk))


@login_required
@require_GET
def hub_guild_cubbies_rentals_csv(request: HttpRequest, pk: int) -> StreamingHttpResponse:
    """Export CSV: one row per rental the guild's cubbies have had."""
    return stream_rentals_csv(_staff_guild(request, pk))


@login_required
@require_POST
def hub_guild_cubby_assign(request: HttpRequest, pk: int) -> HttpResponse:
    """Assign a Cubby: Comped or Paid Another Way, Active today. A refusal reopens the modal with it."""
    guild = _staff_guild(request, pk)
    posted = request.POST.get("cubby", "")
    if posted.isdigit():
        cubby = Cubby.objects.select_related("unit").filter(pk=int(posted)).first()
        if cubby is not None and cubby.unit.guild_id != guild.pk:
            raise Http404("That cubby is another guild's.")
    form = CubbyAssignForm(request.POST, guild=guild)
    if not form.is_valid():
        return _render_rentals(request, guild, assign_form=form)
    cubby = form.cleaned_data["cubby_obj"]
    member = form.cleaned_data["member"]
    try:
        rental = cubby_rentals.assign_rental(
            cubby,
            member,
            source=form.cleaned_data["source"],
            amount_cents=form.cleaned_data["amount_cents"],
            actor=_get_member(request),
        )
    except cubby_rentals.CubbyRentalError as refusal:
        form.add_error(None, str(refusal))
        return _render_rentals(request, guild, assign_form=form)
    messages.success(request, f"{rental.cubby.label} assigned to {member.display_name}.")
    return redirect("hub_guild_cubbies_rentals", pk=guild.pk)


@login_required
@require_POST
def hub_cubby_rental_end(request: HttpRequest, rental_pk: int) -> HttpResponse:
    """End Rental: ends today, opens the cubby, tells the member why. No refund."""
    rental = get_object_or_404(CubbyRental.objects.select_related("cubby__unit__guild", "member"), pk=rental_pk)
    guild = rental.cubby.unit.guild
    if not can_edit_guild(request, guild):
        raise Http404("No such rental among the guilds you run.")
    back = reverse("hub_guild_cubbies_rentals", args=[guild.pk])
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Say why you're ending it. The member gets your reason by email.")
        return redirect(back)
    try:
        cubby_rentals.end_rental(rental, reason=reason)
    except cubby_rentals.CubbyRentalError as refusal:
        messages.error(request, str(refusal))
        return redirect(back)
    except _NO_ANSWER:
        # No answer: Stripe may have cancelled it anyway, and its webhook then ends the rental here.
        logger.exception("Stripe gave no answer cancelling cubby rental %s.", rental.pk)
        messages.error(request, "Stripe didn't answer. Check again in a minute.")
        return redirect(back)
    except stripe.StripeError:
        logger.exception("Stripe refused to cancel the subscription of cubby rental %s.", rental.pk)
        messages.error(request, "Stripe refused, so nothing changed. Try again in a minute.")
        return redirect(back)
    messages.success(request, f"{rental.cubby.label} is open again. We told {rental.member.display_name}.")
    return redirect(back)
