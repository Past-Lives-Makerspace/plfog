"""A Back onto the public class catalog must return the page, not the results grid.

The catalog answers some requests with a fragment and is reachable by a GET, which is
the combination a history restore can reach: a restore is a GET carrying ``HX-Request``
and no ``HX-Boosted``. A view behind ``@require_POST`` cannot be reached this way.

It matters because the catalog is also the one page that pushes history by design: the
category filter and the pagination links both carry ``hx-push-url``. Pressing Back after
using either is an ordinary thing to do.
"""

from __future__ import annotations

from django.test import Client
from django.urls import reverse


def describe_the_public_catalog_under_a_history_restore():
    def it_returns_the_whole_document_not_the_results_grid(db):
        client = Client()
        url = reverse("classes:public_list")

        restored = client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true")

        assert restored.status_code == 200
        body = restored.content.decode()
        assert "<!DOCTYPE html" in body, (
            "a restore got a bare fragment; htmx would swap it in as the entire page, "
            "leaving no topbar, sidebar, hero or filter form"
        )

    def it_still_answers_the_filter_swap_with_just_the_grid(db):
        # The behaviour the fragment branch exists for, unchanged.
        client = Client()
        url = reverse("classes:public_list")

        swapped = client.get(url, HTTP_HX_REQUEST="true")

        assert swapped.status_code == 200
        assert "<!DOCTYPE html" not in swapped.content.decode(), "the filter swap must stay a fragment"

    def it_returns_the_whole_document_for_a_boosted_navigation(db):
        client = Client()
        url = reverse("classes:public_list")

        boosted = client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")

        assert boosted.status_code == 200
        assert "<!DOCTYPE html" in boosted.content.decode()
