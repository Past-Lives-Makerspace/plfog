"""Admin Tools > Spotlight at /manage/spotlight/ (#708).

One page: the Spotlight messages (#889, hub.announcement_admin_views) that crawl across the top
of Home (#935). The Spotlight text and meeting were removed in #935, and polls left this page in
#890. Admin only (``@fog_admin_required``). Nothing here sends an email, push, Discord post or bell.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from hub.announcement_admin_views import announcement_rows
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context


@fog_admin_required
def hub_admin_spotlight(request: HttpRequest) -> HttpResponse:
    """The Spotlight admin page: every Spotlight message as a row, in the ticker's order."""
    context = {**_get_hub_context(request), "announcement_rows": announcement_rows(timezone.now())}
    return render(request, "hub/admin/spotlight.html", context)
