"""Reusable table search / sort / pagination for admin views."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import F, Q, QuerySet
from django.http import HttpRequest, QueryDict

PER_PAGE = 25


def prepare_table(
    request: HttpRequest,
    queryset: QuerySet,
    *,
    search_fields: list[str],
    default_sort: str,
    default_dir: str = "asc",
    per_page: int = PER_PAGE,
    sortable: frozenset[str] | None = None,
) -> dict:
    """Parse query params, apply search/sort, paginate.

    ``sortable`` names the keys a ``sort`` param may take; a key outside it falls back to
    ``default_sort`` instead of reaching ``order_by`` (where an unknown key raises). Rows whose
    sort key is NULL land last in both directions, so an undated class never leads a
    descending Date(s) sort (#544).

    Returns dict with: page, q, sort, sort_dir, base_params (for building URLs).
    """
    params = request.GET
    q = params.get("q", "").strip()
    sort = params.get("sort", default_sort)
    sort_dir = params.get("dir", default_dir)
    page_num = params.get("page", 1)

    if sortable is not None and sort not in sortable:
        sort = default_sort

    if q and search_fields:
        search_q = Q()
        for field in search_fields:
            search_q |= Q(**{f"{field}__icontains": q})
        queryset = queryset.filter(search_q)

    ordering = F(sort).desc(nulls_last=True) if sort_dir == "desc" else F(sort).asc(nulls_last=True)
    queryset = queryset.order_by(ordering)

    paginator = Paginator(queryset, per_page)
    page = paginator.get_page(page_num)

    base_params = QueryDict(mutable=True)
    if q:
        base_params["q"] = q
    if sort != default_sort:
        base_params["sort"] = sort
    if sort_dir != default_dir:
        base_params["dir"] = sort_dir
    for key in params:
        if key not in ("q", "sort", "dir", "page") and params[key]:
            base_params[key] = params[key]

    return {
        "page": page,
        "q": q,
        "sort": sort,
        "sort_dir": sort_dir,
        "base_params": base_params.urlencode(),
    }
