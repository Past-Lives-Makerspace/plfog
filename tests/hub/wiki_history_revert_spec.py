"""BDD specs for the page history and revert (spec D §6.8).

Two things this file exists to pin. A revision row is what the page looked like just
BEFORE that person's save (spec A stores the pre-edit snapshot), so the chips say so —
labelling them "Edit" claimed the opposite and a moderator reverting on that reading
restores one version further back than they intend. And a revert restores the whole
snapshot, facts included: putting back the prose and leaving the Quick Answers alone
resurrects a half-old page whose summary contradicts its text.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.models import SiteActivity
from membership.models import Member, NothingToRevert, WikiError, WikiPage, WikiRevision
from tests.hub.wiki_mod_helpers import enable_wiki, login, login_lead
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    WikiPageFactFactory,
    WikiPageFactory,
    WikiRevisionFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


def _history_url(page: WikiPage) -> str:
    return reverse("hub_wiki_history", args=[page.slug])


def _page_with_two_versions() -> tuple[WikiPage, WikiRevision, Member]:
    """A page saved twice, plus the revision holding its ORIGINAL state."""
    author = MemberFactory(full_legal_name="Rowan Ellis")
    # The facts go in through create_page so the FIRST revision snapshots them — writing
    # the revision first and the rows second would record facts=[] as version one, and a
    # revert would then restore a page state that never existed.
    page = WikiPage.objects.create_page(
        title="Bandsaw",
        kind=WikiPage.Kind.MACHINE,
        author=author,
        body="<p>One.</p>",
        facts=[("Blade", "Half inch")],
    )
    page.apply_edit(editor=author, editor_may_verify=True, title="Bandsaw", body="<p>Two.</p>")
    original = page.revisions.order_by("created_at", "pk").first()
    return page, original, author


def describe_the_history_list():
    def it_lists_every_version_newest_first_with_its_author(client: Client):
        page, _original, author = _page_with_two_versions()
        login(client, "hist_reader")
        body = client.get(_history_url(page)).content
        assert body.count(b"pl-wp-mod__history-card") == 2
        assert author.display_name.encode() in body

    def it_says_what_a_row_actually_means(client: Client):
        page, _original, _author = _page_with_two_versions()
        login(client, "hist_explainer")
        body = client.get(_history_url(page)).content
        assert b"Each row is what the page looked like just before that change." in body
        assert b"Before this edit" in body

    def it_labels_a_revert_and_an_unmerged_draft_distinctly(client: Client):
        page, original, author = _page_with_two_versions()
        page.revert_to(revision=original, by=author)
        WikiRevisionFactory(page=page, author=author, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        login(client, "hist_labels", fog_role=Member.FogRole.ADMIN)
        body = client.get(_history_url(page)).content
        assert b"Before this revert" in body
        assert b"Unmerged draft" in body

    def it_names_a_former_member(client: Client):
        page = WikiPageFactory()
        WikiRevisionFactory(page=page, author=None)
        login(client, "hist_former")
        assert b"a former member" in client.get(_history_url(page)).content

    def it_pages_at_fifty(client: Client):
        page = WikiPageFactory()
        for _ in range(51):
            WikiRevisionFactory(page=page)
        login(client, "hist_pager")
        first = client.get(_history_url(page))
        assert len(first.context["revisions_page"].object_list) == 50
        second = client.get(_history_url(page), {"page": "2"})
        assert len(second.context["revisions_page"].object_list) == 1

    def it_is_readable_by_any_member(client: Client):
        login(client, "hist_member")
        page = WikiPageFactory(guild=GuildFactory())
        WikiRevisionFactory(page=page)
        response = client.get(_history_url(page))
        assert response.status_code == 200
        assert b"Revert to This" not in response.content

    def it_offers_staff_the_revert_control(client: Client):
        login(client, "hist_staff", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(body="<p>Now.</p>")
        # A revision that actually differs from the page; WikiRevisionFactory mirrors the
        # page by default, and a row equal to the page is deliberately not revertible.
        WikiRevisionFactory(page=page, body="<p>Then.</p>")
        assert b"Revert to This" in client.get(_history_url(page)).content

    def it_offers_no_revert_on_a_row_that_already_equals_the_page(client: Client):
        # create_page writes version one from the page itself, so every seeded Equipment
        # stub and every brand-new page carried a Revert button that could only ever
        # answer "That is already the current version." Those stubs are the launch content.
        login(client, "hist_noop", fog_role=Member.FogRole.ADMIN)
        page = WikiPage.objects.create_page(
            title="Fresh Page", kind=WikiPage.Kind.HOWTO, author=MemberFactory(), body="<p>One.</p>"
        )
        body = client.get(_history_url(page)).content
        assert page.revisions.count() == 1
        assert b"Revert to This" not in body

    def it_offers_revert_once_the_page_has_moved_on(client: Client):
        login(client, "hist_noop2", fog_role=Member.FogRole.ADMIN)
        page, _original, _author = _page_with_two_versions()
        assert b"Revert to This" in client.get(_history_url(page)).content

    def it_never_renders_a_blank_box(client: Client):
        login(client, "hist_empty")
        page = WikiPageFactory()
        assert b"No history yet." in client.get(_history_url(page)).content


def describe_the_single_revision_view():
    def it_renders_one_old_version_read_only(client: Client):
        page, original, _author = _page_with_two_versions()
        login(client, "hist_one")
        body = client.get(_history_url(page), {"revision": original.pk}).content
        assert b"You are looking at an old version" in body
        assert b"One." in body

    def it_renders_an_unmerged_draft_with_its_own_bar_and_action(client: Client):
        # Without this a draft row is a dead end, and the conflict screen's promise that
        # "your version stays in this page's history" is unreachable.
        user = login(client, "hist_draft")
        page = WikiPageFactory()
        draft = WikiRevisionFactory(
            page=page,
            author=user.member,
            kind=WikiRevision.Kind.CONFLICT_DRAFT,
            body="<p>The version that lost the race.</p>",
        )
        body = client.get(_history_url(page), {"revision": draft.pk}).content
        assert b"never applied" in body
        assert b"Use This Version" in body
        assert b"The version that lost the race." in body

    def it_404s_on_a_revision_from_another_page(client: Client):
        login(client, "hist_wrong")
        page = WikiPageFactory()
        other = WikiRevisionFactory(page=WikiPageFactory())
        assert client.get(_history_url(page), {"revision": other.pk}).status_code == 404


def describe_reverting():
    def it_restores_the_title_the_body_and_the_facts(db):
        page, original, author = _page_with_two_versions()
        page.facts.all().delete()
        WikiPageFactFactory(page=page, label="Blade", value="Quarter inch", sort_order=0)
        WikiPageFactFactory(page=page, label="Fence", value="Aluminium", sort_order=1)
        page.revert_to(revision=original, by=author)
        page.refresh_from_db()
        assert page.body == "<p>One.</p>"
        restored = list(page.facts.all())
        assert [(fact.label, fact.value, fact.sort_order) for fact in restored] == [("Blade", "Half inch", 0)]
        assert "Half inch" in page.search_text
        assert "Quarter inch" not in page.search_text

    def it_appends_a_new_revision_and_keeps_the_intervening_ones(db):
        page, original, author = _page_with_two_versions()
        before = page.revisions.count()
        page.revert_to(revision=original, by=author)
        assert page.revisions.count() == before + 1
        assert page.revisions.first().kind == WikiRevision.Kind.REVERT
        assert page.revisions.filter(pk=original.pk).exists()

    def it_writes_the_activity_row(db):
        page, original, author = _page_with_two_versions()
        page.revert_to(revision=original, by=author)
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.WIKI_PAGE_REVERTED)
        assert activity.payload["reverted_to_revision"] == original.pk
        assert activity.payload["reverted_to_author"] == author.display_name

    def describe_status():
        def it_does_not_un_verify_a_page_verified_since(db):
            page, original, author = _page_with_two_versions()
            page.status = WikiPage.Status.GUILD_VERIFIED
            page.save(update_fields=["status"])
            page.revert_to(revision=original, by=author)
            page.refresh_from_db()
            assert page.status == WikiPage.Status.GUILD_VERIFIED

        def it_does_not_re_verify_from_an_old_snapshot(db):
            page, _original, author = _page_with_two_versions()
            old = WikiRevisionFactory(
                page=page, author=author, status=WikiPage.Status.GUILD_VERIFIED, body="<p>Old verified prose.</p>"
            )
            page.revert_to(revision=old, by=author)
            page.refresh_from_db()
            assert page.status == WikiPage.Status.COMMUNITY

    def it_refuses_the_current_version(db):
        page, _original, author = _page_with_two_versions()
        head_shaped = WikiRevisionFactory(
            page=page, author=author, title=page.title, body=page.body, facts=page.fact_snapshot()
        )
        with pytest.raises(NothingToRevert):
            page.revert_to(revision=head_shaped, by=author)

    def it_refuses_an_unmerged_draft(db):
        page, _original, author = _page_with_two_versions()
        draft = WikiRevisionFactory(page=page, author=author, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        with pytest.raises(WikiError):
            page.revert_to(revision=draft, by=author)

    def it_refuses_a_revision_from_another_page(db):
        page, _original, author = _page_with_two_versions()
        with pytest.raises(WikiError):
            page.revert_to(revision=WikiRevisionFactory(page=WikiPageFactory()), by=author)

    def describe_the_view():
        def it_reverts_for_staff_and_redirects(client: Client):
            login(client, "rev_staff", fog_role=Member.FogRole.ADMIN)
            page, original, _author = _page_with_two_versions()
            response = client.post(reverse("hub_wiki_revert", args=[page.slug, original.pk]))
            assert response.status_code == 302
            page.refresh_from_db()
            assert page.body == "<p>One.</p>"

        def it_lets_a_guild_lead_revert_in_their_own_guild(client: Client):
            _user, guild = login_lead(client, "rev_lead")
            page, original, _author = _page_with_two_versions()
            WikiPage.objects.filter(pk=page.pk).update(guild=guild)
            assert client.post(reverse("hub_wiki_revert", args=[page.slug, original.pk])).status_code == 302

        def it_refuses_a_guild_moderator_on_an_official_page(client: Client):
            # A revert rewrites title, body and facts wholesale, so it follows the CONTENT
            # gate too. Without that a guild treasurer refused the Edit button on a safety
            # policy could rewrite it from History with the Official chip still on it.
            user, guild = login_lead(client, "rev_official_lead")
            page, original, _author = _page_with_two_versions()
            WikiPage.objects.filter(pk=page.pk).update(guild=guild, status=WikiPage.Status.OFFICIAL)
            page.refresh_from_db()
            assert client.post(reverse("hub_wiki_revert", args=[page.slug, original.pk])).status_code == 403
            page.refresh_from_db()
            assert page.body == "<p>Two.</p>"
            assert user.member is not None or True

        def it_hides_the_control_from_that_moderator(client: Client):
            _user, guild = login_lead(client, "rev_official_lead2")
            page, _original, _author = _page_with_two_versions()
            WikiPage.objects.filter(pk=page.pk).update(guild=guild, status=WikiPage.Status.OFFICIAL)
            assert b"Revert to This" not in client.get(_history_url(page)).content

        def it_still_allows_an_officer_on_an_official_page(client: Client):
            login(client, "rev_official_admin", fog_role=Member.FogRole.ADMIN)
            page, original, _author = _page_with_two_versions()
            WikiPage.objects.filter(pk=page.pk).update(status=WikiPage.Status.OFFICIAL)
            assert client.post(reverse("hub_wiki_revert", args=[page.slug, original.pk])).status_code == 302
            page.refresh_from_db()
            assert page.body == "<p>One.</p>"

        def it_refuses_a_plain_member(client: Client):
            login(client, "rev_member")
            page, original, _author = _page_with_two_versions()
            WikiPage.objects.filter(pk=page.pk).update(guild=GuildFactory())
            assert client.post(reverse("hub_wiki_revert", args=[page.slug, original.pk])).status_code == 403

        def it_answers_a_stale_tab_without_writing(client: Client):
            login(client, "rev_stale", fog_role=Member.FogRole.ADMIN)
            page, _original, author = _page_with_two_versions()
            head_shaped = WikiRevisionFactory(
                page=page, author=author, title=page.title, body=page.body, facts=page.fact_snapshot()
            )
            before = page.revisions.count()
            response = client.post(reverse("hub_wiki_revert", args=[page.slug, head_shaped.pk]))
            assert response.status_code == 302
            assert page.revisions.count() == before

        def it_404s_on_a_revision_that_is_not_this_pages(client: Client):
            login(client, "rev_404", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory()
            other = WikiRevisionFactory(page=WikiPageFactory())
            assert client.post(reverse("hub_wiki_revert", args=[page.slug, other.pk])).status_code == 404
