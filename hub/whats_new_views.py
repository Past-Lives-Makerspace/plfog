"""What's New (#1007, part 2): the modal body by tab and by feature, a page at a time, and the seen writes.

``GET /whats-new/`` renders ``hub/whats_new/_body.html`` into the ``whats-new`` modal Home
includes (an htmx request gets the body alone; a plain GET, a direct link, gets a page that
opens the modal on it) and records every entry it listed as seen; ``?after=<slug>`` is Show
older, the next page of entries alone. ``POST /whats-new/seen/<key>/`` records one feature's
entries seen, for part 3's badge click. Both are members only, by the rule the Spotlight's
version card uses and the preview's, so a guest, a former member or a Guest preview is sent Home.
"""

from __future__ import annotations

import re
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.guidance import registered, title_for
from core.htmx import wants_fragment
from core.whats_new import TAB_LABELS, TAB_NOUNS, TABS, Page, WhatsNew, whats_new_for
from hub.spotlight import sees_spotlight

#: A feature key (``guild.cubbies``) or an area alone (``guild``), the grammar of ``core.help_registry.KEY_PATTERN``.
_FEATURE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+(?:-[a-z0-9]+)*)?$")

#: The sentence a tab shows with nothing on it.
_EMPTY = {tab: f"Nothing here for {noun} yet." for tab, noun in TAB_NOUNS.items()}


def _news(request: HttpRequest) -> WhatsNew | None:
    """The request's What's New, or None for anyone the modal is not for: a non member, or a Guest preview."""
    view_as = getattr(request, "view_as", None)
    news = whats_new_for(request)
    return news if view_as is not None and view_as.is_member and sees_spotlight(news.member) else None


def _feature_key(value: str) -> str:
    """``value`` as a feature key, ``""`` for none; a key neither registered nor an area of one is a 404."""
    if not value:
        return ""
    if not _FEATURE.match(value) or not registered(value):
        raise Http404(f"No feature {value!r}")
    return value


def _title(feature: str) -> str:
    """The modal's title: "What's New", or "What's New · <feature>" when opened on one (D7)."""
    return f"What's New · {title_for(feature)}" if feature else "What's New"


def _href(tab: str, feature: str, after: str = "") -> str:
    """This view's URL for a tab, a feature and, for Show older, the slug to continue after."""
    url = f"{reverse('hub_whats_new')}?tab={tab}"
    if feature:
        url += f"&feature={feature}"
    return f"{url}&after={after}" if after else url


def _page(news: WhatsNew, tab: str, feature: str, after: str) -> Page:
    try:
        return news.page_for(tab, feature or None, after)
    except LookupError as exc:
        # The changelog changed under an open modal (a sweep); the next open starts clean.
        raise Http404(str(exc)) from exc


def _body_context(news: WhatsNew, tab: str, feature: str, page: Page) -> dict[str, Any]:
    """What the body template draws: the tab row, one page of the shown tab, its Show older link and the empty line."""
    return {
        "whats_new_tab": tab,
        "whats_new_feature": feature,
        "whats_new_tabs": [
            {
                "key": key,
                "label": TAB_LABELS[key],
                "href": _href(key, feature),
                "active": key == tab,
                # Counts on the tabs only when opened on one feature, as the mockup has it.
                "count": len(news.entries_for(tab=key, feature=feature)) if feature else 0,
            }
            for key in TABS
        ],
        "whats_new_page": page,
        "whats_new_older_url": _href(tab, feature, page.older_after) if page.older_after else "",
        "whats_new_empty": _EMPTY[tab],
        "modal_title": _title(feature),
    }


@login_required
@require_GET
def hub_whats_new(request: HttpRequest) -> HttpResponse:
    """The What's New modal body, on ``?tab=`` (default: the viewer's first tab with something unseen) and ``?feature=``.

    Rendering it marks every entry on the page as seen (D7). An htmx request gets the body alone,
    or with ``?after=<slug>`` the next page of entries alone (Show older); a plain GET gets
    ``hub/whats_new.html``, a page that opens the modal on the first page, so a direct link lands
    somewhere.
    """
    news = _news(request)
    if news is None:
        return redirect("hub_home")
    feature = _feature_key(request.GET.get("feature", ""))
    tab = request.GET.get("tab", "")
    if tab not in TABS:
        tab = news.default_tab()
    fragment = wants_fragment(request)
    after = request.GET.get("after", "") if fragment else ""
    page = _page(news, tab, feature, after)
    news.mark_seen(page.slugs)
    context = _body_context(news, tab, feature, page)
    if after:
        return render(request, "hub/whats_new/_page.html", context)
    if fragment:
        return render(request, "hub/whats_new/_body.html", {**context, "whats_new_fragment": True})
    return render(request, "hub/whats_new.html", context)


@login_required
@require_POST
def hub_whats_new_seen(request: HttpRequest, key: str) -> HttpResponse:
    """Record one feature's entries (the key and everything under it) as seen: a badge click (part 3). 204."""
    news = _news(request)
    if news is None:
        return redirect("hub_home")
    feature = _feature_key(key)
    news.mark_seen([entry.slug for entry in news.window if entry.under(feature)])
    return HttpResponse(status=204)
