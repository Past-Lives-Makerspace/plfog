"""Every POST form on the sign in screens locks its button after the first submit (#803).

A double tap on Verify posted the login code twice in one second: the first POST signed the
member in and rotated the CSRF token, the second carried the old token and got Django's 403
page. Each form in ``templates/account/`` now carries the ``components/busy_submit.html``
markers (``data-pl-busy-submit`` on the form, ``data-pl-busy`` on its submit button, a busy
label), and the page includes the component's script once. These specs read the rendered HTML
on every host the templates serve; ``tests/e2e/auth_busy_submit_spec.py`` drives the double tap.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

MEMBERS = "members.pastlives.space"
CLASSES = "classes.pastlives.space"
GUILDS = "guilds.pastlives.space"
SCRIPT_GUARD = "window.plBusySubmitBound"


class _Forms(HTMLParser):
    """Collect each POST form's attributes and the submit buttons inside it."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._open: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form" and (a.get("method") or "").lower() == "post":
            self._open = {"attrs": a, "submits": []}
            self.forms.append(self._open)
        elif tag == "button" and self._open is not None and a.get("type") == "submit":
            self._open["submits"].append(a)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._open = None


def _post_forms(html: str) -> list[dict]:
    parser = _Forms()
    parser.feed(html)
    return parser.forms


def _assert_busy_forms(html: str, label: str) -> None:
    forms = _post_forms(html)
    assert forms, "expected at least one POST form"
    for form in forms:
        assert "data-pl-busy-submit" in form["attrs"], form["attrs"]
        assert form["attrs"].get("data-pl-busy-label") == label
        assert form["submits"], f"no submit button in {form['attrs']}"
        for button in form["submits"]:
            assert "data-pl-busy" in button, button
    assert html.count(SCRIPT_GUARD) == 2, "busy_submit.html should be included exactly once"


@pytest.fixture
def hosts(settings):
    settings.PUBLIC_HOSTS = [CLASSES]
    settings.GUILDS_HOSTS = [GUILDS]
    settings.MEMBER_HOST = MEMBERS
    settings.ALLOWED_HOSTS = [MEMBERS, CLASSES, GUILDS, "testserver"]
    cache.clear()  # the request code rate limit lives in the cache


def _client(host: str) -> Client:
    return Client(HTTP_HOST=host)


ALL_HOSTS = pytest.mark.parametrize("host", [MEMBERS, CLASSES, GUILDS])


def describe_the_send_code_forms():
    @ALL_HOSTS
    def it_locks_the_login_page_button_as_sending(hosts, host):
        resp = _client(host).get("/accounts/login/")
        assert resp.status_code == 200
        _assert_busy_forms(resp.content.decode(), "Sending...")

    @ALL_HOSTS
    def it_locks_the_request_code_page_button_as_sending(hosts, host):
        resp = _client(host).get("/accounts/login/code/")
        assert resp.status_code == 200
        _assert_busy_forms(resp.content.decode(), "Sending...")

    def it_leaves_the_biometric_button_unmarked(hosts):
        html = _client(MEMBERS).get("/accounts/login/").content.decode()
        start = html.index("data-biometric-unlock-button")
        tag = html[html.rindex("<button", 0, start) : html.index(">", start)]
        assert "data-pl-busy" not in tag


def describe_the_enter_code_form():
    @ALL_HOSTS
    def it_locks_verify_as_signing_in(hosts, host):
        email = "busy-code@example.com"
        user = User.objects.create_user(username=email, email=email)
        EmailAddress.objects.update_or_create(user=user, email=email, defaults={"verified": True, "primary": True})
        client = _client(host)

        resp = client.post("/accounts/login/code/", {"email": email}, follow=True)

        assert resp.status_code == 200
        assert "account/confirm_login_code.html" in [t.name for t in resp.templates]
        _assert_busy_forms(resp.content.decode(), "Signing in...")


def describe_the_signup_form():
    @ALL_HOSTS
    def it_locks_the_button_as_sending(hosts, host):
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        resp = _client(host).get("/accounts/signup/")

        assert resp.status_code == 200
        _assert_busy_forms(resp.content.decode(), "Sending...")


def describe_the_find_account_form():
    def it_locks_the_button_as_sending(hosts):
        resp = _client(MEMBERS).get("/accounts/find-account/")
        assert resp.status_code == 200
        _assert_busy_forms(resp.content.decode(), "Sending...")


def describe_the_logout_form():
    def it_locks_the_button_as_logging_out(hosts):
        user = User.objects.create_user(username="busy-out@example.com", email="busy-out@example.com")
        client = _client(MEMBERS)
        client.force_login(user)

        resp = client.get("/accounts/logout/")

        assert resp.status_code == 200
        _assert_busy_forms(resp.content.decode(), "Logging out...")
