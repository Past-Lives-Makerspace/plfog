"""BDD specs for Report a Problem and Withdraw (spec D §6.1-6.3).

The pressure valve that makes open editing safe without deletion: two taps from any page,
an amber banner quoting the reporter's own words while the page stays fully readable, and
a route to the people who actually know the shop.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Notification, SiteActivity
from membership.models import DuplicateWikiReport, Member, WikiPage, WikiReport
from tests.hub.wiki_mod_helpers import enable_wiki, login, login_lead, member_user
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    WikiPageFactory,
    WikiReportFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


def _report_url(page: WikiPage) -> str:
    return reverse("hub_wiki_report", args=[page.slug])


def describe_filing_a_report():
    def it_creates_the_row_and_swaps_the_banner_in(client: Client):
        user = login(client, "rep_one")
        page = WikiPageFactory()
        response = client.post(_report_url(page), {"reason": "The blade guard step is backwards."})
        assert response.status_code == 200
        report = WikiReport.objects.get(page=page)
        assert report.reporter == user.member
        assert b'id="wiki-review-banner"' in response.content
        assert b'hx-swap-oob="true"' in response.content
        assert b"The blade guard step is backwards." in response.content

    def it_shows_the_banner_to_every_reader(client: Client):
        page = WikiPageFactory()
        WikiReportFactory(page=page, reason="Wrong bit size.")
        page.mark_needs_review()
        login(client, "rep_reader")
        response = client.get(page.get_absolute_url())
        assert b"Needs review" in response.content
        assert b"Wrong bit size." in response.content

    def it_sets_both_toast_and_close_modal_headers_in_that_order(client: Client):
        # trigger_toast OVERWRITES HX-Trigger while trigger_client_event merges into it,
        # so a toast set second is silently dropped and the modal never closes.
        login(client, "rep_headers")
        page = WikiPageFactory()
        response = client.post(_report_url(page), {"reason": "The fence measurement is wrong."})
        header = json.loads(response["HX-Trigger"])
        assert header["showToast"]["type"] == "success"
        assert header["close-modal"] == "wiki-report"

    def it_writes_the_activity_row_with_the_reason_and_the_reporter(client: Client):
        user = login(client, "rep_activity")
        page = WikiPageFactory()
        client.post(_report_url(page), {"reason": "The steps are in the wrong order."})
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.WIKI_PAGE_REPORTED)
        assert activity.payload["reason"] == "The steps are in the wrong order."
        assert activity.payload["reporter"] == user.member.display_name
        assert activity.payload["slug"] == page.slug

    def it_escapes_the_reason_rather_than_rendering_it(client: Client):
        login(client, "rep_escape")
        page = WikiPageFactory()
        response = client.post(_report_url(page), {"reason": "Broken <script>alert(1)</script> here"})
        assert b"<script>alert(1)</script>" not in response.content
        assert b"&lt;script&gt;" in response.content

    def describe_an_archived_page():
        def it_refuses_the_report_and_writes_nothing(client: Client):
            login(client, "rep_archived")
            page = WikiPageFactory(archived=True)
            response = client.post(_report_url(page), {"reason": "Still says the old thing."})
            assert response.status_code == 404
            assert not WikiReport.objects.exists()

    def describe_a_reason_that_is_too_short():
        def it_re_renders_the_bound_form_into_the_modal_body(client: Client):
            login(client, "rep_short")
            page = WikiPageFactory()
            response = client.post(_report_url(page), {"reason": "nope"})
            assert response.status_code == 200
            assert not WikiReport.objects.exists()
            assert b'hx-target="#wiki-report-body"' in response.content
            # The text they typed is still in the box.
            assert b"nope" in response.content
            assert b"Please say a little more" in response.content

    def describe_a_second_report_from_the_same_member():
        def it_answers_kindly_and_writes_no_second_row(client: Client):
            user = login(client, "rep_dupe")
            page = WikiPageFactory()
            WikiReportFactory(page=page, reporter=user.member)
            response = client.post(_report_url(page), {"reason": "Saying it again, differently."})
            assert response.status_code == 200
            assert WikiReport.objects.filter(page=page).count() == 1
            assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "info"

        def it_lets_a_different_member_file_their_own(client: Client):
            page = WikiPageFactory()
            WikiReportFactory(page=page, reporter=MemberFactory())
            login(client, "rep_other")
            client.post(_report_url(page), {"reason": "A completely different problem."})
            assert WikiReport.objects.filter(page=page).count() == 2

    def it_takes_an_active_membership(client: Client):
        login(client, "rep_lapsed", status=Member.Status.FORMER)
        page = WikiPageFactory()
        assert client.post(_report_url(page), {"reason": "Something is wrong here."}).status_code == 403


def describe_the_banner_with_several_reports():
    def it_quotes_the_oldest_and_counts_the_rest(client: Client):
        page = WikiPageFactory()
        oldest = WikiReportFactory(page=page, reason="First complaint about the guard.")
        WikiReport.objects.filter(pk=oldest.pk).update(created_at=timezone.now() - timezone.timedelta(days=2))
        WikiReportFactory(page=page, reason="Second complaint about the fence.")
        WikiReportFactory(page=page, reason="Third complaint about the blade.")
        page.mark_needs_review()
        login(client, "rep_many")
        response = client.get(page.get_absolute_url())
        assert b"First complaint about the guard." in response.content
        assert b"Second complaint about the fence." not in response.content
        assert b"and 2 more" in response.content


def describe_the_denormalized_review_columns():
    def it_stamps_them_on_file(db):
        page = WikiPageFactory()
        WikiReport.file(page=page, reporter=MemberFactory(), reason="The bit size is wrong.")
        page.refresh_from_db()
        assert page.needs_review_since is not None
        assert page.needs_review_reason == "The bit size is wrong."
        assert WikiPage.objects.needs_review().filter(pk=page.pk).exists()
        assert page.status_pill[1] == "Needs review"

    def it_clears_them_when_the_last_open_report_is_resolved(db):
        page = WikiPageFactory()
        report = WikiReport.file(page=page, reporter=MemberFactory(), reason="The bit size is wrong.")
        report.resolve(by=MemberFactory())
        page.refresh_from_db()
        assert page.needs_review_since is None
        assert page.needs_review_reason == ""

    def it_repoints_them_at_the_next_oldest(db):
        page = WikiPageFactory()
        first = WikiReport.file(page=page, reporter=MemberFactory(), reason="The first problem.")
        WikiReport.objects.filter(pk=first.pk).update(created_at=timezone.now() - timezone.timedelta(days=3))
        WikiReport.file(page=page, reporter=MemberFactory(), reason="The second problem.")
        first.resolve(by=MemberFactory())
        page.refresh_from_db()
        assert page.needs_review_reason == "The second problem."

    def it_truncates_only_the_column_and_never_the_banner(client: Client):
        long_reason = "x" * 480
        page = WikiPageFactory()
        WikiReport.file(page=page, reporter=MemberFactory(), reason=long_reason)
        page.refresh_from_db()
        assert len(page.needs_review_reason) == 300
        login(client, "rep_long")
        response = client.get(page.get_absolute_url())
        assert long_reason.encode() in response.content


def describe_routing():
    def it_reaches_the_guilds_leadership_and_no_uninvolved_admin(client: Client):
        lead_user, guild = login_lead(client, "route_lead")
        lead_user.email = "route_lead@example.com"
        lead_user.save(update_fields=["email"])
        admin = member_user("route_admin", fog_role=Member.FogRole.ADMIN, email="route_admin@example.com")
        reporter = login(client, "route_reporter")
        page = WikiPageFactory(guild=guild)
        client.post(_report_url(page), {"reason": "The dust port fitting is wrong."})
        notified = set(Notification.objects.filter(trigger="wiki.page_reported").values_list("user_id", flat=True))
        assert lead_user.pk in notified
        assert admin.pk not in notified
        assert reporter.pk not in notified or reporter.pk == lead_user.pk

    def it_reaches_the_admins_on_a_space_wide_page(client: Client):
        admin = member_user("route_admin2", fog_role=Member.FogRole.ADMIN, email="route_admin2@example.com")
        lead_user, _guild = login_lead(client, "route_lead2")
        login(client, "route_reporter2")
        page = WikiPageFactory(guild=None)
        client.post(_report_url(page), {"reason": "This page is out of date entirely."})
        notified = set(Notification.objects.filter(trigger="wiki.page_reported").values_list("user_id", flat=True))
        assert notified == {admin.pk}
        assert lead_user.pk not in notified

    def it_delivers_once_per_report_rather_than_once_per_page(client: Client):
        admin = member_user("route_admin3", fog_role=Member.FogRole.ADMIN, email="route_admin3@example.com")
        page = WikiPageFactory(guild=None)
        login(client, "route_two_a")
        client.post(_report_url(page), {"reason": "The first distinct problem."})
        client.logout()
        login(client, "route_two_b")
        client.post(_report_url(page), {"reason": "The second distinct problem."})
        assert Notification.objects.filter(trigger="wiki.page_reported", user=admin).count() == 2

    def it_sends_nothing_a_second_time_for_a_duplicate(client: Client):
        admin = member_user("route_admin4", fog_role=Member.FogRole.ADMIN, email="route_admin4@example.com")
        page = WikiPageFactory(guild=None)
        user = login(client, "route_dupe")
        WikiReportFactory(page=page, reporter=user.member)
        client.post(_report_url(page), {"reason": "Saying it again, differently."})
        assert Notification.objects.filter(trigger="wiki.page_reported", user=admin).count() == 0


def describe_withdrawing_a_report():
    def it_shows_the_reporter_their_own_state_instead_of_the_button(client: Client):
        user = login(client, "wd_state")
        page = WikiPageFactory()
        WikiReportFactory(page=page, reporter=user.member)
        response = client.get(page.get_absolute_url())
        assert b"You reported this" in response.content
        assert b"Withdraw" in response.content
        # The control is replaced, not disabled. (Both modals stay on the page so the
        # Withdraw confirmation still works after an HTMX report swaps the control in.)
        assert b"pl-wp-mod__report-btn" not in response.content

    def it_resolves_the_row_without_deleting_it(client: Client):
        user = login(client, "wd_resolve")
        page = WikiPageFactory()
        report = WikiReportFactory(page=page, reporter=user.member)
        response = client.post(reverse("hub_wiki_report_withdraw", args=[page.slug]))
        assert response.status_code == 302
        report.refresh_from_db()
        assert report.resolved_at is not None
        assert report.resolved_by == user.member
        assert report.resolution == "Withdrawn by the reporter."
        assert WikiReport.objects.filter(pk=report.pk).exists()

    def it_brings_the_banner_down_when_it_was_the_only_one(client: Client):
        user = login(client, "wd_banner")
        page = WikiPageFactory()
        report = WikiReportFactory(page=page, reporter=user.member)
        page.mark_needs_review()
        report.withdraw(by=user.member)
        page.refresh_from_db()
        assert page.needs_review_since is None

    def it_leaves_the_banner_quoting_the_next_when_it_was_not(client: Client):
        user = login(client, "wd_banner2")
        page = WikiPageFactory()
        mine = WikiReportFactory(page=page, reporter=user.member, reason="Mine, filed first.")
        WikiReport.objects.filter(pk=mine.pk).update(created_at=timezone.now() - timezone.timedelta(days=1))
        WikiReportFactory(page=page, reporter=MemberFactory(), reason="Theirs, filed second.")
        mine.withdraw(by=user.member)
        page.refresh_from_db()
        assert page.needs_review_reason == "Theirs, filed second."

    def it_refuses_somebody_elses_report(db):
        from django.core.exceptions import PermissionDenied

        page = WikiPageFactory()
        report = WikiReportFactory(page=page)
        with pytest.raises(PermissionDenied):
            report.withdraw(by=MemberFactory())

    def it_lets_the_same_member_report_again_afterwards(client: Client):
        user = login(client, "wd_again")
        page = WikiPageFactory()
        report = WikiReportFactory(page=page, reporter=user.member)
        report.withdraw(by=user.member)
        client.post(_report_url(page), {"reason": "A different problem entirely."})
        assert WikiReport.objects.filter(page=page, reporter=user.member).count() == 2

    def it_says_so_when_there_is_nothing_to_withdraw(client: Client):
        login(client, "wd_none")
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_report_withdraw", args=[page.slug]))
        assert response.status_code == 302
        assert not WikiReport.objects.exists()

    def it_takes_a_signed_in_member(client: Client):
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_report_withdraw", args=[page.slug]))
        assert response.status_code in (302, 403)


def describe_the_model_guard():
    def it_raises_a_domain_error_rather_than_a_500_on_a_duplicate(db):
        page = WikiPageFactory()
        member = MemberFactory()
        WikiReport.file(page=page, reporter=member, reason="The first problem.")
        with pytest.raises(DuplicateWikiReport):
            WikiReport.file(page=page, reporter=member, reason="The second problem.")

    def it_names_a_member_whose_account_is_gone(db):
        page = WikiPageFactory()
        report = WikiReportFactory(page=page, reporter=None)
        assert report.reporter_name == "a member"
        assert "a member" in str(report)


def describe_the_report_control_on_a_page_nobody_may_report():
    def it_is_absent_on_an_archived_page(client: Client):
        login(client, "ctl_archived")
        guild = GuildFactory()
        page = WikiPageFactory(guild=guild, archived=True)
        response = client.get(page.get_absolute_url())
        assert b"Report a Problem" not in response.content
