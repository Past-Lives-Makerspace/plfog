"""GuildMailingListEmail — custom (non-member) addresses on a guild's announcement list.

Covers the fat-model helpers: ``Guild.mailing_list_emails_deduped`` (normalize + sort +
drop member-collisions), the unique ``(guild, email)`` constraint, and the lenient
``import_from_text`` bulk-import (skip invalid / existing / member rows, optional label,
summary counts).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from membership.models import GuildMailingListEmail, MailingListImportResult
from tests.membership.factories import (
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

_seq = {"n": 0}


def _guild_member(guild, email: str):
    """An ACTIVE member of ``guild`` with a linked, email-bearing user (an announcement recipient)."""
    _seq["n"] += 1
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"gm_{_seq['n']}", email=email, password="pw", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    GuildMembershipFactory(guild=guild, member=member)
    return member


def describe_mailing_list_emails_deduped():
    def it_lower_cases_and_sorts_the_custom_addresses():
        guild = GuildFactory()
        GuildMailingListEmailFactory(guild=guild, email="Zed@Example.com")
        GuildMailingListEmailFactory(guild=guild, email="Amy@Example.com")
        assert guild.mailing_list_emails_deduped(set()) == ["amy@example.com", "zed@example.com"]

    def it_drops_addresses_that_match_a_member_email():
        guild = GuildFactory()
        GuildMailingListEmailFactory(guild=guild, email="Booster@Example.com")
        GuildMailingListEmailFactory(guild=guild, email="member@example.com")
        # member@example.com is a member — it must not appear in the custom list.
        assert guild.mailing_list_emails_deduped({"member@example.com"}) == ["booster@example.com"]

    def it_returns_an_empty_list_when_there_are_no_custom_addresses():
        guild = GuildFactory()
        assert guild.mailing_list_emails_deduped({"member@example.com"}) == []


def describe_unique_constraint():
    def it_rejects_a_duplicate_email_on_the_same_guild():
        guild = GuildFactory()
        GuildMailingListEmailFactory(guild=guild, email="dup@example.com")
        with pytest.raises(IntegrityError):
            GuildMailingListEmail.objects.create(guild=guild, email="dup@example.com")

    def it_allows_the_same_email_on_a_different_guild():
        a = GuildFactory()
        b = GuildFactory()
        GuildMailingListEmailFactory(guild=a, email="shared@example.com")
        GuildMailingListEmailFactory(guild=b, email="shared@example.com")
        assert GuildMailingListEmail.objects.filter(email="shared@example.com").count() == 2


def describe_import_from_text():
    def it_creates_rows_for_valid_new_addresses():
        guild = GuildFactory()
        result = GuildMailingListEmail.import_from_text(guild, "one@example.com\ntwo@example.com\n")
        assert result.imported == 2
        assert set(guild.mailing_list_emails.values_list("email", flat=True)) == {
            "one@example.com",
            "two@example.com",
        }

    def it_lower_cases_imported_addresses():
        guild = GuildFactory()
        GuildMailingListEmail.import_from_text(guild, "Mixed@Example.com")
        assert list(guild.mailing_list_emails.values_list("email", flat=True)) == ["mixed@example.com"]

    def it_reads_a_second_column_as_the_label():
        guild = GuildFactory()
        GuildMailingListEmail.import_from_text(guild, "desk@example.com,Front desk")
        row = guild.mailing_list_emails.get()
        assert row.email == "desk@example.com"
        assert row.label == "Front desk"

    def it_treats_comma_separated_emails_on_one_line_as_separate_rows():
        guild = GuildFactory()
        result = GuildMailingListEmail.import_from_text(guild, "a@example.com, b@example.com")
        assert result.imported == 2

    def it_skips_invalid_tokens():
        guild = GuildFactory()
        result = GuildMailingListEmail.import_from_text(guild, "good@example.com\nnot-an-email\n")
        assert result.imported == 1
        assert result.skipped_invalid == 1

    def it_skips_addresses_already_on_the_list():
        guild = GuildFactory()
        GuildMailingListEmailFactory(guild=guild, email="here@example.com")
        result = GuildMailingListEmail.import_from_text(guild, "here@example.com\nnew@example.com\n")
        assert result.imported == 1
        assert result.skipped_existing == 1

    def it_skips_addresses_that_match_a_member_case_insensitively():
        guild = GuildFactory()
        _guild_member(guild, "member@example.com")
        result = GuildMailingListEmail.import_from_text(guild, "Member@Example.com\nnew@example.com\n")
        assert result.imported == 1
        assert result.skipped_members == 1
        assert not guild.mailing_list_emails.filter(email="member@example.com").exists()

    def it_skips_a_duplicate_within_the_same_import():
        guild = GuildFactory()
        result = GuildMailingListEmail.import_from_text(guild, "same@example.com\nsame@example.com\n")
        assert result.imported == 1
        assert result.skipped_existing == 1

    def it_ignores_blank_lines():
        guild = GuildFactory()
        result = GuildMailingListEmail.import_from_text(guild, "\n\nsolo@example.com\n\n")
        assert result.imported == 1

    def it_creates_no_rows_for_an_empty_file():
        guild = GuildFactory()
        result = GuildMailingListEmail.import_from_text(guild, "")
        assert result.imported == 0
        assert not result.created_any
        assert not guild.mailing_list_emails.exists()


def describe_MailingListImportResult_summary():
    def it_summarizes_a_clean_import():
        result = MailingListImportResult(imported=12, skipped_existing=0, skipped_members=0, skipped_invalid=0)
        assert result.summary == "Imported 12 addresses."

    def it_uses_the_singular_for_one_address():
        result = MailingListImportResult(imported=1, skipped_existing=0, skipped_members=0, skipped_invalid=0)
        assert result.summary == "Imported 1 address."

    def it_lists_every_skip_category():
        result = MailingListImportResult(imported=12, skipped_existing=3, skipped_members=2, skipped_invalid=1)
        assert (
            result.summary
            == "Imported 12 addresses. Skipped 3 already on your list, 2 that are members, and 1 invalid."
        )

    def it_uses_a_single_skip_clause_without_a_comma():
        result = MailingListImportResult(imported=2, skipped_existing=0, skipped_members=0, skipped_invalid=1)
        assert result.summary == "Imported 2 addresses. Skipped 1 invalid."

    def it_reports_nothing_imported_when_all_were_skipped():
        result = MailingListImportResult(imported=0, skipped_existing=0, skipped_members=0, skipped_invalid=3)
        assert not result.created_any
        assert result.summary == "No new addresses imported. Skipped 3 invalid."

    def it_reports_an_empty_file():
        result = MailingListImportResult(imported=0, skipped_existing=0, skipped_members=0, skipped_invalid=0)
        assert result.summary == "No email addresses found in that file."
