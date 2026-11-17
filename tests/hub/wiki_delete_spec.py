"""BDD specs for deleting a wiki page for good (Felix, 2026-11-17).

Archive keeps the URL and the history; Delete takes the page and everything that belongs
only to it. It is gated like Archive, confirmed with a plain modal, and leaves one trace:
the activity row naming who deleted what.
"""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.core.files.storage import default_storage
from django.test import Client
from django.urls import reverse

from core.models import SiteActivity
from membership.models import (
    Member,
    WikiAttachment,
    WikiDraft,
    WikiEditLock,
    WikiPage,
    WikiPageFact,
    WikiReport,
    WikiRevision,
    WikiWantedPage,
)
from tests.hub.wiki_mod_helpers import enable_wiki, login, login_lead
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    WikiAttachmentFactory,
    WikiDraftFactory,
    WikiEditLockFactory,
    WikiPageFactFactory,
    WikiPageFactory,
    WikiReportFactory,
    WikiRevisionFactory,
    WikiWantedPageFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


def _delete_url(page: WikiPage) -> str:
    return reverse("hub_wiki_delete", args=[page.slug])


def _confirmed(client: Client, page: WikiPage):
    return client.post(_delete_url(page))


def describe_deleting_a_page():
    def it_deletes_the_row_and_lands_on_the_wiki_home(client: Client):
        login(client, "del_admin", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(title="Old Bandsaw Notes")
        response = _confirmed(client, page)
        assert response.status_code == 302
        assert response.url == reverse("hub_wiki_home")
        assert not WikiPage.objects.filter(pk=page.pk).exists()
        assert [str(m) for m in get_messages(response.wsgi_request)] == ["Deleted Old Bandsaw Notes."]

    def it_lets_the_guild_lead_delete_their_own_guilds_page(client: Client):
        _user, guild = login_lead(client, "del_lead")
        page = WikiPageFactory(guild=guild)
        assert _confirmed(client, page).status_code == 302
        assert not WikiPage.objects.filter(pk=page.pk).exists()

    def it_deletes_an_archived_page_too(client: Client):
        login(client, "del_archived", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        page.archive(by=MemberFactory(), reason="Replaced by the new page.")
        assert _confirmed(client, page).status_code == 302
        assert not WikiPage.objects.filter(pk=page.pk).exists()

    def it_logs_who_deleted_what(client: Client):
        user = login(client, "del_logger", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Woodshop")
        page = WikiPageFactory(title="Jointer Setup", guild=guild)
        slug = page.slug
        _confirmed(client, page)
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.WIKI_PAGE_DELETED)
        assert row.actor == user
        assert row.payload == {"slug": slug, "title": "Jointer Setup", "guild": "Woodshop"}

    def it_logs_a_blank_guild_for_a_space_wide_page(db):
        page = WikiPageFactory(guild=None)
        page.delete_page(by=MemberFactory())
        assert SiteActivity.objects.get(kind=SiteActivity.Kind.WIKI_PAGE_DELETED).payload["guild"] == ""


def describe_what_goes_with_the_page():
    def it_removes_every_row_that_belongs_only_to_it(db):
        page = WikiPageFactory()
        WikiPageFactFactory(page=page)
        WikiRevisionFactory(page=page)
        WikiAttachmentFactory(page=page)
        WikiDraftFactory(page=page)
        WikiReportFactory(page=page)
        WikiEditLockFactory(page=page)
        page.delete_page(by=MemberFactory())
        for model in (WikiPageFact, WikiRevision, WikiAttachment, WikiDraft, WikiReport, WikiEditLock):
            assert not model.objects.filter(page_id=page.pk).exists(), model.__name__

    def it_clears_another_pages_signpost_to_it(db):
        page = WikiPageFactory()
        tombstone = WikiPageFactory()
        tombstone.archive(by=MemberFactory(), reason="Moved.", redirect=page)
        page.delete_page(by=MemberFactory())
        tombstone.refresh_from_db()
        assert tombstone.archive_redirect is None

    def it_removes_uploaded_files_once_the_delete_commits(db, django_capture_on_commit_callbacks):
        page = WikiPageFactory()
        attachment = WikiAttachmentFactory(page=page, as_file=True)
        name = attachment.file.name
        assert default_storage.exists(name)
        with django_capture_on_commit_callbacks(execute=True):
            page.delete_page(by=MemberFactory())
        assert not default_storage.exists(name)

    def it_keeps_a_file_another_attachment_still_names(db, django_capture_on_commit_callbacks):
        page = WikiPageFactory()
        attachment = WikiAttachmentFactory(page=page, as_file=True)
        name = attachment.file.name
        survivor = WikiAttachmentFactory(page=WikiPageFactory())
        WikiAttachment.objects.filter(pk=survivor.pk).update(file=name, url="")
        with django_capture_on_commit_callbacks(execute=True):
            page.delete_page(by=MemberFactory())
        assert default_storage.exists(name)


def describe_the_wanted_rows_it_answered():
    def it_puts_an_answered_ask_back_on_the_list_unclaimed(db):
        page = WikiPageFactory()
        claimer = MemberFactory()
        want = WikiWantedPageFactory(title="Kiln Loading", fulfilled_page=page, claimed_by=claimer)
        page.delete_page(by=MemberFactory())
        want.refresh_from_db()
        assert want.fulfilled_page is None
        assert want.claimed_by is None
        assert want.claimed_at is None

    def it_folds_an_answered_ask_into_a_duplicate_asked_since(db):
        page = WikiPageFactory()
        WikiWantedPageFactory(title="Kiln Loading", fulfilled_page=page, request_count=3)
        open_ask = WikiWantedPageFactory(title="kiln loading")
        page.delete_page(by=MemberFactory())
        assert list(WikiWantedPage.objects.values_list("pk", flat=True)) == [open_ask.pk]
        open_ask.refresh_from_db()
        assert open_ask.request_count == 4


def describe_refusing_a_delete():
    def it_refuses_a_get(client: Client):
        login(client, "del_get", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        assert client.get(_delete_url(page)).status_code == 405
        assert WikiPage.objects.filter(pk=page.pk).exists()

    def it_refuses_a_crafted_post_from_a_plain_member(client: Client):
        login(client, "del_member")
        page = WikiPageFactory()
        assert _confirmed(client, page).status_code == 403
        assert WikiPage.objects.filter(pk=page.pk).exists()

    def it_refuses_a_lead_on_another_guilds_page(client: Client):
        login_lead(client, "del_other_lead")
        page = WikiPageFactory(guild=GuildFactory())
        assert _confirmed(client, page).status_code == 403
        assert WikiPage.objects.filter(pk=page.pk).exists()

    def it_answers_a_missing_page_with_the_wiki_404(client: Client):
        login(client, "del_missing", fog_role=Member.FogRole.ADMIN)
        assert client.post(reverse("hub_wiki_delete", args=["no-such-page"])).status_code == 404


def describe_the_delete_control():
    def it_puts_delete_in_the_more_menu_with_its_confirm_for_a_moderator(client: Client):
        login(client, "del_menu", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        html = client.get(page.get_absolute_url()).content.decode()
        assert "$dispatch('open-confirm', 'wiki-delete')\">Delete</button>" in html
        assert "Delete this page?" in html
        assert "Type DELETE to confirm" not in html
        assert _delete_url(page) in html

    def it_shows_a_plain_member_neither(client: Client):
        login(client, "del_menu_member")
        page = WikiPageFactory()
        html = client.get(page.get_absolute_url()).content.decode()
        assert "'wiki-delete'" not in html
        assert _delete_url(page) not in html

    def it_puts_delete_on_the_tombstone_for_a_moderator(client: Client):
        login(client, "del_tomb", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        page.archive(by=MemberFactory(), reason="Replaced.")
        html = client.get(page.get_absolute_url()).content.decode()
        banner = html.split("pl-wp-mod__banner-actions", 1)[1].split("</div>", 1)[0]
        assert "'wiki-delete')\">Delete</button>" in banner
        assert _delete_url(page) in html
