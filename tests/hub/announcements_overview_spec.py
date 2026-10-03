"""The Announcements page (/announcements/) and the sent view (/announcements/sent/<pk>/).

Drafts and Sent tabs over every announcement the viewer may handle: admins see every row, guild
leads and staff their guilds' rows, instructors their classes' rows (``hub.views._announcement_rows``).
The sent view reads reach back from the delivery ledger and rebuilds each channel's preview with
the composer preview's own builder. Assertions anchor on markup (``data-`` attributes, URLs, ids)
and factory strings, because the changelog renders on every hub page.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering
from core.models import EventDelivery
from hub.views import _compose_refusal_message
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import (
    AnnouncementDraftFactory,
    FundingSnapshotFactory,
    GuildFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_SITE = AnnouncementDraft.Audience.SITE
_GUILD = AnnouncementDraft.Audience.GUILD
_CLASS = AnnouncementDraft.Audience.CLASS
_CHANNEL = GuildAnnouncement.DiscordChannel


def _login_admin(client: Client, username: str = "admin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _login_lead(client: Client, guild, username: str = "lead") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    client.login(username=username, password="p")
    return user


def _instructor(client: Client, username: str = "teach", *, status: str = ClassOffering.Status.PUBLISHED, slug=True):
    """Log in a member granted teaching who teaches one class; returns (user, offering)."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    member.instructor_oriented_at = timezone.now()
    member.instructor_slug = username if slug else ""
    member.save(update_fields=["instructor_oriented_at", "instructor_slug"])
    offering = ClassOfferingFactory(instructor=member, status=status)
    client.login(username=username, password="p")
    return user, offering


def _page(client: Client, tab: str | None = None, **params: str) -> str:
    query = {**({"tab": tab} if tab is not None else {}), **params}
    return client.get(reverse("hub_announcements"), query).content.decode()


def _row_pks(html: str) -> list[int]:
    return [int(pk) for pk in re.findall(r'data-announcement-row="(\d+)"', html)]


def _row(html: str, pk: int) -> str:
    """The markup of one ``<tr>`` on the page."""
    start = html.index(f'data-announcement-row="{pk}"')
    return html[start : html.index("</tr>", start)]


def _tab(html: str, name: str) -> str:
    """The opening ``<a>`` tag of one tab."""
    match = re.search(rf'<a [^>]*data-announcements-tab="{name}"[^>]*>', html)
    assert match, name
    return match.group(0)


def _active(html: str, name: str) -> bool:
    tag = _tab(html, name)
    return "vote-tab--active" in tag and 'aria-current="page"' in tag


def _messages(response) -> list[str]:
    return [message.message for message in get_messages(response.wsgi_request)]


def _ago(row: AnnouncementDraft, **fields_ago: timedelta) -> None:
    """Move a row's timestamps into the past (``updated_at`` is auto_now, so through ``update``)."""
    AnnouncementDraft.objects.filter(pk=row.pk).update(
        **{field: timezone.now() - delta for field, delta in fields_ago.items()}
    )


def _deliver(row: AnnouncementDraft, target_ref: str, channel: str) -> None:
    EventDelivery.objects.create(
        event_key=row.event_key, target_ref=target_ref, channel=channel, period=row.delivery_period
    )


def describe_the_page():
    def it_renders_the_drafts_tab_by_default_with_both_tabs_and_the_new_button(client: Client):
        _login_admin(client)
        response = client.get(reverse("hub_announcements"))
        assert response.status_code == 200
        assert [t.name for t in response.templates][0] == "hub/announcements.html"
        html = response.content.decode()
        assert (_active(html, "drafts"), _active(html, "sent")) == (True, False)
        assert f'href="{reverse("hub_announcements")}?tab=sent"' in _tab(html, "sent")
        assert (
            f'href="{reverse("hub_compose")}" class="hub-btn hub-btn--primary" data-help-key="announcements.new"'
            in html
        )

    def it_opens_the_sent_tab_when_asked(client: Client):
        _login_admin(client)
        html = _page(client, "sent")
        assert (_active(html, "drafts"), _active(html, "sent")) == (False, True)

    def it_treats_an_unknown_tab_as_drafts(client: Client):
        _login_admin(client)
        AnnouncementDraftFactory(sent=True)
        draft = AnnouncementDraftFactory()
        html = _page(client, "bogus")
        assert (_active(html, "drafts"), _active(html, "sent")) == (True, False)
        assert _row_pks(html) == [draft.pk]

    def it_counts_the_rows_the_viewer_may_see_on_each_tab(client: Client):
        guild, other = GuildFactory(), GuildFactory()
        _login_lead(client, guild)
        AnnouncementDraftFactory(audience=_GUILD, guild=guild)
        AnnouncementDraftFactory(audience=_GUILD, guild=guild, given_up=True)
        AnnouncementDraftFactory(audience=_GUILD, guild=guild, sent=True)
        AnnouncementDraftFactory(audience=_GUILD, guild=other)
        AnnouncementDraftFactory(sent=True)
        html = _page(client)
        assert '<span class="hub-badge" data-announcements-count="drafts">2</span>' in html
        assert '<span class="hub-badge" data-announcements-count="sent">1</span>' in html

    def describe_the_empty_states():
        def it_says_there_are_no_drafts_and_shows_no_table(client: Client):
            _login_admin(client)
            AnnouncementDraftFactory(sent=True)
            html = _page(client)
            assert 'data-announcements-empty="drafts"' in html
            assert "data-announcements-table" not in html

        def it_says_nothing_was_sent_and_shows_no_table(client: Client):
            _login_admin(client)
            AnnouncementDraftFactory()
            html = _page(client, "sent")
            assert 'data-announcements-empty="sent"' in html
            assert "data-announcements-table" not in html


def describe_which_rows_and_in_what_order():
    def it_lists_unsent_unqueued_rows_on_drafts_newest_edit_first(client: Client):
        _login_admin(client)
        older = AnnouncementDraftFactory()
        given_up = AnnouncementDraftFactory(given_up=True)
        newest = AnnouncementDraftFactory()
        AnnouncementDraftFactory(queued=True)
        AnnouncementDraftFactory(sent=True)
        _ago(older, updated_at=timedelta(hours=3))
        _ago(given_up, updated_at=timedelta(hours=1))
        assert _row_pks(_page(client)) == [newest.pk, given_up.pk, older.pk]

    def it_lists_sent_and_queued_rows_on_sent_with_the_queued_one_first(client: Client):
        _login_admin(client)
        old_sent = AnnouncementDraftFactory(sent=True)
        new_sent = AnnouncementDraftFactory(sent=True)
        queued = AnnouncementDraftFactory(queued=True)
        AnnouncementDraftFactory()
        _ago(old_sent, sent_at=timedelta(days=3))
        _ago(new_sent, sent_at=timedelta(days=1))
        _ago(queued, updated_at=timedelta(days=9))
        assert _row_pks(_page(client, "sent")) == [queued.pk, new_sent.pk, old_sent.pk]

    def it_pages_26_rows_into_25_and_1_and_keeps_the_tab(client: Client):
        _login_admin(client)
        rows = [AnnouncementDraftFactory(sent=True) for _ in range(26)]
        first = _page(client, "sent")
        assert len(_row_pks(first)) == 25
        assert 'href="?page=2&tab=sent"' in first
        second = _page(client, "sent", page="2")
        assert len(_row_pks(second)) == 1
        assert set(_row_pks(first) + _row_pks(second)) == {row.pk for row in rows}


def describe_the_columns():
    def it_marks_each_rows_state_and_shows_why_a_send_could_not_go(client: Client):
        _login_admin(client)
        draft = AnnouncementDraftFactory()
        failed = AnnouncementDraftFactory(given_up=True)
        drafts = _page(client)
        assert f'data-announcement-row="{draft.pk}" data-announcement-state="draft"' in drafts
        assert f'data-announcement-row="{failed.pk}" data-announcement-state="could_not_send"' in drafts
        assert '<span class="pl-announcements-table__note" data-send-error>Factory provider timed out.</span>' in drafts
        queued = AnnouncementDraftFactory(queued=True)
        sent = AnnouncementDraftFactory(sent=True)
        sent_tab = _page(client, "sent")
        assert f'data-announcement-row="{queued.pk}" data-announcement-state="sending"' in sent_tab
        assert f'data-announcement-row="{sent.pk}" data-announcement-state="sent"' in sent_tab

    def it_says_a_queued_row_whose_try_failed_is_trying_again(client: Client):
        _login_admin(client)
        retrying = AnnouncementDraftFactory(queued=True, send_error="Provider down.")
        fresh = AnnouncementDraftFactory(queued=True)
        html = _page(client, "sent")
        assert 'class="hub-pill hub-pill--warn"' in _row(html, retrying.pk)
        assert 'data-sending-note="retrying"' in _row(html, retrying.pk)
        assert 'data-sending-note="queued"' in _row(html, fresh.pk)

    def it_truncates_a_long_send_error_to_80_characters(client: Client):
        _login_admin(client)
        failed = AnnouncementDraftFactory(send_error="x" * 200)
        assert f"data-send-error>{'x' * 79}…</span>" in _row(_page(client), failed.pk)

    def it_links_a_draft_to_the_composer_and_a_sent_row_to_its_record(client: Client):
        _login_admin(client)
        draft = AnnouncementDraftFactory()
        sent = AnnouncementDraftFactory(sent=True)
        drafts = _row(_page(client), draft.pk)
        assert (
            f'<a href="{reverse("hub_compose_resume", args=[draft.pk])}" class="pl-announcements-table__title">'
            in drafts
        )
        assert (
            f'href="{reverse("hub_compose_resume", args=[draft.pk])}" class="pl-btn pl-btn--secondary pl-btn--sm"'
            in drafts
        )
        assert f"$dispatch('open-confirm', 'del-draft-{draft.pk}')" in drafts
        sent_row = _row(_page(client, "sent"), sent.pk)
        assert (
            f'<a href="{reverse("hub_announcement_sent", args=[sent.pk])}" class="pl-announcements-table__title">'
            in sent_row
        )
        assert "data-announcement-view" in sent_row
        assert "open-confirm" not in sent_row

    def it_shows_the_flattened_message_excerpt_or_says_there_is_none(client: Client):
        _login_admin(client)
        with_body = AnnouncementDraftFactory(body="<p>Bring <b>gloves</b> on Saturday.</p>")
        empty = AnnouncementDraftFactory(body="")
        html = _page(client)
        assert '<span class="pl-announcements-table__excerpt">Bring gloves on Saturday.</span>' in _row(
            html, with_body.pk
        )
        assert '<span class="pl-announcements-table__excerpt">No message yet.</span>' in _row(html, empty.pk)

    def it_names_the_audience(client: Client):
        _login_admin(client)
        guild = GuildFactory(name="Factory Woodshop")
        offering = ClassOfferingFactory(title="Factory Blacksmithing")
        site = AnnouncementDraftFactory()
        guild_row = AnnouncementDraftFactory(audience=_GUILD, guild=guild)
        class_row = AnnouncementDraftFactory(audience=_CLASS, class_offering=offering)
        html = _page(client)
        assert '<td data-label="Audience">Everyone (site-wide)</td>' in _row(html, site.pk)
        assert '<td data-label="Audience">Factory Woodshop</td>' in _row(html, guild_row.pk)
        assert '<td data-label="Audience">Factory Blacksmithing</td>' in _row(html, class_row.pk)

    def it_lists_the_channels_and_never_discord_for_a_class(client: Client):
        _login_admin(client)
        every = AnnouncementDraftFactory(discord_channel=_CHANNEL.GENERAL)
        class_row = AnnouncementDraftFactory(
            audience=_CLASS, class_offering=ClassOfferingFactory(), discord_channel=_CHANNEL.GENERAL
        )
        none = AnnouncementDraftFactory(send_email=False, push_enabled=False, discord_enabled=False)
        html = _page(client)
        assert '<td data-label="Channels" data-channels>Email · Push · Discord</td>' in _row(html, every.pk)
        assert '<td data-label="Channels" data-channels>Email · Push</td>' in _row(html, class_row.pk)
        assert '<td data-label="Channels" data-channels>App only</td>' in _row(html, none.pk)

    def it_names_who_edited_or_sent_it(client: Client):
        _login_admin(client)
        named = User.objects.create_user(username="ana", first_name="Factory", last_name="Ruiz")
        by_ana = AnnouncementDraftFactory(author=named)
        automatic = AnnouncementDraftFactory(author=None, funding_snapshot=FundingSnapshotFactory())
        left = AnnouncementDraftFactory(author=None)
        orphaned = AnnouncementDraftFactory(author=None, sent=True)
        drafts = _page(client)
        assert '<td data-label="Edited by" class="pl-announcements-table__who">Unknown</td>' in _row(drafts, left.pk)
        assert '<td data-label="Edited by" class="pl-announcements-table__who">Factory Ruiz</td>' in _row(
            drafts, by_ana.pk
        )
        assert '<td data-label="Edited by" class="pl-announcements-table__who">Automatic</td>' in _row(
            drafts, automatic.pk
        )
        assert '<td data-label="Sent by" class="pl-announcements-table__who">Unknown</td>' in _row(
            _page(client, "sent"), orphaned.pk
        )

    def it_dates_a_draft_by_its_last_edit_and_a_queued_row_by_its_queueing(client: Client):
        _login_admin(client)
        draft = AnnouncementDraftFactory()
        queued = AnnouncementDraftFactory(queued=True)
        sent = AnnouncementDraftFactory(sent=True)
        for row in (draft, queued, sent):
            row.refresh_from_db()
        fmt = "%b %-d, %-I:%M"
        local = timezone.localtime
        assert (
            f'<td data-label="Last edited" class="pl-announcements-table__when">{local(draft.updated_at).strftime(fmt)}'
            in _row(_page(client), draft.pk)
        )
        sent_tab = _page(client, "sent")
        assert (
            f'<td data-label="Sent" class="pl-announcements-table__when">Queued {local(queued.send_requested_at).strftime(fmt)}'
            in _row(sent_tab, queued.pk)
        )
        assert (
            f'<td data-label="Sent" class="pl-announcements-table__when">{local(sent.sent_at).strftime(fmt)}'
            in _row(sent_tab, sent.pk)
        )


def describe_reach_on_the_sent_tab():
    def it_counts_distinct_people_and_leaves_out_the_discord_post(client: Client):
        _login_admin(client)
        row = AnnouncementDraftFactory(sent=True, delivery_period="announce:900")
        for target, channel in (
            ("user:1", "in_app"),
            ("user:1", "email"),
            ("user:2", "in_app"),
            ("email:guest@x.com", "email"),
            ("broadcast", "discord"),
        ):
            _deliver(row, target, channel)
        assert '<td data-label="Reached" data-reach="3">3</td>' in _row(_page(client, "sent"), row.pk)

    def it_shows_no_number_for_a_queued_row_or_one_never_recorded(client: Client):
        _login_admin(client)
        queued = AnnouncementDraftFactory(queued=True)
        unrecorded = AnnouncementDraftFactory(sent=True)
        nobody = AnnouncementDraftFactory(sent=True, delivery_period="announce:901")
        html = _page(client, "sent")
        assert 'data-reach="not-yet">Not yet</td>' in _row(html, queued.pk)
        assert 'data-reach="not-recorded">Not recorded</td>' in _row(html, unrecorded.pk)
        assert 'data-reach="0">0</td>' in _row(html, nobody.pk)

    def it_runs_the_same_number_of_queries_for_2_rows_as_for_12(client: Client, django_assert_num_queries):
        _login_admin(client)
        guild = GuildFactory()
        offering = ClassOfferingFactory()

        def _batch() -> None:
            AnnouncementDraftFactory(sent=True, delivery_period=f"announce:{AnnouncementDraft.objects.count()}")
            AnnouncementDraftFactory(audience=_GUILD, guild=guild, queued=True)
            AnnouncementDraftFactory(audience=_CLASS, class_offering=offering, sent=True, author=None)

        AnnouncementDraftFactory(sent=True, delivery_period="announce:two")
        AnnouncementDraftFactory(audience=_GUILD, guild=guild, queued=True)
        _page(client, "sent")  # warm the session and the sidebar caches
        with CaptureQueriesContext(connection) as two:
            assert len(_row_pks(_page(client, "sent"))) == 2
        # Read now: captured_queries slices the connection's log, which the next request resets.
        queries_for_two = len(two.captured_queries)
        for _ in range(3):
            _batch()
        AnnouncementDraftFactory(sent=True)
        assert len(_row_pks(_page(client, "sent"))) == 12
        with django_assert_num_queries(queries_for_two):
            _page(client, "sent")


def describe_who_sees_what():
    def describe_an_admin():
        def it_sees_every_audience_from_every_author_on_both_tabs(client: Client):
            _login_admin(client)
            other = User.objects.create_superuser(username="other", email="o@x.com", password="p")
            guild = GuildFactory()
            offering = ClassOfferingFactory()
            drafts = [
                AnnouncementDraftFactory(author=other),
                AnnouncementDraftFactory(author=None, audience=_GUILD, guild=guild),
                AnnouncementDraftFactory(audience=_CLASS, class_offering=offering),
            ]
            sent = [
                AnnouncementDraftFactory(author=other, sent=True),
                AnnouncementDraftFactory(audience=_GUILD, guild=guild, sent=True),
                AnnouncementDraftFactory(audience=_CLASS, class_offering=offering, sent=True),
            ]
            assert set(_row_pks(_page(client))) == {row.pk for row in drafts}
            assert set(_row_pks(_page(client, "sent"))) == {row.pk for row in sent}

        def it_opens_another_admins_sent_record(client: Client):
            _login_admin(client)
            other = User.objects.create_superuser(username="other", email="o@x.com", password="p")
            row = AnnouncementDraftFactory(author=other, sent=True)
            assert client.get(reverse("hub_announcement_sent", args=[row.pk])).status_code == 200

        def it_is_refused_while_previewing_as_a_member(client: Client):
            admin = _login_admin(client)
            AnnouncementDraftFactory(author=admin)
            session = client.session
            session["view_as_role"] = "member"
            session.save()
            response = client.get(reverse("hub_announcements"))
            assert response.status_code == 302
            assert response.url == reverse("hub_guild_announcement_propose")
            assert _messages(response) == [_compose_refusal_message(response.wsgi_request)]

    def describe_a_guild_lead():
        def it_sees_its_guilds_rows_from_any_author_and_no_other_guilds_or_site_rows(client: Client):
            guild, other = GuildFactory(), GuildFactory()
            _login_lead(client, guild)
            admin = User.objects.create_superuser(username="boss", email="b@x.com", password="p")
            own_draft = AnnouncementDraftFactory(author=admin, audience=_GUILD, guild=guild)
            own_sent = AnnouncementDraftFactory(author=None, audience=_GUILD, guild=guild, sent=True)
            AnnouncementDraftFactory(audience=_GUILD, guild=other)
            AnnouncementDraftFactory(audience=_GUILD, guild=other, sent=True)
            AnnouncementDraftFactory(author=admin)
            AnnouncementDraftFactory(author=admin, sent=True)
            assert _row_pks(_page(client)) == [own_draft.pk]
            assert _row_pks(_page(client, "sent")) == [own_sent.pk]

        def it_sees_rows_for_its_guilds_classes(client: Client):
            from classes.factories import CategoryFactory

            guild = GuildFactory()
            _login_lead(client, guild)
            offering = ClassOfferingFactory(
                category=CategoryFactory(guild=guild), status=ClassOffering.Status.PUBLISHED
            )
            row = AnnouncementDraftFactory(audience=_CLASS, class_offering=offering)
            assert _row_pks(_page(client)) == [row.pk]

        def it_cannot_open_another_guilds_record_or_draft(client: Client):
            guild, other = GuildFactory(), GuildFactory()
            _login_lead(client, guild)
            sent = AnnouncementDraftFactory(audience=_GUILD, guild=other, sent=True)
            draft = AnnouncementDraftFactory(audience=_GUILD, guild=other)
            assert client.get(reverse("hub_announcement_sent", args=[sent.pk])).status_code == 404
            assert client.get(reverse("hub_compose_resume", args=[draft.pk])).status_code == 404
            assert client.post(reverse("hub_compose_delete_draft", args=[draft.pk])).status_code == 404
            assert AnnouncementDraft.objects.filter(pk=draft.pk).exists()

    def describe_a_guild_officer():
        def it_sees_its_staffed_guilds_and_its_own_saves_not_every_guild(client: Client):
            from membership.models import Member

            guild, other = GuildFactory(), GuildFactory()
            user = _login_lead(client, guild, username="officer")
            member = user.member
            member.fog_role = Member.FogRole.GUILD_OFFICER
            member.save(update_fields=["fog_role"])
            own = AnnouncementDraftFactory(audience=_GUILD, guild=guild)
            saved_elsewhere = AnnouncementDraftFactory(author=user, audience=_GUILD, guild=other)
            AnnouncementDraftFactory(audience=_GUILD, guild=other)
            assert set(_row_pks(_page(client))) == {own.pk, saved_elsewhere.pk}

    def describe_an_instructor():
        def it_sees_its_own_classes_rows_and_not_another_instructors(client: Client):
            _user, offering = _instructor(client)
            own = AnnouncementDraftFactory(audience=_CLASS, class_offering=offering, sent=True)
            AnnouncementDraftFactory(audience=_CLASS, class_offering=ClassOfferingFactory(), sent=True)
            AnnouncementDraftFactory(sent=True)
            assert _row_pks(_page(client, "sent")) == [own.pk]

        def it_opens_the_page_with_only_a_lock_link_class_row(client: Client):
            user, offering = _instructor(client, "lockonly", status=ClassOffering.Status.DRAFT, slug=False)
            row = AnnouncementDraftFactory(author=user, audience=_CLASS, class_offering=offering)
            response = client.get(reverse("hub_announcements"))
            assert response.status_code == 200
            assert _row_pks(response.content.decode()) == [row.pk]

        def it_refuses_a_lock_only_instructor_with_no_rows(client: Client):
            _instructor(client, "lockonly", status=ClassOffering.Status.DRAFT, slug=False)
            response = client.get(reverse("hub_announcements"))
            assert response.status_code == 302
            assert response.url == reverse("hub_guild_announcement_propose")

    def describe_an_unlinked_account():
        def it_sees_nothing_and_is_refused(client: Client):
            # No MembershipPlan, so no Member is made for the user: an account with no member.
            user = User.objects.create_user(username="unlinked", email="u@x.com", password="p")
            client.login(username="unlinked", password="p")
            AnnouncementDraftFactory(author=user, audience=_GUILD, guild=GuildFactory())
            response = client.get(reverse("hub_announcements"))
            assert response.status_code == 302
            assert response.url == reverse("hub_guild_announcement_propose")

    def describe_the_gate():
        def it_is_asked_once_per_distinct_audience_never_per_row(client: Client, monkeypatch):
            import hub.views

            guild, other = GuildFactory(), GuildFactory()
            user = _login_lead(client, guild)
            for _ in range(3):
                AnnouncementDraftFactory(audience=_GUILD, guild=guild)
            AnnouncementDraftFactory(author=user, audience=_GUILD, guild=other)
            AnnouncementDraftFactory(author=user)
            asked: list[str] = []
            real = hub.views._compose_audience_forbidden

            def gate(request, raw_audience):
                asked.append(raw_audience)
                return real(request, raw_audience)

            monkeypatch.setattr(hub.views, "_compose_audience_forbidden", gate)
            rows = hub.views._announcement_rows(client.get(reverse("hub_home")).wsgi_request, user.member)
            assert sorted(asked) == sorted(["site", f"guild:{guild.pk}", f"guild:{other.pk}"])
            assert {row.guild for row in rows} == {guild}

    def describe_a_plain_member():
        def it_is_sent_to_propose_with_the_refusal(client: Client):
            MembershipPlanFactory()
            User.objects.create_user(username="plain", email="plain@x.com", password="p")
            client.login(username="plain", password="p")
            AnnouncementDraftFactory(sent=True)
            response = client.get(reverse("hub_announcements"))
            assert response.status_code == 302
            assert response.url == reverse("hub_guild_announcement_propose")
            assert _messages(response) == [
                "Your account cannot send announcements. You can propose one here for a lead to review."
            ]

        def it_cannot_open_a_sent_record(client: Client):
            MembershipPlanFactory()
            User.objects.create_user(username="plain", email="plain@x.com", password="p")
            client.login(username="plain", password="p")
            row = AnnouncementDraftFactory(sent=True)
            assert client.get(reverse("hub_announcement_sent", args=[row.pk])).status_code == 404


def describe_the_composer_tests_and_a_crafted_draft_pk():
    """A ``draft_pk`` the requester may not handle gives the plain category, never the results title."""

    def _results_draft_by_an_admin() -> AnnouncementDraft:
        snapshot = FundingSnapshotFactory(
            cycle_label="Factory Month",
            results={"votes_cast": 2, "results": [{"guild_name": "Metal", "funding": "10.00", "share_pct": 100.0}]},
        )
        return snapshot.make_results_draft()

    def it_keeps_the_test_email_on_the_plain_category(client: Client, mailoutbox):
        results = _results_draft_by_an_admin()
        guild = GuildFactory(name="Factory Forge")
        _login_lead(client, guild)
        data = {"audience": f"guild:{guild.pk}", "body": "<p>x</p>", "draft_pk": str(results.pk)}
        assert client.post(reverse("hub_compose_test"), data).status_code == 204
        assert [m.subject for m in mailoutbox] == ["Factory Forge Announcement"]

    def it_keeps_the_push_test_on_the_plain_category(client: Client, monkeypatch):
        from core import push_admin

        sent: dict = {}

        def fake(user, **kwargs):
            sent.update(kwargs)
            return push_admin.TestSendResult(delivered=1, attempted=1)

        monkeypatch.setattr(push_admin, "send_test_push", fake)
        results = _results_draft_by_an_admin()
        guild = GuildFactory(name="Factory Forge")
        _login_lead(client, guild)
        data = {"audience": f"guild:{guild.pk}", "body": "<p>x</p>", "draft_pk": str(results.pk)}
        client.post(reverse("hub_compose_push_test"), data)
        assert sent["title"] == "Factory Forge Announcement"

    def it_gives_the_results_title_to_an_admin_handling_the_jobs_draft(client: Client, mailoutbox):
        results = _results_draft_by_an_admin()
        _login_admin(client)
        data = {"audience": "site", "body": "<p>x</p>", "draft_pk": str(results.pk)}
        client.post(reverse("hub_compose_test"), data)
        assert [m.subject for m in mailoutbox] == ["Factory Month Voting Results"]


def describe_the_composer_after_a_send():
    def it_lands_a_class_send_on_the_sent_tab_with_the_row_sent(client: Client):
        _user, offering = _instructor(client)
        response = client.post(
            reverse("hub_compose_send"),
            {
                "audience": f"class:{offering.pk}",
                "body": "<p>Bring gloves.</p>",
                "discord_channel": "",
                "mention": "none",
            },
        )
        assert response.url == f"{reverse('hub_announcements')}?tab=sent"
        draft = AnnouncementDraft.objects.get()
        assert (
            f'data-announcement-row="{draft.pk}" data-announcement-state="sent"'
            in client.get(response.url).content.decode()
        )

    def it_shows_the_second_admin_as_the_editor_after_they_save(client: Client):
        first = User.objects.create_superuser(username="first", email="f@x.com", password="p")
        draft = AnnouncementDraftFactory(author=first)
        second = _login_admin(client, username="second")
        second.first_name, second.last_name = "Factory", "Second"
        second.save(update_fields=["first_name", "last_name"])
        client.post(
            reverse("hub_compose_save_draft"),
            {
                "audience": "site",
                "body": "<p>v2</p>",
                "discord_channel": "none",
                "mention": "none",
                "draft_pk": str(draft.pk),
            },
        )
        assert '<td data-label="Edited by" class="pl-announcements-table__who">Factory Second</td>' in _row(
            _page(client), draft.pk
        )


def describe_the_draft_meta_line_in_the_composer():
    def it_names_who_saved_the_draft_last(client: Client):
        _login_admin(client)
        saver = User.objects.create_user(username="saver", first_name="Factory", last_name="Saver")
        draft = AnnouncementDraftFactory(author=saver)
        html = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert '<p class="pl-compose-draft-meta" data-compose-draft-meta>' in html
        assert re.search(r"data-compose-draft-saver>Draft last saved by Factory Saver, \w{3} \d{1,2}, ", html)
        assert "data-compose-draft-automatic" not in html
        assert "data-compose-draft-failed" not in html

    def it_says_the_job_made_a_results_draft(client: Client):
        _login_admin(client)
        snapshot = FundingSnapshotFactory(
            cycle_label="Factory Month",
            results={"votes_cast": 2, "results": [{"guild_name": "Metal", "funding": "10.00", "share_pct": 100.0}]},
        )
        draft = snapshot.make_results_draft()
        html = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert re.search(
            r"data-compose-draft-automatic>Draft made automatically \w{3} \d{1,2}, .* from the Factory Month voting results\.</span>",
            html,
        )
        assert "data-compose-draft-saver" not in html

    def it_says_why_a_send_could_not_go(client: Client):
        admin = _login_admin(client)
        draft = AnnouncementDraftFactory(author=admin, given_up=True)
        html = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert (
            "data-compose-draft-failed>This announcement could not be sent: Factory provider timed out. Check it"
            in html
        )

    def it_shows_no_meta_line_on_a_fresh_compose(client: Client):
        _login_admin(client)
        assert "data-compose-draft-meta" not in client.get(reverse("hub_compose")).content.decode()


def describe_the_sent_view():
    def _sent_site_row(**overrides) -> AnnouncementDraft:
        fields = {
            "sent": True,
            "delivery_period": "announce:950",
            "body": "<p>Factory open house this Saturday.</p>",
            "discord_channel": _CHANNEL.GENERAL,
            "mention": AnnouncementDraft.Mention.EVERYONE,
        }
        fields.update(overrides)
        return AnnouncementDraftFactory(**fields)

    def _view(client: Client, row: AnnouncementDraft) -> str:
        return client.get(reverse("hub_announcement_sent", args=[row.pk])).content.decode()

    def _fact(html: str, name: str) -> str:
        match = re.search(rf'<dd data-fact="{name}">(.*?)</dd>', html, re.S)
        assert match, name
        return match.group(1)

    def it_reads_each_channel_from_the_ledger(client: Client):
        admin = _login_admin(client)
        row = _sent_site_row(author=admin)
        for target, channel in (
            ("user:1", "in_app"),
            ("user:1", "email"),
            ("user:1", "push"),
            ("user:2", "in_app"),
            ("user:2", "email"),
            ("email:g@x.com", "email"),
            ("broadcast", "discord"),
        ):
            _deliver(row, target, channel)
        html = _view(client, row)
        assert (_fact(html, "reached"), _fact(html, "in_app"), _fact(html, "email"), _fact(html, "push")) == (
            "3 people",
            "2 members",
            "3",
            "1 member",
        )
        assert _fact(html, "discord") == "Sent to #general-chat with @everyone"
        assert _fact(html, "audience") == "Everyone (site-wide)"
        assert "data-reach-not-recorded" not in html

    def it_says_one_person_and_names_the_guild_channel_without_a_ping(client: Client):
        _login_admin(client)
        guild = GuildFactory(name="Factory Ceramics")
        row = _sent_site_row(
            audience=_GUILD,
            guild=guild,
            discord_channel=_CHANNEL.GUILD,
            mention=AnnouncementDraft.Mention.NONE,
            recipient_selection={"users": [1]},
        )
        _deliver(row, "user:1", "in_app")
        _deliver(row, "broadcast:guild:1", "discord")
        html = _view(client, row)
        assert _fact(html, "reached") == "1 person"
        assert _fact(html, "discord") == "Sent to the Factory Ceramics channel"
        assert _fact(html, "audience") == "Factory Ceramics (chosen recipients)"

    def it_says_off_for_a_switched_off_channel_and_not_posted_without_a_discord_row(client: Client):
        _login_admin(client)
        off = _sent_site_row(send_email=False, push_enabled=False, discord_enabled=False)
        silent = _sent_site_row(delivery_period="announce:951")
        off_html = _view(client, off)
        assert (_fact(off_html, "email"), _fact(off_html, "push"), _fact(off_html, "discord")) == ("Off", "Off", "Off")
        assert "data-announcement-previews" not in off_html
        assert _fact(_view(client, silent), "discord") == "Not posted"

    def it_says_not_recorded_for_a_row_sent_before_reach_was_kept(client: Client):
        _login_admin(client)
        row = _sent_site_row(delivery_period="")
        html = _view(client, row)
        assert {_fact(html, name) for name in ("reached", "in_app", "email", "push", "discord")} == {"Not recorded"}
        assert "data-reach-not-recorded" in html

    def it_says_not_yet_while_sending_and_why_the_last_try_failed(client: Client):
        _login_admin(client)
        row = AnnouncementDraftFactory(
            queued=True, send_error="Factory provider down.", discord_channel=_CHANNEL.GENERAL
        )
        html = _view(client, row)
        assert {_fact(html, name) for name in ("reached", "in_app", "email", "push", "discord")} == {"Not yet"}
        assert 'data-announcement-state="sending"' in html
        assert "The last try failed: Factory provider down. It tries again" in html
        assert "data-reach-not-recorded" not in html

    def it_says_a_site_send_included_members_who_had_not_logged_in(client: Client):
        _login_admin(client)
        html = _view(client, _sent_site_row(include_never_logged_in=True))
        assert _fact(html, "audience") == "Everyone (site-wide), including members who hadn't logged in yet"

    def it_lists_the_people_added_to_a_site_send(client: Client):
        _login_admin(client)
        html = _view(client, _sent_site_row(added_recipients={"users": [], "custom": ["guest@example.com"]}))
        assert _fact(html, "audience") == "Everyone (site-wide), plus 1 added"
        assert _fact(html, "added") == "guest@example.com"

    def it_names_the_waitlist_on_a_class_send_and_has_no_discord(client: Client):
        _login_admin(client)
        offering = ClassOfferingFactory(title="Factory Forging")
        row = _sent_site_row(audience=_CLASS, class_offering=offering, include_waitlist=True)
        html = _view(client, row)
        assert _fact(html, "audience") == "Factory Forging and the waitlist"
        assert 'data-fact="discord"' not in html
        assert 'data-announcement-channel="discord"' not in html
        assert 'data-announcement-channel="email"' in html

    def it_shows_the_email_exactly_as_it_sends_and_no_out_of_band_swap(client: Client):
        from membership.orientations import _absolute_url

        admin = _login_admin(client)
        row = _sent_site_row(author=admin)
        html = _view(client, row)
        row = AnnouncementDraft.objects.select_related("author").get(pk=row.pk)
        expected = row.build_email_message(_absolute_url("/")).html_body
        assert f'<iframe srcdoc="{escape(expected)}"' in html
        assert "hx-swap-oob" not in html
        assert f"data-push-preview-title>{row.title}</div>" in html
        assert "data-push-preview-body>Factory open house this Saturday.</div>" in html

    def it_shows_the_discord_card_the_composer_preview_would(client: Client):
        admin = _login_admin(client)
        guild = GuildFactory(name="Factory Woodshop", discord_webhook_url="https://discord.test/hook")
        row = _sent_site_row(author=admin, audience=_GUILD, guild=guild, discord_channel=_CHANNEL.GUILD)
        preview = client.post(
            reverse("hub_compose_preview"),
            {"audience": f"guild:{guild.pk}", "body": row.body, "show_sender": "on"},
        ).content.decode()
        html = _view(client, row)

        def _card(markup: str) -> tuple[str, str]:
            title = re.search(r"data-discord-preview-title>(.*?)</div>", markup, re.S)
            description = re.search(r"data-discord-preview-description>(.*?)</div>", markup, re.S)
            assert title and description
            return title.group(1), description.group(1)

        assert _card(html) == _card(preview)
        assert _card(html) == ("Factory Woodshop Announcement", "Factory open house this Saturday.")

    def it_still_renders_when_the_sender_was_deleted(client: Client):
        _login_admin(client)
        sender = User.objects.create_user(username="gone", first_name="Factory")
        row = _sent_site_row(author=sender)
        sender.delete()
        html = _view(client, row)
        assert re.search(r"data-announcement-sentline>\s*Sent .* by Unknown\.", html)
        assert '<td data-label="Sent by" class="pl-announcements-table__who">Unknown</td>' in _row(
            _page(client, "sent"), row.pk
        )

    def it_sends_a_still_draft_pk_to_the_composer(client: Client):
        _login_admin(client)
        draft = AnnouncementDraftFactory()
        response = client.get(reverse("hub_announcement_sent", args=[draft.pk]))
        assert response.status_code == 302
        assert response.url == reverse("hub_compose_resume", args=[draft.pk])

    def it_404s_a_missing_row(client: Client):
        _login_admin(client)
        assert client.get(reverse("hub_announcement_sent", args=[999999])).status_code == 404

    def it_links_back_to_the_sent_tab(client: Client):
        _login_admin(client)
        html = _view(client, _sent_site_row())
        assert (
            f'<a href="{reverse("hub_announcements")}?tab=sent" class="hub-btn hub-btn--sm hub-btn--ghost" data-announcement-back>'
            in html
        )


def describe_the_entry_points():
    def it_adds_drafts_and_sent_beside_compose_on_the_guild_edit_tab(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        html = client.get(reverse("hub_guild_edit", args=[guild.pk]) + "?tab=announcements").content.decode()
        assert f'href="{reverse("hub_compose")}?audience=guild:{guild.pk}" class="hub-btn hub-btn--primary"' in html
        assert (
            f'<a href="{reverse("hub_announcements")}" class="hub-btn hub-btn--ghost" data-guild-announcements-overview>'
            in html
        )
