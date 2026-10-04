"""Data-migration spec for membership 0204: the app store review account is hidden (#614).

The migration only writes fields that exist at head, so its functions run here against the
current app registry instead of rewinding the schema with ``MigrationExecutor``.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model

from membership.models import Member

_migration = import_module("membership.migrations.0204_hide_app_review_account")


def _member_for(email: str) -> Member:
    user = get_user_model().objects.create_user(username=email, email=email)
    Member.objects.filter(user=user).update(show_in_directory=True, hide_from_directory=False)
    return Member.objects.get(user=user)


def _flags(member: Member) -> tuple[bool, bool]:
    member.refresh_from_db()
    return member.hide_from_directory, member.show_in_directory


@pytest.mark.django_db
def describe_migration_0204_hide_app_review_account():
    def it_matches_the_review_email_in_any_case():
        assert _migration.APP_REVIEW_EMAIL == "appreview@pastlives.app"

    def it_hides_the_review_account_and_clears_its_opt_in():
        review = _member_for("AppReview@PastLives.app")

        _migration.hide_app_review_account(apps, None)

        assert _flags(review) == (True, False)
        assert review not in Member.objects.directory_visible()

    def it_leaves_every_other_member_alone():
        other = _member_for("someone@pastlives.app")

        _migration.hide_app_review_account(apps, None)

        assert _flags(other) == (False, True)

    def it_changes_nothing_when_the_account_does_not_exist():
        _migration.hide_app_review_account(apps, None)

        assert not Member.objects.filter(hide_from_directory=True).exists()

    def it_reverses_to_the_listed_state_production_had():
        review = _member_for("appreview@pastlives.app")
        other = _member_for("other@pastlives.app")
        Member.objects.filter(pk=other.pk).update(show_in_directory=False)
        _migration.hide_app_review_account(apps, None)

        _migration.show_app_review_account(apps, None)

        assert _flags(review) == (False, True)
        assert _flags(other) == (False, False)

    def it_keeps_the_review_account_active_with_its_login():
        review = _member_for("appreview@pastlives.app")

        _migration.hide_app_review_account(apps, None)

        review.refresh_from_db()
        assert review.status == Member.Status.ACTIVE
        assert review.user is not None
        assert review.user.is_active is True
