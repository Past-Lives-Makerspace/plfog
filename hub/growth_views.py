"""The admin Growth page at /manage/growth/ (#795).

Admin only, and view-as aware like its Admin Tools card: an admin previewing as a member is
refused, as a member would be. A GET page: the range, metric and mode are the query string, so
every view of it is a link. The numbers are built in ``hub/growth.py``.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from hub import growth
from hub.growth import METRICS, MODES, PRESETS, GrowthForm, dashboard
from hub.views import _get_hub_context, _require_admin


@login_required
def hub_admin_growth(request: HttpRequest) -> HttpResponse:
    """The Growth dashboard for the range, metric and mode the query string asks for."""
    refused = _require_admin(request)
    if refused is not None:
        return refused
    today = timezone.localdate()
    form = GrowthForm(request.GET, today=today)
    form.is_valid()
    selection = form.selection()
    board = dashboard(selection.rng, selection.range_name, selection.metric, selection.mode, today)
    trend = {"metric": selection.metric, "mode": selection.mode}
    if selection.preset == "custom":
        dates = {
            "range": "custom",
            "date_from": selection.rng.start.isoformat(),
            "date_to": selection.rng.end.isoformat(),
        }
    else:
        dates = {"range": selection.preset}
    return render(
        request,
        "hub/admin/growth.html",
        {
            **_get_hub_context(request),
            "form": form,
            "selection": selection,
            "board": board,
            "launch": growth.LAUNCH,
            "metric_label": METRICS[selection.metric].label,
            "presets": [(key, label, urlencode({"range": key, **trend})) for key, label in PRESETS if key != "custom"],
            "modes": [
                (key, label, urlencode({**dates, "metric": selection.metric, "mode": key})) for key, label in MODES
            ],
            "range_query": dates,
        },
    )
