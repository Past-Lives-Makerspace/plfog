"""Tab resolution + template restructure for the User Settings page.

Covers the settings-restructure tab changes: guilds default, the emails->account alias,
the whitelist/XSS guard, the guild-updates write-on-GET (and the failed-POST guard against
it), the allauth redirect, the moved email card + danger-zone polish, the notifications
jump chips + subtitles, and the dirty-guard markup pins.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client

pytestmark = pytest.mark.django_db

_CONTACTS_MGMT = {"contacts-TOTAL_FORMS": "0", "contacts-INITIAL_FORMS": "0"}


def _login(client, username="settingsuser"):
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    client.login(username=username, password="pass")
    return user


def describe_tab_resolution():
    def it_defaults_to_guilds_with_no_param(client: Client):
        _login(client)
        assert client.get("/settings/").context["active_tab"] == "guilds"

    def it_falls_back_to_guilds_for_a_garbage_param(client: Client):
        _login(client)
        response = client.get("/settings/?tab=%27%2Balert(1)%2B%27")
        assert response.context["active_tab"] == "guilds"
        assert b"alert(1)" not in response.content

    def it_aliases_the_legacy_emails_param_to_account(client: Client):
        _login(client)
        assert client.get("/settings/?tab=emails").context["active_tab"] == "account"

    def it_passes_through_each_whitelisted_value(client: Client):
        _login(client)
        for tab in ("profile", "notifications", "guilds", "account"):
            assert client.get(f"/settings/?tab={tab}").context["active_tab"] == tab


def describe_guild_updates_stamp():
    def it_stamps_on_a_bare_settings_get(client: Client):
        user = _login(client)
        member = user.member
        assert member.guild_updates_prompt_answered_at is None
        client.get("/settings/")
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None

    def it_is_a_noop_on_a_second_get(client: Client):
        user = _login(client)
        client.get("/settings/")
        user.member.refresh_from_db()
        first = user.member.guild_updates_prompt_answered_at
        client.get("/settings/")
        user.member.refresh_from_db()
        assert user.member.guild_updates_prompt_answered_at == first

    def it_re_renders_the_profile_tab_on_a_failed_save_without_stamping(client: Client):
        # A failed profile POST re-renders (no redirect) on the profile tab, and must NOT
        # stamp the guild-updates answer on a landing the user never chose (§5.3).
        user = _login(client)
        assert user.member.guild_updates_prompt_answered_at is None
        response = client.post(
            "/settings/",
            {"form_id": "profile", "phone": "1" * 40, **_CONTACTS_MGMT},  # phone > max_length=20
        )
        assert response.status_code == 200  # re-render, not a redirect
        assert response.context["active_tab"] == "profile"
        user.member.refresh_from_db()
        assert user.member.guild_updates_prompt_answered_at is None


def describe_account_email_redirect():
    def it_redirects_account_email_get_to_the_account_tab(client: Client):
        _login(client, "legacyemail")
        response = client.get("/accounts/email/")
        assert response.status_code == 302
        assert response.url == "/settings/?tab=account"


def describe_account_tab_template():
    def it_drops_the_standalone_emails_pane(client: Client):
        _login(client)
        content = client.get("/settings/").content.decode()
        assert "tab === 'emails'" not in content

    def it_moves_the_email_card_into_account_above_the_danger_zone(client: Client):
        _login(client)
        content = client.get("/settings/?tab=account").content.decode()
        assert "Manage Email Addresses" in content
        assert "pl-danger-zone" in content
        assert content.index("Manage Email Addresses") < content.index("pl-danger-zone")

    def it_keeps_the_typed_delete_confirm_in_the_danger_zone(client: Client):
        _login(client)
        content = client.get("/settings/?tab=account").content.decode()
        assert "confirm-delete-account" in content or "delete-account" in content
        assert 'name="confirm_text"' in content  # typed-DELETE field, unchanged


def describe_notifications_template():
    def it_renders_a_jump_chip_and_anchor_per_section(client: Client):
        _login(client)
        content = client.get("/settings/?tab=notifications").content.decode()
        assert "pl-notif-jump__chip" in content
        # The Guilds section is present for everyone: chip href + matching anchor id.
        assert 'href="#notif-guilds"' in content
        assert 'id="notif-guilds"' in content

    def it_renders_the_guilds_and_meetings_subtitles(client: Client):
        _login(client)
        content = client.get("/settings/?tab=notifications").content.decode()
        assert "Manage which guilds send you updates" in content
        assert "View upcoming meetings" in content


def describe_dirty_guard_markup():
    def it_marks_exactly_the_three_save_forms(client: Client):
        # The keyed attribute lands on exactly the three batch-save forms. (The bare token
        # "data-dirty-key" also appears in the guard JS, so assert the keyed HTML form.)
        _login(client)
        content = client.get("/settings/").content.decode()
        for key in ("profile", "notifications", "tours"):
            assert content.count(f'data-dirty-key="{key}"') == 1

    def it_includes_the_discard_modal_with_a_stay_button(client: Client):
        _login(client)
        content = client.get("/settings/").content.decode()
        assert "discard-settings-changes" in content
        assert "Discard Changes" in content
        assert "Stay" in content  # confirm_cancel_text on the discard modal

    def it_keeps_cancel_as_the_default_confirm_label(client: Client):
        # The delete-account modal passes no confirm_cancel_text, so it still says "Cancel".
        _login(client)
        content = client.get("/settings/?tab=account").content.decode()
        assert ">Cancel<" in content

    def it_arms_dirty_on_alpine_only_profile_controls(client: Client):
        _login(client)
        content = client.get("/settings/?tab=profile").content.decode()
        assert "data-arm-dirty" in content

    def it_dispatches_a_change_event_from_the_bulk_toggle(client: Client):
        _login(client)
        content = client.get("/settings/?tab=notifications").content.decode()
        assert "new Event('change', { bubbles: true })" in content

    def it_wires_the_boosted_navigation_guard_with_cross_form_and_async_resume(client: Client):
        # The htmx:confirm hold, its cross-form-save branch, and the async issueRequest(true)
        # resume are the boosted-nav guard the runtime cannot server-test — pin they render.
        _login(client)
        content = client.get("/settings/").content.decode()
        assert "htmx:confirm" in content
        assert "crossFormSave" in content
        assert "issueRequest(true)" in content
        assert "beforeunload" in content
