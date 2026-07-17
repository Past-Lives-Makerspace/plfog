"""Data-migration spec for 0086 — folding the three legacy contact columns into MemberContact.

Uses Django's ``MigrationExecutor`` so the legacy ``other_contact_info`` /
``instructor_website`` / ``instructor_social_handle`` columns (removed from the current
model in 0087) still exist on the historical model we build fixtures with. Each test
restores the schema to head in a ``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "membership"
_BEFORE = "0085_member_instructor_bio_membercontact"
_AFTER = "0086_fold_member_contacts"
_HEAD = "0087_remove_member_instructor_social_handle_and_more"


def _migrate(target: str):
    """Migrate the membership app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_member(apps, **kwargs):
    MembershipPlan = apps.get_model(_APP, "MembershipPlan")
    plan, _ = MembershipPlan.objects.get_or_create(name="Std", defaults={"monthly_price": Decimal("10")})
    Member = apps.get_model(_APP, "Member")
    return Member.objects.create(membership_plan=plan, **kwargs)


@pytest.mark.django_db(transaction=True)
def describe_migration_0086_fold_member_contacts():
    def it_moves_legacy_fields_into_labeled_contacts_and_backfills_bio():
        try:
            apps = _migrate(_BEFORE)
            member = _make_member(
                apps,
                full_legal_name="Teacher T",
                instructor_slug="teacher-t",
                about_me="I teach clay.",
                other_contact_info="Signal @t",
                instructor_website="https://t.example",
                instructor_social_handle="@teacher",
            )
            blank = _make_member(apps, full_legal_name="Blank B")

            apps = _migrate(_AFTER)
            Member = apps.get_model(_APP, "Member")
            MemberContact = apps.get_model(_APP, "MemberContact")

            member = Member.objects.get(pk=member.pk)
            assert member.instructor_bio == "I teach clay."
            rows = {c.label: c for c in MemberContact.objects.filter(member_id=member.pk)}
            assert rows["Other"].value == "Signal @t"
            assert rows["Other"].show_in_directory is True
            assert rows["Other"].show_on_instructor_page is False
            assert rows["Website"].value == "https://t.example"
            assert rows["Website"].show_in_directory is False
            assert rows["Website"].show_on_instructor_page is True
            assert rows["Social"].value == "@teacher"
            assert rows["Social"].show_on_instructor_page is True

            # A member with no legacy values seeds no contacts and no bio.
            assert MemberContact.objects.filter(member_id=blank.pk).count() == 0
            assert Member.objects.get(pk=blank.pk).instructor_bio == ""
        finally:
            _migrate(_HEAD)

    def it_does_not_overwrite_an_already_written_instructor_bio():
        try:
            apps = _migrate(_BEFORE)
            member = _make_member(
                apps,
                full_legal_name="Prewritten P",
                instructor_slug="prewritten-p",
                about_me="directory blurb",
                instructor_bio="hand-written teaching bio",
            )

            apps = _migrate(_AFTER)
            member = apps.get_model(_APP, "Member").objects.get(pk=member.pk)
            assert member.instructor_bio == "hand-written teaching bio"
        finally:
            _migrate(_HEAD)

    def it_reverse_restores_legacy_fields_and_clears_bio_and_seeded_rows():
        try:
            apps = _migrate(_AFTER)
            MemberContact = apps.get_model(_APP, "MemberContact")
            member = _make_member(
                apps,
                full_legal_name="Rev R",
                instructor_slug="rev-r",
                instructor_bio="teaching bio",
            )
            MemberContact.objects.create(member=member, label="Other", value="other-val", show_in_directory=True)
            MemberContact.objects.create(
                member=member, label="Website", value="https://w", show_on_instructor_page=True
            )
            MemberContact.objects.create(member=member, label="Social", value="@s", show_on_instructor_page=True)

            apps = _migrate(_BEFORE)
            Member = apps.get_model(_APP, "Member")
            MemberContact = apps.get_model(_APP, "MemberContact")
            member = Member.objects.get(pk=member.pk)
            assert member.other_contact_info == "other-val"
            assert member.instructor_website == "https://w"
            assert member.instructor_social_handle == "@s"
            assert member.instructor_bio == ""
            assert MemberContact.objects.filter(member_id=member.pk).count() == 0
        finally:
            _migrate(_HEAD)
