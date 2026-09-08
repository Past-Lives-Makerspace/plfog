"""BDD specs for the member wiki's writing surfaces (spec A, PR A3).

The starter chooser and its parameter passthrough, create and edit (including the fact
formset's starter prompts, the attachment XOR, and the moderator-only Equipment link), the
title/body autosave allowlist, draft resume, one-tap "Still accurate", and the two
micro-contribution modals with their gates.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import Member, WikiDraft, WikiPage, WikiPageFact
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    WikiAttachmentFactory,
    WikiDraftFactory,
    WikiPageFactFactory,
    WikiPageFactory,
    tiny_png_bytes,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    config = SiteConfiguration.load()
    config.wiki_enabled = True
    config.save()
    return config


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER, status: str = Member.Status.ACTIVE) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = status
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, **kwargs: str) -> User:
    user = _member_user(username, **kwargs)
    client.login(username=username, password="pass")
    return user


def _formset_data(*, facts: int = 0, attachments: int = 0) -> dict[str, str]:
    """The management-form scaffolding every create/edit POST has to carry."""
    return {
        "facts-TOTAL_FORMS": str(facts),
        "facts-INITIAL_FORMS": "0",
        "facts-MIN_NUM_FORMS": "0",
        "facts-MAX_NUM_FORMS": "1000",
        "attachments-TOTAL_FORMS": str(attachments),
        "attachments-INITIAL_FORMS": "0",
        "attachments-MIN_NUM_FORMS": "0",
        "attachments-MAX_NUM_FORMS": "1000",
    }


def describe_the_starter_chooser():
    def it_renders_a_card_per_starter(client: Client):
        _login(client, "new_cards")
        response = client.get(reverse("hub_wiki_new"))
        assert response.status_code == 200
        assert b"Machine or tool" in response.content
        assert b"Reference table or chart" in response.content

    def it_carries_the_incoming_parameters_through(client: Client):
        _login(client, "new_params")
        guild = GuildFactory(slug="woodworking")
        response = client.get(reverse("hub_wiki_new"), {"guild": guild.slug, "title": "Table Saw", "wanted": "7"})
        assert b"wanted=7" in response.content
        assert b"guild=woodworking" in response.content
        assert b"title=Table+Saw" in response.content

    def it_tells_an_inactive_member_why_they_cannot_write(client: Client):
        _login(client, "new_inactive", status=Member.Status.FORMER)
        response = client.get(reverse("hub_wiki_new"))
        assert b"Your membership needs to be active to write here." in response.content
        assert b"Machine or tool" not in response.content


def describe_creating_a_page():
    def it_404s_an_unknown_starter(client: Client):
        _login(client, "create_badkind")
        assert client.get(reverse("hub_wiki_create", args=["bogus"])).status_code == 404

    def it_403s_an_inactive_member(client: Client):
        _login(client, "create_inactive", status=Member.Status.FORMER)
        assert client.get(reverse("hub_wiki_create", args=["howto"])).status_code == 403

    def it_renders_one_row_per_starter_prompt(client: Client):
        _login(client, "create_prompts")
        response = client.get(reverse("hub_wiki_create", args=["machine"]))
        assert response.status_code == 200
        assert response.context["facts_formset"].total_form_count() == 4
        assert b"Blade or bit" in response.content

    def it_prefills_the_starter_body_and_the_kind(client: Client):
        _login(client, "create_starter")
        response = client.get(reverse("hub_wiki_create", args=["machine"]))
        assert response.context["form"].initial["kind"] == "machine"
        assert "What It Does" in response.context["form"].initial["body"]

    def it_prefills_the_scope_from_the_query_string(client: Client):
        user = _login(client, "create_scope")
        guild = GuildFactory(name="Woodworking", slug="woodworking")
        GuildMembershipFactory(guild=guild, member=user.member)
        response = client.get(reverse("hub_wiki_create", args=["machine"]), {"guild": "woodworking"})
        assert response.context["form"].initial["guild"] == guild

    def it_prefills_the_only_guild_a_member_belongs_to(client: Client):
        user = _login(client, "create_onlyguild")
        guild = GuildFactory(name="Woodworking", slug="woodworking")
        GuildMembershipFactory(guild=guild, member=user.member)
        response = client.get(reverse("hub_wiki_create", args=["machine"]))
        assert response.context["form"].initial["guild"] == guild

    def it_carries_multipart_encoding(client: Client):
        _login(client, "create_enctype")
        response = client.get(reverse("hub_wiki_create", args=["howto"]))
        assert b'enctype="multipart/form-data"' in response.content

    def it_has_no_safety_toggle(client: Client):
        # The safety gate is spec D's, and a create form that offered a tickbox an edit form
        # did not would be exactly the hole D's gate exists to close.
        _login(client, "create_nosafety")
        response = client.get(reverse("hub_wiki_create", args=["howto"]))
        assert set(response.context["form"].fields) == {"title", "guild", "kind", "body"}
        assert b"tells someone how to stay safe" not in response.content

    def it_creates_the_page_and_redirects_to_it(client: Client):
        _login(client, "create_ok")
        response = client.post(
            reverse("hub_wiki_create", args=["howto"]),
            {"title": "Finishing Walnut", "kind": "howto", "body": "<p>Wipe it on.</p>", **_formset_data()},
        )
        page = WikiPage.objects.get(title="Finishing Walnut")
        assert response.status_code == 302
        assert response["Location"] == page.get_absolute_url()
        assert page.revisions.count() == 1

    def it_saves_the_quick_answers_rows(client: Client):
        _login(client, "create_facts")
        data = {
            "title": "Table Saw",
            "kind": "machine",
            "body": "",
            **_formset_data(facts=2),
            "facts-0-label": "Blade",
            "facts-0-value": "10 inch",
            "facts-0-sort_order": "0",
            "facts-1-label": "Max width",
            "facts-1-value": "24 inch",
            "facts-1-sort_order": "1",
        }
        client.post(reverse("hub_wiki_create", args=["machine"]), data)
        page = WikiPage.objects.get(title="Table Saw")
        assert list(page.facts.values_list("label", "value")) == [("Blade", "10 inch"), ("Max width", "24 inch")]
        assert "10 inch" in page.search_text

    def it_does_not_persist_an_untouched_prompt_row(client: Client):
        _login(client, "create_skipprompt")
        data = {
            "title": "Table Saw Two",
            "kind": "machine",
            "body": "",
            **_formset_data(facts=2),
            "facts-0-label": "Blade",
            "facts-0-value": "10 inch",
            "facts-0-sort_order": "0",
            # A prompt the member skipped: a label with no answer must never block Save.
            "facts-1-label": "Max size",
            "facts-1-value": "",
            "facts-1-sort_order": "1",
        }
        response = client.post(reverse("hub_wiki_create", args=["machine"]), data)
        assert response.status_code == 302
        page = WikiPage.objects.get(title="Table Saw Two")
        assert list(page.facts.values_list("label", flat=True)) == ["Blade"]

    def it_rejects_a_reserved_title(client: Client):
        _login(client, "create_reserved")
        response = client.post(
            reverse("hub_wiki_create", args=["howto"]),
            {"title": "Search", "kind": "howto", "body": "", **_formset_data()},
        )
        assert response.status_code == 200
        assert b"That name is reserved. Pick another title." in response.content

    def it_rejects_a_duplicate_title_in_the_same_scope(client: Client):
        _login(client, "create_dupe")
        WikiPageFactory(title="Table Saw", guild=None)
        response = client.post(
            reverse("hub_wiki_create", args=["howto"]),
            {"title": "Table Saw", "kind": "howto", "body": "", **_formset_data()},
        )
        assert response.status_code == 200
        assert b"already exists" in response.content

    def it_sanitizes_the_body(client: Client):
        _login(client, "create_sanitize")
        client.post(
            reverse("hub_wiki_create", args=["howto"]),
            {
                "title": "Sanitized Page",
                "kind": "howto",
                "body": '<p>ok</p><script>alert(1)</script><iframe src="https://x"></iframe>',
                **_formset_data(),
            },
        )
        page = WikiPage.objects.get(title="Sanitized Page")
        assert "<script>" not in page.body
        assert "<iframe" not in page.body

    def describe_a_wanted_page_parameter():
        def it_creates_the_page_when_the_pk_means_nothing_yet(client: Client):
            # WikiWantedPage is spec B's model, so until B lands the pk is ignored and the
            # page is still created — a month-old digest link must never be an error screen.
            _login(client, "create_wanted")
            response = client.post(
                reverse("hub_wiki_create", args=["howto"]),
                {"title": "Wanted Page", "kind": "howto", "body": "", "wanted": "42", **_formset_data()},
            )
            assert response.status_code == 302
            assert WikiPage.objects.filter(title="Wanted Page").exists()

        def it_ignores_a_non_numeric_pk(client: Client):
            _login(client, "create_wanted_junk")
            response = client.post(
                reverse("hub_wiki_create", args=["howto"]),
                {"title": "Junk Wanted", "kind": "howto", "body": "", "wanted": "abc", **_formset_data()},
            )
            assert response.status_code == 302


def describe_editing_a_page():
    def it_403s_a_member_on_an_official_page(client: Client):
        _login(client, "edit_official")
        page = WikiPageFactory(official=True)
        assert client.get(reverse("hub_wiki_edit", args=[page.slug])).status_code == 403

    def it_404s_an_unknown_slug(client: Client):
        _login(client, "edit_404")
        assert client.get(reverse("hub_wiki_edit", args=["nope"])).status_code == 404

    def it_saves_the_edit_and_writes_a_revision(client: Client):
        _login(client, "edit_save")
        page = WikiPageFactory(title="Old Title", body="<p>Old body.</p>")
        response = client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {"title": "New Title", "body": "<p>New body.</p>", **_formset_data()},
        )
        page.refresh_from_db()
        assert response.status_code == 302
        assert page.title == "New Title"
        revision = page.revisions.first()
        assert revision.title == "Old Title"
        assert revision.body == "<p>Old body.</p>"

    def it_never_changes_the_slug_on_a_rename(client: Client):
        _login(client, "edit_slug")
        page = WikiPageFactory(title="Old Title")
        original = page.slug
        client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {"title": "Completely Different", "body": "", **_formset_data()},
        )
        page.refresh_from_db()
        assert page.slug == original

    def it_rejects_a_rename_onto_a_reserved_name(client: Client):
        _login(client, "edit_reserved")
        page = WikiPageFactory(title="Something Fine")
        response = client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {"title": "History", "body": "", **_formset_data()},
        )
        assert response.status_code == 200
        assert b"That name is reserved. Pick another title." in response.content

    def it_warns_a_non_verifier_before_they_drop_the_check(client: Client):
        _login(client, "edit_warn")
        page = WikiPageFactory(verified=True)
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
        assert b"Saving moves it back to Community until someone checks it again." in response.content

    def it_does_not_warn_someone_who_could_verify(client: Client):
        _login(client, "edit_nowarn", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(verified=True, guild=None)
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
        assert b"Saving moves it back to Community" not in response.content

    def describe_the_equipment_select():
        def it_is_absent_for_a_plain_member(client: Client):
            _login(client, "edit_noequip")
            page = WikiPageFactory()
            response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
            assert "equipment" not in response.context["form"].fields

        def it_renders_for_a_moderator_and_links_the_page(client: Client):
            _login(client, "edit_equip", fog_role=Member.FogRole.ADMIN)
            equipment = EquipmentFactory(name="SawStop")
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
            response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
            assert b"Not about a specific tool" in response.content
            client.post(
                reverse("hub_wiki_edit", args=[page.slug]),
                {"title": page.title, "body": "", "equipment": str(equipment.pk), **_formset_data()},
            )
            page.refresh_from_db()
            assert page.equipment == equipment

    def describe_the_quick_answers_editor():
        def it_renders_no_extra_rows_in_edit_mode(client: Client):
            _login(client, "edit_extra0")
            page = WikiPageFactory()
            WikiPageFactFactory(page=page)
            response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
            assert response.context["facts_formset"].total_form_count() == 1

        def it_carries_the_id_field_on_every_row(client: Client):
            _login(client, "edit_idfield")
            page = WikiPageFactory()
            WikiPageFactFactory(page=page)
            response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
            assert b'name="facts-0-id"' in response.content

        def it_deletes_a_row_and_keeps_the_others_edits(client: Client):
            _login(client, "edit_delrow")
            page = WikiPageFactory()
            keep = WikiPageFactFactory(page=page, label="Blade", value="10 inch")
            drop = WikiPageFactFactory(page=page, label="Bin", value="Under it")
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(facts=2),
                "facts-INITIAL_FORMS": "2",
                "facts-0-id": str(keep.pk),
                "facts-0-label": "Blade",
                "facts-0-value": "12 inch",
                "facts-0-sort_order": "0",
                "facts-1-id": str(drop.pk),
                "facts-1-label": "Bin",
                "facts-1-value": "Under it",
                "facts-1-sort_order": "1",
                "facts-1-DELETE": "on",
            }
            client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert list(page.facts.values_list("label", "value")) == [("Blade", "12 inch")]

        def it_rejects_two_answers_with_the_same_label(client: Client):
            _login(client, "edit_duplabel")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(facts=2),
                "facts-0-label": "Blade",
                "facts-0-value": "10 inch",
                "facts-0-sort_order": "0",
                "facts-1-label": "Blade",
                "facts-1-value": "12 inch",
                "facts-1-sort_order": "1",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.status_code == 200
            assert b"You have two answers called &#x27;Blade&#x27;. Rename one." in response.content

        def it_caps_the_block_at_eight_rows(client: Client):
            _login(client, "edit_cap")
            page = WikiPageFactory()
            data = {"title": page.title, "body": "", **_formset_data(facts=9)}
            for index in range(9):
                data[f"facts-{index}-label"] = f"Label {index}"
                data[f"facts-{index}-value"] = f"Value {index}"
                data[f"facts-{index}-sort_order"] = str(index)
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.status_code == 200
            assert b"Quick answers work best short. Keep it to eight." in response.content

        def it_skips_a_blank_row_that_sits_above_a_real_one(client: Client):
            _login(client, "edit_blankfirst")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(facts=2),
                "facts-0-label": "",
                "facts-0-value": "",
                "facts-0-sort_order": "0",
                "facts-1-label": "Blade",
                "facts-1-value": "10 inch",
                "facts-1-sort_order": "1",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.status_code == 302
            assert list(page.facts.values_list("label", flat=True)) == ["Blade"]

        def it_leaves_the_formset_rules_alone_when_a_row_is_already_invalid(client: Client):
            _login(client, "edit_rowinvalid")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(facts=1),
                "facts-0-label": "Blade",
                "facts-0-value": "x" * 201,
                "facts-0-sort_order": "0",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.status_code == 200
            assert page.facts.count() == 0

        def it_persists_the_submitted_order(client: Client):
            _login(client, "edit_order")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(facts=2),
                "facts-0-label": "Second",
                "facts-0-value": "b",
                "facts-0-sort_order": "1",
                "facts-1-label": "First",
                "facts-1-value": "a",
                "facts-1-sort_order": "0",
            }
            client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert list(page.facts.values_list("label", flat=True)) == ["First", "Second"]

    def describe_the_attachments_editor():
        def it_accepts_a_link_row(client: Client):
            _login(client, "edit_attach_link")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(attachments=1),
                "attachments-0-label": "Spec sheet",
                "attachments-0-url": "https://example.com/sheet",
                "attachments-0-sort_order": "0",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.status_code == 302
            assert page.attachments.get().label == "Spec sheet"

        def it_records_the_uploader(client: Client):
            user = _login(client, "edit_attach_uploader")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(attachments=1),
                "attachments-0-label": "Spec sheet",
                "attachments-0-url": "https://example.com/sheet",
                "attachments-0-sort_order": "0",
            }
            client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert page.attachments.get().uploaded_by == user.member

        def it_rejects_a_row_with_both_a_file_and_a_link(client: Client):
            _login(client, "edit_attach_both")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(attachments=1),
                "attachments-0-label": "Both",
                "attachments-0-url": "https://example.com/sheet",
                "attachments-0-sort_order": "0",
                "attachments-0-file": SimpleUploadedFile("a.png", tiny_png_bytes(), content_type="image/png"),
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert b"Pick a file or paste a link, not both." in response.content

        def it_rejects_a_row_with_neither(client: Client):
            _login(client, "edit_attach_neither")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(attachments=1),
                "attachments-0-label": "Nothing",
                "attachments-0-sort_order": "0",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert b"Add a file or a link." in response.content

        def it_rejects_a_row_with_no_label(client: Client):
            _login(client, "edit_attach_nolabel")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(attachments=1),
                "attachments-0-url": "https://example.com/sheet",
                "attachments-0-sort_order": "0",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert b"Give it a one line name so people know what it is." in response.content

        def it_ignores_a_cloned_row_the_member_abandoned(client: Client):
            _login(client, "edit_attach_blank")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(attachments=1),
                "attachments-0-label": "",
                "attachments-0-url": "",
                "attachments-0-sort_order": "3",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.status_code == 302
            assert page.attachments.count() == 0

        def it_rejects_a_disallowed_extension(client: Client):
            _login(client, "edit_attach_badext")
            page = WikiPageFactory()
            data = {
                "title": page.title,
                "body": "",
                **_formset_data(attachments=1),
                "attachments-0-label": "Script",
                "attachments-0-sort_order": "0",
                "attachments-0-file": SimpleUploadedFile("evil.exe", b"MZ", content_type="application/exe"),
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.status_code == 200
            assert page.attachments.count() == 0


def describe_autosave():
    def it_saves_the_title_into_a_draft(client: Client):
        user = _login(client, "auto_title")
        page = WikiPageFactory()
        response = client.post(
            reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "Half typed"}
        )
        assert response.status_code == 204
        assert json.loads(response["HX-Trigger"]) == {"wiki-saved": {}}
        assert WikiDraft.objects.get(page=page, author=user.member).title == "Half typed"

    def it_sanitizes_the_body_it_stores(client: Client):
        user = _login(client, "auto_body")
        page = WikiPageFactory()
        client.post(
            reverse("hub_wiki_autosave", args=[page.slug]),
            {"field": "body", "value": "<p>ok</p><script>alert(1)</script>"},
        )
        draft = WikiDraft.objects.get(page=page, author=user.member)
        assert "<script>" not in draft.body

    def it_400s_an_unlisted_field(client: Client):
        _login(client, "auto_badfield")
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "facts-0-label", "value": "x"})
        assert response.status_code == 400

    def it_422s_an_invalid_value_with_an_error_toast(client: Client):
        _login(client, "auto_toolong")
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "x" * 201})
        assert response.status_code == 422
        assert "error" in response["HX-Trigger"]

    def it_upserts_rather_than_making_a_second_draft(client: Client):
        user = _login(client, "auto_upsert")
        page = WikiPageFactory()
        client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "One"})
        client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "Two"})
        assert WikiDraft.objects.filter(page=page, author=user.member).count() == 1

    def it_does_not_raise_while_spec_ds_lock_model_is_absent(client: Client):
        # _refresh_edit_lock's guarded import is the A3-before-D seam; it must be a no-op,
        # never a 500, until spec D lands WikiEditLock.
        _login(client, "auto_lock")
        page = WikiPageFactory()
        assert (
            client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "ok"}).status_code
            == 204
        )

    def it_403s_a_member_on_an_official_page(client: Client):
        _login(client, "auto_official")
        page = WikiPageFactory(official=True)
        response = client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "x"})
        assert response.status_code == 403

    def it_404s_an_unknown_slug(client: Client):
        _login(client, "auto_404")
        response = client.post(reverse("hub_wiki_autosave", args=["nope"]), {"field": "title", "value": "x"})
        assert response.status_code == 404

    def describe_a_page_that_does_not_exist_yet():
        def it_keys_the_draft_on_the_author_and_the_kind(client: Client):
            user = _login(client, "auto_new")
            response = client.post(
                reverse("hub_wiki_new_autosave", args=["machine"]), {"field": "title", "value": "Table Saw"}
            )
            assert response.status_code == 204
            assert WikiDraft.objects.get(page=None, author=user.member, kind="machine").title == "Table Saw"

        def it_404s_an_unknown_starter(client: Client):
            _login(client, "auto_new_badkind")
            response = client.post(reverse("hub_wiki_new_autosave", args=["bogus"]), {"field": "title", "value": "x"})
            assert response.status_code == 404

        def it_403s_an_inactive_member(client: Client):
            _login(client, "auto_new_inactive", status=Member.Status.FORMER)
            response = client.post(reverse("hub_wiki_new_autosave", args=["howto"]), {"field": "title", "value": "x"})
            assert response.status_code == 403

        def it_400s_an_unlisted_field(client: Client):
            _login(client, "auto_new_badfield")
            response = client.post(reverse("hub_wiki_new_autosave", args=["howto"]), {"field": "guild", "value": "1"})
            assert response.status_code == 400

        def it_422s_an_invalid_value(client: Client):
            _login(client, "auto_new_toolong")
            response = client.post(
                reverse("hub_wiki_new_autosave", args=["howto"]), {"field": "title", "value": "x" * 201}
            )
            assert response.status_code == 422


def describe_draft_resume():
    def it_offers_a_draft_newer_than_the_page(client: Client):
        user = _login(client, "resume_offer")
        page = WikiPageFactory()
        WikiDraftFactory(page=page, author=user.member, title="Half typed title")
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
        assert b"You have unsaved changes from" in response.content
        # The form is still populated from the SAVED page — ignoring the card is safe.
        assert response.context["form"]["title"].value() == page.title

    def it_offers_nothing_for_a_draft_older_than_the_page(client: Client):
        user = _login(client, "resume_stale")
        page = WikiPageFactory()
        draft = WikiDraftFactory(page=page, author=user.member)
        WikiDraft.objects.filter(pk=draft.pk).update(updated_at=page.updated_at - timezone.timedelta(minutes=5))
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
        assert b"You have unsaved changes from" not in response.content

    def it_offers_nothing_for_an_empty_draft(client: Client):
        user = _login(client, "resume_empty")
        page = WikiPageFactory()
        WikiDraftFactory(page=page, author=user.member, title="", body="")
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
        assert b"You have unsaved changes from" not in response.content

    def it_populates_from_the_draft_with_draft_use(client: Client):
        user = _login(client, "resume_use")
        page = WikiPageFactory()
        WikiDraftFactory(page=page, author=user.member, title="Draft Title", facts=[{"label": "A", "value": "B"}])
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]), {"draft": "use"})
        assert response.context["form"]["title"].value() == "Draft Title"
        assert b'value="A"' in response.content

    def it_deletes_the_draft_with_draft_fresh(client: Client):
        user = _login(client, "resume_fresh")
        page = WikiPageFactory()
        WikiDraftFactory(page=page, author=user.member)
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]), {"draft": "fresh"})
        assert response.status_code == 302
        assert not WikiDraft.objects.filter(page=page, author=user.member).exists()

    def it_deletes_the_draft_on_a_successful_save(client: Client):
        user = _login(client, "resume_saved")
        page = WikiPageFactory()
        WikiDraftFactory(page=page, author=user.member)
        client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {"title": page.title, "body": "<p>done</p>", **_formset_data()},
        )
        assert not WikiDraft.objects.filter(page=page, author=user.member).exists()

    def describe_a_new_page_draft():
        def it_resumes_on_the_starter_it_was_typed_under(client: Client):
            user = _login(client, "resume_new_use")
            WikiDraftFactory(page=None, author=user.member, kind="howto", title="Half A Guide")
            response = client.get(reverse("hub_wiki_create", args=["howto"]), {"draft": "use"})
            assert response.context["form"].initial["title"] == "Half A Guide"

        def it_offers_the_card_on_a_plain_visit(client: Client):
            user = _login(client, "resume_new_offer")
            WikiDraftFactory(page=None, author=user.member, kind="howto", title="Half A Guide")
            response = client.get(reverse("hub_wiki_create", args=["howto"]))
            assert b"You have unsaved changes from" in response.content

        def it_is_deleted_with_draft_fresh(client: Client):
            user = _login(client, "resume_new_fresh")
            WikiDraftFactory(page=None, author=user.member, kind="howto")
            response = client.get(reverse("hub_wiki_create", args=["howto"]), {"draft": "fresh"})
            assert response.status_code == 302
            assert not WikiDraft.objects.filter(author=user.member, page=None).exists()

        def it_is_deleted_on_a_successful_create(client: Client):
            user = _login(client, "resume_new_saved")
            WikiDraftFactory(page=None, author=user.member, kind="howto")
            client.post(
                reverse("hub_wiki_create", args=["howto"]),
                {"title": "A Finished Guide", "kind": "howto", "body": "", **_formset_data()},
            )
            assert not WikiDraft.objects.filter(author=user.member, page=None).exists()


def describe_still_accurate():
    def it_answers_200_with_the_swap_and_a_toast(client: Client):
        user = _login(client, "confirm_ok")
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_confirm", args=[page.slug]))
        assert response.status_code == 200
        assert b'id="wiki-status"' in response.content
        assert b'id="wiki-confirm-cue" hx-swap-oob="true"' in response.content
        assert "Marked as checked today" in response["HX-Trigger"]
        page.refresh_from_db()
        assert page.last_checked_by == user.member

    def it_renders_the_confirm_modal_as_an_htmx_post(client: Client):
        # A plain-POST form here would full-page reload instead of swapping the pill, which
        # is exactly the "did that work?" failure the one-tap action exists to avoid.
        _login(client, "confirm_hx")
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert f'hx-post="{reverse("hub_wiki_confirm", args=[page.slug])}"'.encode() in response.content
        assert b'hx-target="#wiki-status"' in response.content

    def it_clears_the_out_of_date_pill(client: Client):
        _login(client, "confirm_clears")
        page = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
        WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - timezone.timedelta(days=800))
        response = client.post(reverse("hub_wiki_confirm", args=[page.slug]))
        assert b"Out of date" not in response.content
        assert b"Community" in response.content

    def it_409s_on_an_archived_page(client: Client):
        _login(client, "confirm_archived")
        page = WikiPageFactory(archived=True)
        response = client.post(reverse("hub_wiki_confirm", args=[page.slug]))
        assert response.status_code == 409
        assert "There is nothing to confirm" in response["HX-Trigger"]

    def it_403s_an_inactive_member(client: Client):
        _login(client, "confirm_inactive", status=Member.Status.FORMER)
        page = WikiPageFactory()
        assert client.post(reverse("hub_wiki_confirm", args=[page.slug])).status_code == 403

    def it_404s_an_unknown_slug(client: Client):
        _login(client, "confirm_404")
        assert client.post(reverse("hub_wiki_confirm", args=["nope"])).status_code == 404

    def it_writes_no_revision(client: Client):
        _login(client, "confirm_norev")
        page = WikiPageFactory()
        client.post(reverse("hub_wiki_confirm", args=[page.slug]))
        assert page.revisions.count() == 0


def describe_the_quick_tip():
    def it_appends_the_tip_and_swaps_the_body(client: Client):
        _login(client, "tip_ok")
        page = WikiPageFactory(body="<p>How it works.</p>")
        response = client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": "Keep the guard on."})
        assert response.status_code == 200
        assert b'id="wiki-body"' in response.content
        page.refresh_from_db()
        assert "Keep the guard on." in page.body
        assert "Tip added. Thanks." in response["HX-Trigger"]

    def it_escapes_what_the_member_typed(client: Client):
        _login(client, "tip_escape")
        page = WikiPageFactory()
        client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": "<script>alert(1)</script>"})
        page.refresh_from_db()
        assert "<script>" not in page.body

    def it_drops_a_green_check(client: Client):
        _login(client, "tip_drops")
        page = WikiPageFactory(verified=True)
        client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": "A tip."})
        page.refresh_from_db()
        assert page.status == WikiPage.Status.COMMUNITY

    def it_warns_before_the_member_commits(client: Client):
        _login(client, "tip_warns")
        page = WikiPageFactory(verified=True)
        response = client.get(page.get_absolute_url())
        assert b"Adding a tip moves it back to Community until someone checks it." in response.content

    def it_422s_an_empty_tip(client: Client):
        _login(client, "tip_empty")
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": ""})
        assert response.status_code == 422
        assert "Write a sentence first." in response["HX-Trigger"]

    def it_422s_a_full_page_with_an_edit_hint(client: Client):
        _login(client, "tip_full")
        page = WikiPageFactory(body="x" * 60_000)
        response = client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": "A tip."})
        assert response.status_code == 422
        assert "This page is full" in response["HX-Trigger"]

    def it_403s_a_member_on_an_official_page(client: Client):
        _login(client, "tip_official")
        page = WikiPageFactory(official=True)
        response = client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": "A tip."})
        assert response.status_code == 403

    def it_404s_an_unknown_slug(client: Client):
        _login(client, "tip_404")
        assert client.post(reverse("hub_wiki_quick_tip", args=["nope"]), {"tip": "x"}).status_code == 404

    def it_renders_the_field_through_the_form_component(client: Client):
        _login(client, "tip_field")
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert b"pl-form-group" in response.content
        assert b'name="tip"' in response.content


def describe_the_quick_photo():
    def it_attaches_the_photo_and_swaps_the_card(client: Client):
        user = _login(client, "photo_ok")
        page = WikiPageFactory()
        response = client.post(
            reverse("hub_wiki_quick_photo", args=[page.slug]),
            {
                "photo": SimpleUploadedFile("blade.png", tiny_png_bytes(), content_type="image/png"),
                "caption": "The right blade",
            },
        )
        assert response.status_code == 200
        assert b'id="wiki-attachments"' in response.content
        attachment = page.attachments.get()
        assert attachment.label == "The right blade"
        assert attachment.uploaded_by == user.member
        assert "Photo added. Thanks." in response["HX-Trigger"]

    def it_never_drops_a_green_check(client: Client):
        # The asymmetry with a tip is deliberate: the cheapest contribution is the one with
        # no consequences, because on a phone it is the only one most people will make.
        _login(client, "photo_keeps")
        page = WikiPageFactory(verified=True)
        client.post(
            reverse("hub_wiki_quick_photo", args=[page.slug]),
            {
                "photo": SimpleUploadedFile("blade.png", tiny_png_bytes(), content_type="image/png"),
                "caption": "The right blade",
            },
        )
        page.refresh_from_db()
        assert page.status == WikiPage.Status.GUILD_VERIFIED

    def it_reaches_the_search_text(client: Client):
        _login(client, "photo_search")
        page = WikiPageFactory()
        client.post(
            reverse("hub_wiki_quick_photo", args=[page.slug]),
            {
                "photo": SimpleUploadedFile("blade.png", tiny_png_bytes(), content_type="image/png"),
                "caption": "Riving knife",
            },
        )
        page.refresh_from_db()
        assert "Riving knife" in page.search_text

    def it_422s_a_document(client: Client):
        _login(client, "photo_pdf")
        page = WikiPageFactory()
        response = client.post(
            reverse("hub_wiki_quick_photo", args=[page.slug]),
            {
                "photo": SimpleUploadedFile("sheet.pdf", b"%PDF-1.4", content_type="application/pdf"),
                "caption": "A document",
            },
        )
        assert response.status_code == 422
        assert "not an image" in response["HX-Trigger"]

    def it_422s_a_missing_caption(client: Client):
        _login(client, "photo_nocaption")
        page = WikiPageFactory()
        response = client.post(
            reverse("hub_wiki_quick_photo", args=[page.slug]),
            {"photo": SimpleUploadedFile("blade.png", tiny_png_bytes(), content_type="image/png")},
        )
        assert response.status_code == 422

    def it_403s_a_member_on_an_official_page(client: Client):
        _login(client, "photo_official")
        page = WikiPageFactory(official=True)
        response = client.post(reverse("hub_wiki_quick_photo", args=[page.slug]), {"caption": "x"})
        assert response.status_code == 403

    def it_404s_an_unknown_slug(client: Client):
        _login(client, "photo_404")
        assert client.post(reverse("hub_wiki_quick_photo", args=["nope"]), {"caption": "x"}).status_code == 404


def describe_the_editor_image_upload():
    def it_returns_a_url_under_the_wiki_prefix(client: Client):
        _login(client, "img_ok")
        page = WikiPageFactory()
        response = client.post(
            reverse("hub_wiki_image_upload", args=[page.slug]),
            {"image": SimpleUploadedFile("shot.png", tiny_png_bytes(), content_type="image/png")},
        )
        assert response.status_code == 200
        assert "/wiki/body/" in response.json()["url"]

    def it_400s_with_no_file(client: Client):
        _login(client, "img_nofile")
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_image_upload", args=[page.slug]))
        assert response.status_code == 400
        assert response.json()["error"] == "No file provided."

    def it_400s_an_oversize_file(client: Client, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 10
        _login(client, "img_big")
        page = WikiPageFactory()
        response = client.post(
            reverse("hub_wiki_image_upload", args=[page.slug]),
            {"image": SimpleUploadedFile("shot.png", tiny_png_bytes(), content_type="image/png")},
        )
        assert response.status_code == 400
        assert "or smaller" in response.json()["error"]

    def it_400s_something_that_is_not_a_photo(client: Client):
        _login(client, "img_notimage")
        page = WikiPageFactory()
        response = client.post(
            reverse("hub_wiki_image_upload", args=[page.slug]),
            {"image": SimpleUploadedFile("shot.png", b"not a png at all", content_type="image/png")},
        )
        assert response.status_code == 400

    def it_403s_a_member_on_an_official_page(client: Client):
        _login(client, "img_official")
        page = WikiPageFactory(official=True)
        response = client.post(reverse("hub_wiki_image_upload", args=[page.slug]))
        assert response.status_code == 403

    def it_404s_an_unknown_slug(client: Client):
        _login(client, "img_404")
        assert client.post(reverse("hub_wiki_image_upload", args=["nope"])).status_code == 404


def describe_the_drafts_list():
    def it_shows_the_members_own_drafts(client: Client):
        user = _login(client, "drafts_own")
        WikiDraftFactory(author=user.member, title="Mine")
        WikiDraftFactory(author=MemberFactory(), title="Somebody Elses")
        response = client.get(reverse("hub_wiki_drafts"))
        assert b"Mine" in response.content
        assert b"Somebody Elses" not in response.content

    def it_shows_the_empty_state(client: Client):
        _login(client, "drafts_empty")
        response = client.get(reverse("hub_wiki_drafts"))
        assert b"No drafts. Your title and writing are saved here automatically as you type." in response.content

    def it_says_what_actually_autosaves(client: Client):
        user = _login(client, "drafts_copy")
        WikiDraftFactory(author=user.member)
        response = client.get(reverse("hub_wiki_drafts"))
        assert b"Quick answers and files are saved when you press Save." in response.content

    def it_explains_a_page_the_safety_gate_is_holding(client: Client):
        user = _login(client, "drafts_held")
        WikiPageFactory(title="Held Page", is_published=False, created_by=user.member)
        response = client.get(reverse("hub_wiki_drafts"))
        assert b"Waiting for a guild lead to publish it." in response.content

    def it_discards_only_the_members_own_draft(client: Client):
        user = _login(client, "drafts_discard")
        mine = WikiDraftFactory(author=user.member)
        theirs = WikiDraftFactory(author=MemberFactory())
        assert client.post(reverse("hub_wiki_draft_discard", args=[theirs.pk])).status_code == 404
        response = client.post(reverse("hub_wiki_draft_discard", args=[mine.pk]))
        assert response.status_code == 302
        assert not WikiDraft.objects.filter(pk=mine.pk).exists()


def describe_the_card_partial_contract():
    def it_takes_an_actions_partial_slot_for_spec_b(client: Client):
        # Spec B's compact Verify control renders into this slot; without it B would have
        # nowhere to put a Verify button but a second card shape.
        from django.template.loader import render_to_string

        page = WikiPageFactory(title="Slotted Page")
        html = render_to_string(
            "hub/partials/_wiki_card.html",
            {"page": page, "actions_partial": "hub/partials/_wiki_byline.html"},
        )
        assert 'class="pl-wp-card__actions"' in html
        assert "versions saved" in html or "version saved" in html

    def it_replaces_the_lead_line_with_a_snippet(client: Client):
        from django.template.loader import render_to_string

        page = WikiPageFactory(title="Snippy", body="A long lead line nobody wants here.")
        page.rebuild_search_text()
        page.save()
        html = render_to_string("hub/partials/_wiki_card.html", {"page": page, "snippet": "<mark>hit</mark> context"})
        assert "<mark>hit</mark>" in html
        assert "A long lead line nobody wants here." not in html

    def it_renders_a_source_chip_when_one_is_passed(client: Client):
        from django.template.loader import render_to_string

        html = render_to_string("hub/partials/_wiki_card.html", {"page": WikiPageFactory(), "source_chip": "Help"})
        assert "pl-wp-card__source" in html
        assert ">Help<" in html


def describe_attachments_partial():
    def it_renders_photos_and_files_apart(client: Client):
        _login(client, "attach_split")
        page = WikiPageFactory()
        WikiAttachmentFactory(page=page, label="A Link")
        response = client.get(page.get_absolute_url())
        assert b"A Link" in response.content
        assert b"pl-wp-photos" not in response.content


def describe_the_fact_model_rows():
    def it_orders_by_sort_order_then_pk(db):
        page = WikiPageFactory()
        second = WikiPageFactFactory(page=page, label="Second", sort_order=1)
        first = WikiPageFactFactory(page=page, label="First", sort_order=0)
        assert list(WikiPageFact.objects.filter(page=page)) == [first, second]


def describe_the_cross_spec_seams():
    """A and B/D merge separately, so A's guarded calls need proof they still fire."""

    def it_refreshes_spec_ds_edit_lock_once_the_model_exists(client: Client, monkeypatch):
        import membership.models as models_module

        calls: list[tuple[object, object]] = []

        class FakeEditLock:
            @staticmethod
            def refresh(page: object, member: object) -> None:
                calls.append((page, member))

        monkeypatch.setattr(models_module, "WikiEditLock", FakeEditLock, raising=False)
        user = _login(client, "seam_lock")
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "x"})
        assert response.status_code == 204
        assert calls == [(page, user.member)]

    def it_fulfils_spec_bs_wanted_page_once_the_model_exists(client: Client, monkeypatch):
        import membership.models as models_module

        fulfilled: list[object] = []

        class FakeWanted:
            pk = 7

            def fulfil(self, page: object) -> bool:
                fulfilled.append(page)
                return True

        class FakeManager:
            def filter(self, **kwargs: object) -> "FakeManager":
                self._match = kwargs.get("pk") == 7
                return self

            def first(self) -> object | None:
                return FakeWanted() if self._match else None

        class FakeWantedPage:
            objects = FakeManager()

        monkeypatch.setattr(models_module, "WikiWantedPage", FakeWantedPage, raising=False)
        _login(client, "seam_wanted")
        response = client.post(
            reverse("hub_wiki_create", args=["howto"]),
            {"title": "Fulfilled Page", "kind": "howto", "body": "", "wanted": "7", **_formset_data()},
        )
        assert response.status_code == 302
        assert len(fulfilled) == 1

    def it_ignores_a_wanted_pk_that_no_longer_exists(client: Client, monkeypatch):
        import membership.models as models_module

        class FakeManager:
            def filter(self, **kwargs: object) -> "FakeManager":
                return self

            def first(self) -> object | None:
                return None

        class FakeWantedPage:
            objects = FakeManager()

        monkeypatch.setattr(models_module, "WikiWantedPage", FakeWantedPage, raising=False)
        _login(client, "seam_wanted_gone")
        response = client.post(
            reverse("hub_wiki_create", args=["howto"]),
            {"title": "Still Created", "kind": "howto", "body": "", "wanted": "9", **_formset_data()},
        )
        assert response.status_code == 302
        assert WikiPage.objects.filter(title="Still Created").exists()


def describe_the_remaining_edges():
    def it_ignores_a_guild_slug_the_member_may_not_scope_to(client: Client):
        user = _login(client, "edge_badscope")
        mine = GuildFactory(name="Mine", slug="mine")
        GuildMembershipFactory(guild=mine, member=user.member)
        GuildFactory(name="Somebody Elses", slug="somebody-elses")
        response = client.get(reverse("hub_wiki_create", args=["howto"]), {"guild": "somebody-elses"})
        assert response.context["form"].initial["guild"] is None

    def it_reloads_cleanly_when_draft_fresh_finds_no_draft(client: Client):
        _login(client, "edge_freshnodraft")
        page = WikiPageFactory()
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]), {"draft": "fresh"})
        assert response.status_code == 200

    def it_shows_a_moderator_the_archived_pages_refusal(client: Client):
        _login(client, "edge_archived_edit", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(archived=True)
        response = client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {"title": "New Title", "body": "", **_formset_data()},
        )
        assert response.status_code == 200
        assert b"This page is archived." in response.content

    def it_deletes_an_attachment_row_and_keeps_the_uploader_on_the_others(client: Client):
        _login(client, "edge_attach_delete")
        page = WikiPageFactory()
        keeper = MemberFactory()
        keep = WikiAttachmentFactory(page=page, label="Keep Me", uploaded_by=keeper)
        drop = WikiAttachmentFactory(page=page, label="Drop Me")
        data = {
            "title": page.title,
            "body": "",
            **_formset_data(attachments=2),
            "attachments-INITIAL_FORMS": "2",
            "attachments-0-id": str(keep.pk),
            "attachments-0-label": "Keep Me",
            "attachments-0-url": keep.url,
            "attachments-0-sort_order": "0",
            "attachments-1-id": str(drop.pk),
            "attachments-1-label": "Drop Me",
            "attachments-1-url": drop.url,
            "attachments-1-sort_order": "1",
            "attachments-1-DELETE": "on",
        }
        client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
        remaining = page.attachments.get()
        assert remaining.label == "Keep Me"
        # The original uploader is never overwritten by whoever happened to save the page.
        assert remaining.uploaded_by == keeper

    def it_stamps_the_uploader_only_on_a_row_that_has_none(client: Client):
        user = _login(client, "edge_attach_stamp")
        page = WikiPageFactory()
        original = MemberFactory()
        existing = WikiAttachmentFactory(page=page, label="Already Mine", uploaded_by=original)
        data = {
            "title": page.title,
            "body": "",
            **_formset_data(attachments=2),
            "attachments-INITIAL_FORMS": "1",
            "attachments-0-id": str(existing.pk),
            "attachments-0-label": "Already Mine, Renamed",
            "attachments-0-url": existing.url,
            "attachments-0-sort_order": "0",
            "attachments-1-label": "Brand New",
            "attachments-1-url": "https://example.com/new",
            "attachments-1-sort_order": "1",
        }
        client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
        assert page.attachments.get(label="Already Mine, Renamed").uploaded_by == original
        assert page.attachments.get(label="Brand New").uploaded_by == user.member

    def it_403s_the_drafts_screens_for_an_account_with_no_member_row(client: Client):
        user = _login(client, "edge_nomember")
        draft = WikiDraftFactory(author=user.member)
        user.member.delete()
        assert client.get(reverse("hub_wiki_drafts")).status_code == 403
        assert client.post(reverse("hub_wiki_draft_discard", args=[draft.pk])).status_code == 403


def describe_the_review_round_fixes():
    """One spec per defect an independent review of PR #340 found."""

    def describe_the_quick_tip_target():
        def it_exists_on_a_page_with_no_body_yet(client: Client):
            # The stub-page-first flow: a seeded machine page has no body, and the first
            # tip lands right here. With the id only on the prose branch htmx aborted on
            # targetError, so the tip saved, the toast fired, and nothing moved on screen.
            _login(client, "tip_target_empty")
            page = WikiPageFactory(body="")
            response = client.get(page.get_absolute_url())
            assert b'id="wiki-body"' in response.content
            assert b"Nobody has written this one yet." in response.content

        def it_swaps_the_prose_in_on_the_first_tip(client: Client):
            _login(client, "tip_target_first")
            page = WikiPageFactory(body="")
            response = client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": "Keep the guard on."})
            assert response.status_code == 200
            assert b'id="wiki-body"' in response.content
            assert b"Keep the guard on." in response.content
            assert b"Nobody has written this one yet." not in response.content

    def describe_create_mode_draft_resume():
        def it_keeps_the_starter_prompts(client: Client):
            # Autosave's allowlist is title and body, so a new-page draft's facts are
            # ALWAYS empty. Treating that as authoritative rendered zero prompt rows, so
            # "Use My Draft" produced a promptless form while a plain visit did not.
            user = _login(client, "draft_prompts")
            WikiDraftFactory(page=None, author=user.member, kind="machine", title="Half A Saw")
            response = client.get(reverse("hub_wiki_create", args=["machine"]), {"draft": "use"})
            assert response.context["form"].initial["title"] == "Half A Saw"
            assert response.context["facts_formset"].total_form_count() == 4
            assert b"Blade or bit" in response.content

        def it_prefers_the_drafts_own_rows_when_it_has_any(client: Client):
            user = _login(client, "draft_ownrows")
            WikiDraftFactory(
                page=None,
                author=user.member,
                kind="machine",
                title="Half A Saw",
                facts=[{"label": "Typed", "value": "By hand"}],
            )
            response = client.get(reverse("hub_wiki_create", args=["machine"]), {"draft": "use"})
            assert response.context["facts_formset"].total_form_count() == 1
            assert b"Typed" in response.content

    def describe_an_ignored_list_editor_row():
        def it_never_prints_a_database_constraint_name(client: Client):
            # ModelForm._post_clean runs AFTER clean() and re-adds model errors, so a row
            # whose errors clean() had just cleared came back carrying the raw
            # ck_wikiattach_file_xor_url constraint name — visible the moment the
            # submission bounced for some other reason.
            _login(client, "ignored_row_leak")
            page = WikiPageFactory(title="Something Fine")
            data = {
                "title": "History",  # reserved, so the form bounces and re-renders bound
                "body": "",
                **_formset_data(attachments=1, facts=1),
                "attachments-0-label": "",
                "attachments-0-url": "",
                "attachments-0-sort_order": "0",
                "facts-0-label": "",
                "facts-0-value": "",
                "facts-0-sort_order": "0",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.status_code == 200
            assert b"That name is reserved. Pick another title." in response.content
            assert b"ck_wikiattach_file_xor_url" not in response.content
            assert b"Constraint" not in response.content
            assert b"This field cannot be blank." not in response.content

        def it_reports_nothing_at_all_for_the_ignored_rows(client: Client):
            _login(client, "ignored_row_clean")
            page = WikiPageFactory(title="Also Fine")
            data = {
                "title": "History",
                "body": "",
                **_formset_data(attachments=1),
                "attachments-0-label": "",
                "attachments-0-url": "",
                "attachments-0-sort_order": "0",
            }
            response = client.post(reverse("hub_wiki_edit", args=[page.slug]), data)
            assert response.context["attachments_formset"].forms[0].errors == {}

    def describe_the_creating_revision():
        def it_snapshots_the_quick_answers_it_was_created_with(client: Client):
            # create_page writes revision one; the formsets used to commit the rows after
            # it, so version one recorded facts=[] and a spec D revert would restore a
            # page state that never existed.
            _login(client, "rev_snapshot")
            data = {
                "title": "Snapshot Saw",
                "kind": "machine",
                "body": "<p>Body.</p>",
                **_formset_data(facts=2),
                "facts-0-label": "Blade",
                "facts-0-value": "10 inch",
                "facts-0-sort_order": "0",
                "facts-1-label": "Max width",
                "facts-1-value": "24 inch",
                "facts-1-sort_order": "1",
            }
            client.post(reverse("hub_wiki_create", args=["machine"]), data)
            page = WikiPage.objects.get(title="Snapshot Saw")
            assert page.revisions.count() == 1
            assert page.revisions.first().facts == [
                {"label": "Blade", "value": "10 inch"},
                {"label": "Max width", "value": "24 inch"},
            ]

        def it_honors_the_submitted_sort_order(client: Client):
            _login(client, "rev_order")
            data = {
                "title": "Ordered Saw",
                "kind": "machine",
                "body": "",
                **_formset_data(facts=2),
                "facts-0-label": "Second",
                "facts-0-value": "b",
                "facts-0-sort_order": "1",
                "facts-1-label": "First",
                "facts-1-value": "a",
                "facts-1-sort_order": "0",
            }
            client.post(reverse("hub_wiki_create", args=["machine"]), data)
            page = WikiPage.objects.get(title="Ordered Saw")
            assert list(page.facts.values_list("label", flat=True)) == ["First", "Second"]
            assert [row["label"] for row in page.revisions.first().facts] == ["First", "Second"]

        def it_snapshots_nothing_when_no_answers_were_filled_in(client: Client):
            _login(client, "rev_noanswers")
            data = {
                "title": "Bare Guide",
                "kind": "howto",
                "body": "",
                **_formset_data(facts=1),
                "facts-0-label": "Tools needed",
                "facts-0-value": "",
                "facts-0-sort_order": "0",
            }
            client.post(reverse("hub_wiki_create", args=["howto"]), data)
            page = WikiPage.objects.get(title="Bare Guide")
            assert page.facts.count() == 0
            assert page.revisions.first().facts == []
