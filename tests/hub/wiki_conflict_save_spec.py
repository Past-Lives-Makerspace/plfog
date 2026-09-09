"""BDD specs for the non-destructive conflict save (spec D §5.5, §6.10, D7).

Nothing the member typed exists anywhere but in a durable database row before they see a
single pixel of the conflict screen. That ordering is the entire feature.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from membership.models import Member, WikiPage, WikiRevision
from tests.hub.wiki_mod_helpers import enable_wiki, login
from tests.membership.factories import GuildFactory, MemberFactory, WikiPageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


def _post(client: Client, page: WikiPage, *, body: str, base_revision: str, title: str = "") -> object:
    return client.post(
        reverse("hub_wiki_edit", args=[page.slug]),
        {
            "title": title or page.title,
            "body": body,
            "base_revision": base_revision,
            "facts-TOTAL_FORMS": "0",
            "facts-INITIAL_FORMS": "0",
            "facts-MIN_NUM_FORMS": "0",
            "facts-MAX_NUM_FORMS": "1000",
            "attachments-TOTAL_FORMS": "0",
            "attachments-INITIAL_FORMS": "0",
            "attachments-MIN_NUM_FORMS": "0",
            "attachments-MAX_NUM_FORMS": "1000",
        },
    )


def _page_somebody_else_saved(author: Member) -> tuple[WikiPage, int]:
    """A page, plus the ``base_revision`` a stale tab would still be carrying."""
    page = WikiPage.objects.create_page(
        title="Bandsaw", kind=WikiPage.Kind.MACHINE, author=author, body="<p>Original.</p>"
    )
    stale_base = page.revisions.first().pk
    page.apply_edit(editor=author, editor_may_verify=True, title="Bandsaw", body="<p>Dana's version.</p>")
    return page, stale_base


def describe_a_conflicting_save():
    def it_parks_the_submitted_text_verbatim_and_changes_nothing(client: Client):
        user = login(client, "conf_loser")
        page, stale_base = _page_somebody_else_saved(MemberFactory(full_legal_name="Dana Kim"))
        response = _post(client, page, body="<p>My version.</p>", base_revision=str(stale_base))
        assert response.status_code == 302
        draft = WikiRevision.objects.get(page=page, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        assert draft.body == "<p>My version.</p>"
        assert draft.author == user.member
        assert draft.note == "Unmerged: someone else saved first"
        page.refresh_from_db()
        assert page.body == "<p>Dana's version.</p>"
        assert response["Location"] == reverse("hub_wiki_conflict", args=[page.slug, draft.pk])

    def it_saves_normally_when_the_base_revision_is_current(client: Client):
        login(client, "conf_clean")
        author = MemberFactory()
        page, _stale = _page_somebody_else_saved(author)
        current = page.revisions.exclude(kind=WikiRevision.Kind.CONFLICT_DRAFT).first().pk
        _post(client, page, body="<p>Mine, cleanly.</p>", base_revision=str(current))
        page.refresh_from_db()
        assert page.body == "<p>Mine, cleanly.</p>"
        assert not WikiRevision.objects.filter(kind=WikiRevision.Kind.CONFLICT_DRAFT).exists()

    def it_saves_normally_when_no_base_revision_was_carried(client: Client):
        login(client, "conf_nobase")
        page = WikiPageFactory()
        _post(client, page, body="<p>Fresh save.</p>", base_revision="")
        page.refresh_from_db()
        assert page.body == "<p>Fresh save.</p>"


def describe_the_conflict_screen():
    def it_renders_the_live_page_and_not_the_head_revision(client: Client):
        # A stores the PRE-edit snapshot, so the newest revision row is the version BEFORE
        # the other person's save — the exact one this screen exists to reconcile away
        # from. A test that only checks "two bodies render" passes on the wrong one.
        login(client, "conf_right_card")
        page, stale_base = _page_somebody_else_saved(MemberFactory(full_legal_name="Dana Kim"))
        _post(client, page, body="<p>My version.</p>", base_revision=str(stale_base))
        draft = WikiRevision.objects.get(page=page, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        body = client.get(reverse("hub_wiki_conflict", args=[page.slug, draft.pk])).content
        assert b"Dana&#x27;s version." in body or b"Dana's version." in body
        assert b"Original." not in body
        assert b"My version." in body
        assert b"Your text is saved. Nothing you wrote has been lost." in body

    def it_names_who_saved_last(client: Client):
        login(client, "conf_names")
        page, stale_base = _page_somebody_else_saved(MemberFactory(full_legal_name="Dana Kim"))
        _post(client, page, body="<p>My version.</p>", base_revision=str(stale_base))
        draft = WikiRevision.objects.get(page=page, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        assert b"Saved by Dana Kim" in client.get(reverse("hub_wiki_conflict", args=[page.slug, draft.pk])).content

    def it_links_the_draft_into_the_history(client: Client):
        # The footnote's promise has to be a link that works, or "your version stays in
        # this page's history" is a promise the UI does not keep.
        login(client, "conf_history_link")
        page, stale_base = _page_somebody_else_saved(MemberFactory())
        _post(client, page, body="<p>My version.</p>", base_revision=str(stale_base))
        draft = WikiRevision.objects.get(page=page, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        body = client.get(reverse("hub_wiki_conflict", args=[page.slug, draft.pk])).content
        assert f"{reverse('hub_wiki_history', args=[page.slug])}?revision={draft.pk}".encode() in body

    def it_says_so_when_it_has_already_been_sorted_out(client: Client):
        user = login(client, "conf_done")
        page = WikiPageFactory()
        draft = WikiRevision.objects.create(
            page=page,
            author=user.member,
            kind=WikiRevision.Kind.CONFLICT_DRAFT,
            title=page.title,
            body=page.body,
            status=page.status,
        )
        assert (
            b"already been sorted out" in client.get(reverse("hub_wiki_conflict", args=[page.slug, draft.pk])).content
        )

    def it_refuses_a_non_author_who_cannot_moderate(client: Client):
        login(client, "conf_403")
        page = WikiPageFactory(guild=GuildFactory())
        draft = WikiRevision.objects.create(
            page=page,
            author=MemberFactory(),
            kind=WikiRevision.Kind.CONFLICT_DRAFT,
            title=page.title,
            body="<p>Somebody else's text.</p>",
            status=page.status,
        )
        assert client.get(reverse("hub_wiki_conflict", args=[page.slug, draft.pk])).status_code == 403

    def it_404s_on_an_ordinary_revision(client: Client):
        login(client, "conf_404")
        page, _stale = _page_somebody_else_saved(MemberFactory())
        ordinary = page.revisions.exclude(kind=WikiRevision.Kind.CONFLICT_DRAFT).first()
        assert client.get(reverse("hub_wiki_conflict", args=[page.slug, ordinary.pk])).status_code == 404


def describe_keeping_my_version():
    def it_goes_through_the_one_save_method_and_leaves_the_draft_in_place(client: Client):
        user = login(client, "conf_keep")
        page, stale_base = _page_somebody_else_saved(MemberFactory())
        _post(client, page, body="<p>My version.</p>", base_revision=str(stale_base))
        draft = WikiRevision.objects.get(page=page, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        before = page.revisions.count()
        response = client.post(reverse("hub_wiki_conflict_keep", args=[page.slug, draft.pk]))
        assert response.status_code == 302
        page.refresh_from_db()
        assert page.body == "<p>My version.</p>"
        assert page.revisions.count() == before + 1
        assert page.revisions.filter(pk=draft.pk).exists()
        assert page.revisions.filter(note="Resolved an edit conflict").exists()
        assert page.revisions.get(note="Resolved an edit conflict").author == user.member

    def it_restores_the_drafts_facts_too(client: Client):
        user = login(client, "conf_keep_facts")
        page = WikiPageFactory()
        draft = WikiRevision.objects.create(
            page=page,
            author=user.member,
            kind=WikiRevision.Kind.CONFLICT_DRAFT,
            title=page.title,
            body="<p>My version with facts.</p>",
            facts=[{"label": "Blade", "value": "Half inch"}],
            status=page.status,
        )
        client.post(reverse("hub_wiki_conflict_keep", args=[page.slug, draft.pk]))
        page.refresh_from_db()
        assert [(f.label, f.value) for f in page.facts.all()] == [("Blade", "Half inch")]
        assert "Half inch" in page.search_text

    def it_refuses_somebody_elses_draft(client: Client):
        login(client, "conf_keep_403")
        page = WikiPageFactory(guild=GuildFactory())
        draft = WikiRevision.objects.create(
            page=page,
            author=MemberFactory(),
            kind=WikiRevision.Kind.CONFLICT_DRAFT,
            title=page.title,
            body="<p>Not mine.</p>",
            status=page.status,
        )
        assert client.post(reverse("hub_wiki_conflict_keep", args=[page.slug, draft.pk])).status_code == 403

    def it_lets_a_moderator_apply_somebody_elses_draft(client: Client):
        login(client, "conf_keep_mod", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        draft = WikiRevision.objects.create(
            page=page,
            author=MemberFactory(),
            kind=WikiRevision.Kind.CONFLICT_DRAFT,
            title=page.title,
            body="<p>Somebody else's good text.</p>",
            status=page.status,
        )
        client.post(reverse("hub_wiki_conflict_keep", args=[page.slug, draft.pk]))
        page.refresh_from_db()
        assert page.body == "<p>Somebody else's good text.</p>"


def describe_keeping_their_version():
    def it_writes_nothing_and_deletes_nothing(client: Client):
        login(client, "conf_theirs")
        page, stale_base = _page_somebody_else_saved(MemberFactory())
        _post(client, page, body="<p>My version.</p>", base_revision=str(stale_base))
        draft = WikiRevision.objects.get(page=page, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        before = page.revisions.count()
        # "Keep Their Version" is a plain link back to the page — no POST at all.
        client.get(page.get_absolute_url())
        page.refresh_from_db()
        assert page.body == "<p>Dana's version.</p>"
        assert page.revisions.count() == before
        assert page.revisions.filter(pk=draft.pk).exists()


def describe_opening_the_editor_with_both():
    def it_prefills_the_live_page_the_marker_and_the_draft(client: Client):
        login(client, "conf_merge")
        page, stale_base = _page_somebody_else_saved(MemberFactory())
        _post(client, page, body="<p>My version.</p>", base_revision=str(stale_base))
        draft = WikiRevision.objects.get(page=page, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        body = client.get(reverse("hub_wiki_edit", args=[page.slug]), {"merge": draft.pk}).content
        assert b"--- your version ---" in body
        assert b"My version." in body
