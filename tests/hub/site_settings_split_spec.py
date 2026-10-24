"""Site Settings split into five pages (#922): old links, stale posts, and saves that stay on their page.

Member Agreement, Automations, Manage Features and Manage CMS were Site Settings tabs, all in one
form whose ``submitted_tab`` named the tab showing. Each is its own page and form now, so:

- an old ``?tab=`` link to one of them redirects to its page;
- a POST from a Site Settings page loaded before the split is handed on with a 307, so the new
  page saves or runs what was sent instead of Site Settings reading it as a save of fields it no
  longer has;
- saving any one page writes only that page's fields and rows.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.features import FeatureState
from core.models import FeatureSwitch, ScheduledJobState, SiteConfiguration
from membership.models import Member, MemberAgreementAcceptance
from tests.hub.settings_posts import features_post, jobstate_formset_post, site_settings_post

pytestmark = pytest.mark.django_db

SITE_SETTINGS = reverse("hub_admin_site_settings")

_MOVED = {
    "features": "hub_admin_features",
    "member-agreement": "hub_admin_member_agreement",
    "automations": "hub_admin_automations",
    "legacy-cms": "hub_admin_cms",
}


@pytest.fixture
def admin(client: Client) -> Client:
    user = User.objects.create_superuser(username="splitadmin", email="splitadmin@x.com", password="p")
    # Accepted the edition _DISTINCT switches on, so the agreement gate never bounces a save.
    for version in ("9.9", "4.0"):
        MemberAgreementAcceptance.objects.create(
            member=Member.objects.get(user=user),
            agreement_url="https://kb.example.com/agreement",
            ip_address="127.0.0.1",
            document_version=version,
        )
    client.login(username="splitadmin", password="p")
    return client


def describe_an_old_tab_link():
    @pytest.mark.parametrize(("tab", "url_name"), _MOVED.items())
    def it_redirects_to_the_tabs_page(admin: Client, tab: str, url_name: str):
        response = admin.get(f"{SITE_SETTINGS}?tab={tab}")
        assert response.status_code == 302
        assert response["Location"] == reverse(url_name)

    @pytest.mark.parametrize("tab", ["brand", "general", "calendar", "emails", "discord"])
    def it_still_opens_a_tab_site_settings_kept(admin: Client, tab: str):
        response = admin.get(f"{SITE_SETTINGS}?tab={tab}")
        assert response.status_code == 200
        assert response.context["active_tab"] == tab

    def it_opens_general_for_a_tab_nobody_has(admin: Client):
        assert admin.get(f"{SITE_SETTINGS}?tab=nonsense").context["active_tab"] == "general"

    def it_shows_only_the_kept_tabs(admin: Client):
        html = admin.get(SITE_SETTINGS).content.decode()
        for tab in _MOVED:
            assert f"tab = '{tab}'" not in html
        for tab in ("brand", "general", "calendar", "emails", "discord"):
            assert f"@click=\"tab = '{tab}'\"" in html


def describe_a_post_from_a_page_loaded_before_the_split():
    def _assert_handed_to(response: Any, url_name: str) -> None:
        assert response.status_code == 307
        assert response["Location"] == reverse(url_name)

    @pytest.mark.parametrize(("tab", "url_name"), _MOVED.items())
    def it_hands_a_save_from_a_moved_tab_to_its_page(admin: Client, tab: str, url_name: str):
        before = SiteConfiguration.load().org_name
        response = admin.post(SITE_SETTINGS, site_settings_post(submitted_tab=tab, org_name="Not Saved Here"))
        _assert_handed_to(response, url_name)
        assert SiteConfiguration.load().org_name == before

    def it_reads_the_tab_from_the_address_when_the_post_names_none(admin: Client):
        data = site_settings_post()
        del data["submitted_tab"]
        _assert_handed_to(admin.post(f"{SITE_SETTINGS}?tab=features", data), "hub_admin_features")

    def it_hands_run_now_to_automations_from_any_tab(admin: Client):
        # The bill_tabs confirm modal posted only run_job, with no tab at all.
        with patch("django.core.management.call_command") as cc:
            response = admin.post(SITE_SETTINGS, {"run_job": "bill_tabs"})
        _assert_handed_to(response, "hub_admin_automations")
        cc.assert_not_called()

    def it_hands_sync_now_from_the_automations_tab_to_automations(admin: Client):
        with patch("classes.import_service.sync_legacy_cms") as sync:
            response = admin.post(SITE_SETTINGS, {"action": "sync_now", "submitted_tab": "automations"})
        _assert_handed_to(response, "hub_admin_automations")
        sync.assert_not_called()

    @pytest.mark.parametrize("data", [{"submitted_tab": "legacy-cms"}, {}])
    def it_hands_any_other_sync_now_to_manage_cms(admin: Client, data: dict[str, str]):
        response = admin.post(SITE_SETTINGS, {"action": "sync_now", **data})
        _assert_handed_to(response, "hub_admin_cms")

    def it_still_saves_a_post_from_a_tab_site_settings_kept(admin: Client):
        response = admin.post(SITE_SETTINGS, site_settings_post(submitted_tab="brand", org_name="Renamed"))
        assert response.status_code == 302
        assert response["Location"] == f"{SITE_SETTINGS}?tab=brand"
        assert SiteConfiguration.load().org_name == "Renamed"

    def describe_followed_through_to_the_new_page():
        """The test client re-posts a 307 with the same body, as a browser does."""

        def it_saves_the_agreement_on_the_member_agreement_page(admin: Client):
            data = site_settings_post(
                submitted_tab="member-agreement",
                member_agreement_required="on",
                member_agreement_url="https://kb.example.com/agreement",
                member_agreement_version="4.0",
            )
            response = admin.post(SITE_SETTINGS, data, follow=True)
            assert response.redirect_chain[-1] == (reverse("hub_admin_member_agreement"), 302)
            config = SiteConfiguration.load()
            assert config.member_agreement_required is True
            assert config.member_agreement_version == "4.0"

        def it_saves_the_features_on_manage_features(admin: Client):
            data = {**site_settings_post(submitted_tab="features"), **features_post(help_page_enabled="")}
            admin.post(SITE_SETTINGS, data, follow=True)
            assert SiteConfiguration.load().help_page_enabled is False

        def it_runs_the_job_on_the_automations_page(admin: Client):
            with patch("django.core.management.call_command") as cc:
                response = admin.post(SITE_SETTINGS, {"run_job": "send_class_reminders"}, follow=True)
            cc.assert_called_once_with("send_class_reminders")
            assert response.redirect_chain[-1] == (reverse("hub_admin_automations"), 302)

        def it_syncs_from_manage_cms(admin: Client):
            with patch("classes.import_service.sync_legacy_cms", return_value=2) as sync:
                response = admin.post(SITE_SETTINGS, {"action": "sync_now", "submitted_tab": "legacy-cms"}, follow=True)
            sync.assert_called_once_with()
            assert response.redirect_chain[-1] == (reverse("hub_admin_cms"), 302)


# Which page owns which field, written out here rather than read from the forms, so a field that
# drifts onto a second page's form fails these specs instead of being excused by them.
_OWN: dict[str, set[str]] = {
    "hub_admin_site_settings": {
        "org_name",
        "org_support_email",
        "kiln_tickets_open",
        "suspended_members_locked_out",
        "sync_classes_enabled",
        "google_calendar_sync_enabled",
        "discord_server_id",
        "discord_calendar_posts_enabled",
    },
    "hub_admin_member_agreement": {"member_agreement_required", "member_agreement_url", "member_agreement_version"},
    "hub_admin_features": {
        "late_cancel_fees_enabled",
        "late_cancel_notice_hours",
        "late_cancel_grace_hours",
        "class_registration_enabled",
        "class_registration_disabled_note",
        "help_page_enabled",
        "guided_tours_enabled",
        "guild_welcome_email_enabled",
        "display_demo_classes",
        "display_demo_guild",
        "member_directory_public",
    },
    "hub_admin_cms": {"instructor_discount_codes_enabled", "instructor_discount_codes_need_approval"},
    "hub_admin_automations": {"legacy_cms_sync_enabled"},
}

# A non-default value for every field above, so a save that wrote back a default (a missing
# checkbox read as off, a blank text) would show as a change.
_DISTINCT: dict[str, Any] = {
    # Site Settings
    "org_name": "Distinct Org",
    "org_support_email": "help@distinct.example",
    "kiln_tickets_open": True,
    "suspended_members_locked_out": True,
    "sync_classes_enabled": True,
    "google_calendar_sync_enabled": True,
    "discord_server_id": "123456789012345678",
    "discord_calendar_posts_enabled": True,
    # Member Agreement
    "member_agreement_required": True,
    "member_agreement_url": "https://kb.example.com/agreement",
    "member_agreement_version": "9.9",
    # Manage Features
    "late_cancel_fees_enabled": True,
    "late_cancel_notice_hours": 48,
    "late_cancel_grace_hours": 6,
    "class_registration_enabled": False,
    "class_registration_disabled_note": "Back soon",
    "help_page_enabled": False,
    "guided_tours_enabled": False,
    "guild_welcome_email_enabled": False,
    "display_demo_classes": True,
    "display_demo_guild": True,
    "member_directory_public": True,
    # Manage CMS
    "instructor_discount_codes_enabled": True,
    "instructor_discount_codes_need_approval": True,
    # Automations
    "legacy_cms_sync_enabled": True,
}


def _post_for(url_name: str) -> dict[str, str]:
    """A valid save of that page with every one of its own checkboxes off."""
    return {
        "hub_admin_site_settings": site_settings_post,
        "hub_admin_member_agreement": lambda: {"member_agreement_url": "", "member_agreement_version": ""},
        "hub_admin_features": features_post,
        "hub_admin_cms": dict,
        "hub_admin_automations": jobstate_formset_post,
    }[url_name]()


def _snapshot() -> dict[str, Any]:
    config = SiteConfiguration.load()
    return {name: getattr(config, name) for name in _DISTINCT}


def _rows() -> tuple[dict[str, str], dict[str, bool]]:
    features = dict(FeatureSwitch.objects.values_list("feature_key", "state"))
    jobs = dict(ScheduledJobState.objects.values_list("task_key", "enabled"))
    return features, jobs


def describe_saving_one_page():
    @pytest.fixture
    def distinct() -> None:
        assert set(_DISTINCT) == set().union(*_OWN.values())
        SiteConfiguration.objects.filter(pk=SiteConfiguration.load().pk).update(**_DISTINCT)
        FeatureSwitch.objects.sync_registry()
        FeatureSwitch.objects.filter(feature_key="wiki").update(state=FeatureState.HIDDEN)
        ScheduledJobState.objects.sync_registry()
        ScheduledJobState.objects.filter(task_key="send_voting_reminders").update(enabled=False)

    @pytest.mark.parametrize("url_name", _OWN)
    def it_leaves_every_other_pages_fields_alone(admin: Client, distinct: None, url_name: str):
        response = admin.post(reverse(url_name), _post_for(url_name))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse(url_name)), "the save itself, not a bounce elsewhere"
        own = _OWN[url_name]
        after = _snapshot()
        assert {name: after[name] for name in _DISTINCT if name not in own} == {
            name: value for name, value in _DISTINCT.items() if name not in own
        }

    @pytest.mark.parametrize("url_name", _OWN)
    def it_writes_its_own_fields(admin: Client, distinct: None, url_name: str):
        # The other half: the post above switched this page's checkboxes off, and that landed.
        admin.post(reverse(url_name), _post_for(url_name))
        after = _snapshot()
        own_booleans = [name for name in _OWN[url_name] if _DISTINCT[name] is True]
        assert own_booleans, "every page has a distinct switch to watch"
        assert [name for name in own_booleans if after[name] is not False] == []

    @pytest.mark.parametrize("url_name", [name for name in _OWN if name != "hub_admin_features"])
    def it_leaves_the_feature_switches_alone(admin: Client, distinct: None, url_name: str):
        features_before, _ = _rows()
        admin.post(reverse(url_name), _post_for(url_name))
        assert _rows()[0] == features_before

    @pytest.mark.parametrize("url_name", [name for name in _OWN if name != "hub_admin_automations"])
    def it_leaves_the_job_switches_alone(admin: Client, distinct: None, url_name: str):
        _, jobs_before = _rows()
        admin.post(reverse(url_name), _post_for(url_name))
        assert _rows()[1] == jobs_before
