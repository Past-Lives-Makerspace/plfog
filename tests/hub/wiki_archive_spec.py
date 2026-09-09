"""BDD specs for archive, the tombstone, restore and the redirect (spec D §6.7, D2, D19, D20).

Archive is the strongest action in the wiki, and the one the brief names as ending a
contributor's participation when it goes wrong: the URL keeps working, the tombstone names
the person who removed it, and the author hears why, by name.

There is deliberately NO manager swap here, so the assertions are on the querysets, which
is where the rule actually lives.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from core.models import SiteActivity, TransactionalEmailLog
from membership.models import AlreadyArchived, Member, WikiError, WikiPage
from tests.hub.wiki_mod_helpers import enable_wiki, login, login_lead, member_user
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    WikiPageFactory,
    WikiReportFactory,
    WikiRevisionFactory,
)

pytestmark = pytest.mark.django_db

REASON = "The information here was replaced by the Bandsaw Safety page."


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


def _archive_url(page: WikiPage) -> str:
    return reverse("hub_wiki_archive", args=[page.slug])


def describe_archiving():
    def it_keeps_the_url_working(client: Client):
        login(client, "arc_admin", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        client.post(_archive_url(page), {"reason": REASON})
        page.refresh_from_db()
        assert page.archived_at is not None
        assert client.get(page.get_absolute_url()).status_code == 200

    def it_shows_a_member_the_tombstone_and_none_of_the_body(client: Client):
        archiver = MemberFactory(full_legal_name="Kate Mizuno")
        page = WikiPageFactory(guild=GuildFactory(), body="<p>The old prose nobody should see.</p>")
        page.archive(by=archiver, reason=REASON)
        login(client, "arc_member")
        body = client.get(page.get_absolute_url()).content
        assert b"Removed by Kate Mizuno on" in body
        assert REASON.encode() in body
        assert b"The old prose nobody should see." not in body

    def it_shows_a_moderator_the_tombstone_and_the_body(client: Client):
        page = WikiPageFactory(body="<p>The old prose a moderator must read.</p>")
        page.archive(by=MemberFactory(full_legal_name="Kate Mizuno"), reason=REASON)
        login(client, "arc_mod", fog_role=Member.FogRole.ADMIN)
        body = client.get(page.get_absolute_url()).content
        assert b"Removed by Kate Mizuno on" in body
        assert b"The old prose a moderator must read." in body

    def it_degrades_to_the_date_when_the_archiver_is_gone(client: Client):
        page = WikiPageFactory(guild=GuildFactory())
        page.archive(by=MemberFactory(), reason=REASON)
        WikiPage.objects.filter(pk=page.pk).update(archived_by=None)
        login(client, "arc_no_archiver")
        body = client.get(page.get_absolute_url()).content
        assert b"Removed on" in body
        assert b"Removed by" not in body

    def it_leaves_every_listing_while_the_row_stays_reachable(db):
        page = WikiPageFactory()
        report = WikiReportFactory(page=page)
        revision = WikiRevisionFactory(page=page)
        page.archive(by=MemberFactory(), reason=REASON)
        assert not WikiPage.objects.not_archived().filter(pk=page.pk).exists()
        assert WikiPage.objects.all().filter(pk=page.pk).exists()
        report.refresh_from_db()
        revision.refresh_from_db()
        assert report.page == page
        assert revision.page == page

    def it_writes_the_activity_row_with_the_reason_and_the_author(db):
        author = MemberFactory(full_legal_name="Rowan Ellis")
        page = WikiPageFactory(created_by=author)
        page.archive(by=MemberFactory(), reason=REASON)
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.WIKI_PAGE_ARCHIVED)
        assert activity.payload["reason"] == REASON
        assert activity.payload["author"] == "Rowan Ellis"
        assert activity.payload["redirect_slug"] == ""

    def it_resolves_no_open_reports(db):
        # Archiving answers SOME reports and not others, so a human still marks them.
        page = WikiPageFactory()
        report = WikiReportFactory(page=page)
        page.archive(by=MemberFactory(), reason=REASON)
        report.refresh_from_db()
        assert report.resolved_at is None

    def describe_a_blank_reason():
        def it_is_rejected_and_nothing_is_archived(client: Client):
            login(client, "arc_blank", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory()
            client.post(_archive_url(page), {"reason": "   "})
            page.refresh_from_db()
            assert page.archived_at is None

        def it_raises_on_the_model_too(db):
            with pytest.raises(WikiError):
                WikiPageFactory().archive(by=MemberFactory(), reason="")

    def describe_a_reason_longer_than_the_column():
        def it_is_rejected_by_the_form_rather_than_truncated(client: Client):
            login(client, "arc_long", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory()
            client.post(_archive_url(page), {"reason": "x" * 301})
            page.refresh_from_db()
            assert page.archived_at is None
            assert page.archive_reason == ""

    def it_refuses_a_second_archive(db):
        page = WikiPageFactory(archived=True)
        with pytest.raises(AlreadyArchived):
            page.archive(by=MemberFactory(), reason=REASON)

    def it_refuses_a_plain_member(client: Client):
        login(client, "arc_403")
        page = WikiPageFactory(guild=GuildFactory())
        assert client.post(_archive_url(page), {"reason": REASON}).status_code == 403


def describe_the_notice_to_the_author():
    def it_emails_them_by_name_with_the_reason_and_the_link(db):
        author = member_user("arc_author", email="arc_author@example.com").member
        author.full_legal_name = "Rowan Ellis"
        author.save(update_fields=["full_legal_name"])
        page = WikiPageFactory(created_by=author)
        page.archive(by=MemberFactory(full_legal_name="Kate Mizuno"), reason=REASON)
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["arc_author@example.com"]
        assert page.title in message.subject
        html = message.alternatives[0][0]
        for body in (message.body, html):
            assert "Rowan Ellis" in body
            assert "Kate Mizuno" in body
            assert REASON in body
            assert page.get_absolute_url() in body

    def it_logs_the_send_with_its_trigger_kind(db):
        author = member_user("arc_author2", email="arc_author2@example.com").member
        WikiPageFactory(created_by=author).archive(by=MemberFactory(), reason=REASON)
        log = TransactionalEmailLog.objects.get(trigger_kind="wiki.page_archived")
        assert log.to_email == "arc_author2@example.com"

    def describe_a_page_with_no_author():
        def it_sends_nothing_and_logs_nothing(db):
            # Every seeded Equipment stub is authorless, so this is an ordinary state.
            page = WikiPageFactory(seeded=True)
            page.archive(by=MemberFactory(), reason=REASON)
            assert mail.outbox == []
            assert not TransactionalEmailLog.objects.filter(trigger_kind="wiki.page_archived").exists()
            activity = SiteActivity.objects.get(kind=SiteActivity.Kind.WIKI_PAGE_ARCHIVED)
            assert activity.payload["author"] == ""

        def it_says_so_in_the_confirmation_before_the_click(client: Client):
            login(client, "arc_stub_mod", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory(seeded=True)
            body = client.get(page.get_absolute_url()).content
            assert b"Nobody is listed as the author of this page" in body

        def it_names_the_author_in_the_confirmation_when_there_is_one(client: Client):
            login(client, "arc_named_mod", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory(created_by=MemberFactory(full_legal_name="Rowan Ellis"))
            body = client.get(page.get_absolute_url()).content
            assert b"Rowan Ellis wrote this page and will be emailed your reason" in body

        def it_still_sends_nothing_when_other_people_wrote_revisions(db):
            # The deliberate under-reach in §7.3, pinned so a later change is a decision.
            page = WikiPageFactory(seeded=True)
            WikiRevisionFactory(page=page, author=member_user("arc_contrib", email="c@example.com").member)
            page.archive(by=MemberFactory(), reason=REASON)
            assert mail.outbox == []


def describe_restoring():
    def it_clears_all_four_archive_fields(db):
        page = WikiPageFactory()
        redirect_target = WikiPageFactory()
        page.archive(by=MemberFactory(), reason=REASON, redirect=redirect_target)
        page.restore(by=MemberFactory())
        page.refresh_from_db()
        assert page.archived_at is None
        assert page.archived_by is None
        assert page.archive_reason == ""
        assert page.archive_redirect is None
        assert WikiPage.objects.not_archived().filter(pk=page.pk).exists()

    def it_logs_an_edit_row_flagged_as_a_restore(db):
        page = WikiPageFactory(archived=True)
        page.restore(by=MemberFactory())
        activity = SiteActivity.objects.filter(kind=SiteActivity.Kind.WIKI_PAGE_EDITED).latest("id")
        assert activity.payload["restored_from_archive"] is True

    def it_works_from_the_tombstone(client: Client):
        login(client, "arc_restore", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(archived=True)
        assert client.post(reverse("hub_wiki_restore", args=[page.slug])).status_code == 302
        page.refresh_from_db()
        assert page.archived_at is None

    def it_refuses_a_page_that_is_not_archived(db):
        with pytest.raises(WikiError):
            WikiPageFactory().restore(by=MemberFactory())

    def it_refuses_a_plain_member(client: Client):
        login(client, "arc_restore_403")
        page = WikiPageFactory(guild=GuildFactory(), archived=True)
        assert client.post(reverse("hub_wiki_restore", args=[page.slug])).status_code == 403


def describe_the_redirect():
    def it_renders_the_link_on_the_tombstone(client: Client):
        guild = GuildFactory()
        replacement = WikiPageFactory(guild=guild, title="Bandsaw Safety")
        page = WikiPageFactory(guild=guild)
        page.archive(by=MemberFactory(), reason=REASON)
        page.set_archive_redirect(target=replacement)
        login(client, "arc_redirect_reader")
        body = client.get(page.get_absolute_url()).content
        assert b"Go to Bandsaw Safety" in body

    def it_is_set_from_the_tombstone_afterwards(client: Client):
        _user, guild = login_lead(client, "arc_redirect_lead")
        replacement = WikiPageFactory(guild=guild)
        page = WikiPageFactory(guild=guild, archived=True)
        response = client.post(reverse("hub_wiki_set_redirect", args=[page.slug]), {"target": replacement.pk})
        assert response.status_code == 302
        page.refresh_from_db()
        assert page.archive_redirect == replacement

    def it_refuses_a_page_pointing_at_itself(db):
        page = WikiPageFactory(archived=True)
        with pytest.raises(WikiError):
            page.set_archive_redirect(target=page)

    def it_refuses_a_target_that_is_archived_too(db):
        page = WikiPageFactory(archived=True)
        with pytest.raises(WikiError):
            page.set_archive_redirect(target=WikiPageFactory(archived=True))

    def it_refuses_to_signpost_a_live_page(db):
        with pytest.raises(WikiError):
            WikiPageFactory().set_archive_redirect(target=WikiPageFactory())

    def it_is_cleared_from_every_page_pointing_at_a_newly_archived_one(client: Client):
        # _check_redirect refuses an archived target when a redirect is SET, but a page can
        # be archived after others already point at it.
        guild = GuildFactory()
        replacement = WikiPageFactory(guild=guild)
        first = WikiPageFactory(guild=guild)
        second = WikiPageFactory(guild=guild)
        for page in (first, second):
            page.archive(by=MemberFactory(), reason=REASON, redirect=replacement)
        replacement.archive(by=MemberFactory(), reason="This one went too.")
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.archive_redirect is None
        assert second.archive_redirect is None

    def it_leaves_other_pages_redirects_alone(client: Client):
        guild = GuildFactory()
        keeper = WikiPageFactory(guild=guild)
        pointing = WikiPageFactory(guild=guild)
        pointing.archive(by=MemberFactory(), reason=REASON, redirect=keeper)
        WikiPageFactory(guild=guild).archive(by=MemberFactory(), reason=REASON)
        pointing.refresh_from_db()
        assert pointing.archive_redirect == keeper

    def it_refuses_a_target_outside_the_scope(client: Client):
        _user, guild = login_lead(client, "arc_redirect_scope")
        page = WikiPageFactory(guild=guild, archived=True)
        outsider = WikiPageFactory(guild=GuildFactory())
        client.post(reverse("hub_wiki_set_redirect", args=[page.slug]), {"target": outsider.pk})
        page.refresh_from_db()
        assert page.archive_redirect is None
