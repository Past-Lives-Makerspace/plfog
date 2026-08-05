"""Specs for the temporary secret-gated reviewer-login diagnostics endpoint.

NOTE: specs are deliberately flat. ``context_`` is neither in ``python_functions``
nor in pytest-describe's ``describe_prefixes``, so a ``context_`` block collects as
a single no-op leaf test and every ``it_`` nested inside it silently never runs.
"""

import hashlib
import json

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TOKEN = "diag-token-for-tests"


def _get(client, monkeypatch, token=TOKEN, diag_token=TOKEN):
    """Call the endpoint with the given supplied/configured tokens."""
    if diag_token is None:
        monkeypatch.delenv("DIAG_TOKEN", raising=False)
    else:
        monkeypatch.setenv("DIAG_TOKEN", diag_token)
    url = reverse("reviewer_login_diagnostics")
    return client.get(url, {"t": token} if token is not None else {})


def describe_reviewer_login_diagnostics():
    def it_404s_when_the_env_var_is_unset(client, monkeypatch):
        assert _get(client, monkeypatch, diag_token=None).status_code == 404

    def it_404s_when_the_env_var_is_blank(client, monkeypatch):
        assert _get(client, monkeypatch, diag_token="   ").status_code == 404

    def it_404s_when_no_token_is_supplied(client, monkeypatch):
        assert _get(client, monkeypatch, token=None).status_code == 404

    def it_404s_when_the_token_does_not_match(client, monkeypatch):
        assert _get(client, monkeypatch, token="wrong-token").status_code == 404

    def it_returns_json_when_the_token_matches(client, monkeypatch):
        response = _get(client, monkeypatch)

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"

    def it_reports_the_wired_up_confirm_form(client, monkeypatch):
        payload = json.loads(_get(client, monkeypatch).content)

        assert payload["confirm_login_code_form"] == "plfog.adapters.GoldenTicketConfirmLoginCodeForm"

    def it_reports_that_the_golden_form_is_present(client, monkeypatch):
        payload = json.loads(_get(client, monkeypatch).content)

        assert payload["golden_form_present"] is True

    def it_reports_that_the_old_override_is_gone(client, monkeypatch):
        payload = json.loads(_get(client, monkeypatch).content)

        assert payload["adapter_overrides_generate_login_code"] is False

    def it_reports_the_build_identifiers(client, monkeypatch):
        monkeypatch.setenv("RENDER_GIT_COMMIT", "abc1234")
        payload = json.loads(_get(client, monkeypatch).content)

        assert payload["render_git_commit"] == "abc1234"
        assert payload["app_version"]
        assert payload["allauth_version"].startswith("65.")

    def it_reports_the_review_code_as_present_with_a_fingerprint(client, monkeypatch):
        monkeypatch.setenv("PLAY_REVIEW_CODE", "  supersecretvalue  ")
        payload = json.loads(_get(client, monkeypatch).content)

        assert payload["play_review_code_present"] is True
        assert payload["play_review_code_length"] == len("supersecretvalue")
        assert payload["play_review_code_fingerprint"] == hashlib.sha256(b"supersecretvalue").hexdigest()[:8]

    def it_never_returns_the_review_code_itself(client, monkeypatch):
        monkeypatch.setenv("PLAY_REVIEW_CODE", "supersecretvalue")
        body = _get(client, monkeypatch).content.decode()

        assert "supersecretvalue" not in body

    def it_reports_the_review_code_as_absent_when_unset(client, monkeypatch):
        monkeypatch.delenv("PLAY_REVIEW_CODE", raising=False)
        payload = json.loads(_get(client, monkeypatch).content)

        assert payload["play_review_code_present"] is False
        assert payload["play_review_code_length"] == 0
        assert payload["play_review_code_fingerprint"] == ""
