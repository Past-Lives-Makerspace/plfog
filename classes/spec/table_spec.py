"""BDD specs for classes/table.py — prepare_table utility."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from classes.factories import CategoryFactory
from classes.models import Category
from classes.table import prepare_table


@pytest.fixture
def rf():
    return RequestFactory()


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
