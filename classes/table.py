"""Reusable table search / sort / pagination for admin views."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
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
) -> dict:
    """Parse query params, apply search/sort, paginate.

    Returns dict with: page, q, sort, sort_dir, base_params (for building URLs).
    """
    params = request.GET
    q = params.get("q", "").strip()
    sort = params.get("sort", default_sort)
    sort_dir = params.get("dir", default_dir)
    page_num = params.get("page", 1)

    if q and search_fields:
        search_q = Q()
        for field in search_fields:
            search_q |= Q(**{f"{field}__icontains": q})
        queryset = queryset.filter(search_q)

    order_prefix = "-" if sort_dir == "desc" else ""
    queryset = queryset.order_by(f"{order_prefix}{sort}")

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
