"""The Door Code tile's Change save at /manage/door-code/ (#813).

Admin only (``@fog_admin_required``, the actual role, so a view as preview can neither grant
nor revoke it). A plain POST from the tile's modal that always lands back on Home with a
message, shown as a toast: the refusal for a blank code as much as the success.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from hub.door_code import DoorCodeForm
from hub.view_as import fog_admin_required

if TYPE_CHECKING:
    from django.contrib.auth.models import User


@fog_admin_required
@require_POST
def hub_admin_door_code(request: HttpRequest) -> HttpResponse:
    """Save a new door code for every member, or refuse it with the form's message."""
    form = DoorCodeForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(str(error) for error in form.errors["door_code"]))
        return redirect("hub_home")
    user: User = request.user  # type: ignore[assignment]  # @fog_admin_required guarantees a User
    form.save(by=user, now=timezone.now())
    messages.success(request, "Door code saved. Members see it now.")
    return redirect("hub_home")
