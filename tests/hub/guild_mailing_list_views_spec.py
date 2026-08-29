"""Guild "Your Mailing List" editor + import views, and the guild-edit rename.

Covers the two new endpoints on the Announcements tab — ``guild_mailing_list_save``
(inline-formset editor with inline-error re-render) and ``guild_mailing_list_import`` (lenient
CSV/text import) — the permission gate, and the "Guild Settings" rename + section render.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from membership.models import Member
from tests.membership.factories import (
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_seq = {"n": 0}


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _guild_member(guild, email: str):
    _seq["n"] += 1
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"vgm_{_seq['n']}", email=email, password="pw", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    GuildMembershipFactory(guild=guild, member=member)
    return member


def _formset_payload(rows: list[dict[str, str]], *, initial: int) -> dict[str, str]:
    data: dict[str, str] = {
        "mailing_list-TOTAL_FORMS": str(len(rows)),
        "mailing_list-INITIAL_FORMS": str(initial),
        "mailing_list-MIN_NUM_FORMS": "0",
        "mailing_list-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        for key, value in row.items():
            data[f"mailing_list-{i}-{key}"] = value
    return data


def _msgs(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def describe_guild_mailing_list_save():
    def it_adds_a_new_custom_address(client: Client):
        _user_with_role("ml_add", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ml_add", password="pass")
        response = client.post(
            reverse("hub_guild_mailing_list_save", args=[guild.pk]),
            _formset_payload(
                [{"email": "booster@example.com", "label": "Booster", "sort_order": "0", "id": ""}], initial=0
            ),
        )
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements"
        row = guild.mailing_list_emails.get()
        assert row.email == "booster@example.com"
        assert row.label == "Booster"

    def it_edits_an_existing_address(client: Client):
        _user_with_role("ml_edit", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        existing = GuildMailingListEmailFactory(guild=guild, email="old@example.com")
        client.login(username="ml_edit", password="pass")
        client.post(
            reverse("hub_guild_mailing_list_save", args=[guild.pk]),
            _formset_payload(
                [{"email": "new@example.com", "label": "", "sort_order": "0", "id": str(existing.pk)}], initial=1
            ),
        )
        existing.refresh_from_db()
        assert existing.email == "new@example.com"

    def it_deletes_an_existing_address(client: Client):
        _user_with_role("ml_del", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        existing = GuildMailingListEmailFactory(guild=guild, email="gone@example.com")
        client.login(username="ml_del", password="pass")
        client.post(
            reverse("hub_guild_mailing_list_save", args=[guild.pk]),
            _formset_payload(
                [{"email": "gone@example.com", "sort_order": "0", "id": str(existing.pk), "DELETE": "on"}], initial=1
            ),
        )
        assert not guild.mailing_list_emails.exists()

    def it_re_renders_inline_with_errors_and_preserves_input_on_an_invalid_email(client: Client):
        _user_with_role("ml_bad", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ml_bad", password="pass")
        response = client.post(
            reverse("hub_guild_mailing_list_save", args=[guild.pk]),
            _formset_payload([{"email": "not-an-email", "label": "Oops", "sort_order": "0", "id": ""}], initial=0),
        )
        assert response.status_code == 200
        assert response.context["active_tab"] == "announcements"
        assert response.context["mailing_list_formset"].errors[0]
        # The typed input is preserved (not lost to a redirect).
        assert "not-an-email" in response.content.decode()
        assert not guild.mailing_list_emails.exists()

    def it_surfaces_a_duplicate_email_within_the_submission(client: Client):
        _user_with_role("ml_dup", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ml_dup", password="pass")
        response = client.post(
            reverse("hub_guild_mailing_list_save", args=[guild.pk]),
            _formset_payload(
                [
                    {"email": "dup@example.com", "label": "", "sort_order": "0", "id": ""},
                    {"email": "dup@example.com", "label": "", "sort_order": "1", "id": ""},
                ],
                initial=0,
            ),
        )
        assert response.status_code == 200
        assert not guild.mailing_list_emails.exists()

    def it_redirects_a_get_to_the_announcements_tab(client: Client):
        _user_with_role("ml_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ml_get", password="pass")
        response = client.get(reverse("hub_guild_mailing_list_save", args=[guild.pk]))
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements"

    def it_lets_the_guild_lead_save(client: Client):
        user = _user_with_role("ml_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="ml_lead", password="pass")
        response = client.post(
            reverse("hub_guild_mailing_list_save", args=[guild.pk]),
            _formset_payload([{"email": "lead@example.com", "label": "", "sort_order": "0", "id": ""}], initial=0),
        )
        assert response.status_code == 302
        assert guild.mailing_list_emails.filter(email="lead@example.com").exists()

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("ml_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="ml_reg", password="pass")
        response = client.post(reverse("hub_guild_mailing_list_save", args=[guild.pk]), {})
        assert response.status_code == 403

    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.get(reverse("hub_guild_mailing_list_save", args=[guild.pk]))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]


def describe_guild_mailing_list_import():
    def _upload(name: str, content: bytes) -> SimpleUploadedFile:
        return SimpleUploadedFile(name, content, "text/csv")

    def it_imports_valid_addresses_and_reports_a_summary(client: Client):
        _user_with_role("imp_ok", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="imp_ok", password="pass")
        response = client.post(
            reverse("hub_guild_mailing_list_import", args=[guild.pk]),
            {"import_file": _upload("list.csv", b"one@example.com\ntwo@example.com\nnope\n")},
        )
        assert response.status_code == 302
        assert guild.mailing_list_emails.count() == 2
        assert _msgs(response) == ["Imported 2 addresses. Skipped 1 invalid."]

    def it_reads_a_second_column_as_a_label(client: Client):
        _user_with_role("imp_lbl", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="imp_lbl", password="pass")
        client.post(
            reverse("hub_guild_mailing_list_import", args=[guild.pk]),
            {"import_file": _upload("list.csv", b"desk@example.com,Front desk")},
        )
        assert guild.mailing_list_emails.get().label == "Front desk"

    def it_skips_member_collisions(client: Client):
        _user_with_role("imp_mem", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        _guild_member(guild, "member@example.com")
        client.login(username="imp_mem", password="pass")
        response = client.post(
            reverse("hub_guild_mailing_list_import", args=[guild.pk]),
            {"import_file": _upload("list.csv", b"Member@Example.com\nnew@example.com\n")},
        )
        assert list(guild.mailing_list_emails.values_list("email", flat=True)) == ["new@example.com"]
        assert _msgs(response) == ["Imported 1 address. Skipped 1 that is a member."]

    def it_errors_and_creates_nothing_with_no_file(client: Client):
        _user_with_role("imp_nofile", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="imp_nofile", password="pass")
        response = client.post(reverse("hub_guild_mailing_list_import", args=[guild.pk]), {})
        assert response.status_code == 302
        assert not guild.mailing_list_emails.exists()
        assert _msgs(response) == ["Choose a CSV or text file to import."]

    def it_errors_when_every_line_is_invalid(client: Client):
        _user_with_role("imp_bad", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="imp_bad", password="pass")
        response = client.post(
            reverse("hub_guild_mailing_list_import", args=[guild.pk]),
            {"import_file": _upload("list.csv", b"nope\nalso-nope\n")},
        )
        assert not guild.mailing_list_emails.exists()
        assert _msgs(response) == ["No new addresses imported. Skipped 2 invalid."]

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("imp_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="imp_reg", password="pass")
        response = client.post(
            reverse("hub_guild_mailing_list_import", args=[guild.pk]),
            {"import_file": _upload("list.csv", b"a@example.com")},
        )
        assert response.status_code == 403
        assert not guild.mailing_list_emails.exists()

    def it_rejects_a_get(client: Client):
        _user_with_role("imp_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="imp_get", password="pass")
        response = client.get(reverse("hub_guild_mailing_list_import", args=[guild.pk]))
        assert response.status_code == 405


def describe_guild_settings_page():
    def it_renders_the_mailing_list_section_and_the_settings_rename(client: Client):
        _user_with_role("pg_view", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        _guild_member(guild, "member@example.com")
        client.login(username="pg_view", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        content = response.content.decode()
        assert response.status_code == 200
        # Rename: title + h1 both read "<Guild> Settings".
        assert f"{guild.name} Settings" in content
        # The "Your Mailing List" section with its tooltip copy.
        assert "Your Mailing List" in content
        assert "members are added automatically" in content
        # The roster moved here — its own count line + the member's address.
        assert "on your list automatically" in content
        assert "member@example.com" in content
        # Editor + import controls.
        assert "+ Add address" in content
        assert "Save mailing list" in content
        assert "Import addresses" in content

    def it_shows_the_empty_state_when_there_are_no_custom_addresses(client: Client):
        _user_with_role("pg_empty", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="pg_empty", password="pass")
        content = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        assert "No custom addresses yet." in content

    def it_no_longer_shows_the_roster_in_the_post_announcement_card(client: Client):
        _user_with_role("pg_dup", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="pg_dup", password="pass")
        content = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        # The old reach block's unique phrasing is gone (roster is not shown twice).
        assert "will receive an emailed announcement" not in content
