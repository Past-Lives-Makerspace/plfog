"""BDD specs for the collision-safe slug helpers (_unique_slug retry on a race)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import IntegrityError

from classes.factories import ClassOfferingFactory
from classes.models import _is_slug_unique_violation, _save_with_unique_slug


def describe__is_slug_unique_violation():
    def it_matches_the_sqlite_slug_message():
        error = IntegrityError("UNIQUE constraint failed: classes_classoffering.slug")
        assert _is_slug_unique_violation(error) is True

    def it_matches_the_postgres_slug_message():
        error = IntegrityError(
            'duplicate key value violates unique constraint "classes_classoffering_slug_key"\n'
            "DETAIL:  Key (slug)=(pottery) already exists."
        )
        assert _is_slug_unique_violation(error) is True

    def it_ignores_a_non_slug_constraint():
        error = IntegrityError("UNIQUE constraint failed: classes_classoffering.legacy_cms_id")
        assert _is_slug_unique_violation(error) is False


def describe__save_with_unique_slug():
    def it_retries_with_the_next_free_slug_when_a_racing_writer_takes_it(db):
        ClassOfferingFactory(slug="taken")  # a concurrent writer already holds this slug
        offering = ClassOfferingFactory(slug="mover-original")
        # The probe hands back the taken slug first (the save collides), then a free one.
        with patch("classes.models._unique_slug", side_effect=["taken", "won-the-retry"]):
            _save_with_unique_slug(offering, "base", exclude_pk=offering.pk, save=offering.save)
        offering.refresh_from_db()
        assert offering.slug == "won-the-retry"

    def it_reraises_after_exhausting_the_retry_budget(db):
        ClassOfferingFactory(slug="taken")
        offering = ClassOfferingFactory(slug="loser-original")
        with (
            patch("classes.models._unique_slug", return_value="taken"),
            pytest.raises(IntegrityError),
        ):
            _save_with_unique_slug(offering, "base", exclude_pk=offering.pk, save=offering.save)

    def it_propagates_a_non_slug_integrity_error_without_retrying(db):
        offering = ClassOfferingFactory(slug="unrelated")
        calls = 0

        def _raise_non_slug() -> None:
            nonlocal calls
            calls += 1
            raise IntegrityError("UNIQUE constraint failed: classes_classoffering.legacy_cms_id")

        with (
            patch("classes.models._unique_slug", return_value="anything"),
            pytest.raises(IntegrityError),
        ):
            _save_with_unique_slug(offering, "base", exclude_pk=offering.pk, save=_raise_non_slug)
        assert calls == 1  # re-raised on the first attempt, never retried
