"""Reusable table search / sort / pagination for admin views."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import F, Q, QuerySet
from django.http import HttpRequest, QueryDict

PER_PAGE = 25


def table_search(queryset: QuerySet, q: str, search_fields: list[str]) -> QuerySet:
    """``queryset`` narrowed to rows where any of ``search_fields`` contains ``q`` (all rows when blank)."""
    if not q or not search_fields:
        return queryset
    search_q = Q()
    for field in search_fields:
        search_q |= Q(**{f"{field}__icontains": q})
    return queryset.filter(search_q)


def prepare_table(
    request: HttpRequest,
    queryset: QuerySet,
    *,
    search_fields: list[str],
    default_sort: str,
    default_dir: str = "asc",
    per_page: int = PER_PAGE,
    sortable: frozenset[str] | None = None,
    search_param: str = "q",
) -> dict:
    """Parse query params, apply search/sort, paginate.

    ``sortable`` names the keys a ``sort`` param may take; a key outside it falls back to
    ``default_sort`` instead of reaching ``order_by`` (where an unknown key raises). Rows whose
    sort key is NULL land last in both directions, so an undated class never leads a
    descending Date(s) sort (#544). ``search_param`` names the search box's parameter, for a
    table that shares its page with another search (the Orientations page's Bookings tab, #626).

    Returns dict with: page, q, sort, sort_dir, base_params (for building URLs).
    """
    params = request.GET
    q = params.get(search_param, "").strip()
    sort = params.get("sort", default_sort)
    sort_dir = params.get("dir", default_dir)
    page_num = params.get("page", 1)

    if sortable is not None and sort not in sortable:
        sort = default_sort

    queryset = table_search(queryset, q, search_fields)

    ordering = F(sort).desc(nulls_last=True) if sort_dir == "desc" else F(sort).asc(nulls_last=True)
    queryset = queryset.order_by(ordering)

    paginator = Paginator(queryset, per_page)
    page = paginator.get_page(page_num)

    base_params = QueryDict(mutable=True)
    if q:
        base_params[search_param] = q
    if sort != default_sort:
        base_params["sort"] = sort
    if sort_dir != default_dir:
        base_params["dir"] = sort_dir
    for key in params:
        if key not in (search_param, "sort", "dir", "page") and params[key]:
            base_params[key] = params[key]

    return {
        "page": page,
        "q": q,
        "sort": sort,
        "sort_dir": sort_dir,
        "base_params": base_params.urlencode(),
    }
