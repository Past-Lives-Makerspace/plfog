"""A failed automation alerts the Webmasters: once per job per Pacific day, never masking the job's error.

``core.scheduled_jobs.record_run`` marks a raising job FAILED and then calls
``alert_webmasters`` before it re-raises. The alert reaches WEBMASTER capability holders
only, on the bell, by email and by push, and it must still arrive when the email cannot
be built, because the incident behind it was an email template that would not render in
the cron.
"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core import mail
from django.urls import reverse

from core.events.channels import EmailAdapter
from core.models import Notification, PushSubscription, ScheduledTaskRun
from core.scheduled_jobs import Trigger, alert_webmasters, record_run
from membership.models import AdminCapability, Member

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")
EVENT = "automation.failed"
INCIDENT = "Missing staticfiles manifest entry for 'img/favicon.png'"


def _webmaster(linked_member, **member_kwargs):
    member = linked_member(**member_kwargs)
    member.admin_capabilities.create(capability=AdminCapability.Capability.WEBMASTER)
    PushSubscription.objects.create(user=member.user, endpoint=f"https://push/{member.pk}", p256dh="k", auth="a")
    return member


def _fail(key: str = "send_class_reminders", *, trigger: str = Trigger.SCHEDULED, error: Exception | None = None):
    """Run a job that raises inside ``record_run``; the job's own exception must come back out."""
    error = error if error is not None else ValueError(INCIDENT)
    with pytest.raises(type(error)) as caught:
        with record_run(key, trigger=trigger):
            raise error
    assert caught.value is error
    return ScheduledTaskRun.objects.filter(task_key=key).latest("started_at")


def _failed_run(key: str, finished_at: datetime, error: str = INCIDENT) -> ScheduledTaskRun:
    """A FAILED run read back from the database, so ``finished_at`` is UTC as a real run's is."""
    run = ScheduledTaskRun.objects.create(
        task_key=key, status=ScheduledTaskRun.Status.FAILED, finished_at=finished_at, error=error
    )
    return ScheduledTaskRun.objects.get(pk=run.pk)


def _bells(member) -> list[Notification]:
    return list(Notification.objects.filter(user=member.user, trigger=EVENT))


@pytest.fixture
def pushed():
    with patch("core.events.channels.send_web_push") as send_web_push:
        yield send_web_push


def describe_the_alert():
    def it_puts_the_job_name_time_error_and_automations_link_on_the_bell(linked_member, pushed):
        holder = _webmaster(linked_member)
        run = _failed_run("send_class_reminders", datetime(2026, 10, 3, 6, 15, tzinfo=PACIFIC))

        alert_webmasters(run, ValueError(INCIDENT))

        [bell] = _bells(holder)
        assert bell.title == "Class reminder emails failed"
        assert bell.body == (
            "It failed on Saturday, October 3 at 6:15 AM Pacific time.\n"
            f"Error: {INCIDENT}\n"
            "You will get at most one alert a day for this automation."
        )
        assert bell.url == f"{reverse('hub_admin_site_settings')}?tab=automations"

    def it_emails_plain_text_with_the_full_automations_link(linked_member, pushed):
        holder = _webmaster(linked_member)
        alert_webmasters(
            _failed_run("send_class_reminders", datetime(2026, 10, 3, 6, 15, tzinfo=PACIFIC)), ValueError()
        )

        [message] = mail.outbox
        assert message.to == [holder.user.email]
        assert message.subject == "Class reminder emails failed"
        assert message.body.startswith("It failed on Saturday, October 3 at 6:15 AM Pacific time.\n")
        assert message.body.endswith(
            "See the run history on the Automations page: "
            f"{settings.MEMBER_BASE_URL}{reverse('hub_admin_site_settings')}?tab=automations"
        )
        # No HTML part: nothing renders the branded shell, so nothing loads {% static %}.
        assert getattr(message, "alternatives", []) == []

    def it_pushes_to_the_webmasters_device(linked_member, pushed):
        _webmaster(linked_member)
        alert_webmasters(
            _failed_run("send_class_reminders", datetime(2026, 10, 3, 6, 15, tzinfo=PACIFIC)), ValueError()
        )

        pushed.assert_called_once()
        assert pushed.call_args.kwargs["title"] == "Class reminder emails failed"
        assert pushed.call_args.kwargs["url"] == f"{reverse('hub_admin_site_settings')}?tab=automations"

    def it_names_an_unknown_job_by_its_key(linked_member, pushed):
        holder = _webmaster(linked_member)
        alert_webmasters(_failed_run("retired_job", datetime(2026, 10, 3, 6, 15, tzinfo=PACIFIC)), ValueError())
        assert _bells(holder)[0].title == "retired_job failed"

    def it_quotes_only_the_last_non_empty_line_of_the_error(linked_member, pushed):
        holder = _webmaster(linked_member)
        run = _failed_run(
            "bill_tabs", datetime(2026, 10, 3, 6, 15, tzinfo=PACIFIC), "first line\n  the real cause  \n\n"
        )
        alert_webmasters(run, ValueError())
        assert "\nError: the real cause\n" in _bells(holder)[0].body
        assert "first line" not in _bells(holder)[0].body

    def it_truncates_a_long_error_line_to_300_characters(linked_member, pushed):
        holder = _webmaster(linked_member)
        alert_webmasters(
            _failed_run("bill_tabs", datetime(2026, 10, 3, 6, 15, tzinfo=PACIFIC), "x" * 400), ValueError()
        )
        error_line = _bells(holder)[0].body.splitlines()[1]
        assert error_line == "Error: " + "x" * 299 + "…"

    def it_names_the_exception_type_when_the_error_has_no_message(linked_member, pushed):
        holder = _webmaster(linked_member)
        alert_webmasters(_failed_run("bill_tabs", datetime(2026, 10, 3, 6, 15, tzinfo=PACIFIC), ""), KeyError())
        assert "\nError: KeyError\n" in _bells(holder)[0].body


def describe_the_hook_in_record_run():
    @pytest.mark.parametrize("trigger", [Trigger.SCHEDULED, Trigger.MANUAL])
    def it_alerts_a_webmaster_when_a_job_fails_and_re_raises_the_jobs_error(linked_member, pushed, trigger):
        holder = _webmaster(linked_member)
        run = _fail(trigger=trigger)
        assert run.failed
        assert [bell.title for bell in _bells(holder)] == ["Class reminder emails failed"]
        assert len(mail.outbox) == 1
        pushed.assert_called_once()

    def it_alerts_for_the_airtable_pull_under_its_registry_name(linked_member, pushed):
        holder = _webmaster(linked_member)
        _fail("airtable_pull")
        assert _bells(holder)[0].title == "Airtable member pull failed"

    def it_sends_nothing_for_an_ok_run(linked_member, pushed):
        holder = _webmaster(linked_member)
        with record_run("send_class_reminders", trigger=Trigger.SCHEDULED):
            pass
        assert _bells(holder) == []
        assert mail.outbox == []
        pushed.assert_not_called()

    def it_sends_a_plain_admin_without_the_capability_nothing(linked_member, pushed):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        PushSubscription.objects.create(user=admin.user, endpoint="https://push/admin", p256dh="k", auth="a")
        _fail()
        assert _bells(admin) == []
        assert mail.outbox == []
        pushed.assert_not_called()

    def it_alerts_from_the_run_now_button(linked_member, pushed, client):
        from django.contrib.auth.models import User

        holder = _webmaster(linked_member)
        User.objects.create_superuser(username="runner", email="runner@example.com", password="p")
        client.login(username="runner", password="p")
        with patch("django.core.management.call_command", side_effect=RuntimeError("kaboom")):
            client.post(reverse("hub_admin_site_settings"), data={"run_job": "send_class_reminders"})
        [bell] = _bells(holder)
        assert "\nError: kaboom\n" in bell.body


def describe_once_a_day_per_job():
    def it_sends_nothing_new_when_the_same_job_fails_again_the_same_day(linked_member, pushed):
        holder = _webmaster(linked_member)
        _fail()
        _fail()
        assert len(_bells(holder)) == 1
        assert len(mail.outbox) == 1
        pushed.assert_called_once()

    def it_alerts_again_for_a_different_job_the_same_day(linked_member, pushed):
        holder = _webmaster(linked_member)
        _fail("send_class_reminders")
        _fail("send_voting_reminders")
        assert sorted(bell.title for bell in _bells(holder)) == [
            "Class reminder emails failed",
            "Guild voting reminders failed",
        ]
        assert len(mail.outbox) == 2

    def it_alerts_again_on_the_next_pacific_day(linked_member, pushed):
        holder = _webmaster(linked_member)
        late = datetime(2026, 10, 3, 23, 30, tzinfo=PACIFIC)
        after_midnight = datetime(2026, 10, 4, 0, 30, tzinfo=PACIFIC)
        alert_webmasters(_failed_run("send_class_reminders", late), ValueError())
        alert_webmasters(_failed_run("send_class_reminders", after_midnight), ValueError())
        assert len(_bells(holder)) == 2

    def it_counts_the_day_in_pacific_time_not_utc(linked_member, pushed):
        # 4 PM and 6 PM Pacific straddle midnight UTC; they are one Pacific day, so one alert.
        holder = _webmaster(linked_member)
        alert_webmasters(_failed_run("bill_tabs", datetime(2026, 10, 3, 16, 0, tzinfo=PACIFIC)), ValueError())
        alert_webmasters(_failed_run("bill_tabs", datetime(2026, 10, 3, 18, 0, tzinfo=PACIFIC)), ValueError())
        assert len(_bells(holder)) == 1


def describe_when_the_alert_itself_fails():
    def it_logs_it_and_lets_the_jobs_own_error_propagate(linked_member, caplog):
        _webmaster(linked_member)
        with patch("core.events.emit.emit", side_effect=RuntimeError("spine down")):
            with caplog.at_level(logging.ERROR, logger="core.scheduled_jobs"):
                run = _fail()  # asserts the ValueError that came out is the job's own
        assert run.failed
        assert run.error == INCIDENT
        failures = [r for r in caplog.records if r.name == "core.scheduled_jobs"]
        assert [r.getMessage() for r in failures] == [
            "Could not send the Webmasters the bell and push alert that the send_class_reminders automation failed",
            "Could not send the Webmasters the email alert that the send_class_reminders automation failed",
        ]
        assert all(r.exc_info is not None and str(r.exc_info[1]) == "spine down" for r in failures)


def describe_when_the_email_channel_fails():
    def it_still_lands_the_bell_and_push_for_every_webmaster(linked_member, pushed, caplog):
        first, second = _webmaster(linked_member), _webmaster(linked_member)
        with patch.object(EmailAdapter, "deliver", side_effect=ValueError(INCIDENT)):
            with caplog.at_level(logging.ERROR, logger="core.scheduled_jobs"):
                _fail()
        assert len(_bells(first)) == 1
        assert len(_bells(second)) == 1
        assert pushed.call_count == 2
        assert mail.outbox == []
        [logged] = [r for r in caplog.records if r.name == "core.scheduled_jobs"]
        assert logged.getMessage() == (
            "Could not send the Webmasters the email alert that the send_class_reminders automation failed"
        )
        assert str(logged.exc_info[1]) == INCIDENT

    def it_retries_the_email_on_the_next_failure_that_day_without_a_second_bell(linked_member, pushed):
        holder = _webmaster(linked_member)
        with patch.object(EmailAdapter, "deliver", side_effect=ValueError(INCIDENT)):
            _fail()
        _fail()
        assert len(_bells(holder)) == 1
        assert len(mail.outbox) == 1
        pushed.assert_called_once()

    def it_emails_where_static_files_cannot_resolve_as_in_the_cron(linked_member, pushed, settings, tmp_path):
        from django.templatetags.static import static

        # The cron's condition: manifest storage with no collected manifest.
        settings.STATIC_ROOT = str(tmp_path)
        settings.STORAGES = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
        }
        with pytest.raises(ValueError, match="Missing staticfiles manifest entry"):
            static("img/favicon.png")  # the setup reproduces the incident

        holder = _webmaster(linked_member)
        _fail()
        assert len(_bells(holder)) == 1
        assert [message.subject for message in mail.outbox] == ["Class reminder emails failed"]
