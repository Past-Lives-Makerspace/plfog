"""BDD specs for the official note (spec D §6.6, D1).

Three fields on the page rather than a model, because the note must be revert-immune: in
the body a revert would silently destroy staff copy, and in a child model it would invite
a stack of notes, which is a comment thread wearing a hat.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from core.models import SiteActivity
from membership.models import Member, WikiError, WikiRevision
from tests.hub.wiki_mod_helpers import enable_wiki, login, login_lead
from tests.membership.factories import GuildFactory, MemberFactory, WikiPageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


NOTE = "The 12in blade is out of service until the arbor is replaced."


def describe_setting_the_note():
    def it_saves_and_swaps_the_rendered_region_in(client: Client):
        user = login(client, "note_admin", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_official_note", args=[page.slug]), {"note": NOTE})
        assert response.status_code == 200
        page.refresh_from_db()
        assert page.official_note == NOTE
        assert page.official_note_by == user.member
        assert page.official_note_at is not None
        assert b'id="wiki-official-note"' in response.content
        assert b'hx-swap-oob="true"' in response.content
        header = json.loads(response["HX-Trigger"])
        assert header["showToast"]["message"] == "Official note saved."
        assert header["close-modal"] == "wiki-official-note"

    def it_lets_a_guild_lead_write_one_in_their_own_guild(client: Client):
        _user, guild = login_lead(client, "note_lead")
        page = WikiPageFactory(guild=guild)
        client.post(reverse("hub_wiki_official_note", args=[page.slug]), {"note": NOTE})
        page.refresh_from_db()
        assert page.official_note == NOTE

    def it_writes_no_revision(db):
        # D1, asserted: a revert must never destroy staff copy.
        page = WikiPageFactory()
        before = WikiRevision.objects.filter(page=page).count()
        page.set_official_note(text=NOTE, by=MemberFactory())
        assert WikiRevision.objects.filter(page=page).count() == before

    def it_survives_a_revert(db):
        page = WikiPageFactory(body="<p>Version one.</p>")
        editor = MemberFactory()
        page.apply_edit(editor=editor, editor_may_verify=True, title=page.title, body="<p>Version two.</p>")
        page.set_official_note(text=NOTE, by=editor)
        target = page.revisions.exclude(kind=WikiRevision.Kind.CONFLICT_DRAFT).last()
        page.revert_to(revision=target, by=editor)
        page.refresh_from_db()
        assert page.official_note == NOTE

    def it_logs_an_edit_activity_naming_what_happened(db):
        page = WikiPageFactory()
        page.set_official_note(text=NOTE, by=MemberFactory())
        activity = SiteActivity.objects.filter(kind=SiteActivity.Kind.WIKI_PAGE_EDITED).latest("id")
        assert activity.payload["official_note"] == "set"

    def it_rejects_a_note_over_a_thousand_characters(client: Client):
        login(client, "note_long", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_official_note", args=[page.slug]), {"note": "x" * 1001})
        assert response.status_code == 200
        page.refresh_from_db()
        assert page.official_note == ""
        assert b"1000 characters" in response.content or b"at most" in response.content


def describe_the_note_on_the_page():
    def it_renders_with_its_byline_above_the_member_content(client: Client):
        author = MemberFactory(full_legal_name="Kate Mizuno")
        page = WikiPageFactory(body="<p>Member prose here.</p>")
        page.set_official_note(text=NOTE, by=author)
        login(client, "note_reader")
        body = client.get(page.get_absolute_url()).content
        assert NOTE.encode() in body
        assert b"Added by Kate Mizuno" in body
        assert body.index(NOTE.encode()) < body.index(b"Member prose here.")

    def it_gives_a_member_no_edit_affordance_at_all(client: Client):
        page = WikiPageFactory(guild=GuildFactory())
        page.set_official_note(text=NOTE, by=MemberFactory())
        login(client, "note_member")
        body = client.get(page.get_absolute_url()).content
        assert NOTE.encode() in body
        assert b"Edit Note" not in body
        assert b"Add an Official Note" not in body

    def it_shows_staff_the_control(client: Client):
        page = WikiPageFactory()
        page.set_official_note(text=NOTE, by=MemberFactory())
        login(client, "note_staff", fog_role=Member.FogRole.ADMIN)
        assert b"Edit Note" in client.get(page.get_absolute_url()).content


def describe_removing_the_note():
    def it_blanks_all_three_fields(client: Client):
        user = login(client, "note_remove", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        page.set_official_note(text=NOTE, by=user.member)
        response = client.post(reverse("hub_wiki_official_note_remove", args=[page.slug]))
        assert response.status_code == 200
        page.refresh_from_db()
        assert page.official_note == ""
        assert page.official_note_by is None
        assert page.official_note_at is None

    def it_leaves_the_pages_own_text_alone(db):
        page = WikiPageFactory(body="<p>Member prose here.</p>")
        member = MemberFactory()
        page.set_official_note(text=NOTE, by=member)
        page.clear_official_note(by=member)
        page.refresh_from_db()
        assert page.body == "<p>Member prose here.</p>"

    def it_answers_an_empty_note_with_an_info_toast(client: Client):
        login(client, "note_remove_empty", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_official_note_remove", args=[page.slug]))
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "info"

    def it_raises_on_the_model_when_there_is_nothing_to_clear(db):
        with pytest.raises(WikiError):
            WikiPageFactory().clear_official_note(by=MemberFactory())


def describe_the_gate():
    def it_refuses_a_plain_member_on_post(client: Client):
        login(client, "note_403")
        page = WikiPageFactory(guild=GuildFactory())
        assert client.post(reverse("hub_wiki_official_note", args=[page.slug]), {"note": NOTE}).status_code == 403
        assert client.post(reverse("hub_wiki_official_note_remove", args=[page.slug])).status_code == 403

    def it_refuses_a_lead_of_another_guild(client: Client):
        login_lead(client, "note_other_lead")
        page = WikiPageFactory(guild=GuildFactory())
        assert client.post(reverse("hub_wiki_official_note", args=[page.slug]), {"note": NOTE}).status_code == 403
