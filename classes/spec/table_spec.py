"""BDD specs for classes/table.py — prepare_table utility."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db.models import Min
from django.test import RequestFactory
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import Category, ClassOffering
from classes.table import prepare_table


@pytest.fixture
def rf():
    return RequestFactory()


def _dated_offerings() -> dict[str, ClassOffering]:
    """Three classes: one dated early, one late, one with no sessions at all."""
    now = timezone.now()
    early = ClassOfferingFactory(title="Early", slug="early")
    ClassSessionFactory(class_offering=early, starts_at=now + timedelta(days=1))
    late = ClassOfferingFactory(title="Late", slug="late")
    ClassSessionFactory(class_offering=late, starts_at=now + timedelta(days=30))
    undated = ClassOfferingFactory(title="Undated", slug="undated")
    return {"early": early, "late": late, "undated": undated}


def _by_first_session(rf, direction: str) -> list[str]:
    request = rf.get("/", {"sort": "first_session", "dir": direction})
    queryset = ClassOffering.objects.annotate(first_session=Min("sessions__starts_at"))
    result = prepare_table(request, queryset, search_fields=[], default_sort="created_at", default_dir="desc")
    return [c.slug for c in result["page"]]


def describe_prepare_table():
    def it_returns_all_records_on_first_page(rf, db):
        CategoryFactory(name="Alpha", sort_order=1)
        CategoryFactory(name="Beta", sort_order=2)
        request = rf.get("/")
        result = prepare_table(request, Category.objects.all(), search_fields=["name"], default_sort="sort_order")
        assert len(result["page"]) == 2
        assert result["q"] == ""
        assert result["sort"] == "sort_order"
        assert result["sort_dir"] == "asc"

    def it_filters_by_search_query(rf, db):
        CategoryFactory(name="Woodworking", sort_order=1)
        CategoryFactory(name="Metalwork", sort_order=2)
        request = rf.get("/", {"q": "wood"})
        result = prepare_table(request, Category.objects.all(), search_fields=["name"], default_sort="sort_order")
        assert len(result["page"]) == 1
        assert result["page"][0].name == "Woodworking"
        assert result["q"] == "wood"

    def it_sorts_descending_when_requested(rf, db):
        CategoryFactory(name="Alpha", sort_order=1)
        CategoryFactory(name="Beta", sort_order=2)
        request = rf.get("/", {"sort": "name", "dir": "desc"})
        result = prepare_table(request, Category.objects.all(), search_fields=["name"], default_sort="sort_order")
        names = [c.name for c in result["page"]]
        assert names == ["Beta", "Alpha"]

    def it_paginates_results(rf, db):
        for i in range(30):
            CategoryFactory(name=f"Cat {i:02d}", sort_order=i)
        request = rf.get("/", {"page": "2"})
        result = prepare_table(
            request, Category.objects.all(), search_fields=["name"], default_sort="sort_order", per_page=10
        )
        assert len(result["page"]) == 10
        assert result["page"].number == 2

    def it_preserves_extra_params_in_base_params(rf, db):
        CategoryFactory(name="X", sort_order=1)
        request = rf.get("/", {"q": "X", "status": "active"})
        result = prepare_table(request, Category.objects.all(), search_fields=["name"], default_sort="sort_order")
        assert "status=active" in result["base_params"]
        assert "q=X" in result["base_params"]

    def it_uses_default_dir_when_not_specified(rf, db):
        CategoryFactory(name="A", sort_order=1)
        request = rf.get("/")
        result = prepare_table(
            request, Category.objects.all(), search_fields=["name"], default_sort="sort_order", default_dir="desc"
        )
        assert result["sort_dir"] == "desc"


def describe_nulls_last():
    def it_puts_undated_rows_last_when_ascending(rf, db):
        _dated_offerings()
        assert _by_first_session(rf, "asc") == ["early", "late", "undated"]

    def it_puts_undated_rows_last_when_descending(rf, db):
        _dated_offerings()
        assert _by_first_session(rf, "desc") == ["late", "early", "undated"]


def describe_sortable_keys():
    def _table(rf, params: dict[str, str]) -> dict:
        request = rf.get("/", params)
        return prepare_table(
            request,
            Category.objects.all(),
            search_fields=["name"],
            default_sort="sort_order",
            sortable=frozenset({"name"}),
        )

    def it_sorts_by_a_listed_key(rf, db):
        CategoryFactory(name="Beta", sort_order=1)
        CategoryFactory(name="Alpha", sort_order=2)
        result = _table(rf, {"sort": "name"})
        assert [c.name for c in result["page"]] == ["Alpha", "Beta"]
        assert result["sort"] == "name"
        assert "sort=name" in result["base_params"]

    def it_falls_back_to_the_default_sort_for_an_unknown_key(rf, db):
        CategoryFactory(name="Alpha", sort_order=2)
        CategoryFactory(name="Beta", sort_order=1)
        result = _table(rf, {"sort": "nonsense"})
        assert [c.name for c in result["page"]] == ["Beta", "Alpha"]
        assert result["sort"] == "sort_order"
        assert "sort=" not in result["base_params"]

    def it_keeps_the_direction_when_the_key_falls_back(rf, db):
        CategoryFactory(name="Alpha", sort_order=2)
        CategoryFactory(name="Beta", sort_order=1)
        result = _table(rf, {"sort": "nonsense", "dir": "desc"})
        assert [c.name for c in result["page"]] == ["Alpha", "Beta"]
        assert result["base_params"] == "dir=desc"

    def it_leaves_the_sort_unrestricted_when_no_allowlist_is_given(rf, db):
        CategoryFactory(name="Alpha", sort_order=2, slug="alpha")
        CategoryFactory(name="Beta", sort_order=1, slug="beta")
        request = rf.get("/", {"sort": "slug"})
        result = prepare_table(request, Category.objects.all(), search_fields=["name"], default_sort="sort_order")
        assert [c.name for c in result["page"]] == ["Alpha", "Beta"]
        assert result["sort"] == "slug"
