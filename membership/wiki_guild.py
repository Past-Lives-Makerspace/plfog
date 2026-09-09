"""The guild Wiki tab's payload, and the monthly lead digest's (spec B).

Everything here is a *filtered view of the one global* :class:`~membership.models.WikiPage`
store. There is no guild-local wiki, no per-guild namespace, and no page that lives "in" a
tab: every row links to ``/wiki/p/<slug>/``, and a page whose scope changes simply stops
appearing here.

Kept out of ``hub/views.py`` so ``guild_detail`` stays thin, and out of ``models.py``
because this is cross-model assembly rather than one object's business logic.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from membership.models import (
    MISS_RETENTION_DAYS,
    WikiPage,
    WikiSearchMiss,
    WikiWantedPage,
)

# The one absolute-URL helper every other caller in this repo lazily imports. There is no
# shared "spine resolver"; this private four-line function exists twice (orientations and
# equipment) and a dozen call sites import the orientations one. Do not write a third copy
# and do not tidy the duplication here.
from membership.orientations import _absolute_url
from membership.permissions import can_edit_guild, visible_wiki_pages

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Guild

# The grouped list's fixed order and its member-facing headings. Machines first because
# that is what somebody standing in the shop opened the tab for.
GROUP_ORDER: tuple[tuple[str, str], ...] = (
    (WikiPage.Kind.MACHINE, "Machines"),
    (WikiPage.Kind.HOWTO, "How-To"),
    (WikiPage.Kind.MATERIAL, "Materials"),
    (WikiPage.Kind.GUILD_INFO, "Guild Info"),
    (WikiPage.Kind.REFERENCE, "Reference"),
    (WikiPage.Kind.PROJECT, "Projects"),
)

# A group longer than this ends with a "See all N" link into the scoped search: the tab is
# a directory, not an index.
GROUP_ROW_LIMIT = 12
RECENT_LIMIT = 5
PANEL_ROW_LIMIT = 8
MISS_PANEL_LIMIT = 5
MISS_DIGEST_LIMIT = 5
WANTED_CARD_LIMIT = 8
DIGEST_SECTION_LIMIT = 10

# The failed-search panel's window is a ROLLING 30 days, not the calendar month. "This
# month" would show a false all-clear on the 1st — the exact day the digest lands and the
# lead most likely to open the panel is looking at it. The digest keeps the calendar month,
# because a monthly report of a month is what it is.
MISS_PANEL_DAYS = 30


def _search_url(**params: str) -> str:
    """A ``/wiki/search/`` link carrying only the parameters that are set."""
    query = urlencode({key: value for key, value in params.items() if value})
    base = reverse("hub_wiki_search")
    return f"{base}?{query}" if query else base


def guild_wiki_tab_context(request: HttpRequest, guild: Guild) -> dict[str, Any]:
    """Everything ``_guild_wiki_tab.html`` renders, in a constant number of queries.

    One page query feeds the grouped list, Recently Updated and the Overdue panel; the
    lead-only panels add the wanted rows and the failed searches. Nothing here is
    per-row, which is the whole N+1 guard: a guild with 12 pages and a guild with 72 cost
    the same.

    Called from ``guild_detail`` **only inside the ``wiki_tab_enabled`` guard**, so a wiki
    that is off, half-deployed, or raising can never take the guild page down with it.

    Args:
        request: The viewing request; carries the ``view_as`` preview the filters honor.
        guild: The guild whose tab this is.

    Returns:
        The template context block, ready to merge into ``guild_detail``'s dict.
    """
    # can_edit_guild is now the WHOLE of can_verify_wiki_page's guild-scoped leg, asked
    # once for the tab instead of once per row, so the two can no longer disagree about who
    # may verify. The per-row flag below re-adds the guards that are about the page rather
    # than the person: Official is never verifiable, and neither is a page carrying an open
    # report. This used to miss the Equipment-orienter leg; brief section 9.1 defers that
    # case and can_verify_wiki_page no longer admits it either.
    can_verify = can_edit_guild(request, guild)

    # not_archived() on purpose: visible_for() hands effective staff EVERYTHING, archived
    # rows included, because A's tombstone needs to reach them by URL. A directory is a
    # different question — an archived page leaves the lists.
    pages = list(
        visible_wiki_pages(request)
        .for_guild(guild)
        .not_archived()
        .select_related("guild", "updated_by", "verified_by")
        .order_by("title")
    )
    for page in pages:
        # Attached to the object rather than passed alongside it, so A's shared card partial
        # can hand it to the actions slot without B forking a second card shape. The compact
        # control is one-tap Verify and nothing else, so it shows only on a page that is
        # still Community: Official is never verifiable and re-verify / remove live on the
        # page itself, where there is room to explain them.
        #
        # And never on a page carrying an open report. verify() clears the denormalized
        # needs-review pair, so a one-tap Verify sitting beside an amber "Needs review"
        # pill would let a lead make the banner disappear from a list, without ever opening
        # the page or reading what somebody said was wrong. A reported page is spec D's
        # surface: the lead follows the title, reads the report, and verifies from there.
        page.tab_show_verify = (  # type: ignore[attr-defined]
            can_verify and page.status == WikiPage.Status.COMMUNITY and page.needs_review_since is None
        )

    groups: list[dict[str, Any]] = []
    for kind, label in GROUP_ORDER:
        rows = [page for page in pages if page.kind == kind]
        if not rows:
            continue  # an empty group is omitted entirely — no "0 pages" headings
        groups.append(
            {
                "kind": kind,
                "label": label,
                "rows": rows[:GROUP_ROW_LIMIT],
                "total": len(rows),
                "has_more": len(rows) > GROUP_ROW_LIMIT,
                "see_all_url": _search_url(guild=guild.slug, kind=kind),
            }
        )

    recent = sorted(pages, key=lambda page: page.updated_at, reverse=True)[:RECENT_LIMIT]

    context: dict[str, Any] = {
        "wiki_tab_guild": guild,
        "wiki_tab_groups": groups,
        "wiki_tab_recent": recent,
        "wiki_tab_can_verify": can_verify,
        # Drives .pl-wp-tab__grid--lead, which reorders the two columns at <=900px so a
        # lead's to-do list is not underneath as many as 72 page rows. The ordering lives
        # in that CSS class and never in an inline style on the x-show element (Rule 12).
        "wiki_tab_lead_panels_first": can_verify,
        "wiki_tab_search_url": reverse("hub_wiki_search"),
        "wiki_tab_new_url": f"{reverse('hub_wiki_new')}?{urlencode({'guild': guild.slug})}",
        "wiki_tab_wanted_url": f"{reverse('hub_wiki_wanted')}?{urlencode({'guild': guild.slug})}",
        "wiki_tab_has_pages": bool(pages),
    }

    wanted_rows = list(WikiWantedPage.objects.for_guild(guild).open().with_people()[: WANTED_CARD_LIMIT + 1])
    context.update(
        {
            "wiki_tab_wanted": wanted_rows[:WANTED_CARD_LIMIT],
            "wiki_tab_wanted_has_more": len(wanted_rows) > WANTED_CARD_LIMIT,
        }
    )

    if not can_verify:
        # A work queue shown to people who cannot work it is noise. Members still see the
        # Out of date chip on the page itself, and they still see the wanted list.
        return context

    overdue = sorted(
        (page for page in pages if page.is_out_of_date and page.needs_review_since is None),
        key=lambda page: page.freshness_at,
    )
    misses = WikiSearchMiss.objects.top_for_guild(guild, since=timezone.now() - timedelta(days=MISS_PANEL_DAYS))
    open_titles = WikiWantedPage.objects.open_titles_for_guild(guild)
    for miss in misses[:MISS_PANEL_LIMIT]:
        # An actioned row that still shows a live "Add To Wanted" button is a control that
        # lies: a second tap re-posts, and the panel's headline number is supposed to count
        # people who asked. Annotated here so the check costs no extra query.
        miss["on_wanted"] = miss["query_normalized"] in open_titles

    context.update(
        {
            "wiki_tab_overdue": overdue[:PANEL_ROW_LIMIT],
            "wiki_tab_overdue_total": len(overdue),
            "wiki_tab_overdue_has_more": len(overdue) > PANEL_ROW_LIMIT,
            "wiki_tab_overdue_url": _search_url(guild=guild.slug, stale="1"),
            "wiki_tab_misses": misses[:MISS_PANEL_LIMIT],
        }
    )
    return context


# --- The monthly digest ----------------------------------------------------------------


def previous_month_window(today: date | None = None) -> tuple[datetime, datetime]:
    """``(start, end)`` of last calendar month as aware datetimes in the project timezone.

    Computed from ``timezone.localdate()`` and not UTC: the job fires at 13:00 UTC, which
    is 06:00 in Portland, so "last month" has to mean last month *here*. A run at 23:30 PT
    on the last day of a month must not slip a day forward into the next one.
    """
    from dateutil.relativedelta import relativedelta

    local_today = today if today is not None else timezone.localdate()
    end_date = local_today.replace(day=1)
    start_date = end_date - relativedelta(months=1)
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(end_date, datetime.min.time()), tz)
    return start, end


def guild_digest_payload(guild: Guild, *, month_start: datetime, month_end: datetime) -> dict[str, Any] | None:
    """The one guild's digest content, or ``None`` when there is nothing to say.

    ``None`` means **do not send**. A guild with a quiet month gets no email at all rather
    than an email of three empty sections, which is the difference between a digest people
    open and a digest people filter.

    Args:
        guild: The guild whose leadership receives it.
        month_start: Inclusive start of the reporting window, project timezone.
        month_end: Exclusive end of the same window.

    Returns:
        The template context, or None when all three sections are empty.
    """
    live = WikiPage.objects.published().not_archived().for_guild(guild)
    new_pages = list(
        live.filter(created_at__gte=month_start, created_at__lt=month_end)
        .select_related("created_by")
        .order_by("-created_at")[:DIGEST_SECTION_LIMIT]
    )
    # needs_review() is a SUPERSET of "overdue": it ORs the per-kind interval cutoffs with
    # spec D's reported-page state. A reported page in a list whose only verb is "still
    # accurate" is the wrong answer to "somebody says this is wrong", and duplicates D's
    # own queue, so it is filtered back out here and on the panel alike.
    overdue_pages = list(
        live.needs_review()
        .filter(needs_review_since__isnull=True)
        .select_related("last_checked_by")
        # The freshness clock spelled out rather than ordering on needs_review()'s own
        # ``_freshness_at`` alias, which is invisible to the type checker.
        .order_by(Coalesce("last_checked_at", "verified_at", "created_at").asc())[:DIGEST_SECTION_LIMIT]
    )
    misses = WikiSearchMiss.objects.top_for_guild(guild, since=month_start, until=month_end, limit=MISS_DIGEST_LIMIT)
    if not (new_pages or overdue_pages or misses):
        return None

    guild_url = _absolute_url(reverse("hub_guild_detail", args=[guild.slug]))
    tab_url = f"{guild_url}?{urlencode({'tab': 'wiki'})}"
    new_url = _absolute_url(f"{reverse('hub_wiki_new')}?{urlencode({'guild': guild.slug})}")
    wanted_url = _absolute_url(f"{reverse('hub_wiki_wanted')}?{urlencode({'guild': guild.slug})}")
    return {
        "guild": guild,
        "guild_url": guild_url,
        "tab_url": tab_url,
        "new_url": new_url,
        "wanted_url": wanted_url,
        "wanted_open_count": WikiWantedPage.objects.for_guild(guild).open().count(),
        "new_pages": [
            {
                "title": page.title,
                "url": _absolute_url(page.get_absolute_url()),
                "author": page.created_by.display_name if page.created_by is not None else "",
                "kind": page.get_kind_display(),
            }
            for page in new_pages
        ],
        "overdue": [
            {
                "title": page.title,
                # Lands on the page with "Still accurate" cued, rather than a mutating GET
                # in an email: a lead reading a monthly digest is already signed in, and a
                # one-click GET is a mail-client-prefetch hazard. One extra tap buys
                # correctness.
                "url": _absolute_url(f"{page.get_absolute_url()}?{urlencode({'confirm': '1'})}"),
                "last_checked": timezone.localtime(page.last_checked_at).strftime("%-d %b %Y")
                if page.last_checked_at is not None
                else "",
                "kind": page.get_kind_display(),
            }
            for page in overdue_pages
        ],
        "misses": [
            {
                "query": miss["query"],
                "people": miss["people"],
                "start_url": _absolute_url(
                    f"{reverse('hub_wiki_new')}?{urlencode({'guild': guild.slug, 'title': miss['query']})}"
                ),
            }
            for miss in misses
        ],
    }


def _plural(count: int, singular: str, plural: str) -> str:
    """``"1 page"`` / ``"3 pages"`` — written out, because "1 pages" is what leads notice."""
    return f"{count} {singular if count == 1 else plural}"


def digest_subject(guild: Guild, payload: dict[str, Any]) -> str:
    """The subject: the guild plus the single largest actionable count.

    Precedence is overdue, then failed searches, then new pages — most actionable first.
    There is no all-empty case, because :func:`guild_digest_payload` returns None and
    nothing is sent. No time of day appears, so subject and body cannot disagree about a
    timezone.
    """
    if payload["overdue"]:
        return f"{guild.name} wiki: {_plural(len(payload['overdue']), 'page needs', 'pages need')} a look"
    if payload["misses"]:
        return f"{guild.name} wiki: {_plural(len(payload['misses']), 'search', 'searches')} found nothing"
    return f"{guild.name} wiki: {_plural(len(payload['new_pages']), 'new page', 'new pages')}"


def digest_in_app_body(payload: dict[str, Any]) -> str:
    """The bell row's one plain sentence, mirroring the subject's precedence.

    A section with a zero count is omitted from the sentence rather than rendered as "0",
    and the sentence is never empty because an all-empty payload sends nothing at all.
    """
    parts: list[str] = []
    if payload["overdue"]:
        parts.append(_plural(len(payload["overdue"]), "page needs", "pages need") + " a look")
    if payload["misses"]:
        parts.append(_plural(len(payload["misses"]), "search", "searches") + " found nothing")
    if payload["new_pages"]:
        parts.append(_plural(len(payload["new_pages"]), "new page", "new pages") + " went up")
    if len(parts) == 1:
        return f"{parts[0]}."
    return f"{', '.join(parts[:-1])}, and {parts[-1]}."


def purge_old_search_misses() -> int:
    """Drop failed searches past their retention window. Runs on EVERY digest invocation.

    Riding the daily command rather than a second job row is what keeps the table bounded
    without another cron, another dashboard entry, and another thing to forget.
    """
    return WikiSearchMiss.objects.purge_before(timezone.now() - timedelta(days=MISS_RETENTION_DAYS))
