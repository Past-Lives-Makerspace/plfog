"""Where the old Days Off modal's routes lead since #1300: My Hours, which keeps one Days Off for every kind.

The modal (#767) opened from a guild's or a tool's Orientation Schedule, per person. Its eight routes
(the body, the preview, save and delete, for a guild and for a tool) stay as redirects, so a bookmark
or an old link still lands somewhere: My Hours' Days Off, with ``?member=`` when it named someone else.
Nothing is read or written here; My Hours decides who may see whose hours.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse

from hours.links import to_my_hours


@login_required
def old_days_off(request: HttpRequest, **_route: Any) -> HttpResponse:
    """Any of the old Days Off routes: My Hours' Days Off, for the person it named (``orienter``)."""
    return to_my_hours(request, request.GET.get("orienter", "") or request.POST.get("orienter", ""), "#days-off")
