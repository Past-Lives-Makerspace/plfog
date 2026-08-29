"""Template context processors for core app."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest


def registration_mode(request: HttpRequest) -> dict[str, bool]:
    """Add registration_is_open flag to template context."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    return {"registration_is_open": config.registration_mode == SiteConfiguration.RegistrationMode.OPEN}


def app_version(request: HttpRequest) -> dict[str, Any]:
    """Add app version and changelog to template context."""
    from plfog.version import CHANGELOG, VERSION

    return {"app_version": VERSION, "changelog": CHANGELOG}


def makerspace_wiki(request: HttpRequest) -> dict[str, str]:
    """Expose the external MediaWiki knowledge-base URL site-wide.

    The base template's "Wiki" sidebar link opens ``makerspace_wiki_url`` in a new tab.
    A blank setting hides the link (the template gates it on the truthy value).
    """
    return {"makerspace_wiki_url": settings.MAKERSPACE_WIKI_URL}


def theme(request: HttpRequest) -> dict[str, str]:
    """Expose the theme cookie's domain scope to base.html's early inline script.

    The script writes the light/dark choice to the ``pl_theme`` cookie so an
    explicit choice survives navigation across subdomains of the registrable
    domain. Empty string → a host-only cookie (correct for local dev like
    ``pastlives.test``); production sets ``THEME_COOKIE_DOMAIN=.pastlives.app``
    so the member hub and the guilds surface share the choice.
    """
    return {"theme_cookie_domain": settings.THEME_COOKIE_DOMAIN}


def feature_flags(request: HttpRequest) -> dict[str, Any]:
    """Expose the Site Settings → Features toggles site-wide (members + public)."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    return {
        "my_tab_enabled": config.my_tab_enabled,
        "class_registration_enabled": config.class_registration_enabled,
        "class_registration_disabled_note": config.class_registration_disabled_note,
        "help_page_enabled": config.help_page_enabled,
        "wiki_link_enabled": config.wiki_link_enabled,
        "instructor_discount_codes_enabled": config.instructor_discount_codes_enabled,
    }


def surface(request: HttpRequest) -> dict[str, str | bool]:
    """Expose which surface the request arrived on so templates can branch chrome.

    ``surface`` is ``"public"`` on book.pastlives.space, ``"guilds"`` on
    guilds.pastlives.app, ``"signage"`` on slideshow.pastlives.space, and
    ``"members"`` everywhere else (members host, local dev, Hetzner staging,
    Render preview). ``is_public_surface`` / ``is_guilds_surface`` /
    ``is_signage_surface`` are the convenience booleans templates branch on;
    ``is_guest_surface`` is the unified "no member chrome" flag (true on book and
    guilds). ``guilds_page_base`` / ``parent_template`` let shared and allauth
    templates pick their base via ``{% extends ... %}`` without forking the files.
    """
    value = getattr(request, "surface", "members")
    is_public = value == "public"
    is_guilds = value == "guilds"
    is_signage = value == "signage"
    return {
        "surface": value,
        "is_public_surface": is_public,
        "is_guilds_surface": is_guilds,
        "is_signage_surface": is_signage,
        "is_guest_surface": is_public or is_guilds,
        "MEMBER_HOST": settings.MEMBER_HOST,
        "MEMBER_BASE_URL": getattr(settings, "MEMBER_BASE_URL", f"https://{settings.MEMBER_HOST}"),
        "BOOK_BASE_URL": getattr(settings, "BOOK_BASE_URL", "https://book.pastlives.space"),
        "GUILDS_BASE_URL": getattr(settings, "GUILDS_BASE_URL", "https://guilds.pastlives.app"),
        "SIGNAGE_BASE_URL": getattr(settings, "SIGNAGE_BASE_URL", "https://slideshow.pastlives.space"),
        "guilds_page_base": "guilds/base_public.html" if is_guilds else "hub/base.html",
        "signage_page_base": "signage/base.html" if is_signage else "hub/base.html",
        "parent_template": (
            "guilds/base_public.html" if is_guilds else "classes/base_public.html" if is_public else "base.html"
        ),
    }


def google_analytics(request: HttpRequest) -> dict[str, str]:
    """Expose the GA4 measurement ID on every page, the Django admin included.

    There is deliberately no path exclusion: FOG is measured end to end, so staff
    back-office activity is part of the picture. Templates gate the gtag block on the
    truthy value, so a blank measurement ID acts as "disabled" everywhere at once.
    """
    from core.models import SiteConfiguration

    return {"google_analytics_measurement_id": SiteConfiguration.load().google_analytics_measurement_id}


def notification_badge(request: HttpRequest) -> dict[str, int]:
    """Unread notification count for the topbar bell. 0 for anonymous users.

    ``request.user`` is read defensively. ``SurfaceMiddleware`` short-circuits member-only
    paths on the guest surfaces with an ``Http404`` *before* ``AuthenticationMiddleware``
    has run, and the themed ``templates/404.html`` renders every context processor — so on
    those responses the attribute does not exist yet. Same guard as ``persona`` below.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"unread_notification_count": 0}
    from core.models import Notification

    count = Notification.objects.filter(user=user, read_at__isnull=True).count()
    return {"unread_notification_count": count}


def persona(request: HttpRequest) -> dict[str, str | bool]:
    """Derive the active persona for the current request.

    Returns a single string in {"anon", "nonmember", "member", "instructor"} plus
    convenience booleans so templates can render banners and topbar variants
    without re-deriving. Cached on the request so it's safe to call from both
    the context processor and view code.

    The "member" persona is reserved for users whose Member record was imported
    from Airtable (``airtable_record_id`` set). Auto-created shell Members from
    the ``ensure_user_has_member`` signal — created for everyone who signs up
    on book.pastlives.space without paying dues — read as "nonmember" here.
    Airtable is the authoritative roster for real dues-paying members.
    """
    cached = getattr(request, "_persona", None)
    if cached is not None:
        return cached

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        result: dict[str, str | bool] = {"persona": "anon", "is_member_persona": False, "is_instructor_persona": False}
        request._persona = result  # type: ignore[attr-defined]
        return result

    from membership.models import Member

    member = getattr(user, "member", None)
    is_active_member = bool(member and member.status == Member.Status.ACTIVE and member.airtable_record_id)
    is_instructor = bool(member and member.instructor_slug)

    if is_active_member:
        slug = "member"
    elif is_instructor:
        slug = "instructor"
    else:
        slug = "nonmember"

    result = {
        "persona": slug,
        "is_member_persona": is_active_member,
        "is_instructor_persona": is_instructor,
    }
    request._persona = result  # type: ignore[attr-defined]
    return result


def _apply_resume_step(request: HttpRequest, ctx: dict[str, Any]) -> None:
    """Fold a clamped ``?step=`` into the payload so a driven hop resumes mid-tour."""
    payload = ctx.get("tour_json")
    if not payload:
        return
    try:
        step = int(request.GET.get("step", "0"))
    except ValueError:
        step = 0
    last = len(payload["steps"]) - 1
    payload["resume_step"] = max(0, min(step, last)) if last >= 0 else 0


def tour_runtime(request: HttpRequest) -> dict[str, Any]:
    """Make a guided-tour payload available on every page a tour can land on.

    The single choke-point that decides, per request, whether the page emits a
    tour payload (so ``static/js/pl_tour.js`` can start, resume, or offer a tour):

    1. A ``?tour=<key>`` for an *eligible* member -> autostart/resume payload with
       a clamped ``resume_step`` (works on any page, which is what lets a driven
       multi-page hop re-hydrate). No ``TourState`` write.
    2. Otherwise, on a tour's entry page -> the usual offer/auto-offer guards
       (first eligible GET writes the ``offered`` row).
    3. Else nothing.

    ``request.user`` / ``request.resolver_match`` are read defensively: themed
    404s render every context processor before auth and URL resolution have run.
    """
    from core.tours import TOURS, tour_offer_context

    empty: dict[str, Any] = {
        "tour": None,
        "tour_json": None,
        "show_tour_offer": False,
        "tour_autostart": False,
    }
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return empty
    if getattr(request, "method", "GET") != "GET":
        # Tours only ride GET renders — a POST re-render (e.g. a form error) must
        # not write an ``offered`` row or emit a payload.
        return empty

    requested = request.GET.get("tour")
    if requested in TOURS:
        ctx = tour_offer_context(request, requested)
        if ctx["tour_autostart"]:
            _apply_resume_step(request, ctx)
            return ctx
        # Ineligible or foreign ?tour= — the param is ignored; fall through so an
        # entry page still shows its own offer.

    match = getattr(request, "resolver_match", None)
    if match is not None:
        for tour in TOURS.values():
            if match.view_name == tour.entry_url_name:
                return tour_offer_context(request, tour.key)
    return empty
