"""Member wiki views — reading, search and browse (PR A2) and writing (PR A3).

The member-facing ``/wiki/`` surface from ``docs/superpowers/plans/2026-09-07-member-wiki-core.md``
§6: the home page, the search-and-browse screen, the reading page, the starter chooser,
the create/edit editor with its autosave and draft resume, the two micro-contribution
modals, one-tap "Still accurate", and the drafts list.

Every view is thin per CLAUDE.md: parse the request, ask a permission *filter* for what it
may show, call a model method, then toast / redirect / render. Permissions are filters and
not checks — a view that forgets to gate shows too little, never too much.

Spec D appends its moderation surfaces here (``docs/superpowers/plans/2026-09-07-member-wiki-moderation.md``):
Report and Withdraw, the review queue and its archived view, the official note, archive /
tombstone / restore, history and revert, the advisory lock and the conflict save, and the
safety gate. Two seams still belong to spec B and stay lazily imported until it merges:
``_wiki_search_empty.html`` and ``WikiWantedPage.fulfil``.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.timesince import timesince
from django.views.decorators.http import require_POST

from core.models import SiteConfiguration
from hub.forms import (
    WikiArchiveForm,
    WikiAttachmentFormSet,
    WikiDeclineForm,
    WikiOfficialNoteForm,
    WikiPageCreateForm,
    WikiPageForm,
    WikiQuickPhotoForm,
    WikiQuickTipForm,
    WikiRedirectForm,
    WikiReportForm,
    WikiResolveForm,
    WikiVerifyNoteForm,
    WikiWantedFulfilForm,
    build_wiki_wanted_formset,
    wiki_fact_formset_class,
)
from hub.toast import trigger_client_event, trigger_toast
from hub.views import _get_hub_context
from membership.markdown import sanitize_wiki_submission
from membership.models import (
    AlreadyArchived,
    AlreadyResolved,
    DuplicateWikiReport,
    MISS_MIN_QUERY_LENGTH,
    Guild,
    Member,
    NothingToRevert,
    WikiArticle,
    WikiAttachment,
    WikiDraft,
    WikiEditLock,
    WikiError,
    WikiPage,
    WikiPageFact,
    WikiReport,
    WikiRevision,
    WikiSaveConflict,
    WikiSearchMiss,
    WikiWantedPage,
)
from membership.permissions import (
    _editing_member,
    can_edit_guild,
    can_edit_wiki_page,
    can_moderate_wiki_page,
    can_moderate_wiki_scope,
    can_verify_wiki_page,
    editable_meeting_scopes,
    editable_wiki_scopes,
    is_effective_staff,
    moderatable_wiki_scopes,
    visible_wiki_pages,
)
from membership.wiki_guild import WANTED_CARD_LIMIT, guild_wiki_tab_context
from membership.wiki_starters import STARTERS

# How many rows one search group shows before the pager takes over.
_SEARCH_PAGE_SIZE = 20
# Home-page section caps — enough to be useful, short enough to scan on a phone.
_HOME_GUILD_LIMIT = 8
_HOME_RECENT_LIMIT = 10
_HOME_MACHINE_LIMIT = 8
_HOME_DRAFT_LIMIT = 3


def wiki_feature_required(view_func: Any) -> Any:
    """404 every wiki route while the Site Settings toggle is off.

    A disabled feature is fully dark — reading pages, write POSTs and sticker links
    alike — so a crafted request learns nothing about a half-built wiki. Mirrors
    ``equipment_feature_required``.
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not SiteConfiguration.load().wiki_enabled:
            raise Http404("The wiki is turned off.")
        return view_func(request, *args, **kwargs)

    return wrapper


# --- Small shared helpers ------------------------------------------------------------


def _active_member(request: HttpRequest) -> Member | None:
    """The request's member when their membership is active, else None.

    Writing anywhere in the wiki takes an active membership; reading does not.
    """
    # _editing_member and not _get_member: an admin previewing as Guest must not be able
    # to create a page, tap "Still accurate", or open somebody's drafts. can_edit_wiki_page
    # already answers through _editing_member, so using the raw member here would have let
    # the two disagree about the same request.
    member = _editing_member(request)
    if member is None or member.status != Member.Status.ACTIVE:
        return None
    return member


def _forbidden() -> HttpResponse:
    """The plain 403 every wiki write route answers a member who may not write."""
    return HttpResponse("Forbidden", status=403)


def _ordinal(number: int) -> str:
    """``4`` -> ``"4th"``. Telling somebody they are the fourth to ask beats a silent dupe.

    Hand-rolled rather than ``django.contrib.humanize``: that app is not installed here and
    adding it for one string would be a settings change for a suffix.
    """
    if 11 <= number % 100 <= 13:
        return f"{number}th"
    return f"{number}{_ORDINAL_SUFFIXES[number % 10]}"


# Indexed by last digit rather than looked up with a default: every digit has an answer, so
# a total mapping is the honest shape (house rule: dict[key], never .get with a fallback).
_ORDINAL_SUFFIXES: tuple[str, ...] = ("th", "st", "nd", "rd", "th", "th", "th", "th", "th", "th")


def _not_found(request: HttpRequest) -> HttpResponse:
    """The wiki's own 404 body, not the site-wide one.

    ``get_object_or_404`` renders ``templates/404.html``, which offers to "Browse Past
    Lives classes" — the wrong answer for a member standing in a shop holding a phone.
    """
    context = _get_hub_context(request)
    return render(request, "hub/wiki_not_found.html", context, status=404)


def _page_or_none(slug: str) -> WikiPage | None:
    """Resolve a page by slug through ``objects.all()``, archived rows included.

    The reading view deliberately does NOT use ``visible_wiki_pages``: an archived page
    must still resolve at its own URL so it can explain itself to its author by name,
    which is a different question from what belongs in a list. Do not "fix" that.
    """
    return (
        WikiPage.objects.select_related("guild", "equipment", "equipment__guild", "created_by", "updated_by")
        .select_related("verified_by", "last_checked_by", "archived_by", "equipment__required_orientation")
        .filter(slug=slug)
        .first()
    )


def _hidden_from(request: HttpRequest, page: WikiPage) -> bool:
    """True when spec D's safety gate is holding this page back from this request.

    The write routes get this for free through ``can_edit_wiki_page``, which carries the
    same leg. The two routes that do NOT go through it need it by name: the reading page
    (which resolves archived rows through ``objects.all()`` on purpose) and "Still
    accurate" (which any active member may tap, so it cannot borrow the edit gate).
    """
    return not page.is_published and not WikiPage.objects.visible_for(request).filter(pk=page.pk).exists()


def _refresh_edit_lock(page: WikiPage, member: Member) -> None:
    """Keep the advisory edit lock warm from the autosave POST.

    Every accepted autosave refreshes the caller's own lock row, which is the entire
    reason the lock needs no polling timer, no second endpoint and no JS of its own. A
    no-op for somebody who does not hold it.
    """
    WikiEditLock.refresh(page, member)


def _fulfil_wanted_page(wanted_pk: str, page: WikiPage) -> bool:
    """Close spec B's wanted-page loop when a create carried ``?wanted=<pk>``.

    Returns True when a wanted row was actually marked done, so the success message can
    say so. A stale, deleted, already-fulfilled or invisible pk is ignored silently — the
    page is still created, because a month-old digest link must never be an error screen.
    """
    if not wanted_pk.isdigit():
        return False
    try:
        from membership.models import WikiWantedPage  # type: ignore[attr-defined]
    except ImportError:
        return False
    wanted = WikiWantedPage.objects.filter(pk=int(wanted_pk)).first()
    if wanted is None:
        return False
    return bool(wanted.fulfil(page))


def _carry_params(request: HttpRequest) -> str:
    """The ``?guild=&title=&wanted=`` query string the starter chooser hands onward.

    Spec B renders "Start this page" links from four different surfaces and every one of
    them has to survive the hop from ``/wiki/new/`` to ``/wiki/new/<kind>/``, or B's whole
    wanted-page loop silently never completes.
    """
    carried = {key: request.GET[key] for key in ("guild", "title", "wanted") if request.GET.get(key, "").strip()}
    return urlencode(carried)


# --- Reading, search and browse (PR A2) ----------------------------------------------


@login_required
@wiki_feature_required
def hub_wiki_home(request: HttpRequest) -> HttpResponse:
    """``/wiki/`` — search first, then the three lists a member actually browses.

    The scope and kind chip rows are GET-param navigation rather than a form, so every
    chip is a plain link and the state survives a body swap. Each "See all" link lands on
    ``/wiki/search/``, which browses on an empty query — under a ``none()``-on-empty
    search every one of them would have dead-ended on a blank page.
    """
    member = _editing_member(request)
    visible = visible_wiki_pages(request)
    kind = request.GET.get("kind", "")
    if kind not in WikiPage.Kind.values:
        kind = ""
    guild_slug = request.GET.get("guild", "").strip()
    guild = Guild.objects.filter(slug=guild_slug).first() if guild_slug else None
    space_wide_only = guild_slug == "space-wide"

    filtered = visible.filtered(guild=guild, kind=kind)
    if space_wide_only:
        filtered = filtered.space_wide()
    elif guild_slug and guild is None:
        filtered = filtered.none()

    # Every card renders page.attribute_line (guild.name) and the updated_by byline, so
    # without this the home page costs two queries PER ROW and grows with the wiki. The
    # search screen has always been flat because it chains with_fact_prefetch(); this is
    # the same call, on the page more members land on.
    cards = filtered.with_fact_prefetch()
    joined_guild_ids = list(member.guild_memberships.values_list("guild_id", flat=True)) if member else []
    your_guild_pages = (
        list(cards.filter(guild_id__in=joined_guild_ids).order_by("-updated_at")[:_HOME_GUILD_LIMIT])
        if joined_guild_ids
        else []
    )
    recent_pages = list(cards.order_by("-updated_at")[:_HOME_RECENT_LIMIT])
    machine_pages = list(cards.filter(kind=WikiPage.Kind.MACHINE).order_by("title")[:_HOME_MACHINE_LIMIT])
    drafts = list(WikiDraft.objects.for_member(member)[:_HOME_DRAFT_LIMIT]) if member is not None else []
    can_review, open_review_count = _review_link(request)

    context = _get_hub_context(request)
    context.update(
        {
            "kind_filter": kind,
            "guild_filter": guild_slug,
            "is_filtered": bool(kind or guild_slug),
            "filter_guilds": Guild.objects.filter(pk__in=visible.values("guild")).order_by("name"),
            "kind_choices": WikiPage.Kind.choices,
            "your_guild_pages": your_guild_pages,
            "recent_pages": recent_pages,
            "machine_pages": machine_pages,
            "drafts": drafts,
            "starters": [{"kind": key, **value} for key, value in STARTERS.items()],
            "can_write": _active_member(request) is not None,
            # The review queue's only entry point on an ordinary day. Without it the queue
            # is reachable only from a report banner on a page you happen to be reading,
            # which means a held safety proposal and the ?archived=1 view both sit behind
            # a screen nobody arrives at.
            "can_review": can_review,
            "open_review_count": open_review_count,
            # The printable sticker sheet's only entry point. Without it /wiki/stickers/
            # is a URL you have to already know, which is the same as not shipping it.
            "can_print_stickers": is_effective_staff(request),
            "has_any_page": visible.exists(),
        }
    )
    return render(request, "hub/wiki_home.html", context)


def _search_result_rows(pages: Any, q: str) -> list[dict[str, Any]]:
    """Wrap each result in the card partial's ``{page, snippet}`` shape.

    On a browse there is no query and therefore no snippet, and the card falls back to its
    lead line — which is the whole reason the card takes ``snippet`` as an optional
    parameter instead of search results forking into a second card shape.
    """
    return [{"page": page, "snippet": page.search_snippet(q) if q else ""} for page in pages]


@login_required
@wiki_feature_required
def hub_wiki_search(request: HttpRequest) -> HttpResponse:
    """``/wiki/search/`` — one box across every store, and a browse when the box is empty.

    An empty ``q`` browses rather than blanking: this URL is where spec B's "See all"
    links and spec E's Policies box land, and ``none()`` on an empty query would dead-end
    all of them. The queryset keeps its ``none()`` behavior; the *view* is what decides
    that no query means browse.

    Unknown parameter values are ignored rather than raising, because these arrive from
    printed links, month-old emails, and other people's templates.
    """
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    if kind not in WikiPage.Kind.values:
        kind = ""
    stale = request.GET.get("stale") == "1"
    source = request.GET.get("source", "")
    if source not in ("wiki", "help", "policies"):
        source = ""
    scope_all = request.GET.get("scope") == "all"
    guild_slug = "" if scope_all else request.GET.get("guild", "").strip()
    guild = Guild.objects.filter(slug=guild_slug).first() if guild_slug else None

    base = visible_wiki_pages(request).filtered(guild=guild, kind=kind, stale=stale)
    if guild_slug and guild is None:
        # An unknown slug narrows to nothing and says so, rather than silently widening
        # the search to the whole wiki and pretending the filter was honored.
        base = base.none()
    wiki_results = base.search(q) if q else base.order_by("-updated_at")
    # Spec B's failed-search log hooks in HERE, and only when q is non-empty: a browse
    # that matches nothing is not a failed search and must not pollute B's panel. The
    # actual record(...) call sits below the help query, because a question the Help
    # Center answered did not "find nothing" — the panel is titled with what it counts.
    wiki_total = wiki_results.count()

    # The help query runs whenever it could produce a group, even under ?source=wiki, so
    # the source chip row knows whether there is more than one source to offer.
    help_results: list[WikiArticle] = []
    help_total = 0
    if q and SiteConfiguration.load().help_page_enabled:
        help_matches = WikiArticle.objects.search(q)
        # The count and the rows are separate on purpose: the group renders at most a page
        # of results, but "20 pages match" when 45 do is a lie the member can check.
        help_total = help_matches.count()
        help_results = list(help_matches[:_SEARCH_PAGE_SIZE])

    if q and wiki_total == 0 and help_total == 0:
        # One line, one call site: WikiSearchMiss.objects.record applies every write rule
        # (signed in, 3-120 characters, one row per member per query per day, 90-day
        # retention) and declines quietly when one of them says no.
        WikiSearchMiss.objects.record(query=q, guild=guild, member=_active_member(request))

    available_sources = ["wiki"]
    if help_results:
        available_sources.append("help")

    groups: list[dict[str, Any]] = []
    page_obj = None
    if source in ("", "wiki"):
        paginator = Paginator(wiki_results.with_fact_prefetch(), _SEARCH_PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page"))
        if page_obj.object_list:
            groups.append(
                {
                    "source_label": "Wiki",
                    "source_slug": "wiki",
                    "rows": _search_result_rows(page_obj.object_list, q),
                }
            )
    if help_results and source in ("", "help"):
        groups.append(
            {
                "source_label": "Help",
                "source_slug": "help",
                "rows": _search_result_rows(help_results, q),
            }
        )

    # dict[key], not a chain of conditionals: source is already validated to one of these
    # three or blank, so a fourth value should raise rather than silently count the wrong
    # store. "policies" is spec E's group and answers 0 until E ships.
    source_totals = {"wiki": wiki_total, "help": help_total, "policies": 0}
    total = source_totals[source] if source else wiki_total + help_total
    context = _get_hub_context(request)
    context.update(
        {
            "q": q,
            "kind_filter": kind,
            "guild_filter": guild_slug,
            "guild": guild,
            "stale_filter": stale,
            "source_filter": source,
            "groups": groups,
            "page": page_obj,
            "base_params": _search_base_params(request),
            "preserved_fields": _search_preserved_fields(kind, guild_slug, stale, source),
            "kind_choices": WikiPage.Kind.choices,
            "available_sources": available_sources,
            "show_source_chips": len(available_sources) > 1,
            "result_count_line": _result_count_line(q, total, guild, stale),
            "is_filtered": bool(kind or guild_slug or stale or source),
            "has_results": bool(groups),
            # Only built on the zero-result branch, so the normal search path pays nothing
            # for a button nobody is going to see.
            "discord_ask": _wiki_discord_ask(guild) if q and not groups else {},
        }
    )
    return render(request, "hub/wiki_search.html", context)


def _wiki_discord_ask(guild: Guild | None) -> dict[str, str]:
    """The zero-result screen's "Ask In #woodworking" target, or ``{}`` for no button.

    Falls back to the server link when the guild has no channel id, and renders nothing at
    all when the server id is unset — a broken Discord link is worse than no button.
    """
    server_id = SiteConfiguration.load().discord_server_id
    if not server_id:
        return {}
    if guild is not None and guild.discord_channel_id:
        return {
            "url": f"https://discord.com/channels/{server_id}/{guild.discord_channel_id}",
            "label": f"Ask In {guild.announcement_channel_label}",
        }
    return {"url": f"https://discord.com/channels/{server_id}", "label": "Ask On Discord"}


def _search_base_params(request: HttpRequest) -> str:
    """Every active search parameter except ``page``, for the pager's links."""
    carried = {
        key: request.GET[key]
        for key in ("q", "guild", "kind", "stale", "source", "scope")
        if request.GET.get(key, "").strip()
    }
    return urlencode(carried)


def _search_preserved_fields(kind: str, guild_slug: str, stale: bool, source: str) -> list[tuple[str, str]]:
    """Hidden inputs the search box re-submits, so searching inside a filter keeps it."""
    fields = []
    if guild_slug:
        fields.append(("guild", guild_slug))
    if kind:
        fields.append(("kind", kind))
    if stale:
        fields.append(("stale", "1"))
    if source:
        fields.append(("source", source))
    return fields


def _result_count_line(q: str, total: int, guild: Guild | None, stale: bool) -> str:
    """The one plain sentence above the results, for a search and for a browse alike."""
    if q:
        return f'{total} {"page" if total == 1 else "pages"} match "{q}".'
    if stale:
        return f"{total} {'page' if total == 1 else 'pages'} are waiting to be checked."
    if guild is not None:
        return f"{total} {'page' if total == 1 else 'pages'} in {guild.name}."
    return "Everything in the wiki, newest first."


@login_required
@wiki_feature_required
def hub_wiki_page(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/`` — the reading page, in the brief's fixed top-to-bottom order.

    Resolves through ``objects.all()`` so an archived page still explains itself, but a
    page spec D's safety gate is holding still 404s for anyone who could not see it in a
    listing — otherwise the gate would only be a filter on the lists.
    """
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    if _hidden_from(request, page):
        return _not_found(request)

    member = _editing_member(request)
    can_edit = can_edit_wiki_page(request, page)
    can_moderate = can_moderate_wiki_page(request, page)
    is_archived = page.archived_at is not None
    attachments = list(page.attachments.select_related("uploaded_by"))
    facts = list(page.facts.all())
    checked_today = (
        page.last_checked_at is not None and timezone.localtime(page.last_checked_at).date() == timezone.localdate()
    )

    context = _get_hub_context(request)
    context.update(
        {
            "page": page,
            "facts": facts,
            # A seeded stub HAS facts, all of them blank, so the no-facts nudge never
            # reaches the pages that most need someone to fill them in.
            "facts_all_blank": bool(facts) and not any(fact.value for fact in facts),
            "photo_attachments": [item for item in attachments if item.is_image],
            "file_attachments": [item for item in attachments if not item.is_image],
            # Empty while the body is still the seeder's headings: the body partial renders
            # the "nobody has written this yet" invitation there instead of the prose, so a
            # chip row would scroll to sections that are not on the page.
            "toc": page.toc() if page.has_written_body else [],
            # No bulk sets: one page renders exactly one Official block, so the two-query
            # optimization Equipment.access_state offers buys nothing here.
            "official_block": page.official_block_context(member),
            "related_pages": page.related_pages(),
            # Counted once here rather than in the byline partial, which renders twice.
            "revision_count": page.revision_count,
            "can_edit": can_edit,
            "can_verify": can_verify_wiki_page(request, page),
            "is_archived": is_archived,
            # "Still accurate" is the one action that does not need edit rights (a member
            # can attest that a page matched reality; authority is a different claim), but
            # there is nothing to keep accurate about an archived page.
            "can_confirm": _active_member(request) is not None and not is_archived,
            "checked_today": checked_today,
            "show_confirm_cue": (
                request.GET.get("confirm") == "1" and _active_member(request) is not None and not is_archived
            ),
            "quick_tip_form": WikiQuickTipForm(),
            "quick_photo_form": WikiQuickPhotoForm(),
            # Spec B's "with a note…" modal. One field, so it is a modal and a toast.
            "verify_note_form": WikiVerifyNoteForm(),
            "tip_drops_verification": (
                page.status == WikiPage.Status.GUILD_VERIFIED and not can_verify_wiki_page(request, page)
            ),
        }
    )
    context.update(_review_banner_context(request, page))
    context.update(
        {
            "can_moderate": can_moderate,
            # A member sees the tombstone and nothing else; a moderator sees it and then
            # the whole page below, because the only way to fix an over-archive is to read
            # what was archived.
            "show_body": not is_archived or can_moderate,
            "archive_form": WikiArchiveForm() if can_moderate and not is_archived else None,
            "archive_confirm_message": _archive_confirm_message(page),
            # The bar renders when there is something on it: an editor's photo/tip, or the
            # Report anyone active gets. Hidden entirely on an archived page — there is
            # nothing to add to, keep accurate, or report on a tombstone.
            "show_actionbar": not is_archived and (can_edit or _active_member(request) is not None),
            "redirect_form": WikiRedirectForm(page=page) if can_moderate and is_archived else None,
            "note_form": WikiOfficialNoteForm(initial={"note": page.official_note}) if can_moderate else None,
        }
    )
    return render(request, "hub/wiki_page.html", context)


# --- Moderation: shared context (spec D) ----------------------------------------------


def _review_banner_context(request: HttpRequest, page: WikiPage) -> dict[str, Any]:
    """The amber banner and the Report control's two states, from ONE queryset evaluation.

    The banner reads :class:`WikiReport` rows; the status pill, the ``needs_review()``
    queryset and the search-result chip read the two denormalized columns
    ``mark_needs_review`` maintains. Both consumers exist, and this is the row-reading one.
    """
    member = _editing_member(request)
    open_reports = list(WikiReport.objects.open().for_page(page).select_related("reporter"))
    mine = next((report for report in open_reports if member is not None and report.reporter_id == member.pk), None)
    return {
        "open_report": open_reports[0] if open_reports else None,
        "other_open_reports": max(len(open_reports) - 1, 0),
        "my_open_report": mine,
        "resolve_form": WikiResolveForm(),
        "report_form": WikiReportForm(),
        # Nothing to report on a tombstone, and reporting takes an active membership.
        "can_report": _active_member(request) is not None and page.archived_at is None,
    }


def _archive_confirm_message(page: WikiPage) -> str:
    """The archive confirmation's copy, which depends on there being an author to email.

    Every seeded Equipment stub has ``created_by=None`` and the FK is ``SET_NULL``, so a
    fixed "Rowan Ellis wrote this page and will be emailed your reason" would be a lie
    told at the moment a moderator is about to do the most consequential thing in the wiki.
    """
    shared = (
        "The link keeps working, and anyone who opens it sees when it was removed and why. "
        "Members stop finding it in search. "
    )
    author = page.created_by
    if author is None:
        return (
            f"{shared}Nobody is listed as the author of this page, so no one will be emailed "
            "— the reason still shows on the page."
        )
    return f"{shared}{author.display_name} wrote this page and will be emailed your reason, by name."


# --- Writing (PR A3) ------------------------------------------------------------------


@login_required
@wiki_feature_required
def hub_wiki_new(request: HttpRequest) -> HttpResponse:
    """``/wiki/new/`` — the starter chooser.

    Renders from ``membership/wiki_starters.py`` module data and never from hardcoded
    markup, which is the seam spec D extends with its seventh Safety & Rules card.
    """
    context = _get_hub_context(request)
    context.update(
        {
            "starters": [{"kind": key, **value} for key, value in STARTERS.items()],
            "carried_params": _carry_params(request),
            "can_write": _active_member(request) is not None,
        }
    )
    return render(request, "hub/wiki_new.html", context)


def _fact_formset(
    *,
    data: Any = None,
    page: WikiPage | None,
    prompts: list[str] | None = None,
    initial_facts: list[dict[str, str]] | None = None,
) -> Any:
    """Build the Quick Answers formset for create mode or for edit mode.

    Create mode needs ``extra=len(prompts)`` with matching ``initial``: a model formset
    renders ``initial_form_count() + extra`` rows, which is ``0 + 0`` on a brand-new page,
    so ``extra=0`` there would render no starter prompts at all and the brief's
    "prompt for the facts first" mechanic would silently not exist. Edit mode keeps
    ``extra=0`` per FRONTEND.md Rule 11, so no perpetual blank row can block Save.
    """
    queryset = WikiPageFact.objects.filter(page=page) if page is not None else WikiPageFact.objects.none()
    # A resumed draft only overrides the prompts when it actually carries rows. Autosave's
    # allowlist is title and body, so a new-page draft's facts are ALWAYS [] — treating
    # "not None" as authoritative rendered zero rows, and "Use My Draft" silently produced
    # a promptless form while a plain visit to the same URL got one row per prompt.
    if initial_facts:
        initial = initial_facts
    elif prompts:
        initial = [{"label": prompt, "value": ""} for prompt in prompts]
    else:
        initial = []
    formset_class = wiki_fact_formset_class(extra=len(initial))
    return formset_class(data, queryset=queryset, initial=initial, prefix="facts")


def _attachment_formset(*, data: Any = None, files: Any = None, page: WikiPage | None) -> Any:
    """The attachments formset — ``extra=0`` in both modes; attachments have no prompts."""
    queryset = WikiAttachment.objects.filter(page=page) if page is not None else WikiAttachment.objects.none()
    return WikiAttachmentFormSet(data, files, queryset=queryset, prefix="attachments")


def _submitted_facts(fact_formset: Any) -> list[tuple[str, str]]:
    """The validated Quick Answers rows as ``(label, value)``, in the submitted order.

    Create mode hands these to ``create_page`` instead of saving the formset, so the
    page's FIRST revision snapshots the facts that were actually written with it. Writing
    the revision first and the rows second recorded ``facts=[]`` as version one, and spec
    D's revert would have restored a page state that never existed.

    Rows are ordered by the hidden ``sort_order`` the reorder handler rewrites, with the
    submitted position as the tie break, because ``create_page`` numbers them by position.
    """
    rows: list[tuple[int, int, str, str]] = []
    for index, form in enumerate(fact_formset.forms):
        cleaned = getattr(form, "cleaned_data", None)
        if not cleaned or cleaned.get("DELETE"):
            continue
        rows.append((cleaned["sort_order"], index, cast(str, cleaned["label"]).strip(), cleaned["value"].strip()))
    rows.sort()
    return [(label, value) for _order, _index, label, value in rows]


def _save_child_formsets(
    page: WikiPage,
    fact_formset: Any | None,
    attachment_formset: Any,
    member: Member,
) -> None:
    """Commit the list editors against ``page`` and refresh the search text.

    ``fact_formset`` is None on create, where the rows went in through ``create_page`` so
    the first revision could snapshot them (see :func:`_submitted_facts`).

    Children are saved after the parent, so the page's own ``save()`` could not have seen
    them — which is why ``rebuild_search_text`` is called again here rather than trusted
    to the model's save. Search has to reach the Quick Answers; that is where the useful
    nouns live.
    """
    if fact_formset is not None:
        _save_fact_formset(page, fact_formset)
    attachments = attachment_formset.save(commit=False)
    for attachment in attachments:
        attachment.page = page
        if attachment.uploaded_by_id is None:
            attachment.uploaded_by = member
        attachment.save()
    for deleted in attachment_formset.deleted_objects:
        deleted.delete()
    page.rebuild_search_text()
    page.save(update_fields=["search_text"])


def _save_fact_formset(page: WikiPage, fact_formset: Any) -> None:
    """Commit the Quick Answers rows for an edit."""
    facts = fact_formset.save(commit=False)
    for fact in facts:
        # sort_order comes from the row's hidden input, which the reorder handler rewrites
        # to the visual index. Never re-derive it here: a legitimate 0 would be clobbered
        # and the first row would sort last.
        fact.page = page
        fact.save()
    for deleted in fact_formset.deleted_objects:
        deleted.delete()


def _create_page_from_form(
    request: HttpRequest,
    *,
    starter: Any,
    form: WikiPageCreateForm,
    member: Member,
    fact_formset: Any,
    attachment_formset: Any,
    draft: WikiDraft | None,
    wanted_pk: str,
) -> HttpResponse | None:
    """Create one page, or return None so the view re-renders with a form error.

    This is where THE safety gate lives, and it is the only one in the round. Safety
    content *is* Official content (the brief's Official is "policy, safety, membership
    terms"), so there is no separate field and no box on any form for a member to untick.

    **A Safety page is ALWAYS created held and then published through
    ``publish_proposal``**, which is the round's one authority rule for safety content
    rather than a second one written out here. It matters: ``can_moderate_wiki_scope`` is
    true for any ``GuildStaffMembership`` row, so deciding "published or not" on that
    alone let a guild orienter, secretary or treasurer create a page wearing the Official
    chip — which brief §4 and §5.2 lock to admins and officers. Routing through
    ``publish_proposal`` gives the create path exactly the outcomes the queue already
    gives: effective staff land Official, a guild lead or staff land Guild verified, and
    everyone else stays a held proposal for a second read.

    It also closes the secondary bug that arrangement had: a guild orienter who created an
    Official page was instantly locked out of it, because ``can_edit_wiki_page`` answers
    ``is_effective_staff`` on a published Official page and ``can_verify_wiki_page``
    refuses Official outright. Landing Guild verified leaves it theirs to edit.
    """
    status = starter["status"]
    gated = bool(status)
    # ONE transaction for the whole create. Held-then-publish is four write groups, and
    # this project sets no ATOMIC_REQUESTS: without it, a failure in publish_proposal or
    # its emit left the page committed unpublished, the author looking at a 500 instead of
    # the held screen, nobody told, and their retry meeting "a page called that already
    # exists". A rollback costs a retry that works.
    try:
        with transaction.atomic():
            page = WikiPage.objects.create_page(
                title=form.cleaned_data["title"],
                kind=form.cleaned_data["kind"],
                author=member,
                guild=form.cleaned_data["guild"],
                body=form.cleaned_data["body"],
                status=status,
                facts=_submitted_facts(fact_formset),
                is_published=not gated,
            )
            _save_child_formsets(page, None, attachment_formset, member)
            if draft is not None:
                draft.delete()
            # Whether the wanted row closes does NOT depend on the safety gate: the member
            # wrote the page either way, and the credit is theirs. Held here rather than
            # after the branch because a proposal is where it used to be silently skipped.
            fulfilled = _fulfil_wanted_page(wanted_pk, page)
            if gated and can_moderate_wiki_scope(request, page.guild):
                page.publish_proposal(by=member, as_official=is_effective_staff(request), newly_created=True)
                gated = False
            elif gated:
                page.notify_scope_of_proposal(by=member)
    except WikiError as exc:
        # create_page is the only realistic raiser here: a duplicate title in this scope.
        form.add_error("title", str(exc))
        return None
    if gated:
        # A full-page answer, not a toast: this is a state change the member did not
        # expect, and it needs room to say who has it and what happens next.
        context = _get_hub_context(request)
        context.update({"page": page, "scope_label": _scope_label(page.guild), "is_space_wide": page.guild is None})
        return render(request, "hub/wiki_proposal_held.html", context)
    messages.success(
        request,
        "Page created. That was on the Wanted list. Thanks for writing it."
        if fulfilled
        else "Page created. Thanks for writing it.",
    )
    return redirect(page.get_absolute_url())


@login_required
@wiki_feature_required
def hub_wiki_create(request: HttpRequest, kind: str) -> HttpResponse:
    """``/wiki/new/<kind>/`` — write a page, live, with no approval queue.

    Two required fields, both pre-filled from context, plus the starter prompts and the
    starter headings. A ``?wanted=<pk>`` that rode in from spec B's digest or wanted list
    is closed out on success.
    """
    if kind not in STARTERS:
        return _not_found(request)
    member = _active_member(request)
    if member is None:
        return _forbidden()

    starter = STARTERS[kind]
    scope_guilds, _may_create_space_wide = editable_wiki_scopes(request)
    wanted_pk = request.GET.get("wanted", "") or request.POST.get("wanted", "")
    draft = WikiDraft.objects.filter(page__isnull=True, author=member, kind=kind).first()
    draft_mode = request.GET.get("draft", "")

    if request.method == "POST":
        form = WikiPageCreateForm(request.POST, scope_guilds=scope_guilds)
        fact_formset = _fact_formset(data=request.POST, page=None, prompts=starter["fact_prompts"])
        attachment_formset = _attachment_formset(data=request.POST, files=request.FILES, page=None)
        if form.is_valid() and fact_formset.is_valid() and attachment_formset.is_valid():
            created = _create_page_from_form(
                request,
                starter=starter,
                form=form,
                member=member,
                fact_formset=fact_formset,
                attachment_formset=attachment_formset,
                draft=draft,
                wanted_pk=wanted_pk,
            )
            if created is not None:
                return created
    else:
        if draft_mode == "fresh" and draft is not None:
            draft.delete()
            return redirect(f"{reverse('hub_wiki_create', args=[kind])}?{_carry_params(request)}")
        if draft_mode == "use" and draft is not None:
            form = WikiPageCreateForm(
                initial={
                    "title": draft.title,
                    "kind": starter["page_kind"],
                    "guild": draft.guild,
                    "body": draft.body,
                },
                scope_guilds=scope_guilds,
            )
            fact_formset = _fact_formset(page=None, prompts=starter["fact_prompts"], initial_facts=draft.facts)
        else:
            form = WikiPageCreateForm(
                initial={
                    # The starter's own kind, which is the URL segment for the six content
                    # starters and a real kind the Safety starter picks (its segment is not
                    # one — the brief locks the six kinds).
                    "title": request.GET.get("title", ""),
                    "kind": starter["page_kind"],
                    "guild": _prefilled_guild(request, scope_guilds),
                    "body": starter["body"],
                },
                scope_guilds=scope_guilds,
            )
            fact_formset = _fact_formset(page=None, prompts=starter["fact_prompts"])
        attachment_formset = _attachment_formset(page=None)

    context = _get_hub_context(request)
    context.update(
        {
            "form": form,
            "facts_formset": fact_formset,
            "attachments_formset": attachment_formset,
            "is_create": True,
            "kind": kind,
            "starter": starter,
            "wanted_pk": wanted_pk,
            "resume_draft": draft if _draft_is_offerable(draft, None) and draft_mode == "" else None,
            "resume_use_url": f"{reverse('hub_wiki_create', args=[kind])}?draft=use",
            "resume_fresh_js": f"window.location.href='{reverse('hub_wiki_create', args=[kind])}?draft=fresh';",
            "autosave_url": reverse("hub_wiki_new_autosave", args=[kind]),
            "cancel_url": reverse("hub_wiki_new"),
            "base_revision": "",
        }
    )
    return render(request, "hub/wiki_edit.html", context)


def _prefilled_guild(request: HttpRequest, scope_guilds: list[Guild]) -> Guild | None:
    """The scope the New form opens on: ``?guild=``, else the member's only guild.

    Filing is meant to be two fields both pre-filled from context. A member who belongs to
    exactly one guild almost always means that one, and a member in several is asked.
    """
    slug = request.GET.get("guild", "").strip()
    if slug:
        for guild in scope_guilds:
            if guild.slug == slug:
                return guild
        return None
    return scope_guilds[0] if len(scope_guilds) == 1 else None


def _draft_is_offerable(draft: WikiDraft | None, page: WikiPage | None) -> bool:
    """True when the resume banner should offer this draft back.

    A draft older than the page is stale by definition — the page moved on after it was
    written — and an autosave that fired on focus-out of an untouched editor leaves an
    empty row, which is not "unsaved changes" and must not be offered as if it were.
    """
    if draft is None:
        return False
    if not (draft.title.strip() or draft.body.strip()):
        return False
    if page is not None and draft.updated_at <= page.updated_at:
        return False
    return True


# The marker "Open the Editor With Both" puts between the live page and the parked draft.
# Plain text and not styled markup: it has to survive the sanitizer, read as a divider in
# the editor, and be easy to delete once the member has merged the two by hand.
_MERGE_MARKER = "<p>--- your version ---</p>"


def _merge_body(page: WikiPage, draft: WikiRevision) -> str:
    """The current page, the marker, then the parked draft — for a human to reconcile.

    The one tool that can actually merge two versions is a person in an editor, so this
    screen hands them both and gets out of the way.
    """
    return f"{page.body}{_MERGE_MARKER}{draft.body}"


def _edit_forms_from(
    page: WikiPage,
    draft: WikiDraft | None,
    draft_mode: str,
    can_moderate: bool,
    merge_draft: WikiRevision | None = None,
) -> tuple[WikiPageForm, Any]:
    """The unbound edit form and fact formset, populated from the page or from the draft.

    The saved page is the default even while the resume card is on screen: a member who
    ignores the card entirely edits the live page, which is the safe outcome. Only an
    explicit ``?draft=use`` loads what they typed, and only an explicit ``?merge=<pk>``
    from the conflict screen loads both versions stacked.
    """
    if merge_draft is not None:
        return (
            WikiPageForm(
                page=page,
                can_moderate=can_moderate,
                initial={"title": page.title, "body": _merge_body(page, merge_draft)},
            ),
            _fact_formset(page=page),
        )
    if draft_mode == "use" and draft is not None:
        return (
            WikiPageForm(page=page, can_moderate=can_moderate, initial={"title": draft.title, "body": draft.body}),
            _fact_formset(page=page, initial_facts=draft.facts),
        )
    return WikiPageForm(page=page, can_moderate=can_moderate), _fact_formset(page=page)


def _guard_conflict(request: HttpRequest, page: WikiPage, member: Member, form: WikiPageForm) -> None:
    """Park the submitted text as a draft revision when somebody else saved first.

    The check lives here, around ``apply_edit``, and not inside a new page method: spec A
    owns the one save path in the round and this wraps it, so there is still exactly one
    writer of page content.

    Nothing the member typed exists anywhere but in a durable database row before they see
    a single pixel of the conflict screen. That ordering is the entire feature.

    Raises:
        WikiSaveConflict: When the form's ``base_revision`` is older than the page's
            newest revision. The page itself is not modified.
    """
    submitted = request.POST.get("base_revision", "")
    newest = page.revisions.first()
    if not submitted.isdigit() or newest is None or newest.pk == int(submitted):
        return
    draft = WikiRevision.objects.create(
        page=page,
        author=member,
        kind=WikiRevision.Kind.CONFLICT_DRAFT,
        title=form.cleaned_data["title"],
        body=form.cleaned_data["body"],
        # The page's CURRENT facts, because the fact formset is saved after apply_edit and
        # has not been committed at this point. The prose is what collides; the rows the
        # member reordered are recoverable from the live page either way.
        facts=page.fact_snapshot(),
        status=page.status,
        note="Unmerged: someone else saved first",
    )
    raise WikiSaveConflict(draft=draft, theirs=page)


def _apply_page_edit(
    request: HttpRequest,
    *,
    page: WikiPage,
    form: WikiPageForm,
    member: Member,
    may_verify: bool,
    can_moderate: bool,
    fact_formset: Any,
    attachment_formset: Any,
    draft: WikiDraft | None,
) -> HttpResponse | None:
    """Commit one validated edit, or return None so the view re-renders with a form error.

    ``apply_edit`` snapshots the pre-edit page into a revision, so the child formsets have
    to be saved after it and never before, or the "before" it records is already the after.
    """
    try:
        _guard_conflict(request, page, member, form)
        page.apply_edit(
            editor=member,
            editor_may_verify=may_verify,
            title=form.cleaned_data["title"],
            body=form.cleaned_data["body"],
        )
    except WikiSaveConflict as conflict:
        return redirect("hub_wiki_conflict", slug=page.slug, pk=conflict.draft.pk)
    except WikiError as exc:
        form.add_error(None, str(exc))
        return None
    if can_moderate:
        page.equipment = form.cleaned_data["equipment"]
        page.save(update_fields=["equipment"])
    _save_child_formsets(page, fact_formset, attachment_formset, member)
    if draft is not None:
        draft.delete()
    WikiEditLock.release(page, member)
    messages.success(request, _saved_message(request, page, can_moderate))
    return redirect(page.get_absolute_url())


def _saved_message(request: HttpRequest, page: WikiPage, can_moderate: bool) -> str:
    """The Save confirmation, which for a moderator carries the action the page still needs.

    The path a lead actually walks is banner, Edit, fix the sentence, Save — and nothing on
    it resolved anything, so the page kept quoting a complaint about a sentence that no
    longer existed. Deliberately a prompt and not an automatic resolve: a staff edit is
    often unrelated to the report, and silently closing somebody's report because a typo
    got fixed is exactly the "nobody looked at this" failure the queue exists to prevent.
    """
    if can_moderate and page.reports.filter(resolved_at__isnull=True).exists():
        return "Saved. This page still has a report open, waiting to be marked reviewed."
    return "Saved. Thanks for keeping it right."


@login_required
@wiki_feature_required
def hub_wiki_edit(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/edit/`` — the same template as create, in edit mode.

    On GET with an unsaved draft the editor renders the resume offer and **populates the
    form from the saved page anyway**: a member who ignores the card entirely edits the
    live page, which is the safe outcome. A stale tab from three days ago must never
    quietly overwrite somebody else's finished work.
    """
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_edit_wiki_page(request, page):
        return _forbidden()

    can_moderate = can_moderate_wiki_page(request, page)
    may_verify = can_verify_wiki_page(request, page)
    draft = WikiDraft.objects.filter(page=page, author=member).first()
    draft_mode = request.GET.get("draft", "")
    # Claimed on the GET only. A POST is the save, and claiming there would hand the lock
    # to whoever just finished rather than to whoever is still typing.
    lock_warning = WikiEditLock.claim(page, member) if request.method == "GET" else None

    if request.method == "POST":
        form = WikiPageForm(request.POST, page=page, can_moderate=can_moderate)
        fact_formset = _fact_formset(data=request.POST, page=page)
        attachment_formset = _attachment_formset(data=request.POST, files=request.FILES, page=page)
        if form.is_valid() and fact_formset.is_valid() and attachment_formset.is_valid():
            saved = _apply_page_edit(
                request,
                page=page,
                form=form,
                member=member,
                may_verify=may_verify,
                can_moderate=can_moderate,
                fact_formset=fact_formset,
                attachment_formset=attachment_formset,
                draft=draft,
            )
            if saved is not None:
                return saved
    else:
        if draft_mode == "fresh" and draft is not None:
            draft.delete()
            return redirect(reverse("hub_wiki_edit", args=[page.slug]))
        merge_pk = request.GET.get("merge", "")
        merge_draft = _conflict_or_none(page, int(merge_pk)) if merge_pk.isdigit() else None
        form, fact_formset = _edit_forms_from(page, draft, draft_mode, can_moderate, merge_draft)
        attachment_formset = _attachment_formset(page=page)

    newest_revision = page.revisions.first()
    base_revision = draft.base_revision_id if (draft_mode == "use" and draft is not None) else None
    context = _get_hub_context(request)
    context.update(
        {
            "form": form,
            "page": page,
            "facts_formset": fact_formset,
            "attachments_formset": attachment_formset,
            "is_create": False,
            "can_moderate": can_moderate,
            "verification_warning": page.status == WikiPage.Status.GUILD_VERIFIED and not may_verify,
            "resume_draft": draft if (_draft_is_offerable(draft, page) and draft_mode == "") else None,
            "resume_use_url": f"{reverse('hub_wiki_edit', args=[page.slug])}?draft=use",
            "resume_fresh_js": f"window.location.href='{reverse('hub_wiki_edit', args=[page.slug])}?draft=fresh';",
            "autosave_url": reverse("hub_wiki_autosave", args=[page.slug]),
            "cancel_url": page.get_absolute_url(),
            "base_revision": base_revision or (newest_revision.pk if newest_revision is not None else ""),
            "lock_warning": lock_warning,
        }
    )
    return render(request, "hub/wiki_edit.html", context)


# The autosave allowlist is exactly these two. Fact and attachment rows deliberately do
# NOT autosave: a formset row is only meaningful as part of a complete TOTAL_FORMS
# submission, and a file input cannot be debounce-posted at all. Every other field name is
# the 400 branch, which is what makes the allowlist testable.
_AUTOSAVE_FIELDS = ("title", "body")
_AUTOSAVE_TITLE_MAX = 200


def _clean_autosave(field: str, value: str) -> str:
    """Clean one autosaved field, raising ``ValueError`` with the toast copy on refusal."""
    if field == "title":
        stripped = value.strip()
        if len(stripped) > _AUTOSAVE_TITLE_MAX:
            raise ValueError(f"Keep the title under {_AUTOSAVE_TITLE_MAX} characters.")
        return stripped
    return sanitize_wiki_submission(value)


def _autosave_saved() -> HttpResponse:
    """204 plus the client event the savestate pill listens for."""
    response = HttpResponse(status=204)
    trigger_client_event(response, "wiki-saved", {})
    return response


def _autosave_invalid(message: str) -> HttpResponse:
    """422 plus an error toast — the shipped contract the savestate pill already speaks."""
    response = HttpResponse(message, status=422)
    trigger_toast(response, message, "error")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_autosave(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/autosave/`` — one field at a time into this member's draft.

    A crash net, not a second save path: the page itself only changes when the member
    presses Save. Every accepted write also refreshes spec D's advisory edit lock, which
    is why D needs no polling timer of its own.
    """
    page = _page_or_none(slug)
    if page is None:
        raise Http404("No such wiki page.")
    member = _editing_member(request)
    if member is None or not can_edit_wiki_page(request, page):
        return _forbidden()
    field = request.POST.get("field", "")
    if field not in _AUTOSAVE_FIELDS:
        return HttpResponse("Unknown field.", status=400)
    try:
        value = _clean_autosave(field, request.POST.get("value", ""))
    except ValueError as exc:
        return _autosave_invalid(str(exc))
    newest = page.revisions.first()
    draft, _created = WikiDraft.objects.get_or_create(
        page=page,
        author=member,
        defaults={"kind": page.kind, "guild": page.guild, "base_revision": newest},
    )
    setattr(draft, field, value)
    draft.save(update_fields=[field, "updated_at"])
    _refresh_edit_lock(page, member)
    return _autosave_saved()


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_new_autosave(request: HttpRequest, kind: str) -> HttpResponse:
    """``/wiki/new/<kind>/autosave/`` — the same crash net for a page that does not exist yet.

    Keyed on (author, kind) rather than (page, author), so a member who closes the tab
    mid-sentence finds the text again in ``/wiki/drafts/`` and the resume card offers it
    back on the next visit to the same starter.
    """
    if kind not in STARTERS:
        raise Http404("No such starter.")
    member = _active_member(request)
    if member is None:
        return _forbidden()
    field = request.POST.get("field", "")
    if field not in _AUTOSAVE_FIELDS:
        return HttpResponse("Unknown field.", status=400)
    try:
        value = _clean_autosave(field, request.POST.get("value", ""))
    except ValueError as exc:
        return _autosave_invalid(str(exc))
    draft, _created = WikiDraft.objects.get_or_create(page=None, author=member, kind=kind)
    setattr(draft, field, value)
    draft.save(update_fields=[field, "updated_at"])
    return _autosave_saved()


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_confirm(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/confirm/`` — one tap saying the page still matches the space.

    Answers **200 with a body**, not 204: a 204 carries no body and therefore cannot carry
    the out-of-band swap, so the toast would fire while the stale pill sat there unchanged
    until the next reload. That is exactly the "did that work?" failure a one-tap action
    exists to avoid.
    """
    page = _page_or_none(slug)
    if page is None:
        raise Http404("No such wiki page.")
    member = _active_member(request)
    if member is None:
        return _forbidden()
    if _hidden_from(request, page):
        raise Http404("No such wiki page.")
    if page.archived_at is not None:
        response = HttpResponse("This page is archived.", status=409)
        trigger_toast(response, "This page is archived. There is nothing to confirm.", "error")
        return response
    page.confirm_still_accurate(member)
    response = render(request, "hub/partials/_wiki_status_oob.html", {"page": page, "show_confirm_cue": False})
    trigger_toast(response, "Thanks. Marked as checked today.")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_quick_photo(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/photo/`` — thirty seconds and a camera, the phone-first contribution.

    Writes an attachment and never the body, so unlike a quick tip it does **not** drop a
    green check. That asymmetry is deliberate: the cheapest contribution should also be
    the one with no consequences, because on a phone in a shop it is the only contribution
    most people will ever make.
    """
    page = _page_or_none(slug)
    if page is None:
        raise Http404("No such wiki page.")
    member = _editing_member(request)
    if member is None or not can_edit_wiki_page(request, page):
        return _forbidden()
    form = WikiQuickPhotoForm(request.POST, request.FILES)
    if not form.is_valid():
        message = str(next(iter(form.errors.values()))[0])
        response = HttpResponse(message, status=422)
        trigger_toast(response, message, "error")
        return response
    attachment = WikiAttachment(
        page=page,
        label=form.cleaned_data["caption"],
        file=form.cleaned_data["photo"],
        uploaded_by=member,
        sort_order=page.attachments.count(),
    )
    attachment.save()
    page.rebuild_search_text()
    page.save(update_fields=["search_text"])
    response = render(
        request,
        "hub/partials/_wiki_attachments.html",
        {
            "page": page,
            "photo_attachments": [item for item in page.attachments.select_related("uploaded_by") if item.is_image],
            "file_attachments": [item for item in page.attachments.select_related("uploaded_by") if not item.is_image],
            "can_edit": True,
        },
    )
    trigger_toast(response, "Photo added. Thanks.")
    trigger_client_event(response, "close-modal", "wiki-photo")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_quick_tip(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/tip/`` — one or two sentences under "Tips From Members".

    A tip *is* unreviewed text on the page, so it goes through ``apply_edit`` and drops a
    green check exactly like a full edit would. The modal says so before the member
    commits, which is the difference between a rule and a trap.
    """
    page = _page_or_none(slug)
    if page is None:
        raise Http404("No such wiki page.")
    member = _editing_member(request)
    if member is None or not can_edit_wiki_page(request, page):
        return _forbidden()
    form = WikiQuickTipForm(request.POST)
    if not form.is_valid():
        message = str(next(iter(form.errors.values()))[0])
        response = HttpResponse(message, status=422)
        trigger_toast(response, message, "error")
        return response
    try:
        page.add_tip(
            member=member,
            editor_may_verify=can_verify_wiki_page(request, page),
            tip_html=form.cleaned_data["tip_html"],
        )
    except WikiError as exc:
        response = HttpResponse(str(exc), status=422)
        trigger_toast(response, str(exc), "error")
        return response
    # can_edit is True by construction here (the route is gated on it), and the partial
    # needs it for the empty-body branch's "+ Add What You Know" link.
    response = render(request, "hub/partials/_wiki_body.html", {"page": page, "can_edit": True})
    trigger_toast(response, "Tip added. Thanks.")
    trigger_client_event(response, "close-modal", "wiki-tip")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_image_upload(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/image/`` — the editor's image button, in the AJAX upload contract.

    Returns a URL under ``wiki/body/`` so the sanitizer's wiki profile lets the resulting
    ``<img>`` through. Quill's default base64 paste is refused by that same allowlist, so
    a pasted image is dropped rather than bloating the body column.
    """
    from PIL import UnidentifiedImageError

    from core.images import normalize_image, store_content_addressed

    page = _page_or_none(slug)
    if page is None:
        raise Http404("No such wiki page.")
    if not can_edit_wiki_page(request, page):
        return _forbidden()
    upload = request.FILES.get("image")
    if upload is None:
        return JsonResponse({"error": "No file provided."}, status=400)
    max_bytes = settings.MAX_UPLOAD_IMAGE_BYTES
    if upload.size is None or upload.size > max_bytes:
        return JsonResponse({"error": f"Photo must be {max_bytes / (1024 * 1024):.0f} MB or smaller."}, status=400)
    try:
        normalized = normalize_image(upload, max_long_edge=settings.IMAGE_MAX_LONG_EDGE_GALLERY)
    except (UnidentifiedImageError, OSError, ValueError):
        # Pillow could not read the bytes. The editor gets a plain message rather than a
        # 500, because a member holding a phone cannot act on a stack trace.
        return JsonResponse({"error": "That does not look like a photo."}, status=400)
    from django.core.files.storage import default_storage

    stored = store_content_addressed(normalized.read(), prefix="wiki/body/")
    return JsonResponse({"url": default_storage.url(stored)})


@login_required
@wiki_feature_required
def hub_wiki_drafts(request: HttpRequest) -> HttpResponse:
    """``/wiki/drafts/`` — the member's own unfinished writing, and nobody else's.

    Also lists the pages spec D's safety gate is holding, so a member whose page went to
    a lead for a read is never left wondering where it went.
    """
    member = _editing_member(request)
    if member is None:
        return _forbidden()
    held_pages = list(
        WikiPage.objects.filter(is_published=False, created_by=member).not_archived().select_related("guild")
    )
    context = _get_hub_context(request)
    context.update(
        {
            # openable_for and not for_member: once the safety gate can hold a page back,
            # archiving can lock it, and publishing can make it Official, a member can be
            # left holding a draft whose editor now refuses them — and this page would list
            # the title and then 403 on "Keep Writing".
            "drafts": WikiDraft.objects.openable_for(request, member),
            "held_pages": held_pages,
        }
    )
    return render(request, "hub/wiki_drafts.html", context)


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_draft_discard(request: HttpRequest, pk: int) -> HttpResponse:
    """``/wiki/drafts/<pk>/discard/`` — throw away one of my own drafts, and only mine."""
    member = _editing_member(request)
    if member is None:
        return _forbidden()
    draft = WikiDraft.objects.filter(pk=pk, author=member).first()
    if draft is None:
        raise Http404("No such draft.")
    draft.delete()
    messages.success(request, "Draft discarded.")
    return redirect("hub_wiki_drafts")


# --- Stickers: the /m/ short link, the QR download, and the print sheet (PR A4) --------


@wiki_feature_required
def hub_wiki_qr(request: HttpRequest, code: str) -> HttpResponse:
    """``/m/<code>/`` — the sticker route. A scan must never dead-end.

    The one wiki view with no ``@login_required``, because the whole point is the signed
    out case: a member scanning a sticker on a machine is sent to the login screen
    carrying that machine's page as ``?next=``, so finishing the emailed-code login lands
    them on the tool they are standing in front of rather than on the home page. Without
    that, every first scan teaches people the sticker does not work.

    The code is uppercased before lookup, so a phone camera that lower-cases the path
    still resolves. Visibility is deliberately NOT checked here: the reading page already
    answers that question, and duplicating the gate is how two gates drift apart.
    """
    page = WikiPage.objects.filter(qr_code=code.upper()).first()
    if page is None:
        # A printed sticker outlives the page it was made for, so an unknown code gets a
        # written explanation rather than a bare 404 — but the wiki is member-only, and
        # that explanation lives in the member shell. Sending a signed-out scanner
        # through login with this same path as ``next`` gets them the real page, one hop
        # later, instead of rendering member chrome to the street.
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        context = _get_hub_context(request)
        return render(request, "hub/wiki_qr_missing.html", context, status=404)
    target = page.get_absolute_url()
    if request.user.is_authenticated:
        return redirect(target)
    return redirect_to_login(target)


@login_required
@wiki_feature_required
def hub_wiki_qr_download(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/qr/?fmt=svg|png`` — the sticker QR as a file, in the guild shape.

    Gated on ``can_edit_wiki_page``: the QR is a page's own share artifact and sits in the
    "Share This Page" card, which the same gate renders.
    """
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    if not can_edit_wiki_page(request, page):
        return _forbidden()
    fmt = request.GET.get("fmt", "svg")
    if fmt == "svg":
        resp = HttpResponse(page.qr_svg(), content_type="image/svg+xml")
    elif fmt == "png":
        resp = HttpResponse(page.qr_png_bytes(), content_type="image/png")
    else:
        raise Http404("Unknown QR format.")
    resp["Content-Disposition"] = f'attachment; filename="{page.slug}-qr.{fmt}"'
    return resp


@login_required
@wiki_feature_required
def hub_wiki_stickers(request: HttpRequest) -> HttpResponse:
    """``/wiki/stickers/`` — a printable sheet of QR stickers, one shop at a time.

    ``is_effective_staff`` rather than ``can_moderate_wiki_page``: a staff-wide printable
    sheet has no single page in scope, so there is no page argument to moderate against.

    A standalone print document in the ``guild_flyer`` idiom — its own stylesheet, its own
    ``@page`` rule, black on white in both themes, no member chrome.
    """
    if not is_effective_staff(request):
        return _forbidden()
    guild_slug = request.GET.get("guild", "").strip()
    space_wide_only = guild_slug == "space-wide"
    guild = Guild.objects.filter(slug=guild_slug).first() if guild_slug and not space_wide_only else None
    kind = request.GET.get("kind", WikiPage.Kind.MACHINE.value).strip()
    if kind not in WikiPage.Kind.values:
        kind = WikiPage.Kind.MACHINE.value

    # published(): visible_wiki_pages hands effective staff EVERYTHING, so this is the only
    # thing keeping a page spec D's safety gate is holding off a sheet somebody prints and
    # tapes to a wall.
    pages = visible_wiki_pages(request).not_archived().published().filtered(guild=guild, kind=kind)
    if space_wide_only:
        pages = pages.space_wide()
    elif guild_slug and guild is None:
        pages = pages.none()
    return render(
        request,
        "hub/wiki_sticker_sheet.html",
        {
            "stickers": [{"page": page, "qr_svg": page.qr_svg()} for page in pages.order_by("title")],
            "guild": guild,
            # Only the machine sheet can honestly point at the equipment seeder, and
            # "No how to do something pages yet" is the enum's label doing a noun's job.
            "kind_is_machine": kind == WikiPage.Kind.MACHINE,
        },
    )


# --- Moderation: report, withdraw, resolve (spec D §6.1-6.5) --------------------------


def _review_link(request: HttpRequest) -> tuple[bool, int]:
    """``(may open the queue, items waiting)`` for the wiki home's link.

    "Waiting" is BOTH lists the queue shows: open reports and held safety proposals. It
    counted reports alone at first, so a lead with three proposals and no reports saw a
    bare "Review queue" with no number — on the very screen added to make held proposals
    discoverable, chased by the very event added to announce them.

    One call, because every answer comes off the same two-query scope lookup and the home
    page's query count is budgeted: asking twice put four avoidable queries on the busiest
    screen in the feature.
    """
    guilds, space_wide = moderatable_wiki_scopes(request)
    if not guilds and not space_wide:
        return False, 0
    guild_ids = [guild.pk for guild in guilds]
    reports = WikiReport.objects.open().for_scopes(guild_ids, include_space_wide=space_wide).count()
    proposals = (
        _scoped_pages(guild_ids, space_wide=space_wide)
        .filter(archived_at__isnull=True, is_published=False, status=WikiPage.Status.OFFICIAL)
        .count()
    )
    return True, reports + proposals


def _scope_label(guild: Guild | None) -> str:
    """The quiet neutral attribute chip a queue row carries. Never a coloured pill."""
    return guild.name if guild is not None else "Space-wide"


def _report_response(request: HttpRequest, page: WikiPage, *, status: int = 200) -> HttpResponse:
    """The report modal's body, plus OOB swaps for the banner and both Report controls.

    A 204 cannot carry an ``hx-swap-oob`` swap, so a "204 plus a toast plus an OOB" would
    toast success over a stale screen. This is a 200 whose body resets the modal and whose
    out-of-band fragments update the two things the member is looking at.
    """
    context = _get_hub_context(request)
    context.update({"page": page, "oob": True})
    context.update(_review_banner_context(request, page))
    context["can_moderate"] = can_moderate_wiki_page(request, page)
    return render(request, "hub/partials/_wiki_report_result.html", context, status=status)


# --- The guild Wiki tab: verification and wanted pages (spec B) ------------------------

# How many wanted rows one page of the dedicated list shows before the pager takes over.
_WANTED_PAGE_SIZE = 25


def _can_manage_wanted_scope(request: HttpRequest, guild: Guild | None) -> bool:
    """``can_verify_wiki_page``'s authority test, asked about a SCOPE rather than a page.

    A guild scope defers to :func:`can_edit_guild` (lead, every staff role, orienters
    included, plus effective staff); the space-wide scope takes :func:`is_effective_staff`,
    because there is no guild whose authority could stand behind it. Deliberately the same
    test the lead panels render against, so a lead never sees a control the view refuses.
    """
    if guild is None:
        return is_effective_staff(request)
    return can_edit_guild(request, guild)


def _wanted_scope(request: HttpRequest) -> tuple[Guild | None, bool]:
    """``(guild, known)`` from ``?guild=<slug>``. Blank means the space-wide scope.

    ``known`` is False for a slug that matches nothing, which the view answers with a 404
    rather than silently widening to the space-wide list.
    """
    slug = request.GET.get("guild", "").strip()
    if not slug:
        return None, True
    guild = Guild.objects.filter(slug=slug).first()
    return guild, guild is not None


def _fulfilable_pages(request: HttpRequest, guild: Guild | None) -> list[WikiPage]:
    """The live pages in one scope that could close a wanted row, fetched ONCE per screen.

    A list and not a queryset on purpose: every open row's Mark As Written modal renders
    the same candidates, so a lead looking at 25 requests costs one query rather than 25.
    """
    live = visible_wiki_pages(request).not_archived().published()
    scoped = live.for_guild(guild) if guild is not None else live.space_wide()
    return list(scoped.order_by("title"))


def _wanted_row_context(
    request: HttpRequest,
    row: WikiWantedPage,
    *,
    can_manage: bool | None = None,
    fulfil_pages: list[WikiPage] | None = None,
) -> dict[str, Any]:
    """The context one wanted row renders against, on the tab and on the list alike.

    ``can_manage`` and ``fulfil_pages`` are passed in by list callers, which already know
    the answer for the whole scope; a single-row caller (an HTMX swap) lets them default
    and pays for the two lookups once.
    """
    member = _active_member(request)
    manage = _can_manage_wanted_scope(request, row.guild) if can_manage is None else can_manage
    context: dict[str, Any] = {
        "row": row,
        # Rendered into the Release confirm modal, which teleports over the row and hides
        # the "Claimed by Sam, 5 weeks ago" line the lead would otherwise be reading.
        "claimed_ago": timesince(row.claimed_at) if row.claimed_at is not None else "",
        "target": f"#wiki-wanted-row-{row.pk}",
        "release_confirm_id": f"wiki-wanted-release-{row.pk}",
        "fulfil_modal_id": f"wiki-wanted-fulfil-{row.pk}",
        "can_manage": manage,
        "is_claimer": member is not None and row.claimed_by_id == member.pk,
        "can_claim": member is not None,
        "fulfil_form": None,
    }
    if manage and row.fulfilled_page_id is None:
        pages = _fulfilable_pages(request, row.guild) if fulfil_pages is None else fulfil_pages
        # Prefixed per row: without it, 25 modals would all render id_page and every label
        # would point at the first one.
        context["fulfil_form"] = WikiWantedFulfilForm(pages=pages, prefix=f"fulfil{row.pk}")
    return context


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_report(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/report/`` — say this page is wrong, in two taps.

    The reporter sees the amber banner appear with their own words in it, on the page they
    were reading. That is the answer to "what happens next": the toast says who was told,
    the banner shows the effect.
    """
    page = _page_or_none(slug)
    if page is None or _hidden_from(request, page) or page.archived_at is not None:
        return _not_found(request)
    member = _active_member(request)
    if member is None:
        return _forbidden()
    form = WikiReportForm(request.POST)
    if not form.is_valid():
        context = _get_hub_context(request)
        context.update({"page": page, "report_form": form})
        return render(request, "hub/partials/_wiki_report_form.html", context, status=200)
    try:
        WikiReport.file(page=page, reporter=member, reason=form.cleaned_data["reason"])
    except DuplicateWikiReport as exc:
        # Not an error page: they did nothing wrong, and the state they are in is exactly
        # the one the "You reported this" control describes.
        response = _report_response(request, page)
        trigger_toast(response, str(exc), "info")
        trigger_client_event(response, "close-modal", "wiki-report")
        return response
    response = _report_response(request, page)
    # Order matters: trigger_toast OVERWRITES HX-Trigger and trigger_client_event merges
    # into it, so a toast set second is silently dropped.
    trigger_toast(response, "Thanks. A guild lead has been notified.", "success")
    trigger_client_event(response, "close-modal", "wiki-report")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_report_withdraw(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/report/withdraw/`` — take back my own misfire.

    Keyed on the page and the requester rather than on a report id, so the confirmation
    rendered with the page never goes stale against a report filed after it loaded.
    """
    page = _page_or_none(slug)
    if page is None or _hidden_from(request, page):
        return _not_found(request)
    member = _editing_member(request)
    if member is None:
        return _forbidden()
    report = WikiReport.objects.open().for_page(page).filter(reporter=member).first()
    if report is None:
        messages.info(request, "You do not have a report open on this page.")
        return redirect(page.get_absolute_url())
    report.withdraw(by=member)
    messages.success(request, "Report withdrawn.")
    return redirect(page.get_absolute_url())


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_report_resolve(request: HttpRequest, pk: int) -> HttpResponse:
    """``/wiki/report/<pk>/resolve/`` — one view for both places a lead can close a report.

    The queue card and the amber banner open the same modal and post here; only the swap
    differs, and the caller says which with a ``source`` field. Marking a report reviewed
    from the banner is the fix for the round's worst half-built loop: the path a lead walks
    is banner, Edit, fix the sentence, and nothing on it used to resolve anything.
    """
    report = WikiReport.objects.select_related("page", "page__guild", "reporter").filter(pk=pk).first()
    if report is None:
        raise Http404("No such report.")
    if not can_moderate_wiki_page(request, report.page):
        return _forbidden()
    member = _editing_member(request)
    if member is None:
        return _forbidden()
    form = WikiResolveForm(request.POST)
    from_banner = request.POST.get("source") == "banner"
    if not form.is_valid():
        # Never resolve on invalid input, and never drop what they typed behind a green
        # toast. The success swap targets the card or the banner, so the error response is
        # retargeted at the modal body it was typed into — htmx's documented mechanism for
        # exactly this split.
        context = _get_hub_context(request)
        context.update(
            {
                "report": report,
                "resolve_form": form,
                "source": request.POST.get("source", ""),
                "resolve_target": request.POST.get("resolve_target", ""),
            }
        )
        response = render(request, "hub/partials/_wiki_resolve_form.html", context)
        response["HX-Retarget"] = f"#resolve-{report.pk}-body"
        response["HX-Reswap"] = "innerHTML"
        return response
    note = form.cleaned_data["resolution"]
    try:
        report.resolve(by=member, note=note)
    except AlreadyResolved as exc:
        message, tone = str(exc), "info"
    else:
        message, tone = "Marked reviewed.", "success"
    if from_banner:
        page = report.page
        context = _get_hub_context(request)
        context.update({"page": page, "can_moderate": True})
        context.update(_review_banner_context(request, page))
        response = render(request, "hub/partials/_wiki_review_banner.html", context)
    else:
        guilds, space_wide = moderatable_wiki_scopes(request)
        remaining = (
            WikiReport.objects.open().for_scopes([guild.pk for guild in guilds], include_space_wide=space_wide).exists()
        )
        context = _get_hub_context(request)
        context.update({"remaining": remaining})
        response = render(request, "hub/partials/_wiki_review_resolved.html", context)
    trigger_toast(response, message, tone)
    trigger_client_event(response, "close-modal", f"resolve-{report.pk}")
    return response


def _queue_scope_line(guilds: list[Guild], space_wide: bool) -> str:
    """What this reviewer is being shown, said out loud. A queue that silently filters is
    a queue people stop trusting."""
    if space_wide:
        return "Showing every scope."
    names = [guild.name for guild in guilds]
    if not names:
        return "You do not lead or staff a guild yet."
    if len(names) == 1:
        return f"Showing reports for {names[0]}."
    return f"Showing reports for {', '.join(names[:-1])} and {names[-1]}."


_REVIEW_PAGE_SIZE = 25


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_verify(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/verify/`` — one tap that says "I read this and I stand behind it".

    Answers **200 with a body carrying the out-of-band fragment**, never 204: a 204 has no
    body, so it could not carry the swap and the toast would fire while a stale Community
    pill sat there until the next reload. The toast is set first, because
    ``trigger_toast`` overwrites ``HX-Trigger`` while ``trigger_client_event`` merges.

    A plain (non-HTMX) post still works and redirects back with a Django message, so the
    page-header control degrades with JavaScript off.
    """
    page = _page_or_none(slug)
    if page is None:
        raise Http404("No such wiki page.")
    member = _active_member(request)
    if member is None:
        return _forbidden()
    if _hidden_from(request, page):
        raise Http404("No such wiki page.")
    if not can_verify_wiki_page(request, page):
        return _forbidden()

    # HX-Boosted is the discriminator, not HX-Request alone: hub/base.html boosts the whole
    # body, so a plain <form method="post"> (the confirm modal, and the JS-off fallback)
    # arrives carrying HX-Request too. Answering that with a fragment would swap a bare
    # <div> in where the page used to be.
    is_htmx = request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"
    removing = request.POST.get("remove") == "1"
    note = ""
    if not removing:
        note_form = WikiVerifyNoteForm(request.POST)
        if not note_form.is_valid():
            return _verify_error(request, page, "Keep the note to 280 characters.", is_htmx=is_htmx)
        note = note_form.cleaned_data["note"]
    try:
        if removing:
            page.unverify(member)
        else:
            page.verify(member, note=note)
    except WikiError as exc:
        return _verify_error(request, page, str(exc), is_htmx=is_htmx)

    message = "Verification removed." if removing else "Verified. Thanks for reading it."
    if not is_htmx:
        messages.success(request, message)
        return redirect(page.get_absolute_url())
    response = render(request, *_verify_fragment(request, page))
    trigger_toast(response, message)
    return response


def _verify_error(request: HttpRequest, page: WikiPage, message: str, *, is_htmx: bool) -> HttpResponse:
    """One place both verify failure paths answer from, so the two cannot drift."""
    if not is_htmx:
        messages.error(request, message)
        return redirect(page.get_absolute_url())
    response = HttpResponse(message, status=400)
    trigger_toast(response, message, "error")
    return response


def _verify_fragment(request: HttpRequest, page: WikiPage) -> tuple[str, dict[str, Any]]:
    """``(template, context)`` for the surface that posted — the page header or a tab row.

    Both fragments are out-of-band, so the caller's form can use ``hx-swap="none"`` and
    neither surface needs to know where the other's markup lives.
    """
    if request.POST.get("surface") == "tab":
        page.tab_show_verify = (  # type: ignore[attr-defined]
            page.status == WikiPage.Status.COMMUNITY and page.needs_review_since is None
        )
        return "hub/partials/_wiki_tab_row_oob.html", {"page": page}
    return "hub/partials/_wiki_verify_oob.html", {
        "page": page,
        "can_verify": True,
        "verify_note_form": WikiVerifyNoteForm(),
    }


@login_required
@wiki_feature_required
def hub_wiki_review(request: HttpRequest) -> HttpResponse:
    """``/wiki/review/`` — what is waiting on this moderator, oldest first.

    Two lists on the open view (reported pages, and safety pages waiting on a second read)
    and a third on ``?archived=1``, which exists because Restore used to live only on the
    tombstone — reachable only by somebody who already knew the slug, so an over-archive,
    the exact failure that ends a contributor's participation, was unfixable in practice.

    Only ONE list is ever paginated: ``table_pagination.html`` hard-codes ``?page=`` in
    every link it builds, so two paginators on one screen would drive each other.
    """
    guilds, space_wide = moderatable_wiki_scopes(request)
    if not guilds and not space_wide:
        return _forbidden()
    guild_ids = [guild.pk for guild in guilds]
    archived_view = request.GET.get("archived") == "1"

    context = _get_hub_context(request)
    context.update(
        {
            "archived_view": archived_view,
            "scope_line": _queue_scope_line(guilds, space_wide),
            "is_admin_scope": space_wide,
            "open_url": reverse("hub_wiki_review"),
            "archived_url": f"{reverse('hub_wiki_review')}?archived=1",
        }
    )
    if archived_view:
        archived = _scoped_pages(guild_ids, space_wide=space_wide).filter(archived_at__isnull=False)
        context["archived_page"] = Paginator(
            archived.select_related("guild", "archived_by").order_by("-archived_at"), _REVIEW_PAGE_SIZE
        ).get_page(request.GET.get("page"))
        context["base_params"] = "archived=1"
        return render(request, "hub/wiki_review.html", context)

    reports = (
        WikiReport.objects.open()
        .for_scopes(guild_ids, include_space_wide=space_wide)
        .select_related("page", "page__guild", "reporter")
    )
    proposals = (
        _scoped_pages(guild_ids, space_wide=space_wide)
        .filter(archived_at__isnull=True, is_published=False, status=WikiPage.Status.OFFICIAL)
        .select_related("guild", "created_by")
        .order_by("created_at")
    )
    context.update(
        {
            "reports_page": Paginator(reports, _REVIEW_PAGE_SIZE).get_page(request.GET.get("page")),
            "proposals": list(proposals),
            "resolve_form": WikiResolveForm(),
            "decline_form": WikiDeclineForm(),
            "publishes_as_official": space_wide,
            "base_params": "",
        }
    )
    return render(request, "hub/wiki_review.html", context)


def _scoped_pages(guild_ids: list[int], *, space_wide: bool) -> Any:
    """Wiki pages inside one moderator's scopes, as a filter and not a per-row check."""
    from django.db.models import Q

    condition = Q(guild_id__in=guild_ids)
    if space_wide:
        condition |= Q(guild__isnull=True)
    return WikiPage.objects.filter(condition)


# --- Moderation: the official note (spec D §6.6) --------------------------------------


def _note_response(request: HttpRequest, page: WikiPage) -> HttpResponse:
    """The note modal's body reset, plus an OOB swap of the rendered note region."""
    context = _get_hub_context(request)
    context.update(
        {
            "page": page,
            "can_moderate": True,
            "note_form": WikiOfficialNoteForm(initial={"note": page.official_note}),
            "oob": True,
        }
    )
    return render(request, "hub/partials/_wiki_official_note_result.html", context)


@login_required
@wiki_feature_required
def hub_wiki_wanted(request: HttpRequest) -> HttpResponse:
    """``/wiki/wanted/`` — the list every member can work, and the editor leads curate.

    One page, two roles. GET is any member (they can Claim or just Start one); the POST is
    the ``extra=0`` formset save and takes the scope's own authority. An invalid save
    re-renders bound in place rather than redirecting, so nobody loses what they typed.
    """
    guild, known = _wanted_scope(request)
    if not known:
        return _not_found(request)
    can_manage = _can_manage_wanted_scope(request, guild)
    formset = None

    if request.method == "POST":
        if not can_manage:
            return _forbidden()
        formset = build_wiki_wanted_formset(data=request.POST, guild=guild)
        if formset.is_valid():
            for row in formset.save(commit=False):
                row.guild = guild
                if row.created_by_id is None:
                    row.created_by = _active_member(request)
                row.save()
            for row in formset.deleted_objects:
                row.delete()
            messages.success(request, "Wanted pages saved.")
            query = urlencode({"guild": guild.slug}) if guild is not None else ""
            return redirect(f"{reverse('hub_wiki_wanted')}{'?' + query if query else ''}")
        messages.error(request, "Couldn't save — check the highlighted fields.")
    elif can_manage:
        formset = build_wiki_wanted_formset(guild=guild)

    open_rows = WikiWantedPage.objects.for_guild(guild).open().with_people()
    paginator = Paginator(open_rows, _WANTED_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    scopes, _may_space_wide = editable_meeting_scopes(request)
    open_page = list(page_obj.object_list)
    done_rows = list(WikiWantedPage.objects.for_guild(guild).done().with_people()[:_WANTED_PAGE_SIZE])
    # Only the open rows carry a Mark As Written modal, so an empty list must not pay for
    # every published page in the guild. At launch the wiki ships empty and that is the
    # common case on every lead's guild page.
    fulfil_pages = _fulfilable_pages(request, guild) if (can_manage and open_page) else []

    context = _get_hub_context(request)
    context.update(
        {
            "wanted_guild": guild,
            "wanted_scope_label": guild.name if guild is not None else "Space-wide",
            "wanted_rows": [
                _wanted_row_context(request, row, can_manage=can_manage, fulfil_pages=fulfil_pages) for row in open_page
            ],
            "wanted_done": [
                _wanted_row_context(request, row, can_manage=can_manage, fulfil_pages=fulfil_pages) for row in done_rows
            ],
            "wanted_formset": formset,
            "can_manage_wanted": can_manage,
            "page": page_obj,
            "base_params": urlencode({"guild": guild.slug}) if guild is not None else "",
            "switcher_guilds": scopes,
        }
    )
    return render(request, "hub/wiki_wanted.html", context)


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_official_note(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/note/`` — write the locked staff callout above the member content."""
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_moderate_wiki_page(request, page):
        return _forbidden()
    form = WikiOfficialNoteForm(request.POST)
    if not form.is_valid():
        context = _get_hub_context(request)
        context.update({"page": page, "note_form": form})
        return render(request, "hub/partials/_wiki_official_note_form.html", context)
    page.set_official_note(text=form.cleaned_data["note"], by=member)
    response = _note_response(request, page)
    trigger_toast(response, "Official note saved.", "success")
    trigger_client_event(response, "close-modal", "wiki-official-note")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_official_note_remove(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/note/remove/`` — take the note off. The page's text is untouched."""
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_moderate_wiki_page(request, page):
        return _forbidden()
    try:
        page.clear_official_note(by=member)
    except WikiError as exc:
        message, tone = str(exc), "info"
    else:
        message, tone = "Official note removed.", "success"
    context = _get_hub_context(request)
    context.update({"page": page, "can_moderate": True})
    response = render(request, "hub/partials/_wiki_official_note.html", context)
    trigger_toast(response, message, tone)
    return response


# --- Moderation: archive, tombstone, restore (spec D §6.7) ----------------------------


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_archive(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/archive/`` — remove the page, keep the URL, tell the author why."""
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_moderate_wiki_page(request, page):
        return _forbidden()
    form = WikiArchiveForm(request.POST)
    if not form.is_valid():
        # A blank reason still POSTs with JavaScript off; the flag on the confirm modal is
        # a courtesy and this is the gate.
        messages.error(request, "Add a reason. The author will read it.")
        return redirect(page.get_absolute_url())
    try:
        page.archive(by=member, reason=form.cleaned_data["reason"])
    except (AlreadyArchived, WikiError) as exc:
        messages.error(request, str(exc))
        return redirect(page.get_absolute_url())
    author = page.created_by
    messages.success(
        request,
        f"Page archived. {author.display_name} has been emailed." if author is not None else "Page archived.",
    )
    return redirect(page.get_absolute_url())


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_wanted_claim(request: HttpRequest, pk: int) -> HttpResponse:
    """``/wiki/wanted/<pk>/claim/`` — put your name on a row, or take it back off.

    ``release=1`` hands the row back. The claimer needs no ceremony; a lead releasing
    somebody ELSE's stale claim comes through the confirm modal, because
    ``is_claim_stale`` without a lever was a label that described a problem nobody could
    fix.
    """
    row = WikiWantedPage.objects.filter(pk=pk).select_related("guild", "claimed_by").first()
    if row is None:
        raise Http404("No such request.")
    member = _active_member(request)
    if member is None:
        return _forbidden()
    releasing = request.POST.get("release") == "1"
    if releasing and row.claimed_by_id != member.pk and not _can_manage_wanted_scope(request, row.guild):
        return _forbidden()
    try:
        if releasing:
            row.release()
        else:
            row.claim(member)
    except ValueError as exc:
        response = HttpResponse(str(exc), status=400)
        trigger_toast(response, str(exc), "error")
        return response
    row.refresh_from_db()
    response = render(
        request,
        "hub/partials/_wiki_wanted_row.html",
        {"wanted": _wanted_row_context(request, row)},
    )
    trigger_toast(response, "Released. It is back on the list." if releasing else "Claimed. It's yours.")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_wanted_fulfil(request: HttpRequest, pk: int) -> HttpResponse:
    """``/wiki/wanted/<pk>/fulfil/`` — Mark As Written, the caller ``fulfil()`` never had.

    Spec A's ``?wanted=<pk>`` create path only closes a row for a writer who started from
    that row's own button. Every other route to the page — written from ``/wiki/new/``,
    written from a search result, or already existing — left the row open forever, which
    made the "Already Written" section permanently empty and left Delete, which throws the
    credit away, as a lead's only control.
    """
    row = WikiWantedPage.objects.filter(pk=pk).select_related("guild").first()
    if row is None:
        raise Http404("No such request.")
    if _active_member(request) is None or not _can_manage_wanted_scope(request, row.guild):
        return _forbidden()
    if request.POST.get("reopen") == "1":
        # The other direction of the same transition on the same object. Without it, the
        # only way back from a wrong close is the editor's Delete, which throws away the
        # ask, the note and the count of how many people asked for it.
        try:
            row.reopen()
        except ValueError as exc:
            # An already-open row, or a duplicate ask filed while this one sat closed --
            # the latter would otherwise surface as an uncaught IntegrityError against the
            # partial unique index the reopen puts this row back into.
            response = HttpResponse(str(exc), status=400)
            trigger_toast(response, str(exc), "error")
            return response
        row.refresh_from_db()
        response = render(
            request,
            "hub/partials/_wiki_wanted_row.html",
            {"wanted": _wanted_row_context(request, row)},
        )
        trigger_toast(response, "Back on the list.")
        return response
    form = WikiWantedFulfilForm(request.POST, pages=_fulfilable_pages(request, row.guild), prefix=f"fulfil{row.pk}")
    if not form.is_valid():
        message = str(next(iter(form.errors["page"])))
        response = HttpResponse(message, status=400)
        trigger_toast(response, message, "error")
        return response
    # No WikiError guard here: fulfil() raises only on an archived page, and the form's
    # candidate list is rebuilt from _fulfilable_pages on every POST, which excludes them.
    closed = row.fulfil(form.cleaned_data["page"])
    if not closed:
        message = "That request was already closed."
        response = HttpResponse(message, status=400)
        trigger_toast(response, message, "error")
        return response
    row.refresh_from_db()
    response = render(
        request,
        "hub/partials/_wiki_wanted_row.html",
        {"wanted": _wanted_row_context(request, row)},
    )
    trigger_toast(response, "Marked as written. Nice.")
    trigger_client_event(response, "close-modal", f"wiki-wanted-fulfil-{row.pk}")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_restore(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/restore/`` — put an archived page back for every member."""
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_moderate_wiki_page(request, page):
        return _forbidden()
    try:
        page.restore(by=member)
    except WikiError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Page restored.")
    return redirect(page.get_absolute_url())


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_set_redirect(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/redirect/`` — point a tombstone's readers at a live replacement."""
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_moderate_wiki_page(request, page):
        return _forbidden()
    # The form's own queryset is the rule — live pages in this page's scope, never this
    # page and never another tombstone — so a crafted pk fails validation here rather than
    # reaching the model. ``set_archive_redirect`` re-checks it for programmatic callers.
    form = WikiRedirectForm(request.POST, page=page)
    if not form.is_valid():
        messages.error(request, "Pick a live page in the same scope.")
        return redirect(page.get_absolute_url())
    page.set_archive_redirect(target=form.cleaned_data["target"])
    messages.success(request, "Readers will be pointed there.")
    return redirect(page.get_absolute_url())


# --- Moderation: history and revert (spec D §6.8) -------------------------------------


_HISTORY_PAGE_SIZE = 50


@login_required
@wiki_feature_required
def hub_wiki_history(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/history/`` — every saved version, and a staff-only Revert.

    Any member may read it: seeing that a page has been worked on is part of trusting it.
    ``?revision=<pk>`` renders one old version read-only, which answers "what did it used
    to say" at a hundredth of the cost of a word-level diff viewer — including for an
    unmerged conflict draft, without which the conflict screen's promise that "your
    version stays in this page's history" would be true and unreachable.
    """
    page = _page_or_none(slug)
    if page is None or _hidden_from(request, page):
        return _not_found(request)
    can_moderate = can_moderate_wiki_page(request, page)
    context = _get_hub_context(request)
    context.update({"page": page, "can_moderate": can_moderate, "can_edit": can_edit_wiki_page(request, page)})

    requested = request.GET.get("revision", "")
    if requested.isdigit():
        revision = page.revisions.select_related("author").filter(pk=int(requested)).first()
        if revision is None:
            return _not_found(request)
        context.update(
            {
                "revision": revision,
                "is_draft": revision.kind == WikiRevision.Kind.CONFLICT_DRAFT,
                "can_use_draft": can_edit_wiki_page(request, page),
            }
        )
        return render(request, "hub/wiki_revision.html", context)

    revisions = page.revisions.select_related("author")
    revisions_page = Paginator(revisions, _HISTORY_PAGE_SIZE).get_page(request.GET.get("page"))
    # create_page writes version one with the page's OWN title, body and facts, so every
    # seeded Equipment stub and every brand-new page has a row whose Revert could only
    # ever answer "That is already the current version." Those stubs are the launch
    # content, so the button is not rendered on a row that already equals the page.
    facts = page.fact_snapshot()
    for revision in revisions_page:
        revision.is_revertible = not revision.matches(title=page.title, body=page.body, facts=facts)
    context["revisions_page"] = revisions_page
    return render(request, "hub/wiki_history.html", context)


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_revert(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """``/wiki/p/<slug>/revert/<pk>/`` — put an older version back, on top, staff only.

    Gated on ``can_edit_wiki_page`` AS WELL AS ``can_moderate_wiki_page``, because a
    revert rewrites the page's title, body and facts wholesale — it is an edit, and the
    edit gate is the one that keeps Official content away from a guild's staff. Without
    it a guild treasurer refused the Edit button on a safety policy could open History and
    rewrite the whole thing with the Official chip still on it, no officer involved.
    ``revert_to`` deliberately does not touch ``status``, so nothing downstream catches it.
    """
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not (can_moderate_wiki_page(request, page) and can_edit_wiki_page(request, page)):
        return _forbidden()
    revision = page.revisions.filter(pk=pk).first()
    if revision is None:
        raise Http404("No such version.")
    try:
        page.revert_to(revision=revision, by=member)
    except NothingToRevert as exc:
        messages.error(request, str(exc))
    except WikiError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Reverted to the {timezone.localtime(revision.created_at):%-d %B} version.")
    return redirect(page.get_absolute_url())


# --- Moderation: the conflict save (spec D §6.10) -------------------------------------


def _conflict_or_none(page: WikiPage, pk: int) -> WikiRevision | None:
    """The parked draft revision for this page, or None."""
    return page.revisions.select_related("author").filter(pk=pk, kind=WikiRevision.Kind.CONFLICT_DRAFT).first()


@login_required
@wiki_feature_required
def hub_wiki_conflict(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """``/wiki/p/<slug>/conflict/<pk>/`` — pick what should be on the page. Nothing is lost.

    The right card renders ``page.body``, the live page, and never
    ``page.revisions.first()``: spec A stores the PRE-edit snapshot, so the newest revision
    row is the version before the other person's save — the exact one this screen exists to
    reconcile away from. Only the name and the time come from the revision row.
    """
    page = _page_or_none(slug)
    if page is None or _hidden_from(request, page):
        return _not_found(request)
    member = _editing_member(request)
    draft = _conflict_or_none(page, pk)
    if draft is None:
        raise Http404("No such conflict.")
    mine = member is not None and draft.author_id == member.pk
    if not mine and not can_moderate_wiki_page(request, page):
        return _forbidden()
    theirs = page.revisions.select_related("author").exclude(kind=WikiRevision.Kind.CONFLICT_DRAFT).first()
    context = _get_hub_context(request)
    context.update(
        {
            "page": page,
            "draft": draft,
            "theirs": theirs,
            "already_applied": page.body == draft.body and page.title == draft.title,
            "merge_url": f"{reverse('hub_wiki_edit', args=[page.slug])}?merge={draft.pk}",
        }
    )
    return render(request, "hub/wiki_conflict.html", context)


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_conflict_keep(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """``/wiki/p/<slug>/conflict/<pk>/keep/`` — apply a parked draft as an ordinary edit.

    There is no ``apply_conflict_draft``: promoting a draft is an ordinary save whose text
    happens to come from a stored row, and routing it through the one save method keeps the
    verified-drop rule, the search-text rebuild and the activity row identical to every
    other save. The ``CONFLICT_DRAFT`` row stays in place forever as the record.
    """
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_edit_wiki_page(request, page):
        return _forbidden()
    draft = _conflict_or_none(page, pk)
    if draft is None:
        raise Http404("No such conflict.")
    if draft.author_id != member.pk and not can_moderate_wiki_page(request, page):
        return _forbidden()
    try:
        page.apply_edit(
            editor=member,
            editor_may_verify=can_verify_wiki_page(request, page),
            title=draft.title,
            body=draft.body,
            note="Resolved an edit conflict",
        )
    except WikiError as exc:
        messages.error(request, str(exc))
        return redirect(page.get_absolute_url())
    # apply_edit owns title and body; the facts come from the draft's own snapshot, which
    # is why A snapshots them at all — restoring the prose and not the Quick Answers leaves
    # a page whose summary contradicts its text.
    # Atomic for the same reason revert_to's identical delete-then-create is: a failure
    # between the two would leave the winner's prose with no Quick Answers at all,
    # permanently, and this project sets no ATOMIC_REQUESTS.
    with transaction.atomic():
        page.restore_facts(draft.facts)
        page.rebuild_search_text()
        page.save(update_fields=["search_text"])
    WikiEditLock.release(page, member)
    messages.success(request, "Your version is on the page. The other one is still in the history.")
    return redirect(page.get_absolute_url())


# --- Moderation: the safety gate (spec D §6.11) ---------------------------------------


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_publish_proposal(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/publish/`` — publish a held safety page at YOUR OWN authority.

    An admin publishing lands it Official; a guild lead or staff lands it Guild verified.
    One button, an honest outcome, and no escalation ladder to build.
    """
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_moderate_wiki_page(request, page):
        return _forbidden()
    as_official = is_effective_staff(request)
    try:
        page.publish_proposal(by=member, as_official=as_official)
    except WikiError as exc:
        message, tone = str(exc), "info"
    else:
        message = "Published as an Official page." if as_official else "Published as Guild verified."
        tone = "success"
    response = render(request, "hub/partials/_wiki_review_card_done.html", {"line": message})
    trigger_toast(response, message, tone)
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_decline_proposal(request: HttpRequest, slug: str) -> HttpResponse:
    """``/wiki/p/<slug>/decline/`` — send a safety proposal back with something to act on.

    Not a shrug: the reviewer's words land on the page's own history AND in the author's
    inbox, the page stays a draft, nothing is deleted, and the author may edit their own
    unpublished proposal to answer it.
    """
    page = _page_or_none(slug)
    if page is None:
        return _not_found(request)
    member = _editing_member(request)
    if member is None or not can_moderate_wiki_page(request, page):
        return _forbidden()
    form = WikiDeclineForm(request.POST)
    if not form.is_valid():
        context = _get_hub_context(request)
        context.update({"proposal": page, "decline_form": form})
        return render(request, "hub/partials/_wiki_decline_form.html", context)
    try:
        page.decline_proposal(by=member, note=form.cleaned_data["note"])
    except WikiError as exc:
        message, tone = str(exc), "info"
    else:
        message, tone = "Sent back to the author.", "success"
    response = render(
        request,
        "hub/partials/_wiki_decline_result.html",
        {"line": message, "proposal": page},
    )
    trigger_toast(response, message, tone)
    trigger_client_event(response, "close-modal", f"wiki-decline-{page.pk}")
    return response


@login_required
@wiki_feature_required
@require_POST
def hub_wiki_wanted_request(request: HttpRequest) -> HttpResponse:
    """``/wiki/wanted/request/`` — "Request this page" and a lead's "Add To Wanted".

    ``bump=0`` is the lead path: ``request_count`` counts PEOPLE WHO ASKED, and a lead
    filing a row off the failed-search panel is not a fourth person asking. Gated on the
    scope's authority for exactly that reason — the flag suppresses the count, so a member
    must not be able to pass it.
    """
    member = _active_member(request)
    if member is None:
        return _forbidden()
    title = request.POST.get("title", "").strip()
    if not (MISS_MIN_QUERY_LENGTH <= len(title) <= 200):
        message = "Give the page a name of at least three characters."
        response = HttpResponse(message, status=400)
        trigger_toast(response, message, "error")
        return response
    slug = request.POST.get("guild", "").strip()
    guild = Guild.objects.filter(slug=slug).first() if slug else None
    if slug and guild is None:
        raise Http404("No such guild.")
    bump = request.POST.get("bump") != "0"
    # Asked ONCE, and used both as the gate and as what the swapped-back rows render. The
    # earlier version gated only the bump=0 path and then hardcoded can_manage=True on the
    # fragment, so a plain member POSTing surface=panel with no bump got Mark As Written,
    # the fulfil picker for every open row, and Release modals swapped into their own tab.
    can_manage = _can_manage_wanted_scope(request, guild)
    if not bump and not can_manage:
        return _forbidden()

    try:
        row, created = WikiWantedPage.objects.request(title=title, guild=guild, member=member, bump=bump)
    except WikiError as exc:
        response = HttpResponse(str(exc), status=429)
        trigger_toast(response, str(exc), "error")
        return response
    if not bump:
        # The lead's own "Add To Wanted": they ARE the guild's leads, so telling them the
        # leads will see it is noise, and they were never a person asking.
        message = "Added to Wanted pages."
    elif created:
        message = "Added to Wanted pages. Your guild's leads will see it."
    else:
        message = f"Already on the list — you're the {_ordinal(row.request_count)} person to ask."

    surface = request.POST.get("surface", "")
    template = _WANTED_REQUEST_TEMPLATES[surface if surface in _WANTED_REQUEST_TEMPLATES else "empty"]
    context: dict[str, Any] = {"row": row, "guild": guild, "wanted_url": _wanted_url_for(guild)}
    if surface == "panel":
        rows = list(WikiWantedPage.objects.for_guild(guild).open().with_people()[:WANTED_CARD_LIMIT])
        fulfil_pages = _fulfilable_pages(request, guild) if (can_manage and rows) else []
        context["wanted_rows"] = [
            _wanted_row_context(request, item, can_manage=can_manage, fulfil_pages=fulfil_pages) for item in rows
        ]
    response = render(request, template, context)
    trigger_toast(response, message)
    return response


def _wanted_url_for(guild: Guild | None) -> str:
    """``/wiki/wanted/`` scoped to a guild, or the space-wide list."""
    base = reverse("hub_wiki_wanted")
    return f"{base}?{urlencode({'guild': guild.slug})}" if guild is not None else base


# Which fragment a "Request this page" POST swaps back. Both carry hx-swap-oob and a toast;
# neither is a 204, because in both places something on screen has to stop lying.
_WANTED_REQUEST_TEMPLATES: dict[str, str] = {
    "panel": "hub/partials/_wiki_miss_row_swap.html",
    "empty": "hub/partials/_wiki_request_done.html",
}


def guild_wiki_tab_block(request: HttpRequest, guild: Guild) -> dict[str, Any]:
    """The guild page's whole Wiki-tab context: the data, plus the wanted rows' controls.

    ``guild_wiki_tab_context`` assembles the data in ``membership`` where it belongs; the
    wanted rows pick up their per-row controls here, because those need a form and a form
    is a view-layer object. ``guild_detail`` calls this inside its ``wiki_tab_enabled``
    guard and nowhere else, so a wiki that is off or raising cannot take a guild page down.
    """
    context = guild_wiki_tab_context(request, guild)
    can_manage = context["wiki_tab_can_verify"]
    # Skipped entirely when there is nothing to close, which at launch is every guild.
    fulfil_pages = _fulfilable_pages(request, guild) if (can_manage and context["wiki_tab_wanted"]) else []
    context["wiki_tab_wanted"] = [
        _wanted_row_context(request, row, can_manage=can_manage, fulfil_pages=fulfil_pages)
        for row in context["wiki_tab_wanted"]
    ]
    return context
