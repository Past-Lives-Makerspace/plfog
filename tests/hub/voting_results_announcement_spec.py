"""The Voting page's Draft announcement control and the composer it opens.

The Overview banner and a snapshot's history page offer one POST form, Draft announcement,
that opens (or reopens) the month's results announcement in the composer. The composer shows
the "<cycle> Voting Results" title on every surface, previews the email chart and the Discord
bars, and queues the site send for the background job. Assertions anchor on markup (form
actions, ``data-`` attributes, ids) because the changelog renders on every hub page.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from core.models import Notification, SiteConfiguration
from membership.models import AnnouncementDraft, FundingSnapshot
from tests.membership.factories import FundingSnapshotFactory, GuildFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_TITLE = "September 2026 Voting Results"


@pytest.fixture()
def admin_client() -> Client:
    with mute_signals(post_save):
        User.objects.create_superuser("felix", "felix@x.com", "p")
    client = Client()
    client.login(username="felix", password="p")
    return client


def _admin_user() -> User:
    return User.objects.get(username="felix")


def _snapshot(label: str = "September 2026") -> FundingSnapshot:
    return FundingSnapshotFactory(
        cycle_label=label,
        funding_pool=Decimal("1000.00"),
        results={
            "votes_cast": 12,
            "results": [
                {"guild_name": "Metal Guild", "funding": "600.00", "share_pct": 60.0},
                {"guild_name": "Fiber Arts", "funding": "400.00", "share_pct": 40.0},
            ],
        },
    )


def _draft_url(snapshot: FundingSnapshot) -> str:
    return reverse("hub_admin_voting_results_draft", args=[snapshot.pk])


def _activated(username: str) -> User:
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(username=username, email=f"{username}@x.com", last_login=timezone.now())
    member.user = user
    member.save(update_fields=["user"])
    return user


def _messages(response) -> list[str]:
    return [message.message for message in get_messages(response.wsgi_request)]


def _compose_post(draft: AnnouncementDraft, **overrides: str) -> dict[str, str]:
    data = {
        "audience": "site",
        "body": draft.body,
        "push_message": draft.push_message,
        "push_enabled": "on",
        "send_email": "on",
        "discord_enabled": "on",
        "show_sender": "on",
        "discord_channel": "none",
        "mention": "none",
        "draft_pk": str(draft.pk),
    }
    data.update(overrides)
    return data


def describe_the_overview_banner():
    def it_offers_a_draft_announcement_form_for_the_pending_snapshot(admin_client):
        snapshot = _snapshot()
        html = admin_client.get(reverse("hub_admin_voting_overview")).content.decode()
        assert f'data-results-banner="{snapshot.pk}"' in html
        assert f'<form method="post" action="{_draft_url(snapshot)}" data-results-draft-form>' in html
        assert "send-results" not in html
        assert "resend-results" not in html
        assert "open-confirm" not in html

    def it_renders_no_banner_when_nothing_is_pending(admin_client):
        FundingSnapshotFactory(results={})
        html = admin_client.get(reverse("hub_admin_voting_overview")).content.decode()
        assert "data-results-banner" not in html

    def it_says_the_announcement_is_sending_while_it_is_queued(admin_client):
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_admin_user()).queue_send()
        html = admin_client.get(reverse("hub_admin_voting_overview")).content.decode()
        assert f'data-results-banner="{snapshot.pk}"' in html
        assert 'data-results-state="sending"' in html
        assert "data-results-draft-form" not in html

    def it_moves_on_to_the_next_pending_snapshot_once_sent(admin_client):
        august = _snapshot("August 2026")
        september = _snapshot()
        FundingSnapshot.objects.filter(pk=august.pk).update(snapshot_at=timezone.now() - timedelta(days=30))
        draft = september.draft_results_announcement(_admin_user())
        draft.send()
        html = admin_client.get(reverse("hub_admin_voting_overview")).content.decode()
        assert f'data-results-banner="{august.pk}"' in html
        assert f'data-results-banner="{september.pk}"' not in html


def describe_the_history_page():
    def it_offers_the_draft_form_for_a_pending_snapshot(admin_client):
        snapshot = _snapshot()
        html = admin_client.get(reverse("hub_admin_voting_history_detail", args=[snapshot.pk])).content.decode()
        assert f'action="{_draft_url(snapshot)}" data-results-draft-form' in html
        assert "send-results" not in html

    def it_shows_when_the_results_were_sent(admin_client):
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_admin_user()).send()
        html = admin_client.get(reverse("hub_admin_voting_history_detail", args=[snapshot.pk])).content.decode()
        assert 'data-results-state="sent"' in html
        assert "data-results-draft-form" not in html

    def it_shows_the_sending_line_while_queued(admin_client):
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_admin_user()).queue_send()
        html = admin_client.get(reverse("hub_admin_voting_history_detail", args=[snapshot.pk])).content.decode()
        assert 'data-results-state="sending"' in html

    def it_offers_nothing_for_a_snapshot_without_results(admin_client):
        snapshot = FundingSnapshotFactory(results={})
        html = admin_client.get(reverse("hub_admin_voting_history_detail", args=[snapshot.pk])).content.decode()
        assert f'data-results-control="{snapshot.pk}"' in html
        assert "data-results-draft-form" not in html
        assert "data-results-state" not in html


def describe_voting_results_draft():
    def it_creates_one_draft_and_opens_it_in_the_composer(admin_client):
        snapshot = _snapshot()
        response = admin_client.post(_draft_url(snapshot))
        draft = AnnouncementDraft.objects.get()
        assert draft.funding_snapshot == snapshot
        assert draft.author == _admin_user()
        assert response.status_code == 302
        assert response.url == reverse("hub_compose_resume", args=[draft.pk])

    def it_reopens_the_same_draft_on_a_second_post(admin_client):
        snapshot = _snapshot()
        first = admin_client.post(_draft_url(snapshot))
        second = admin_client.post(_draft_url(snapshot))
        assert AnnouncementDraft.objects.count() == 1
        assert second.url == first.url

    def it_opens_the_draft_the_snapshot_job_made(admin_client):
        """Drafts are shared: the banner opens the job's draft rather than making the admin their own."""
        snapshot = _snapshot()
        made = snapshot.make_results_draft()
        response = admin_client.post(_draft_url(snapshot))
        assert response.url == reverse("hub_compose_resume", args=[made.pk])
        assert AnnouncementDraft.objects.count() == 1
        html = admin_client.get(response.url).content.decode()
        assert f'name="draft_pk" value="{made.pk}"' in html
        assert "data-compose-draft-automatic" in html

    def it_never_creates_a_draft_on_a_get(admin_client):
        snapshot = _snapshot()
        assert admin_client.get(_draft_url(snapshot)).status_code == 405
        assert not AnnouncementDraft.objects.exists()

    def it_refuses_a_non_admin(client: Client):
        MembershipPlanFactory()
        User.objects.create_user("plain", "plain@x.com", "p")
        client.login(username="plain", password="p")
        snapshot = _snapshot()
        assert client.post(_draft_url(snapshot)).status_code == 403
        assert not AnnouncementDraft.objects.exists()

    def it_404s_a_missing_snapshot(admin_client):
        assert admin_client.post(reverse("hub_admin_voting_results_draft", args=[999999])).status_code == 404

    def it_returns_to_the_snapshot_when_the_results_already_went_out(admin_client):
        snapshot = _snapshot()
        FundingSnapshot.objects.filter(pk=snapshot.pk).update(results_sent_at=timezone.now())
        response = admin_client.post(_draft_url(snapshot))
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_voting_history_detail", args=[snapshot.pk])
        assert _messages(response) == ["These results were already sent."]
        assert not AnnouncementDraft.objects.exists()

    def it_returns_to_the_snapshot_when_there_is_nothing_to_announce(admin_client):
        snapshot = FundingSnapshotFactory(cycle_label="Legacy", results={})
        response = admin_client.post(_draft_url(snapshot))
        assert response.url == reverse("hub_admin_voting_history_detail", args=[snapshot.pk])
        assert _messages(response) == ["'Legacy' has no results to announce."]


def describe_the_composer_for_a_results_draft():
    def _results_draft() -> AnnouncementDraft:
        return _snapshot().draft_results_announcement(_admin_user())

    def it_opens_with_the_results_title_and_the_prefilled_message(admin_client):
        config = SiteConfiguration.load()
        config.discord_general_webhook_url = "https://discord.test/api/webhooks/general"
        config.save()
        draft = _results_draft()
        html = admin_client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert f"category: '{_TITLE}'" in html
        assert "The votes for September 2026 are in." in html
        assert "September 2026 voting results are in. See how the guild funding was split." in html
        assert '<option value="general" selected>' in html
        assert '<option value="everyone" selected>' in html
        assert f'name="draft_pk" value="{draft.pk}"' in html

    def it_keeps_the_plain_category_for_a_plain_site_draft(admin_client):
        draft = AnnouncementDraft.objects.create(author=_admin_user(), title="Makerspace Announcement", body="<p>x</p>")
        html = admin_client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert "category: 'Makerspace Announcement'" in html

    def it_says_a_site_send_goes_out_within_15_minutes_in_the_confirm_modal(admin_client):
        html = admin_client.get(reverse("hub_compose")).content.decode()
        assert (
            "<span data-send-timing x-text=\"audience === 'site' ? 'within 15 minutes' : 'right now'\">right now</span>"
            in html
        )
        assert 'id="compose-discord-preview"' in html

    def describe_the_preview():
        def it_titles_the_email_and_shows_the_chart(admin_client):
            draft = _results_draft()
            html = admin_client.post(reverse("hub_compose_preview"), _compose_post(draft)).content.decode()
            assert f'<span class="pl-email-preview__subject">{_TITLE}</span>' in html
            assert "How the $1,000.00 funding pool was split" in html
            assert "Metal Guild" in html

        def it_refreshes_the_discord_card_out_of_band_with_the_bars(admin_client):
            draft = _results_draft()
            html = admin_client.post(reverse("hub_compose_preview"), _compose_post(draft)).content.decode()
            assert (
                'id="compose-discord-preview" class="pl-push-preview pl-discord-preview" data-discord-preview hx-swap-oob="true"'
                in html
            )
            assert f'<div class="pl-push-preview__title" data-discord-preview-title>{_TITLE}</div>' in html
            assert (
                '<code class="pl-discord-preview__code">████████████</code> <strong>Metal Guild</strong>: $600.00 (60.0%)'
                in html
            )
            assert "<strong>How the $1,000.00 funding pool was split</strong>" in html

        def it_previews_a_plain_site_draft_with_no_results(admin_client):
            draft = AnnouncementDraft.objects.create(
                author=_admin_user(), title="Makerspace Announcement", body="<p>Hello all</p>"
            )
            html = admin_client.post(reverse("hub_compose_preview"), _compose_post(draft)).content.decode()
            assert '<span class="pl-email-preview__subject">Makerspace Announcement</span>' in html
            assert "data-discord-preview-description>Hello all</div>" in html
            assert "funding pool was split" not in html

        def it_gives_the_results_title_to_nobody_who_may_not_handle_the_draft(client: Client):
            """A lead may preview, but an admin's site draft is not theirs to handle, so its pk gives no title."""
            admin_draft = _snapshot().draft_results_announcement(User.objects.create_superuser("boss", "b@x.com", "p"))
            MembershipPlanFactory()
            lead = User.objects.create_user("lead", "lead@x.com", "p")
            guild = GuildFactory(guild_lead=lead.member)
            client.login(username="lead", password="p")
            data = _compose_post(admin_draft, audience=f"guild:{guild.pk}")
            html = client.post(reverse("hub_compose_preview"), data).content.decode()
            assert f'<span class="pl-email-preview__subject">{guild.name} Announcement</span>' in html
            assert _TITLE not in html

        def it_gives_another_admins_results_title_to_an_admin(admin_client):
            """Drafts are shared, so an admin previewing another admin's results draft sees its title."""
            theirs = _snapshot().draft_results_announcement(User.objects.create_superuser("robin", "r@x.com", "p"))
            html = admin_client.post(reverse("hub_compose_preview"), _compose_post(theirs)).content.decode()
            assert f'<span class="pl-email-preview__subject">{_TITLE}</span>' in html

        def it_gives_a_queued_drafts_results_title_to_nobody(admin_client):
            draft = _results_draft()
            draft.queue_send()
            html = admin_client.post(reverse("hub_compose_preview"), _compose_post(draft)).content.decode()
            assert '<span class="pl-email-preview__subject">Makerspace Announcement</span>' in html

        def it_ignores_a_draft_pk_that_is_not_a_number(admin_client):
            draft = _results_draft()
            data = _compose_post(draft, draft_pk="1 OR 1=1")
            html = admin_client.post(reverse("hub_compose_preview"), data).content.decode()
            assert '<span class="pl-email-preview__subject">Makerspace Announcement</span>' in html

    def describe_the_tests():
        def it_sends_the_test_email_under_the_results_title_with_the_chart(admin_client, mailoutbox):
            draft = _results_draft()
            response = admin_client.post(reverse("hub_compose_test"), _compose_post(draft))
            assert response.status_code == 204
            assert [m.subject for m in mailoutbox] == [_TITLE]
            assert "How the $1,000.00 funding pool was split" in mailoutbox[0].alternatives[0][0]

        def it_sends_the_push_test_under_the_results_title_with_the_phone_line(admin_client, monkeypatch):
            from core import push_admin

            sent: dict = {}

            def fake(user, **kwargs):
                sent.update(kwargs)
                return push_admin.TestSendResult(delivered=1, attempted=1)

            monkeypatch.setattr(push_admin, "send_test_push", fake)
            draft = _results_draft()
            admin_client.post(reverse("hub_compose_push_test"), _compose_post(draft))
            assert sent["title"] == _TITLE
            assert sent["body"] == "September 2026 voting results are in. See how the guild funding was split."

        def it_sends_a_plain_drafts_push_test_under_the_plain_category(admin_client, monkeypatch):
            from core import push_admin

            sent: dict = {}

            def fake(user, **kwargs):
                sent.update(kwargs)
                return push_admin.TestSendResult(delivered=1, attempted=1)

            monkeypatch.setattr(push_admin, "send_test_push", fake)
            admin_client.post(reverse("hub_compose_push_test"), {"audience": "site", "body": "<p>Hi there</p>"})
            assert sent["title"] == "Makerspace Announcement"
            assert sent["body"] == "Hi there"


def describe_sending_from_the_composer():
    def it_queues_a_site_send_and_says_when_it_will_arrive(admin_client, mailoutbox):
        from unittest.mock import patch

        _activated("reader")
        draft = _snapshot().draft_results_announcement(_admin_user())
        with patch("core.events.discord.post_embed") as post_embed:
            response = admin_client.post(reverse("hub_compose_send"), _compose_post(draft))
        assert response.status_code == 302
        assert response.url == f"{reverse('hub_announcements')}?tab=sent"
        assert _messages(response) == [
            "Your announcement is sending in the background. It reaches 1 recipient(s) within 15 minutes."
        ]
        draft.refresh_from_db()
        assert draft.send_requested_at is not None
        assert draft.sent_at is None
        assert draft.title == _TITLE
        assert mailoutbox == []
        assert not Notification.objects.exists()
        post_embed.assert_not_called()

    def it_names_the_sender_of_the_jobs_draft_before_it_queues(admin_client, mailoutbox):
        from django.core.management import call_command

        from core.models import SiteActivity

        _activated("reader")
        made = _snapshot().make_results_draft()
        assert made.author is None
        admin_client.post(reverse("hub_compose_send"), _compose_post(made))
        made.refresh_from_db()
        assert made.author == _admin_user()
        assert made.send_requested_at is not None
        call_command("send_queued_announcements")
        made.refresh_from_db()
        assert made.sent_at is not None
        assert "From felix" in mailoutbox[0].body
        assert SiteActivity.objects.filter(actor=_admin_user()).exists()

    def it_refuses_to_resume_or_resend_a_queued_draft(admin_client):
        draft = _snapshot().draft_results_announcement(_admin_user())
        draft.queue_send()
        assert admin_client.get(reverse("hub_compose_resume", args=[draft.pk])).status_code == 404
        sent = admin_client.post(reverse("hub_compose_send"), _compose_post(draft))
        assert sent.url == reverse("hub_announcements")
        assert _messages(sent) == ["This draft can no longer be edited. It may have been sent or deleted."]
        assert admin_client.post(reverse("hub_compose_save_draft"), _compose_post(draft)).status_code == 404
        assert admin_client.post(reverse("hub_compose_delete_draft", args=[draft.pk])).status_code == 404
        assert AnnouncementDraft.objects.filter(pk=draft.pk).exists()

    def it_returns_to_the_snapshot_when_its_results_already_went_out(admin_client):
        snapshot = _snapshot()
        draft = snapshot.draft_results_announcement(_admin_user())
        FundingSnapshot.objects.filter(pk=snapshot.pk).update(results_sent_at=timezone.now())
        response = admin_client.post(reverse("hub_compose_send"), _compose_post(draft))
        assert response.url == reverse("hub_admin_voting_history_detail", args=[snapshot.pk])
        assert _messages(response) == ["These results were already sent."]
        draft.refresh_from_db()
        assert draft.send_requested_at is None

    def it_refuses_a_second_results_draft_while_the_first_is_sending(admin_client):
        # Drafts are shared now, so a second open results draft for one snapshot can only be one
        # made before that (built here directly); the queue's lock still refuses it.
        MembershipPlanFactory()
        robin = User.objects.create_user("robin", "robin@x.com", "p")
        snapshot = _snapshot()
        theirs = snapshot.draft_results_announcement(robin)
        mine = snapshot._new_results_draft(_admin_user())
        theirs.queue_send()
        response = admin_client.post(reverse("hub_compose_send"), _compose_post(mine))
        assert _messages(response) == ["These results are already sending."]
        mine.refresh_from_db()
        assert mine.send_requested_at is None

    def it_still_sends_a_guild_announcement_in_the_request(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user("lead", "lead@x.com", "p")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead", password="p")
        response = client.post(
            reverse("hub_compose_send"),
            {"audience": f"guild:{guild.pk}", "body": "<p>Forge night</p>", "discord_channel": "none"},
        )
        assert response.status_code == 302
        draft = AnnouncementDraft.objects.get(guild=guild)
        assert draft.sent_at is not None
        assert draft.send_requested_at is None


def describe_a_results_draft_only_goes_to_everyone():
    def _results_draft() -> AnnouncementDraft:
        return _snapshot().draft_results_announcement(_admin_user())

    def _guild_with_a_member():
        from tests.membership.factories import GuildMembershipFactory

        guild = GuildFactory(name="Metal Guild")
        reader = _activated("guildie")
        GuildMembershipFactory(guild=guild, member=reader.member)
        return guild

    def _nothing_went_out(draft: AnnouncementDraft, mailoutbox) -> None:
        from membership.models import GuildAnnouncement

        draft.refresh_from_db()
        assert draft.audience == AnnouncementDraft.Audience.SITE
        assert draft.guild is None
        assert draft.class_offering is None
        assert draft.sent_at is None
        assert draft.send_requested_at is None
        assert draft.funding_snapshot.results_sent_at is None
        assert not GuildAnnouncement.objects.exists()
        assert not Notification.objects.exists()
        assert mailoutbox == []

    def it_shows_the_audience_locked_to_everyone(admin_client):
        draft = _results_draft()
        html = admin_client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert (
            '<div class="pl-compose-locked"><span class="pl-compose-locked__label">Sending to:</span> '
            "Everyone (site-wide)</div>"
        ) in html
        assert '<input type="hidden" name="audience" value="site">' in html
        assert 'select name="audience"' not in html

    def it_keeps_the_audience_locked_when_a_send_is_sent_back(admin_client):
        draft = _results_draft()
        response = admin_client.post(reverse("hub_compose_send"), _compose_post(draft, body="<p><br></p>"))
        assert response.status_code == 200
        assert '<span class="pl-compose-locked__label">Sending to:</span> Everyone (site-wide)' in (
            response.content.decode()
        )

    def it_refuses_a_retarget_to_a_guild_and_sends_nothing(admin_client, mailoutbox):
        guild = _guild_with_a_member()
        draft = _results_draft()
        response = admin_client.post(reverse("hub_compose_send"), _compose_post(draft, audience=f"guild:{guild.pk}"))
        assert response.status_code == 200
        assert response.context["form"].non_field_errors() == ["A results announcement goes to everyone."]
        assert '<span class="pl-compose-locked__label">Sending to:</span>' in response.content.decode()
        _nothing_went_out(draft, mailoutbox)

    def it_refuses_a_retarget_to_a_class_and_sends_nothing(admin_client, mailoutbox):
        from classes.factories import ClassOfferingFactory, RegistrationFactory
        from classes.models import ClassOffering, Registration

        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        student = _activated("student")
        RegistrationFactory(class_offering=offering, member=student.member, status=Registration.Status.CONFIRMED)
        draft = _results_draft()
        response = admin_client.post(
            reverse("hub_compose_send"), _compose_post(draft, audience=f"class:{offering.pk}", lock="1")
        )
        assert response.status_code == 200
        assert response.context["form"].non_field_errors() == ["A results announcement goes to everyone."]
        _nothing_went_out(draft, mailoutbox)

    def it_refuses_a_retarget_on_save_too(admin_client, mailoutbox):
        import json

        guild = _guild_with_a_member()
        draft = _results_draft()
        response = admin_client.post(
            reverse("hub_compose_save_draft"), _compose_post(draft, audience=f"guild:{guild.pk}")
        )
        assert response.status_code == 204
        toast = json.loads(response["HX-Trigger"])["showToast"]
        assert toast["message"] == "A results announcement goes to everyone."
        assert toast["type"] == "error"
        _nothing_went_out(draft, mailoutbox)

    def it_answers_a_retarget_of_results_already_sent_without_a_500(admin_client, mailoutbox):
        guild = _guild_with_a_member()
        draft = _results_draft()
        FundingSnapshot.objects.filter(pk=draft.funding_snapshot_id).update(results_sent_at=timezone.now())
        response = admin_client.post(reverse("hub_compose_send"), _compose_post(draft, audience=f"guild:{guild.pk}"))
        assert response.status_code == 200
        assert response.context["form"].non_field_errors() == ["A results announcement goes to everyone."]
        assert mailoutbox == []


def describe_a_refused_inline_send():
    """Guild and class sends run in the request; a send the model refuses is a message, never a 500."""

    def _lead_with_a_guild(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user("lead", "lead@x.com", "p")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead", password="p")
        return guild

    def _post(client: Client, guild) -> object:
        return client.post(
            reverse("hub_compose_send"),
            {"audience": f"guild:{guild.pk}", "body": "<p>Forge night</p>", "discord_channel": "none"},
        )

    def it_says_results_already_sent_and_reopens_the_saved_draft(client: Client):
        from unittest.mock import patch

        from membership.models import ResultsAlreadySentError

        guild = _lead_with_a_guild(client)
        with patch.object(
            AnnouncementDraft, "send", side_effect=ResultsAlreadySentError("These results were already sent.")
        ):
            response = _post(client, guild)
        draft = AnnouncementDraft.objects.get()
        assert response.status_code == 302
        assert response.url == reverse("hub_compose_resume", args=[draft.pk])
        assert _messages(response) == ["These results were already sent."]

    def it_lands_on_the_sent_tab_when_the_refused_draft_is_no_longer_resumable(client: Client):
        from unittest.mock import patch

        from membership.models import AlreadySentError

        guild = _lead_with_a_guild(client)

        def already_out(draft):
            AnnouncementDraft.objects.filter(pk=draft.pk).update(sent_at=timezone.now())
            draft.sent_at = timezone.now()
            raise AlreadySentError("This announcement was already sent.")

        with patch.object(AnnouncementDraft, "send", autospec=True, side_effect=already_out):
            response = _post(client, guild)
        assert response.url == f"{reverse('hub_announcements')}?tab=sent"
        assert _messages(response) == ["This announcement was already sent."]

    def it_says_an_announcement_was_already_sent(client: Client):
        from unittest.mock import patch

        from membership.models import AlreadySentError

        guild = _lead_with_a_guild(client)
        with patch.object(
            AnnouncementDraft, "send", side_effect=AlreadySentError("This announcement was already sent.")
        ):
            response = _post(client, guild)
        assert response.status_code == 302
        assert _messages(response) == ["This announcement was already sent."]

    def it_says_why_the_model_refused_the_message(client: Client):
        from unittest.mock import patch

        from django.core.exceptions import ValidationError

        guild = _lead_with_a_guild(client)
        with patch.object(AnnouncementDraft, "send", side_effect=ValidationError("Add a message before sending.")):
            response = _post(client, guild)
        assert response.status_code == 302
        assert _messages(response) == ["Add a message before sending."]

    def it_returns_a_refused_results_queue_to_its_snapshot(admin_client):
        from unittest.mock import patch

        from django.core.exceptions import ValidationError

        snapshot = _snapshot()
        draft = snapshot.draft_results_announcement(_admin_user())
        with patch.object(
            AnnouncementDraft, "queue_send", side_effect=ValidationError("Add a message before sending.")
        ):
            response = admin_client.post(reverse("hub_compose_send"), _compose_post(draft))
        assert response.url == reverse("hub_admin_voting_history_detail", args=[snapshot.pk])
        assert _messages(response) == ["Add a message before sending."]


def describe_deleting_a_snapshot():
    def it_refuses_while_its_results_announcement_is_sending(admin_client):
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_admin_user()).queue_send()
        response = admin_client.post(reverse("hub_admin_voting_snapshot_delete", args=[snapshot.pk]))
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_voting_history_detail", args=[snapshot.pk])
        assert _messages(response) == [
            "Its results announcement is sending. Delete it after the announcement has gone out."
        ]
        assert FundingSnapshot.objects.filter(pk=snapshot.pk).exists()
        assert AnnouncementDraft.objects.get().funding_snapshot == snapshot

    def it_still_deletes_a_snapshot_whose_draft_is_not_queued(admin_client):
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_admin_user())
        response = admin_client.post(reverse("hub_admin_voting_snapshot_delete", args=[snapshot.pk]))
        assert response.url == reverse("hub_admin_voting_history")
        assert not FundingSnapshot.objects.filter(pk=snapshot.pk).exists()


def describe_drafting_while_one_is_sending():
    def it_refuses_a_second_draft_from_a_stale_tab(admin_client):
        MembershipPlanFactory()
        robin = User.objects.create_user("robin", "robin@x.com", "p")
        snapshot = _snapshot()
        snapshot.draft_results_announcement(robin).queue_send()
        response = admin_client.post(_draft_url(snapshot))
        assert response.url == reverse("hub_admin_voting_history_detail", args=[snapshot.pk])
        assert _messages(response) == ["These results are already sending."]
        assert AnnouncementDraft.objects.count() == 1


def describe_a_results_announcement_the_queue_gave_up_on():
    def _given_up(snapshot: FundingSnapshot) -> AnnouncementDraft:
        draft = snapshot.draft_results_announcement(_admin_user())
        draft.queue_send()
        AnnouncementDraft.objects.filter(pk=draft.pk).update(
            send_requested_at=None, send_attempts=3, send_error="provider down."
        )
        return draft

    def it_says_why_on_the_banner_and_offers_the_draft_again(admin_client):
        snapshot = _snapshot()
        _given_up(snapshot)
        html = admin_client.get(reverse("hub_admin_voting_overview")).content.decode()
        assert (
            '<span class="pl-results-send__status" data-results-state="failed">The results announcement could not '
            "be sent: provider down.</span>"
        ) in html
        assert f'action="{_draft_url(snapshot)}" data-results-draft-form' in html

    def it_says_why_on_the_history_page(admin_client):
        snapshot = _snapshot()
        _given_up(snapshot)
        html = admin_client.get(reverse("hub_admin_voting_history_detail", args=[snapshot.pk])).content.decode()
        assert 'data-results-state="failed"' in html
        assert "data-results-draft-form" in html

    def it_reopens_the_same_draft(admin_client):
        snapshot = _snapshot()
        draft = _given_up(snapshot)
        response = admin_client.post(_draft_url(snapshot))
        assert response.url == reverse("hub_compose_resume", args=[draft.pk])
        assert admin_client.get(response.url).status_code == 200

    def it_shows_no_failure_line_for_an_untried_draft(admin_client):
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_admin_user())
        html = admin_client.get(reverse("hub_admin_voting_overview")).content.decode()
        assert 'data-results-state="failed"' not in html


def describe_the_push_test_fidelity():
    """The composer's push test goes through the push adapter's own flattening, caps and channel."""

    def _device(user: User):
        from core.models import FcmDevice

        return FcmDevice.objects.create(user=user, token="t1", platform=FcmDevice.Platform.ANDROID)

    def it_caps_a_long_message_used_as_the_phone_line(admin_client, settings):
        from unittest.mock import patch

        settings.MEMBER_BASE_URL = "https://members.example"
        _device(_admin_user())
        draft = _snapshot().draft_results_announcement(_admin_user())
        data = _compose_post(draft, push_message="", body="<p>" + "word " * 200 + "</p>")
        with patch("core.push_admin.send_fcm", return_value=True) as send_fcm:
            admin_client.post(reverse("hub_compose_push_test"), data)
        kwargs = send_fcm.call_args.kwargs
        assert kwargs["title"] == _TITLE
        assert len(kwargs["body"]) == 200
        assert kwargs["body"].endswith("…")
        assert "<p>" not in kwargs["body"]
        assert kwargs["channel_id"] == "general"
        assert kwargs["url"] == "https://members.example/guilds/voting/history/"

    def it_rides_the_channel_the_real_push_would(client: Client):
        from unittest.mock import patch

        MembershipPlanFactory()
        user = User.objects.create_user("lead", "lead@x.com", "p")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead", password="p")
        _device(user)
        with patch("core.push_admin.send_fcm", return_value=True) as send_fcm:
            client.post(reverse("hub_compose_push_test"), {"audience": f"guild:{guild.pk}", "body": "<p>Forge</p>"})
        assert send_fcm.call_args.kwargs["channel_id"] == "guilds"
        assert send_fcm.call_args.kwargs["body"] == "Forge"
