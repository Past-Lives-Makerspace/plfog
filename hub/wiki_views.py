"""Member wiki views — reading, search and browse (PR A2) and writing (PR A3).

The member-facing ``/wiki/`` surface from ``docs/superpowers/plans/2026-09-07-member-wiki-core.md``
§6: the home page, the search-and-browse screen, the reading page, the starter chooser,
the create/edit editor with its autosave and draft resume, the two micro-contribution
modals, one-tap "Still accurate", and the drafts list.

Every view is thin per CLAUDE.md: parse the request, ask a permission *filter* for what it
may show, call a model method, then toast / redirect / render. Permissions are filters and
not checks — a view that forgets to gate shows too little, never too much.

Three seams here belong to later specs in the round and are deliberately left open:
spec B's ``_wiki_search_empty.html`` (included, not written here), spec B's
``WikiWantedPage.fulfil``, and spec D's ``WikiEditLock.refresh`` — all three are lazy,
guarded imports until those specs merge.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.timesince import timesince
from django.views.decorators.http import require_POST

from core.models import SiteConfiguration
from hub.forms import (
    WikiAttachmentFormSet,
    WikiPageCreateForm,
    WikiPageForm,
    WikiQuickPhotoForm,
    WikiQuickTipForm,
    WikiVerifyNoteForm,
    WikiWantedFulfilForm,
    build_wiki_wanted_formset,
    wiki_fact_formset_class,
)
from hub.toast import trigger_client_event, trigger_toast
from hub.views import _get_hub_context
from membership.markdown import sanitize_wiki_submission
from membership.models import (
    MISS_MIN_QUERY_LENGTH,
    Guild,
    Member,
    WikiArticle,
    WikiAttachment,
    WikiDraft,
    WikiError,
    WikiPage,
    WikiPageFact,
    WikiSearchMiss,
    WikiWantedPage,
)
from membership.permissions import (
    _can_moderate_wiki_page,
    _editing_member,
    can_edit_guild,
    can_edit_wiki_page,
    can_verify_wiki_page,
    editable_meeting_scopes,
    editable_wiki_scopes,
    is_effective_staff,
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
    """Keep spec D's advisory edit lock warm from A's autosave, when D has landed.

    Every accepted autosave refreshes the caller's own lock row, which is the entire
    reason spec D needs no polling timer, no second endpoint and no JS. The guard comes
    off when D lands ``WikiEditLock``; until then A must not import a model that does not
    exist yet.
    """
    try:
        from membership.models import WikiEditLock  # type: ignore[attr-defined]
    except ImportError:
        return
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
    return render(request, "hub/wiki_page.html", context)


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
            try:
                page = WikiPage.objects.create_page(
                    title=form.cleaned_data["title"],
                    kind=form.cleaned_data["kind"],
                    author=member,
                    guild=form.cleaned_data["guild"],
                    body=form.cleaned_data["body"],
                    facts=_submitted_facts(fact_formset),
                )
            except WikiError as exc:
                form.add_error("title", str(exc))
            else:
                _save_child_formsets(page, None, attachment_formset, member)
                if draft is not None:
                    draft.delete()
                fulfilled = _fulfil_wanted_page(wanted_pk, page)
                messages.success(
                    request,
                    "Page created. That was on the Wanted list. Thanks for writing it."
                    if fulfilled
                    else "Page created. Thanks for writing it.",
                )
                return redirect(page.get_absolute_url())
    else:
        if draft_mode == "fresh" and draft is not None:
            draft.delete()
            return redirect(f"{reverse('hub_wiki_create', args=[kind])}?{_carry_params(request)}")
        if draft_mode == "use" and draft is not None:
            form = WikiPageCreateForm(
                initial={"title": draft.title, "kind": kind, "guild": draft.guild, "body": draft.body},
                scope_guilds=scope_guilds,
            )
            fact_formset = _fact_formset(page=None, prompts=starter["fact_prompts"], initial_facts=draft.facts)
        else:
            form = WikiPageCreateForm(
                initial={
                    "title": request.GET.get("title", ""),
                    "kind": kind,
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


def _edit_forms_from(
    page: WikiPage,
    draft: WikiDraft | None,
    draft_mode: str,
    can_moderate: bool,
) -> tuple[WikiPageForm, Any]:
    """The unbound edit form and fact formset, populated from the page or from the draft.

    The saved page is the default even while the resume card is on screen: a member who
    ignores the card entirely edits the live page, which is the safe outcome. Only an
    explicit ``?draft=use`` loads what they typed.
    """
    if draft_mode == "use" and draft is not None:
        return (
            WikiPageForm(page=page, can_moderate=can_moderate, initial={"title": draft.title, "body": draft.body}),
            _fact_formset(page=page, initial_facts=draft.facts),
        )
    return WikiPageForm(page=page, can_moderate=can_moderate), _fact_formset(page=page)


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
        page.apply_edit(
            editor=member,
            editor_may_verify=may_verify,
            title=form.cleaned_data["title"],
            body=form.cleaned_data["body"],
        )
    except WikiError as exc:
        form.add_error(None, str(exc))
        return None
    if can_moderate:
        page.equipment = form.cleaned_data["equipment"]
        page.save(update_fields=["equipment"])
    _save_child_formsets(page, fact_formset, attachment_formset, member)
    if draft is not None:
        draft.delete()
    messages.success(request, "Saved. Thanks for keeping it right.")
    return redirect(page.get_absolute_url())


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

    can_moderate = _can_moderate_wiki_page(request, page)
    may_verify = can_verify_wiki_page(request, page)
    draft = WikiDraft.objects.filter(page=page, author=member).first()
    draft_mode = request.GET.get("draft", "")

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
        form, fact_formset = _edit_forms_from(page, draft, draft_mode, can_moderate)
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
    context.update({"drafts": list(WikiDraft.objects.for_member(member)), "held_pages": held_pages})
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
