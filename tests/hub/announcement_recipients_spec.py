"""Who a sent announcement reached, and how far a queued one has got.

Covers ``AnnouncementDraft.ledger_period``, ``send_progress``, ``recipient_list`` (the delivery
ledger plus failed emails from the email log), ``AnnouncementDraftQuerySet.emails_for``, the Sent
tab's progress line and Emails column, the progress poll and the recipients modal.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import EventDelivery, TransactionalEmailLog
from membership.models import AnnouncementDraft
from tests.membership.factories import AnnouncementDraftFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_SITE_KIND = "site_announcement"


def _login_admin(client: Client) -> User:
    user = User.objects.create_superuser(username="admin", email="admin@x.com", password="p")
    client.login(username="admin", password="p")
    return user


def _member(username: str, **extra) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@x.com", password="p", last_login=timezone.now(), **extra
    )


def _delivered(row: AnnouncementDraft, ref: str, *channels: str, period: str | None = None) -> None:
    for channel in channels:
        EventDelivery.objects.create(
            event_key=row.event_key, target_ref=ref, channel=channel, period=period or row.ledger_period
        )


def _failed_email(row: AnnouncementDraft, address: str, error: str = "429 daily quota", **extra) -> None:
    TransactionalEmailLog.objects.create(
        to_email=address,
        subject=extra.pop("subject", row.title),
        trigger_kind=extra.pop("trigger_kind", _SITE_KIND),
        status=TransactionalEmailLog.Status.FAILED,
        error_message=error,
    )


def _sent_row(**extra) -> AnnouncementDraft:
    return AnnouncementDraftFactory(sent=True, delivery_period="announce:900", **extra)


def describe_ledger_period():
    def it_is_the_stamped_period_once_sent():
        assert _sent_row().ledger_period == "announce:900"

    def it_is_the_period_a_queued_site_send_claims():
        row = AnnouncementDraftFactory(queued=True)
        assert row.ledger_period == f"announce:{row.pk}"

    def it_is_blank_for_a_draft_and_for_a_row_sent_before_periods_were_kept():
        assert AnnouncementDraftFactory().ledger_period == ""
        assert AnnouncementDraftFactory(sent=True).ledger_period == ""


def describe_send_progress():
    def it_is_none_before_the_send_starts():
        assert AnnouncementDraftFactory(queued=True).send_progress() is None

    def it_is_none_for_a_row_that_is_not_sending():
        assert _sent_row().send_progress() is None

    def it_counts_the_people_reached_so_far_of_everyone_it_goes_to():
        one, _two = _member("one"), _member("two")
        row = AnnouncementDraftFactory(queued=True)
        _delivered(row, f"user:{one.pk}", "in_app", "email")
        _delivered(row, "broadcast", "discord")
        progress = row.send_progress()
        assert progress is not None
        assert (progress.done, progress.total) == (1, row.recipient_count())
        assert progress.total >= 2

    def it_never_reports_more_done_than_total():
        row = AnnouncementDraftFactory(queued=True)
        for n in range(3):
            _delivered(row, f"email:guest{n}@example.com", "email")
        progress = row.send_progress()
        assert progress is not None
        assert progress.done == progress.total == 3


def describe_emails_for():
    def it_counts_each_rows_emails_and_skips_rows_with_no_period():
        row = _sent_row()
        older = AnnouncementDraftFactory(sent=True)
        _delivered(row, "user:1", "email", "in_app")
        _delivered(row, "email:guest@example.com", "email")
        assert AnnouncementDraft.objects.emails_for([row, older]) == {row.pk: 2}

    def it_returns_nothing_when_no_row_has_a_period():
        assert AnnouncementDraft.objects.emails_for([AnnouncementDraftFactory(sent=True)]) == {}


def describe_recipient_list():
    def it_lists_each_person_with_the_channels_that_reached_them():
        from core.models import FcmDevice

        ada = _member("ada", first_name="Ada", last_name="Lovelace")
        FcmDevice.objects.create(user=ada, token="fcm-ada")
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "in_app", "email", "push")
        _delivered(row, "email:guest@example.com", "email")
        _delivered(row, "broadcast", "discord")
        listed = row.recipient_list()
        assert [(r.name, r.address, r.in_app, r.push, r.email) for r in listed.recipients] == [
            ("Ada Lovelace", "ada@x.com", True, True, "sent"),
            ("", "guest@example.com", False, False, "sent"),
        ]
        assert (listed.reached, listed.emailed, listed.failed, listed.in_app, listed.pushed) == (2, 2, 0, 1, 1)

    def it_shows_a_members_failed_email_with_the_providers_reason():
        ada = _member("ada")
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "in_app")
        _failed_email(row, "ada@x.com")
        (ada_row,) = row.recipient_list().recipients
        assert (ada_row.email, ada_row.email_error, ada_row.in_app) == ("failed", "429 daily quota", True)

    def it_matches_a_failure_sent_to_a_members_notification_email():
        ada = _member("ada")
        ada.member.notification_email = "ada.work@example.com"
        ada.member.save(update_fields=["notification_email"])
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "in_app")
        _failed_email(row, "Ada.Work@example.com")
        assert row.recipient_list().recipients[0].email == "failed"

    def it_lists_a_class_guest_whose_only_email_failed():
        from classes.factories import ClassOfferingFactory, RegistrationFactory
        from classes.models import Registration

        offering = ClassOfferingFactory()
        RegistrationFactory(
            class_offering=offering, member=None, email="Guest@Example.com", status=Registration.Status.CONFIRMED
        )
        row = _sent_row(audience=AnnouncementDraft.Audience.CLASS, class_offering=offering)
        _delivered(row, "email:someone-else@example.com", "email")
        _failed_email(row, "guest@example.com", error="bounced", trigger_kind="class_announcement")
        listed = row.recipient_list()
        assert ("guest@example.com", "failed", "bounced") in [
            (r.address, r.email, r.email_error) for r in listed.recipients
        ]
        assert listed.failed == 1

    def it_never_lists_a_failure_from_outside_this_sends_audience():
        from classes.factories import ClassOfferingFactory

        site = _sent_row()
        _delivered(site, "broadcast", "discord")
        _failed_email(site, "stranger@elsewhere.org")
        assert site.recipient_list().recipients == []
        offering = ClassOfferingFactory()
        lesson = _sent_row(audience=AnnouncementDraft.Audience.CLASS, class_offering=offering)
        _delivered(lesson, "broadcast", "discord")
        _failed_email(lesson, "other-class-student@example.com", trigger_kind="class_announcement")
        assert lesson.recipient_list().recipients == []

    def it_lists_a_guild_mailing_list_address_whose_email_failed():
        from membership.models import GuildMailingListEmail

        guild = GuildFactory()
        GuildMailingListEmail.objects.create(guild=guild, email="List@Example.com")
        row = _sent_row(audience=AnnouncementDraft.Audience.GUILD, guild=guild)
        _delivered(row, "broadcast", "discord")
        _failed_email(row, "list@example.com", trigger_kind="guild_announcement")
        assert [r.address for r in row.recipient_list().recipients] == ["list@example.com"]

    def it_shows_the_address_the_email_went_to():
        from allauth.account.models import EmailAddress

        ada = _member("ada")
        EmailAddress.objects.create(user=ada, email="ada.work@example.com", verified=True)
        ada.member.notification_email = "ada.work@example.com"
        ada.member.save(update_fields=["notification_email"])
        bob = _member("bob")
        bob.member.notification_email = "bob.unverified@example.com"
        bob.member.save(update_fields=["notification_email"])
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "email")
        _delivered(row, f"user:{bob.pk}", "email")
        assert [r.address for r in row.recipient_list().recipients] == ["ada.work@example.com", "bob@x.com"]

    def it_counts_only_failed_emails_inside_the_send_as_failures():
        ada = _member("ada")
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "in_app")
        TransactionalEmailLog.objects.create(
            to_email="ada@x.com", subject=row.title, trigger_kind=_SITE_KIND, status=TransactionalEmailLog.Status.SENT
        )
        late = TransactionalEmailLog.objects.create(
            to_email="ada@x.com",
            subject=row.title,
            trigger_kind=_SITE_KIND,
            status=TransactionalEmailLog.Status.FAILED,
            error_message="a later send",
        )
        TransactionalEmailLog.objects.filter(pk=late.pk).update(created_at=row.sent_at + timedelta(minutes=5))
        assert row.recipient_list().recipients[0].email == ""

    def it_matches_the_staging_subject(settings):
        from core.email_policy import SUBJECT_PREFIX

        settings.IS_STAGING = True
        ada = _member("ada")
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "in_app")
        _failed_email(row, "ada@x.com", subject=f"{SUBJECT_PREFIX}{row.title}")
        assert row.recipient_list().recipients[0].email == "failed"

    def it_shows_sent_when_a_retry_went_through_after_a_failure():
        ada = _member("ada")
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "email")
        _failed_email(row, "ada@x.com")
        assert row.recipient_list().recipients[0].email == "sent"

    def it_ignores_failures_of_other_emails():
        ada = _member("ada")
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "in_app")
        _failed_email(row, "ada@x.com", subject="Some other subject")
        _failed_email(row, "ada@x.com", trigger_kind="billing.receipt")
        old = TransactionalEmailLog.objects.create(
            to_email="ada@x.com",
            subject=row.title,
            trigger_kind=_SITE_KIND,
            status=TransactionalEmailLog.Status.FAILED,
            error_message="last month",
        )
        TransactionalEmailLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=30))
        assert row.recipient_list().recipients[0].email == ""

    def it_says_no_push_for_a_member_without_a_device():
        ada = _member("ada")
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "in_app", "push")
        listed = row.recipient_list()
        assert (listed.recipients[0].push, listed.pushed) == (False, 0)

    def it_names_a_deleted_account():
        row = _sent_row()
        _delivered(row, "user:99999", "in_app")
        assert row.recipient_list().recipients[0].name == "Deleted account"

    def it_sorts_by_name_then_address():
        _member("zed", first_name="Zed")
        row = _sent_row()
        for user in User.objects.filter(username="zed"):
            _delivered(row, f"user:{user.pk}", "in_app")
        _delivered(row, "email:amy@example.com", "email")
        assert [r.address for r in row.recipient_list().recipients] == ["amy@example.com", "zed@x.com"]

    def it_puts_failed_emails_first():
        _member("amy")
        _member("zed")
        row = _sent_row()
        for user in User.objects.filter(username__in=["amy", "zed"]):
            _delivered(row, f"user:{user.pk}", "in_app")
        _failed_email(row, "zed@x.com")
        assert [r.address for r in row.recipient_list().recipients] == ["zed@x.com", "amy@x.com"]

    def it_is_empty_without_a_period():
        assert AnnouncementDraftFactory(sent=True).recipient_list().recipients == []

    def it_reads_a_sending_rows_deliveries_so_far():
        ada = _member("ada")
        row = AnnouncementDraftFactory(queued=True)
        _delivered(row, f"user:{ada.pk}", "in_app")
        assert [r.address for r in row.recipient_list().recipients] == ["ada@x.com"]


def describe_the_sent_tab():
    def _sent_tab(client: Client) -> str:
        return client.get(f"{reverse('hub_announcements')}?tab=sent").content.decode()

    def it_shows_how_many_emails_went_out_as_a_button_to_the_list(client: Client):
        _login_admin(client)
        row = _sent_row()
        _delivered(row, "email:guest@example.com", "email")
        body = _sent_tab(client)
        assert 'data-emails="1"' in body
        assert f'hx-get="{reverse("hub_announcement_recipients", args=[row.pk])}"' in body

    def it_says_off_not_yet_and_not_recorded(client: Client):
        _login_admin(client)
        _sent_row(send_email=False)
        AnnouncementDraftFactory(queued=True)
        AnnouncementDraftFactory(sent=True)
        body = _sent_tab(client)
        assert 'data-emails="off"' in body
        assert 'data-emails="not-yet"' in body
        assert 'data-emails="not-recorded"' in body

    def it_shows_a_sending_rows_progress_and_polls_it(client: Client):
        _login_admin(client)
        one = _member("one")
        row = AnnouncementDraftFactory(queued=True)
        _delivered(row, f"user:{one.pk}", "in_app")
        body = _sent_tab(client)
        total = row.recipient_count()
        assert f'data-send-progress="1/{total}"' in body
        assert f"Sent to 1 of {total} so far." in body
        assert f'hx-get="{reverse("hub_announcement_progress", args=[row.pk])}" hx-trigger="every 5s"' in body
        assert "data-quiet-poll" in body

    def it_polls_slowly_while_waiting_for_the_queue(client: Client):
        _login_admin(client)
        row = AnnouncementDraftFactory(queued=True)
        assert f'hx-get="{reverse("hub_announcement_progress", args=[row.pk])}" hx-trigger="every 30s"' in _sent_tab(
            client
        )

    def it_says_a_queued_row_goes_out_soon_or_is_retrying(client: Client):
        _login_admin(client)
        AnnouncementDraftFactory(queued=True)
        AnnouncementDraftFactory(queued=True, send_error="Provider down.")
        body = _sent_tab(client)
        assert "Goes out within 15 minutes." in body
        assert "The last try failed: Provider down. It tries again on the next run." in body

    def it_says_a_retry_is_under_way_once_it_has_progress(client: Client):
        _login_admin(client)
        one = _member("one")
        row = AnnouncementDraftFactory(queued=True, send_error="Provider down.")
        _delivered(row, f"user:{one.pk}", "in_app")
        assert "so far. The last try failed. It tries again on the next run." in _sent_tab(client)


def describe_announcement_progress():
    def it_returns_the_line_again_while_sending(client: Client):
        _login_admin(client)
        row = AnnouncementDraftFactory(queued=True)
        response = client.get(reverse("hub_announcement_progress", args=[row.pk]))
        assert response.status_code == 200
        assert 'data-send-progress="queued"' in response.content.decode()

    def it_refreshes_the_page_once_sent(client: Client):
        _login_admin(client)
        response = client.get(reverse("hub_announcement_progress", args=[_sent_row().pk]))
        assert response.status_code == 204
        assert response["HX-Refresh"] == "true"

    def it_refreshes_the_page_once_the_queue_gives_up(client: Client):
        _login_admin(client)
        given_up = AnnouncementDraftFactory(given_up=True, send_attempts=3)
        response = client.get(reverse("hub_announcement_progress", args=[given_up.pk]))
        assert response.status_code == 204
        assert response["HX-Refresh"] == "true"

    def it_stops_polling_for_a_row_the_viewer_may_not_see_or_that_is_gone(client: Client):
        _member("plain")
        client.login(username="plain", password="p")
        assert client.get(reverse("hub_announcement_progress", args=[_sent_row().pk])).status_code == 286
        assert client.get(reverse("hub_announcement_progress", args=[99999])).status_code == 286

    def it_404s_the_list_for_a_draft(client: Client):
        _login_admin(client)
        assert (
            client.get(reverse("hub_announcement_recipients", args=[AnnouncementDraftFactory().pk])).status_code == 404
        )


def describe_announcement_recipients():
    def it_renders_the_modal_with_each_person_and_the_summary(client: Client):
        _login_admin(client)
        ada = _member("ada", first_name="Ada")
        bob = _member("bob", first_name="Bob")
        row = _sent_row()
        _delivered(row, f"user:{ada.pk}", "in_app", "email")
        _delivered(row, f"user:{bob.pk}", "in_app")
        _failed_email(row, "bob@x.com", error="Mailbox <full>")
        body = client.get(reverse("hub_announcement_recipients", args=[row.pk])).content.decode()
        assert f'data-announcement-recipients="{row.pk}"' in body
        assert "2 people reached. Emails: 1 sent, 1 failed. In the app: 2. Push: 0." in body
        assert 'data-recipient="ada@x.com"' in body
        assert "Mailbox &lt;full&gt;" in body

    def it_says_a_sending_list_is_so_far(client: Client):
        _login_admin(client)
        ada = _member("ada")
        row = AnnouncementDraftFactory(queued=True)
        _delivered(row, f"user:{ada.pk}", "in_app")
        body = client.get(reverse("hub_announcement_recipients", args=[row.pk])).content.decode()
        assert "data-recipients-sending" in body

    def it_says_nobody_yet_or_not_recorded(client: Client):
        _login_admin(client)
        queued = AnnouncementDraftFactory(queued=True)
        older = AnnouncementDraftFactory(sent=True)
        assert "Nobody yet." in client.get(reverse("hub_announcement_recipients", args=[queued.pk])).content.decode()
        body = client.get(reverse("hub_announcement_recipients", args=[older.pk])).content.decode()
        assert "was not recorded" in body

    def it_404s_for_a_guild_lead_on_another_guilds_announcement(client: Client):
        lead = _member("lead")
        mine, other = GuildFactory(), GuildFactory()
        mine.guild_lead = lead.member
        mine.save(update_fields=["guild_lead"])
        client.login(username="lead", password="p")
        row = _sent_row(audience=AnnouncementDraft.Audience.GUILD, guild=other)
        assert client.get(reverse("hub_announcement_recipients", args=[row.pk])).status_code == 404


def describe_the_record_page():
    def it_offers_the_list_when_the_period_was_recorded(client: Client):
        _login_admin(client)
        row = _sent_row()
        body = client.get(reverse("hub_announcement_sent", args=[row.pk])).content.decode()
        assert f'hx-get="{reverse("hub_announcement_recipients", args=[row.pk])}"' in body
        assert 'id="announcement-recipients-body"' in body

    def it_offers_the_list_so_far_and_the_progress_while_sending(client: Client):
        _login_admin(client)
        row = AnnouncementDraftFactory(queued=True)
        body = client.get(reverse("hub_announcement_sent", args=[row.pk])).content.decode()
        assert "See who it has reached so far" in body
        assert 'data-send-progress="queued"' in body

    def it_hides_the_list_for_a_row_sent_before_periods_were_kept(client: Client):
        _login_admin(client)
        row = AnnouncementDraftFactory(sent=True)
        # The button, not its words: the changelog renders on every hub page and quotes them.
        assert (
            "data-announcement-recipients-open"
            not in client.get(reverse("hub_announcement_sent", args=[row.pk])).content.decode()
        )
