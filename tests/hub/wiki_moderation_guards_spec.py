"""BDD specs for the edges of every moderation route (spec D §5.7, brief §3).

The brief names the failure this file exists to prevent: v1.39.0 gated ``/register/<key>/``
while every register kept its own URL, so a member loading ``/finance/`` got a 200 and the
full financials. Every alternate path to a page needs the same gate, so every route gets
the same three questions here: what does a bad slug answer, what does somebody without
authority answer, and what does a domain error answer.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from membership.models import Member, WikiPage, WikiRevision
from tests.hub.wiki_mod_helpers import enable_wiki, login
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    WikiPageFactory,
    WikiReportFactory,
    WikiRevisionFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


_SLUG_ROUTES_GET = ["hub_wiki_history", "hub_wiki_conflict"]
_SLUG_ROUTES_POST = [
    "hub_wiki_report",
    "hub_wiki_report_withdraw",
    "hub_wiki_official_note",
    "hub_wiki_official_note_remove",
    "hub_wiki_archive",
    "hub_wiki_restore",
    "hub_wiki_set_redirect",
    "hub_wiki_publish_proposal",
    "hub_wiki_decline_proposal",
]


def describe_a_slug_that_does_not_exist():
    @pytest.mark.parametrize("route", _SLUG_ROUTES_POST)
    def it_answers_the_wikis_own_404_on_post(client: Client, route):
        login(client, f"guard_{route}", fog_role=Member.FogRole.ADMIN)
        response = client.post(reverse(route, args=["no-such-page"]))
        assert response.status_code == 404
        assert b"Browse Past Lives classes" not in response.content

    def it_answers_404_on_the_history(client: Client):
        login(client, "guard_history_404")
        assert client.get(reverse("hub_wiki_history", args=["no-such-page"])).status_code == 404

    def it_answers_404_on_the_conflict_screen(client: Client):
        login(client, "guard_conflict_404")
        assert client.get(reverse("hub_wiki_conflict", args=["no-such-page", 1])).status_code == 404

    def it_answers_404_on_the_conflict_keep(client: Client):
        login(client, "guard_keep_404")
        assert client.post(reverse("hub_wiki_conflict_keep", args=["no-such-page", 1])).status_code == 404

    def it_answers_404_on_a_revert(client: Client):
        login(client, "guard_revert_404", fog_role=Member.FogRole.ADMIN)
        assert client.post(reverse("hub_wiki_revert", args=["no-such-page", 1])).status_code == 404


def describe_a_missing_row():
    def it_404s_on_a_report_that_is_gone(client: Client):
        login(client, "guard_report_gone", fog_role=Member.FogRole.ADMIN)
        assert client.post(reverse("hub_wiki_report_resolve", args=[999_999])).status_code == 404

    def it_404s_on_a_conflict_draft_that_is_gone(client: Client):
        login(client, "guard_draft_gone")
        page = WikiPageFactory()
        assert client.post(reverse("hub_wiki_conflict_keep", args=[page.slug, 999_999])).status_code == 404


def describe_a_request_without_authority():
    def it_refuses_a_signed_out_moderation_post(client: Client):
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_archive", args=[page.slug]), {"reason": "Nope."})
        assert response.status_code in (302, 403)

    def it_refuses_a_previewing_admin_acting_as_a_guest(client: Client):
        login(client, "guard_view_as", fog_role=Member.FogRole.ADMIN)
        session = client.session
        session["view_as_role"] = "guest"
        session.save()
        page = WikiPageFactory()
        assert client.post(reverse("hub_wiki_archive", args=[page.slug]), {"reason": "Nope."}).status_code == 403

    def it_refuses_a_plain_member_the_redirect_form(client: Client):
        login(client, "guard_redirect_403")
        page = WikiPageFactory(guild=GuildFactory(), archived=True)
        assert client.post(reverse("hub_wiki_set_redirect", args=[page.slug])).status_code == 403

    def it_refuses_a_plain_member_the_decline_form(client: Client):
        login(client, "guard_decline_403")
        page = WikiPageFactory(guild=GuildFactory(), official=True, is_published=False)
        assert (
            client.post(reverse("hub_wiki_decline_proposal", args=[page.slug]), {"note": "x" * 20}).status_code == 403
        )

    def it_refuses_conflict_keep_to_somebody_who_may_not_edit(client: Client):
        login(client, "guard_keep_403")
        page = WikiPageFactory(guild=GuildFactory(), official=True)
        draft = WikiRevision.objects.create(
            page=page,
            author=MemberFactory(),
            kind=WikiRevision.Kind.CONFLICT_DRAFT,
            title=page.title,
            body="<p>Not theirs.</p>",
            status=page.status,
        )
        assert client.post(reverse("hub_wiki_conflict_keep", args=[page.slug, draft.pk])).status_code == 403


def describe_a_held_page_reached_by_url():
    @pytest.mark.parametrize("route", ["hub_wiki_report", "hub_wiki_report_withdraw"])
    def it_404s_for_somebody_who_could_not_see_it_in_a_listing(client: Client, route):
        author = MemberFactory()
        page = WikiPageFactory(created_by=author, is_published=False, guild=GuildFactory())
        login(client, f"guard_held_{route}")
        assert client.post(reverse(route, args=[page.slug]), {"reason": "x" * 20}).status_code == 404

    def it_404s_the_history_too(client: Client):
        page = WikiPageFactory(created_by=MemberFactory(), is_published=False, guild=GuildFactory())
        login(client, "guard_held_history")
        assert client.get(reverse("hub_wiki_history", args=[page.slug])).status_code == 404

    def it_still_answers_its_own_author(client: Client):
        user = login(client, "guard_held_author")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=user.member)
        page = WikiPageFactory(created_by=user.member, is_published=False, guild=guild)
        assert client.get(reverse("hub_wiki_history", args=[page.slug])).status_code == 200


def describe_domain_errors_surfaced_by_a_view():
    def it_answers_a_second_archive_with_a_message_and_no_write(client: Client):
        login(client, "guard_double_archive", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(archived=True)
        stamp = page.archived_at
        response = client.post(reverse("hub_wiki_archive", args=[page.slug]), {"reason": "Removing it again."})
        assert response.status_code == 302
        page.refresh_from_db()
        assert page.archived_at == stamp

    def it_answers_a_restore_of_a_live_page(client: Client):
        login(client, "guard_restore_live", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        assert client.post(reverse("hub_wiki_restore", args=[page.slug])).status_code == 302
        page.refresh_from_db()
        assert page.archived_at is None

    def it_answers_a_redirect_pointing_at_itself(client: Client):
        login(client, "guard_self_redirect", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None, archived=True)
        response = client.post(reverse("hub_wiki_set_redirect", args=[page.slug]), {"target": page.pk})
        assert response.status_code == 302
        page.refresh_from_db()
        assert page.archive_redirect is None

    def it_answers_a_revert_on_an_archived_page(client: Client):
        login(client, "guard_revert_archived", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(archived=True, body="<p>Now.</p>")
        revision = WikiRevisionFactory(page=page, body="<p>Then.</p>")
        assert client.post(reverse("hub_wiki_revert", args=[page.slug, revision.pk])).status_code == 302
        page.refresh_from_db()
        assert page.body == "<p>Now.</p>"

    def it_answers_conflict_keep_on_an_archived_page(client: Client):
        user = login(client, "guard_keep_archived", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(archived=True, body="<p>Now.</p>")
        draft = WikiRevision.objects.create(
            page=page,
            author=user.member,
            kind=WikiRevision.Kind.CONFLICT_DRAFT,
            title=page.title,
            body="<p>Mine.</p>",
            status=page.status,
        )
        assert client.post(reverse("hub_wiki_conflict_keep", args=[page.slug, draft.pk])).status_code == 302
        page.refresh_from_db()
        assert page.body == "<p>Now.</p>"

    def it_answers_publishing_a_page_that_is_already_live(client: Client):
        login(client, "guard_publish_live", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(official=True)
        response = client.post(reverse("hub_wiki_publish_proposal", args=[page.slug]))
        assert response.status_code == 200
        assert b"already live" in response.content

    def it_answers_declining_a_page_that_is_already_live(client: Client):
        login(client, "guard_decline_live", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(official=True)
        response = client.post(
            reverse("hub_wiki_decline_proposal", args=[page.slug]), {"note": "Please change the title."}
        )
        assert response.status_code == 200
        assert b"already live" in response.content

    def it_answers_a_duplicate_title_on_the_create_form(client: Client):
        login(client, "guard_dupe_title")
        WikiPageFactory(title="Bandsaw", guild=None)
        response = client.post(
            reverse("hub_wiki_create", args=["howto"]),
            {
                "title": "Bandsaw",
                "kind": WikiPage.Kind.HOWTO,
                "guild": "",
                "body": "<p>Another one.</p>",
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
        assert response.status_code == 200
        assert b"already exists" in response.content
        assert WikiPage.objects.filter(title="Bandsaw").count() == 1


def describe_the_queue_scope_line():
    def it_names_one_guild_two_guilds_and_every_scope():
        from hub.wiki_views import _queue_scope_line

        first = GuildFactory(name="Woodworking")
        second = GuildFactory(name="Metals")
        assert _queue_scope_line([first], False) == "Showing reports for Woodworking."
        assert _queue_scope_line([first, second], False) == "Showing reports for Woodworking and Metals."
        assert _queue_scope_line([first, second], True) == "Showing every scope."

    def it_says_so_when_there_is_no_scope_at_all():
        from hub.wiki_views import _queue_scope_line

        assert _queue_scope_line([], False) == "You do not lead or staff a guild yet."


def describe_an_account_with_no_member_row():
    def it_refuses_the_resolve_route(client: Client):
        # is_effective_staff reads the view_as role off the User, so an admin account whose
        # Member row is gone still passes can_moderate_wiki_page and then has nobody to
        # attribute the resolution to. Every other write route in this module carries the
        # same pair of checks for the same reason.
        from django.contrib.auth.models import User

        report = WikiReportFactory(page=WikiPageFactory(guild=None))
        user = User.objects.create_user(username="guard_no_member", password="pass", is_superuser=True, is_staff=True)
        user.member.delete()
        client.login(username="guard_no_member", password="pass")
        assert client.post(reverse("hub_wiki_report_resolve", args=[report.pk])).status_code == 403


def describe_the_withdraw_route_without_a_member():
    def it_refuses_a_previewing_admin_acting_as_a_guest(client: Client):
        login(client, "guard_wd_guest", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory()
        WikiReportFactory(page=page)
        session = client.session
        session["view_as_role"] = "guest"
        session.save()
        assert client.post(reverse("hub_wiki_report_withdraw", args=[page.slug])).status_code == 403


def describe_the_phone_bars_report_cell():
    def it_drops_the_muted_sentence_so_the_fixed_bar_keeps_its_one_row(client: Client):
        # .pl-wp-actionbar is a fixed, no-wrap flex row whose height is reserved by
        # .pl-wp-has-actionbar's 5.5rem. The desktop control's unshrinkable sentence wrapped
        # that cell to two lines and pushed the bar past its reserve, and a fixed element
        # covers content with no warning.
        user = login(client, "bar_reported")
        page = WikiPageFactory()
        WikiReportFactory(page=page, reporter=user.member)
        body = client.get(page.get_absolute_url()).content
        bar = body.split(b'class="pl-wp-actionbar"')[1]
        assert b"You reported this" not in bar
        assert b"Withdraw" in bar

    def it_keeps_the_sentence_on_the_desktop_control(client: Client):
        user = login(client, "bar_reported2")
        page = WikiPageFactory()
        WikiReportFactory(page=page, reporter=user.member)
        header = client.get(page.get_absolute_url()).content.split(b'class="pl-wp-actionbar"')[0]
        assert b"You reported this" in header


def _element_with_id(body: bytes, element_id: str) -> bytes:
    """The full source of the <div> carrying ``element_id``, tags balanced.

    Written because the obvious assertion is worthless: "the modal appears after the first
    </div> following the id" holds whether the modal is inside the card or outside it,
    because that first close belongs to a nested header. Only a depth-counted slice
    actually bounds the element.
    """
    marker = body.index(f'id="{element_id}"'.encode())
    start = body.rindex(b"<div", 0, marker)
    depth = 0
    cursor = start
    while True:
        next_open = body.find(b"<div", cursor)
        next_close = body.find(b"</div>", cursor)
        assert next_close != -1, f"unbalanced markup around #{element_id}"
        if next_open != -1 and next_open < next_close:
            depth += 1
            cursor = next_open + len(b"<div")
        else:
            depth -= 1
            cursor = next_close + len(b"</div>")
            if depth == 0:
                return body[start:cursor]


def describe_the_modals_the_queue_swaps_around():
    """Neither modal may live inside the element its own response replaces.

    An out-of-band swap of the card tears the modal out from under its own form: the
    decline reply had nowhere to land, and the resolve form's target vanished mid-request.
    """

    def it_keeps_the_decline_modal_outside_the_safety_card(client: Client):
        from tests.hub.wiki_mod_helpers import login_lead

        _user, guild = login_lead(client, "decline_placement")
        proposal = WikiPageFactory(guild=guild, official=True, is_published=False)
        body = client.get(reverse("hub_wiki_review")).content
        card = _element_with_id(body, f"safety-{proposal.pk}")
        # The modal BODY is what the swap must not destroy. The card still carries the
        # button that opens it, which is right: the trigger belongs with the row.
        assert f'id="wiki-decline-{proposal.pk}-body"'.encode() not in card
        assert f"open-modal', 'wiki-decline-{proposal.pk}".encode() in card
        assert f'id="wiki-decline-{proposal.pk}-body"'.encode() in body

    def it_keeps_the_resolve_modal_outside_the_report_card(client: Client):
        login(client, "resolve_placement", fog_role=Member.FogRole.ADMIN)
        report = WikiReportFactory(page=WikiPageFactory(guild=None))
        body = client.get(reverse("hub_wiki_review")).content
        card = _element_with_id(body, f"report-{report.pk}")
        assert f'id="resolve-{report.pk}-body"'.encode() not in card
        assert f"open-modal', 'resolve-{report.pk}".encode() in card
        assert f'id="resolve-{report.pk}-body"'.encode() in body

    def it_bounds_the_card_and_not_merely_its_first_nested_close(client: Client):
        # The guard on the guard: the slice must stop at the CARD's close, so it has to be
        # shorter than the document and still hold the card's own content.
        login(client, "slice_sanity", fog_role=Member.FogRole.ADMIN)
        report = WikiReportFactory(page=WikiPageFactory(guild=None), reason="A reason inside the card.")
        body = client.get(reverse("hub_wiki_review")).content
        card = _element_with_id(body, f"report-{report.pk}")
        assert b"A reason inside the card." in card
        assert b"Mark Reviewed" in card
        assert len(card) < len(body)
