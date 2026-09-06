"""BDD-style tests for the biometric selector data migration.

The two ``RunPython`` halves are exercised directly against the real model rather than
through a schema replay: what needs pinning is what they DO to rows, and both are written
to be safe to run against a table of live credentials.

Why they need a test at all: the forward half revokes every credential on production, which
is a member-visible act, and the reverse half deliberately does NOT undo that. Both of those
are decisions someone could quietly "fix" later.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.db import migrations
from django.utils import timezone

from core.models import BiometricCredential

pytestmark = pytest.mark.django_db

migration = importlib.import_module("core.migrations.0077_biometric_selector")


@pytest.fixture()
def member_user(db):
    return User.objects.create_user(username="bio", email="bio@example.com")


def _legacy_credential(user, label, selector, *, revoked_at=None):
    """A row as it stands the moment before the data migration runs.

    The selector is a placeholder rather than blank because the real pre-migration rows have
    no such column at all, and blanking several of them here would collide on the unique
    constraint the finished model carries.
    """
    return BiometricCredential.objects.create(
        user=user,
        selector=selector,
        verifier_hash=f"hash-for-{label}",
        device_label=label,
        expires_at=timezone.now() + timedelta(days=90),
        revoked_at=revoked_at,
    )


def describe_the_migration_itself():
    def it_follows_the_migration_that_created_the_model():
        assert ("core", "0076_biometriccredential") in migration.Migration.dependencies

    def it_ships_a_real_reverse_rather_than_a_noop():
        """A data migration with `RunPython.noop` as its reverse is a one-way door wearing a
        handle. The repo standard is an actual function, and this asserts one is wired up."""
        run_pythons = [op for op in migration.Migration.operations if isinstance(op, migrations.RunPython)]

        assert len(run_pythons) == 1
        assert run_pythons[0].reverse_code is not None
        assert run_pythons[0].reverse_code is not migrations.RunPython.noop
        assert run_pythons[0].reverse_code is migration.clear_selectors

    def it_mints_the_selector_before_making_the_column_unique():
        """Order is load-bearing: unique-ing an empty column across several rows fails, and
        a reordering here would only blow up on a database that has credentials in it."""
        names = [type(op).__name__ for op in migration.Migration.operations]
        alter_selector = [
            index
            for index, op in enumerate(migration.Migration.operations)
            if isinstance(op, migrations.AlterField) and op.name == "selector"
        ]

        assert names.index("RunPython") < alter_selector[0]


def describe_mint_selectors_and_revoke():
    def it_gives_every_row_a_full_entropy_selector(member_user):
        phone = _legacy_credential(member_user, "iPhone", "legacy-1")
        tablet = _legacy_credential(member_user, "iPad", "legacy-2")

        migration.mint_selectors_and_revoke(django_apps, None)

        phone.refresh_from_db()
        tablet.refresh_from_db()
        assert len(phone.selector) == 64
        assert len(tablet.selector) == 64
        assert phone.selector != tablet.selector
        assert {phone.selector, tablet.selector}.isdisjoint({"legacy-1", "legacy-2"})

    def it_revokes_every_pre_existing_credential(member_user):
        """No shipped device knows its selector, so not one of these can be rescued. Leaving
        them live would be a phone that mysteriously stops working; revoking them hands the
        member straight back to an emailed code with the re-enrol offer waiting."""
        phone = _legacy_credential(member_user, "iPhone", "legacy-1")

        migration.mint_selectors_and_revoke(django_apps, None)

        phone.refresh_from_db()
        assert phone.revoked_at is not None
        assert phone.is_active is False

    def it_keeps_the_timestamp_on_a_credential_that_was_already_dead(member_user):
        """When a device actually lost access is a fact worth not overwriting."""
        revoked_long_ago = timezone.now() - timedelta(days=30)
        old = _legacy_credential(member_user, "Lost Phone", "legacy-1", revoked_at=revoked_long_ago)

        migration.mint_selectors_and_revoke(django_apps, None)

        old.refresh_from_db()
        assert old.revoked_at == revoked_long_ago

    def it_runs_cleanly_on_an_empty_table():
        migration.mint_selectors_and_revoke(django_apps, None)

        assert BiometricCredential.objects.count() == 0


def describe_clear_selectors():
    def it_clears_the_column_it_filled(member_user):
        phone = _legacy_credential(member_user, "iPhone", "legacy-1")

        migration.clear_selectors(django_apps, None)

        phone.refresh_from_db()
        assert phone.selector == ""

    def it_does_not_bring_a_revoked_credential_back_to_life(member_user):
        """The security-relevant half. Rolling a migration back must never re-arm a bearer
        credential, and nothing records which rows the forward half revoked, so un-revoking
        would resurrect ones the member killed by hand."""
        revoked_at = timezone.now() - timedelta(days=1)
        phone = _legacy_credential(member_user, "iPhone", "legacy-1", revoked_at=revoked_at)

        migration.clear_selectors(django_apps, None)

        phone.refresh_from_db()
        assert phone.revoked_at == revoked_at
        assert phone.is_active is False

    def it_runs_cleanly_on_an_empty_table():
        migration.clear_selectors(django_apps, None)

        assert BiometricCredential.objects.count() == 0
