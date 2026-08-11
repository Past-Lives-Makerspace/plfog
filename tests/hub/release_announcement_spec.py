"""BDD specs for the Release-mode announcement composer (form + view wiring)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

import core.release_email as release_email_module
from core.models import EventDelivery
from hub.forms import ReleaseAnnouncementForm
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

# A small two-entry changelog so the per-card fields (include_0/1, screenshot_0/1) are
# predictable regardless of the real release line. Entry 0 names a screenshot; entry 1
# does not (its card is text-only by default).
FIXTURE_CHANGELOG: list[dict[str, object]] = [
    {
        "version": "0.20.9",
        "date": "2026-07-11",
        "title": "Home dashboard",
        "changes": ["See what's coming up."],
        "screenshot": "home",
    },
    {
        "version": "0.20.8",
        "date": "2026-07-10",
        "title": "Org info page",
        "changes": ["How our space works."],
    },
]


class _Storage:
    """default_storage stand-in that reports a fixed set of keys as present."""

    def __init__(self, existing: set[str]) -> None:
        self.existing = set(existing)

    def exists(self, key: str) -> bool:
        return key in self.existing

    def url(self, key: str) -> str:
        return f"https://cdn/{key}"

    def listdir(self, prefix: str) -> tuple[list[str], list[str]]:
        """Mirror the storage backends: `(dirs, files)` where `files` are basenames."""
        marker = prefix.rstrip("/") + "/"
        files = sorted(key.rsplit("/", 1)[-1] for key in self.existing if key.startswith(marker))
        return [], files


@pytest.fixture
def release_env(monkeypatch):
    """Pin VERSION + CHANGELOG to the fixture and mark only the 'home' shot as captured."""
    monkeypatch.setattr("plfog.version.VERSION", "0.20.9")
    monkeypatch.setattr("plfog.version.CHANGELOG", FIXTURE_CHANGELOG)
    storage = _Storage({"email/features/home.png"})
    monkeypatch.setattr(release_email_module, "default_storage", storage)
    return storage


def _login_admin(client, *, email: str = "admin@x.com") -> User:
    user = User.objects.create_superuser(username="admin", email=email, password="p")
    client.login(username="admin", password="p")
    return user


def describe_ReleaseAnnouncementForm():
    def it_requires_a_subject(release_env):
        form = ReleaseAnnouncementForm(data={"subject": "", "include_0": "on"})
        assert not form.is_valid()
        assert "subject" in form.errors

    def it_treats_preheader_and_intro_as_optional(release_env):
        form = ReleaseAnnouncementForm(data={"subject": "Hi", "include_0": "on"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["preheader"] == ""
        assert form.cleaned_data["intro"] == ""

    def it_defaults_all_cards_on(release_env):
        form = ReleaseAnnouncementForm()
        assert form.included_count == 2

    def it_offers_only_captured_screenshots_plus_no_screenshot(release_env):
        form = ReleaseAnnouncementForm()
        choices = list(form.fields["screenshot_0"].choices)
        assert ("", "No screenshot") in choices
        assert ("home", "Member home dashboard") in choices
        assert all(value != "org-info" for value, _label in choices)

    def it_defaults_a_captured_slug_selected_and_an_uncaptured_one_to_none(release_env):
        form = ReleaseAnnouncementForm()
        assert form.fields["screenshot_0"].initial == "home"  # captured → preselected
        assert form.fields["screenshot_1"].initial == ""  # no changelog slug → No screenshot

    def describe_with_every_card_toggled_off():
        def it_is_invalid(release_env):
            form = ReleaseAnnouncementForm(data={"subject": "Hi"})  # no include_* → all off
            assert not form.is_valid()
            assert "Include at least one feature" in str(form.errors)


def describe_release_compose_view():
    def it_requires_admin(client):
        User.objects.create_user(username="plain", email="p@x.com", password="p")
        client.login(username="plain", password="p")
        response = client.get(reverse("hub_admin_site_settings") + "?tab=announcements&draft=release")
        assert response.status_code == 403

    def it_enters_release_mode_from_the_draft_button(client, release_env):
        _login_admin(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=announcements&draft=release")
        assert response.context["release_mode"] is True
        assert b'name="mode" value="release"' in response.content
        assert b"Plain announcement" in response.content  # link back to the freeform composer

    def it_previews_the_assembled_email_and_excludes_omitted_cards(client, release_env, mailoutbox):
        _login_admin(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "action": "announce_preview",
                "mode": "release",
                "subject": "What's new",
                "preheader": "peek",
                "intro": "<p>hello</p>",
                "include_0": "on",
                "screenshot_0": "home",
                # card 1 (include_1) omitted → excluded from the email
            },
        )
        assert response.status_code == 200
        preview = response.context["release_preview"]
        assert "Home dashboard" in preview["html"]
        assert "Org info page" not in preview["html"]  # omitted card is absent
        assert 'src="https://cdn/email/features/home.png"' in preview["html"]
        assert mailoutbox == []  # a preview never sends

    def it_swaps_a_card_screenshot_via_the_select(client, release_env):
        _login_admin(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "action": "announce_preview",
                "mode": "release",
                "subject": "S",
                "include_0": "on",
                "screenshot_0": "",  # clear card 0's shot
                "include_1": "on",
                "screenshot_1": "home",  # lend card 1 the home shot
            },
        )
        html = response.context["release_preview"]["html"]
        # Exactly one image now — on the swapped card.
        assert html.count('src="https://cdn/email/features/home.png"') == 1

    def it_renders_the_upgraded_preview_chrome(client, release_env):
        _login_admin(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={"action": "announce_preview", "mode": "release", "subject": "S", "include_0": "on"},
        )
        body = response.content
        assert b"pl-email-preview__inbox" in body  # inbox row (sender/subject/preheader)
        assert b"Plain text" in body  # plain-text toggle
        assert b"Mobile" in body  # mobile-width toggle

    def describe_send_test():
        def it_sends_only_to_the_admin_and_never_consumes_the_real_period(client, release_env, mailoutbox):
            _login_admin(client, email="lead@x.com")
            response = client.post(
                reverse("hub_admin_site_settings"),
                data={"action": "announce_test", "mode": "release", "subject": "Test", "include_0": "on"},
            )
            assert response.status_code == 200
            assert len(mailoutbox) == 1
            assert mailoutbox[0].to == ["lead@x.com"]
            # A direct send — no site_announcement ledger row, so it can't block the real send.
            assert EventDelivery.objects.filter(event_key="site_announcement").count() == 0

            # A subsequent REAL send still delivers to a member.
            MembershipPlanFactory()
            User.objects.create_user(username="m1", email="m1@x.com", password="p", last_login=timezone.now())
            sent = client.post(
                reverse("hub_admin_site_settings"),
                data={"action": "announce_send", "mode": "release", "subject": "For real", "include_0": "on"},
            )
            assert sent.status_code == 302
            recipients = {addr for message in mailoutbox for addr in message.to}
            assert "m1@x.com" in recipients

    def describe_send():
        def it_emits_to_activated_members_with_the_assembled_html(client, release_env, mailoutbox):
            _login_admin(client)
            MembershipPlanFactory()
            User.objects.create_user(username="act", email="act@x.com", password="p", last_login=timezone.now())
            User.objects.create_user(username="never", email="never@x.com", password="p")  # never signed in
            response = client.post(
                reverse("hub_admin_site_settings"),
                data={"action": "announce_send", "mode": "release", "subject": "What's new", "include_0": "on"},
            )
            assert response.status_code == 302
            sent = {addr for message in mailoutbox for addr in message.to}
            assert "act@x.com" in sent
            assert "never@x.com" not in sent
            # The HTML alternative is the sectioned release email, not the flat card.
            member_msg = next(m for m in mailoutbox if "act@x.com" in m.to)
            html_alt = next(body for body, mime in member_msg.alternatives if mime == "text/html")
            assert "Heads-Up: New Member Portal Features" in html_alt
            assert "Home dashboard" in html_alt

        def it_delivers_a_corrective_resend(client, release_env, mailoutbox):
            _login_admin(client)
            MembershipPlanFactory()
            User.objects.create_user(username="m", email="m@x.com", password="p", last_login=timezone.now())
            data = {"action": "announce_send", "mode": "release", "subject": "v1", "include_0": "on"}
            first = client.post(reverse("hub_admin_site_settings"), data=data)
            assert first.status_code == 302
            # A corrective resend uses a fresh timestamp period → delivers again (not version-deduped).
            second = client.post(reverse("hub_admin_site_settings"), data={**data, "subject": "v2 corrected"})
            assert second.status_code == 302
            to_member = [m for m in mailoutbox if "m@x.com" in m.to]
            assert len(to_member) == 2

        def describe_with_every_card_off():
            def it_re_renders_with_an_error_and_sends_nothing(client, release_env, mailoutbox):
                _login_admin(client)
                response = client.post(
                    reverse("hub_admin_site_settings"),
                    data={"action": "announce_send", "mode": "release", "subject": "Hi"},  # all cards off
                )
                assert response.status_code == 200  # re-render, not a redirect
                assert b"Include at least one feature" in response.content
                assert mailoutbox == []
