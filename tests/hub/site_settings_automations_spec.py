"""Site Settings → Automations tab — the scheduled-job dashboard (toggles + Run now).

Covers the run-now handler (gating, no --force, run recording, no 500), toggle persistence,
the money-job confirm guard, and the per-job render states (never-run / failed / paused / the
legacy-CMS OFF sentence). The tab lives inside #site-settings-form; toggles save with the page.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from core.factories import ScheduledTaskRunFactory
from core.models import ScheduledJobState, ScheduledTaskRun, SiteConfiguration
from core.scheduled_jobs import SCHEDULED_JOBS, Trigger

pytestmark = pytest.mark.django_db

URL = reverse("hub_admin_site_settings")


def _superuser(client: Client):
    from django.contrib.auth.models import User

    user = User.objects.create_superuser(username="autoadmin", email="autoadmin@x.com", password="p")
    client.login(username="autoadmin", password="p")
    return user


def _jobstate_post_data(disabled_keys: tuple[str, ...] = ()) -> dict[str, str]:
    """Build the jobstate formset POST from the current rows (ordered like the formset). A key in
    ``disabled_keys`` omits its checkbox (unchecked = off)."""
    ScheduledJobState.objects.sync_registry()
    toggleable = [job.key for job in SCHEDULED_JOBS if job.toggleable]
    rows = list(ScheduledJobState.objects.filter(task_key__in=toggleable).order_by("task_key"))
    data = {
        "jobstates-TOTAL_FORMS": str(len(rows)),
        "jobstates-INITIAL_FORMS": str(len(rows)),
        "jobstates-MIN_NUM_FORMS": "0",
        "jobstates-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        data[f"jobstates-{i}-id"] = str(row.pk)
        if row.task_key not in disabled_keys:
            data[f"jobstates-{i}-enabled"] = "on"
    return data


def _settings_post(**overrides: str) -> dict[str, str]:
    """A full Automations-tab save: the shared SiteSettingsForm fields + feeds management form +
    the jobstate formset."""
    data = {
        "registration_mode": SiteConfiguration.RegistrationMode.INVITE_ONLY,
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "signage_default_slide_seconds": "12",
        "signage_event_days_ahead": "30",
        "feeds-TOTAL_FORMS": "0",
        "feeds-INITIAL_FORMS": "0",
        "feeds-MIN_NUM_FORMS": "0",
        "feeds-MAX_NUM_FORMS": "1000",
        "submitted_tab": "automations",
    }
    data.update(_jobstate_post_data(disabled_keys=tuple(overrides.pop("_disabled_keys", ()))))
    data.update(overrides)
    return data


def describe_automations_access():
    def it_forbids_non_admins(client: Client):
        from django.contrib.auth.models import User

        User.objects.create_user(username="plain", email="plain@x.com", password="p")
        client.login(username="plain", password="p")
        assert client.get(f"{URL}?tab=automations").status_code == 403

    def it_lists_every_registry_job_for_an_admin(client: Client):
        from django.utils.html import escape

        _superuser(client)
        html = client.get(f"{URL}?tab=automations").content.decode()
        for job in SCHEDULED_JOBS:
            assert escape(job.name) in html


def describe_run_now():
    def it_dispatches_the_job_without_force_and_records_a_manual_run(client: Client):
        user = _superuser(client)
        with patch("django.core.management.call_command") as cc:
            response = client.post(URL, data={"run_job": "send_class_reminders"})
        cc.assert_called_once_with("send_class_reminders")
        run = ScheduledTaskRun.objects.get(task_key="send_class_reminders")
        assert run.trigger == Trigger.MANUAL
        assert run.actor == user
        assert run.succeeded
        assert response.status_code == 302
        assert "tab=automations" in response.url

    def it_reports_success_in_a_message(client: Client):
        _superuser(client)
        with patch("django.core.management.call_command"):
            response = client.post(URL, data={"run_job": "send_class_reminders"}, follow=True)
        assert any("Ran Class reminder emails" in m.message for m in response.context["messages"])

    def describe_with_an_unknown_key():
        def it_errors_without_dispatching(client: Client):
            _superuser(client)
            with patch("django.core.management.call_command") as cc:
                response = client.post(URL, data={"run_job": "not_a_job"}, follow=True)
            cc.assert_not_called()
            assert not ScheduledTaskRun.objects.exists()
            assert any("Unknown automation" in m.message for m in response.context["messages"])

    def describe_when_the_command_raises():
        def it_records_a_failed_run_and_does_not_500(client: Client):
            _superuser(client)
            with patch("django.core.management.call_command", side_effect=RuntimeError("kaboom")):
                response = client.post(URL, data={"run_job": "send_class_reminders"}, follow=True)
            assert response.status_code == 200  # redirect followed, not a 500
            run = ScheduledTaskRun.objects.get(task_key="send_class_reminders")
            assert run.failed
            assert run.error == "kaboom"
            assert any("failed: kaboom" in m.message for m in response.context["messages"])


def describe_money_job_guard():
    def it_dispatches_bill_tabs_with_no_force(client: Client):
        _superuser(client)
        with patch("django.core.management.call_command") as cc:
            client.post(URL, data={"run_job": "bill_tabs"})
        cc.assert_called_once_with("bill_tabs")

    def it_renders_a_confirm_modal_and_danger_button_for_bill_tabs(client: Client):
        _superuser(client)
        html = client.get(f"{URL}?tab=automations").content.decode()
        assert "open-confirm', 'run-bill_tabs'" in html
        assert "Run tab billing now?" in html  # the confirm modal include expanded
        assert "This attempts real card charges" in html
        assert 'name="run_job" value="bill_tabs"' in html  # carried as the modal's hidden field

    def it_renders_a_plain_primary_run_button_for_a_normal_job(client: Client):
        _superuser(client)
        html = client.get(f"{URL}?tab=automations").content.decode()
        assert 'name="run_job" value="send_class_reminders"' in html
        assert "Always on" in html  # bill_tabs shows the non-toggleable chip


def describe_toggle_persistence():
    def it_saves_a_job_toggled_off(client: Client):
        _superuser(client)
        client.post(URL, data=_settings_post(_disabled_keys=("send_class_reminders",)))
        assert ScheduledJobState.objects.get(task_key="send_class_reminders").enabled is False

    def it_leaves_other_jobs_enabled(client: Client):
        _superuser(client)
        client.post(URL, data=_settings_post(_disabled_keys=("send_class_reminders",)))
        assert ScheduledJobState.objects.get(task_key="send_voting_reminders").enabled is True

    def it_saves_the_legacy_sub_toggle_onto_the_config(client: Client):
        _superuser(client)
        client.post(URL, data=_settings_post(legacy_cms_sync_enabled="on"))
        assert SiteConfiguration.load().legacy_cms_sync_enabled is True


def describe_run_now_preserves_toggle_edits():
    def it_saves_the_toggle_that_rode_along_the_run_now_post(client: Client):
        _superuser(client)
        data = _settings_post(_disabled_keys=("send_voting_reminders",))
        data["run_job"] = "send_class_reminders"
        with patch("django.core.management.call_command"):
            client.post(URL, data=data)
        assert ScheduledJobState.objects.get(task_key="send_voting_reminders").enabled is False

    def it_saves_the_legacy_cms_toggle_that_rode_along_the_run_now_post(client: Client):
        _superuser(client)
        data = _settings_post(legacy_cms_sync_enabled="on")
        data["run_job"] = "send_class_reminders"
        with patch("django.core.management.call_command"):
            client.post(URL, data=data)
        assert SiteConfiguration.load().legacy_cms_sync_enabled is True

    def it_clears_the_legacy_cms_toggle_when_unchecked_on_the_run_now_post(client: Client):
        _superuser(client)
        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = True
        config.save(update_fields=["legacy_cms_sync_enabled"])
        data = _settings_post()  # legacy_cms checkbox absent = unchecked
        data["run_job"] = "send_class_reminders"
        with patch("django.core.management.call_command"):
            client.post(URL, data=data)
        assert SiteConfiguration.load().legacy_cms_sync_enabled is False


def describe_run_now_never_writes_the_settings_singleton():
    def it_leaves_the_config_untouched_on_a_bill_tabs_confirm_only_post(client: Client):
        # The bill_tabs confirm modal posts only run_job + csrf — no management form and no
        # settings fields. The run_job path must not bind/save SiteSettingsForm, so an existing
        # config value the sparse POST omits (here legacy_cms_sync_enabled) must survive intact.
        _superuser(client)
        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = True
        config.save(update_fields=["legacy_cms_sync_enabled"])
        with patch("django.core.management.call_command"):
            client.post(URL, data={"run_job": "bill_tabs"})
        assert SiteConfiguration.load().legacy_cms_sync_enabled is True


def describe_render_states():
    def it_shows_never_run_for_a_job_that_has_no_history(client: Client):
        _superuser(client)
        html = client.get(f"{URL}?tab=automations").content.decode()
        assert "Never run" in html

    def it_shows_a_failed_pill_and_the_error_title(client: Client):
        _superuser(client)
        ScheduledTaskRunFactory(
            task_key="send_class_reminders",
            status=ScheduledTaskRun.Status.FAILED,
            error="connection refused",
        )
        html = client.get(f"{URL}?tab=automations").content.decode()
        assert "hub-pill--danger" in html
        assert 'title="connection refused"' in html

    def it_shows_the_paused_note_for_a_disabled_job(client: Client):
        _superuser(client)
        ScheduledJobState.objects.sync_registry()
        ScheduledJobState.objects.set_enabled("send_class_reminders", False)
        html = client.get(f"{URL}?tab=automations").content.decode()
        assert "Paused — won't run on schedule." in html

    def it_states_the_legacy_pull_is_off_when_the_flag_is_off(client: Client):
        _superuser(client)
        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = False
        config.save()
        html = client.get(f"{URL}?tab=automations").content.decode()
        assert "is NOT running" in html

    def it_cross_links_from_the_legacy_tab_to_automations(client: Client):
        _superuser(client)
        html = client.get(f"{URL}?tab=legacy-cms").content.decode()
        assert 'href="?tab=automations"' in html
