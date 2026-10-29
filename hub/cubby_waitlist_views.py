"""The cubby waitlist's buttons (#980): Notify Me When One Opens and Leave the List on the guild page's
Cubbies tab, and guild staff's Remove on the Rentals tab's Waitlist chip.

Every change is :mod:`membership.cubby_waitlist`; these views parse, call and answer with a message
the hub shows as a toast. Remove reads the guild from the entry and answers 404 to anyone
:func:`membership.permissions.can_manage_cubbies` refuses (a lead or staff member of another
guild, or a Guild Officer who runs no guild, #1015), admins passing everywhere. The member is not
told they were removed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from hub.views import _get_member
from membership import cubby_rentals, cubby_waitlist
from membership.models import CubbyWaitlistEntry, Guild
from membership.permissions import can_manage_cubbies

if TYPE_CHECKING:
    from membership.models import Member


def _member(request: HttpRequest) -> Member:
    member = _get_member(request)
    if member is None:
        raise Http404("No member for this account.")
    return member


@login_required
@require_POST
def hub_cubby_waitlist_join(request: HttpRequest, pk: int) -> HttpResponse:
    """Notify Me When One Opens: join the guild's waitlist (a second tap keeps the one place)."""
    guild = get_object_or_404(Guild, pk=pk)
    try:
        cubby_waitlist.join(guild, _member(request))
    except cubby_waitlist.WaitlistError as refusal:
        messages.error(request, str(refusal))
    else:
        messages.success(request, "You're on the list.")
    return redirect(cubby_rentals.tab_url(guild))


@login_required
@require_POST
def hub_cubby_waitlist_leave(request: HttpRequest, pk: int) -> HttpResponse:
    """Leave the List: off the guild's waitlist; joining again puts them at the back."""
    guild = get_object_or_404(Guild, pk=pk)
    if cubby_waitlist.leave(guild, _member(request)):
        messages.success(request, "You've left the list.")
    else:
        messages.info(request, "You weren't on the list any more.")
    return redirect(cubby_rentals.tab_url(guild))


@login_required
@require_POST
def hub_cubby_waitlist_remove(request: HttpRequest, entry_pk: int) -> HttpResponse:
    """Guild staff's Remove: the entry leaves the line and the member is not told."""
    entry = get_object_or_404(CubbyWaitlistEntry.objects.select_related("guild", "member"), pk=entry_pk)
    if not can_manage_cubbies(request, entry.guild):
        raise Http404("No such waitlist entry among the guilds you run.")
    name = entry.member.display_name
    if cubby_waitlist.remove(entry):
        messages.success(request, f"{name} is off the waitlist.")
    else:
        messages.info(request, f"{name} wasn't waiting any more.")
    return redirect(f"{reverse('hub_guild_cubbies_rentals', args=[entry.guild_id])}?show=waitlist")
