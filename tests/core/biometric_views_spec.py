"""BDD-style tests for the biometric login endpoints.

These prove the server half of the feature: who gets a secret, what a secret buys, and what
happens when one is aimed somewhere it should not go. The Keychain/Keystore and the
biometric prompt itself do not exist here and are never exercised.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core import views as core_views
from core.models import BIOMETRIC_ROTATION_GRACE_SECONDS, BiometricCredential

pytestmark = pytest.mark.django_db

ENROLL_URL = "/accounts/biometric/enroll/"
UNLOCK_URL = "/accounts/biometric/unlock/"
DISABLE_URL = "/accounts/biometric/disable/"


@pytest.fixture(autouse=True)
def _clear_rate_counters():
    """The unlock limiter counts in the cache, which outlives a single test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def member_user(db):
    return User.objects.create_user(username="bio", email="bio@example.com")


@pytest.fixture()
def other_user(db):
    return User.objects.create_user(username="other", email="other@example.com")


def _post(client, url, payload, *, remote_addr=None, xff=None):
    extra = {}
    if remote_addr is not None:
        extra["REMOTE_ADDR"] = remote_addr
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return client.post(url, data=json.dumps(payload), content_type="application/json", **extra)


def _authenticated_user_id(client):
    """The user id on the client's session, or None when there is no session."""
    return client.session.get("_auth_user_id")


def describe_the_login_page():
    """The unlock button's mount and the script that reveals it. Both are inert in a
    browser — nothing here proves the Keychain half, which does not exist in this run."""

    def it_renders_the_unlock_mount(client):
        html = client.get(reverse("account_login")).content.decode()

        assert 'id="biometric-unlock"' in html

    def it_loads_the_biometric_script(client):
        html = client.get(reverse("account_login")).content.decode()

        assert "biometric-auth.js" in html

    def it_marks_the_page_as_signed_out_for_the_script(client):
        html = client.get(reverse("account_login")).content.decode()

        assert 'data-pl-authenticated="0"' in html


def describe_biometric_enroll():
    def it_is_routed_at_the_documented_path():
        assert reverse("biometric_enroll") == ENROLL_URL

    def it_redirects_an_anonymous_caller_to_log_in(client):
        response = _post(client, ENROLL_URL, {"device_label": "iPhone", "platform": "ios"})

        assert response.status_code == 302
        assert BiometricCredential.objects.count() == 0

    def it_returns_a_secret_to_a_logged_in_member(client, member_user):
        client.force_login(member_user)

        response = _post(client, ENROLL_URL, {"device_label": "iPhone", "platform": "ios"})

        assert response.status_code == 200
        assert response.json()["secret"]

    def it_stores_only_the_hash_of_the_secret_it_returned(client, member_user):
        client.force_login(member_user)

        secret = _post(client, ENROLL_URL, {"device_label": "iPhone", "platform": "ios"}).json()["secret"]

        credential = BiometricCredential.objects.get(user=member_user)
        assert credential.secret_hash != secret
        assert BiometricCredential.objects.filter(secret_hash=secret).count() == 0

    def it_records_the_device_label_and_platform(client, member_user):
        client.force_login(member_user)

        _post(client, ENROLL_URL, {"device_label": "Pixel 9", "platform": "android"})

        credential = BiometricCredential.objects.get(user=member_user)
        assert credential.device_label == "Pixel 9"
        assert credential.platform == BiometricCredential.Platform.ANDROID

    def it_returns_the_credential_id_so_logout_can_revoke_this_device(client, member_user):
        client.force_login(member_user)

        body = _post(client, ENROLL_URL, {"device_label": "iPhone", "platform": "ios"}).json()

        assert body["credential_id"] == BiometricCredential.objects.get(user=member_user).pk

    def it_rejects_an_unknown_platform(client, member_user):
        client.force_login(member_user)

        response = _post(client, ENROLL_URL, {"device_label": "iPhone", "platform": "windows-phone"})

        assert response.status_code == 400
        assert BiometricCredential.objects.count() == 0

    def it_rejects_a_missing_device_label(client, member_user):
        client.force_login(member_user)

        response = _post(client, ENROLL_URL, {"platform": "ios"})

        assert response.status_code == 400
        assert BiometricCredential.objects.count() == 0

    def it_rejects_a_blank_device_label(client, member_user):
        client.force_login(member_user)

        response = _post(client, ENROLL_URL, {"device_label": "   ", "platform": "ios"})

        assert response.status_code == 400

    def it_trims_an_over_long_device_label_to_the_column_width(client, member_user):
        client.force_login(member_user)

        _post(client, ENROLL_URL, {"device_label": "x" * 400, "platform": "ios"})

        assert len(BiometricCredential.objects.get(user=member_user).device_label) == 120

    def it_rejects_a_malformed_body(client, member_user):
        client.force_login(member_user)

        response = client.post(ENROLL_URL, data="not json", content_type="application/json")

        assert response.status_code == 400

    def it_refuses_a_GET(client, member_user):
        client.force_login(member_user)

        assert client.get(ENROLL_URL).status_code == 405

    def it_still_requires_a_csrf_token(member_user):
        """Only unlock is exempt. Enroll rides on a session cookie, so it keeps CSRF."""
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(member_user)

        response = _post(csrf_client, ENROLL_URL, {"device_label": "iPhone", "platform": "ios"})

        assert response.status_code == 403


def describe_biometric_unlock():
    def it_is_routed_at_the_documented_path():
        assert reverse("biometric_unlock") == UNLOCK_URL

    def it_signs_the_owning_member_in(client, member_user):
        _credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )

        response = _post(client, UNLOCK_URL, {"secret": secret})

        assert response.status_code == 200
        assert response.wsgi_request.user == member_user
        assert _authenticated_user_id(client) == str(member_user.pk)

    def it_returns_a_new_secret(client, member_user):
        _credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )

        body = _post(client, UNLOCK_URL, {"secret": secret}).json()

        assert body["ok"] is True
        assert body["secret"] != secret

    def it_returns_a_secret_that_works_on_the_next_unlock(client, member_user):
        _credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        new_secret = _post(client, UNLOCK_URL, {"secret": secret}).json()["secret"]

        response = _post(Client(), UNLOCK_URL, {"secret": new_secret})

        assert response.status_code == 200

    def it_signs_in_the_secrets_owner_and_nobody_else_because_the_request_names_no_user(
        client, member_user, other_user
    ):
        """A crafted unlock cannot be aimed at an arbitrary account.

        The body carries a secret and nothing else — no user id, no email — so there is no
        field an attacker could point at a different member. Holding another member's secret
        makes you that member, which is exactly what a stolen bearer token means, and is why
        the secret rotates on every use and a replay revokes the credential.
        """
        _credential, their_secret = BiometricCredential.objects.issue(
            other_user, device_label="Their Phone", platform=BiometricCredential.Platform.IOS
        )

        response = _post(client, UNLOCK_URL, {"secret": their_secret, "user_id": member_user.pk, "email": "bio@x"})

        assert response.status_code == 200
        assert _authenticated_user_id(client) == str(other_user.pk)

    def it_does_not_need_a_csrf_token(member_user):
        """Deliberate: the caller has no session yet, so it may have no CSRF cookie."""
        _credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        csrf_client = Client(enforce_csrf_checks=True)

        response = _post(csrf_client, UNLOCK_URL, {"secret": secret})

        assert response.status_code == 200

    def it_refuses_an_unknown_secret(client, member_user):
        response = _post(client, UNLOCK_URL, {"secret": "nope"})

        assert response.status_code == 401
        assert _authenticated_user_id(client) is None

    def it_refuses_a_missing_secret(client):
        response = _post(client, UNLOCK_URL, {})

        assert response.status_code == 400
        assert _authenticated_user_id(client) is None

    def it_refuses_a_malformed_body(client):
        response = client.post(UNLOCK_URL, data="not json", content_type="application/json")

        assert response.status_code == 400

    def it_refuses_an_expired_secret(client, member_user):
        credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        credential.expires_at = timezone.now() - timedelta(seconds=1)
        credential.save(update_fields=["expires_at"])

        response = _post(client, UNLOCK_URL, {"secret": secret})

        assert response.status_code == 401
        assert _authenticated_user_id(client) is None

    def it_refuses_a_revoked_secret(client, member_user):
        credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        BiometricCredential.objects.revoke(credential)

        response = _post(client, UNLOCK_URL, {"secret": secret})

        assert response.status_code == 401
        assert _authenticated_user_id(client) is None

    def it_refuses_a_deactivated_member_and_kills_their_credentials(client, member_user):
        """A deleted account keeps a deactivated User row. login() would not catch that."""
        _credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        member_user.is_active = False
        member_user.save(update_fields=["is_active"])

        response = _post(client, UNLOCK_URL, {"secret": secret})

        assert response.status_code == 401
        assert _authenticated_user_id(client) is None
        assert BiometricCredential.objects.active_for(member_user).count() == 0

    def it_refuses_a_GET(client):
        assert client.get(UNLOCK_URL).status_code == 405

    def describe_the_failure_message():
        """Every rejection must read identically. The moment one flavor of failure is
        distinguishable from another, the endpoint answers "does this secret exist?" and
        "did it used to exist?" for anyone willing to ask, which is the probing oracle the
        single shared message exists to deny."""

        def _body_for_unknown(client):
            return _post(client, UNLOCK_URL, {"secret": "never-issued"}).json()

        def _body_for_expired(client, user):
            credential, secret = BiometricCredential.objects.issue(
                user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
            )
            credential.expires_at = timezone.now() - timedelta(seconds=1)
            credential.save(update_fields=["expires_at"])
            return _post(client, UNLOCK_URL, {"secret": secret}).json()

        def _body_for_revoked(client, user):
            credential, secret = BiometricCredential.objects.issue(
                user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
            )
            BiometricCredential.objects.revoke(credential)
            return _post(client, UNLOCK_URL, {"secret": secret}).json()

        def _body_for_replayed(client, user):
            credential, secret = BiometricCredential.objects.issue(
                user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
            )
            BiometricCredential.objects.redeem(secret)
            credential.rotated_at = timezone.now() - timedelta(seconds=BIOMETRIC_ROTATION_GRACE_SECONDS + 1)
            credential.save(update_fields=["rotated_at"])
            return _post(client, UNLOCK_URL, {"secret": secret}).json()

        def _body_for_inactive(client, user):
            _credential, secret = BiometricCredential.objects.issue(
                user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
            )
            user.is_active = False
            user.save(update_fields=["is_active"])
            return _post(client, UNLOCK_URL, {"secret": secret}).json()

        def it_is_word_for_word_identical_however_the_secret_failed(client, member_user, other_user):
            bodies = [
                _body_for_unknown(client),
                _body_for_expired(client, member_user),
                _body_for_revoked(client, member_user),
                _body_for_replayed(client, member_user),
                _body_for_inactive(client, other_user),
            ]

            assert all(body == bodies[0] for body in bodies), bodies

        def it_never_says_which_kind_of_failure_it_was(client, member_user):
            body = _body_for_replayed(client, member_user)

            leaky = ["revoke", "expire", "replay", "unknown", "already", "inactive", "disabled"]
            assert not any(word in body["error"].lower() for word in leaky), body

    def describe_rate_limiting():
        """The caps are exercised through patched constants so the boundary is exact and the
        test does not have to make a few hundred requests. The production numbers get their
        own assertion below."""

        def it_allows_attempts_right_up_to_the_cap(client):
            with patch.object(core_views, "_BIOMETRIC_UNLOCK_HOURLY_LIMIT", 3):
                statuses = [_post(client, UNLOCK_URL, {"secret": "wrong"}).status_code for _ in range(3)]

            assert statuses == [401, 401, 401]

        def it_returns_429_on_the_attempt_after_the_cap(client):
            with patch.object(core_views, "_BIOMETRIC_UNLOCK_HOURLY_LIMIT", 3):
                for _ in range(3):
                    _post(client, UNLOCK_URL, {"secret": "wrong"})

                response = _post(client, UNLOCK_URL, {"secret": "wrong"})

            assert response.status_code == 429

        def it_refuses_a_valid_secret_too_once_the_cap_is_passed(client, member_user):
            _credential, secret = BiometricCredential.objects.issue(
                member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
            )
            with patch.object(core_views, "_BIOMETRIC_UNLOCK_HOURLY_LIMIT", 2):
                for _ in range(2):
                    _post(client, UNLOCK_URL, {"secret": "wrong"})

                response = _post(client, UNLOCK_URL, {"secret": secret})

            assert response.status_code == 429
            assert _authenticated_user_id(client) is None

        def it_leaves_the_credential_usable_once_the_counters_clear(client, member_user):
            _credential, secret = BiometricCredential.objects.issue(
                member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
            )
            with patch.object(core_views, "_BIOMETRIC_UNLOCK_HOURLY_LIMIT", 2):
                for _ in range(3):
                    _post(client, UNLOCK_URL, {"secret": "wrong"})
                cache.clear()

                response = _post(client, UNLOCK_URL, {"secret": secret})

            assert response.status_code == 200

        def it_gives_separate_budgets_to_separate_addresses(client):
            """Without this, keying the limiter on a constant survives every other test here
            — and that mutant lets one attacker lock every member out of biometric sign in."""
            with patch.object(core_views, "_BIOMETRIC_UNLOCK_HOURLY_LIMIT", 1):
                _post(client, UNLOCK_URL, {"secret": "wrong"}, remote_addr="10.0.0.1")
                exhausted = _post(client, UNLOCK_URL, {"secret": "wrong"}, remote_addr="10.0.0.1")
                neighbour = _post(client, UNLOCK_URL, {"secret": "wrong"}, remote_addr="10.0.0.2")

            assert exhausted.status_code == 429
            assert neighbour.status_code == 401

        def it_keys_on_the_caps_this_project_actually_ships(client):
            """Finding 5: everyone on the shop wifi leaves through one address, and an
            enrolled phone spends a slot on every signed-out app open. Caps tuned to one
            person silently disable biometric sign in for the whole building."""
            assert core_views._BIOMETRIC_UNLOCK_HOURLY_LIMIT >= 200
            assert core_views._BIOMETRIC_UNLOCK_DAILY_LIMIT >= 1000

    def describe_the_rate_limit_key():
        """The key must come from an address the caller cannot choose. If it does not, the
        cap simply does not exist for the one caller it is meant to bound."""

        def it_ignores_a_client_supplied_forwarded_for_entry(client):
            """Proxies APPEND, so the LEFTMOST entry is whatever the client sent. Rotating
            it must not buy a fresh budget."""
            with patch.object(core_views, "_BIOMETRIC_UNLOCK_HOURLY_LIMIT", 1):
                _post(client, UNLOCK_URL, {"secret": "wrong"}, xff="1.1.1.1, 203.0.113.9")
                response = _post(client, UNLOCK_URL, {"secret": "wrong"}, xff="2.2.2.2, 203.0.113.9")

            assert response.status_code == 429

        def it_separates_budgets_by_the_entry_our_own_proxy_appended(client):
            with patch.object(core_views, "_BIOMETRIC_UNLOCK_HOURLY_LIMIT", 1):
                _post(client, UNLOCK_URL, {"secret": "wrong"}, xff="1.1.1.1, 203.0.113.9")
                exhausted = _post(client, UNLOCK_URL, {"secret": "wrong"}, xff="1.1.1.1, 203.0.113.9")
                other_edge = _post(client, UNLOCK_URL, {"secret": "wrong"}, xff="1.1.1.1, 198.51.100.4")

            assert exhausted.status_code == 429
            assert other_edge.status_code == 401

        def it_falls_back_to_the_connecting_address_with_no_proxy_header(client):
            assert core_views._rate_limit_key(RequestFactory().post("/", REMOTE_ADDR="192.0.2.7")) == "192.0.2.7"

        def it_takes_the_rightmost_forwarded_for_entry(client):
            request = RequestFactory().post("/", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2, 203.0.113.9")

            assert core_views._rate_limit_key(request) == "203.0.113.9"

        def it_bounds_an_absurdly_long_header_before_it_becomes_a_cache_key(client):
            request = RequestFactory().post("/", HTTP_X_FORWARDED_FOR="x" * 5000)

            assert len(core_views._rate_limit_key(request)) <= 64

    def describe_the_login_csrf_guard():
        """csrf_exempt gives up login-CSRF protection as well as forgery protection. An
        attacker posting THEIR OWN secret from a page the victim visits would sign the
        victim's browser into the attacker's account. A plain HTML form cannot send
        application/json, so requiring it is what closes that."""

        def it_refuses_a_form_encoded_post(client, member_user):
            _credential, secret = BiometricCredential.objects.issue(
                member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
            )

            response = client.post(UNLOCK_URL, data={"secret": secret})

            assert response.status_code == 415
            assert _authenticated_user_id(client) is None

        def it_refuses_the_text_plain_body_a_cross_site_form_can_send(client, member_user):
            """enctype="text/plain" is the one that needs no script at all."""
            _credential, secret = BiometricCredential.objects.issue(
                member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
            )

            response = client.post(UNLOCK_URL, data=json.dumps({"secret": secret}), content_type="text/plain")

            assert response.status_code == 415
            assert _authenticated_user_id(client) is None

        def it_does_not_spend_the_victims_rate_budget(client, member_user):
            """Checked before the limiter, so a cross-site form cannot burn the budget of
            whoever the victim shares an address with."""
            with patch.object(core_views, "_BIOMETRIC_UNLOCK_HOURLY_LIMIT", 1):
                client.post(UNLOCK_URL, data={"secret": "wrong"})

                response = _post(client, UNLOCK_URL, {"secret": "still-wrong"})

            assert response.status_code == 401


def describe_biometric_disable():
    def it_is_routed_at_the_documented_path():
        assert reverse("biometric_disable") == DISABLE_URL

    def it_redirects_an_anonymous_caller_to_log_in(client, member_user):
        _credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )

        response = _post(client, DISABLE_URL, {"secret": secret})

        assert response.status_code == 302
        assert BiometricCredential.objects.active_for(member_user).count() == 1

    def it_revokes_exactly_the_credential_whose_secret_was_sent(client, member_user):
        phone, phone_secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        tablet, _tablet_secret = BiometricCredential.objects.issue(
            member_user, device_label="iPad", platform=BiometricCredential.Platform.IOS
        )
        client.force_login(member_user)

        response = _post(client, DISABLE_URL, {"secret": phone_secret})

        assert response.status_code == 200
        phone.refresh_from_db()
        tablet.refresh_from_db()
        assert phone.revoked_at is not None
        assert tablet.revoked_at is None

    def it_revokes_every_credential_when_the_body_is_empty(client, member_user):
        BiometricCredential.objects.issue(member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS)
        BiometricCredential.objects.issue(member_user, device_label="iPad", platform=BiometricCredential.Platform.IOS)
        client.force_login(member_user)

        response = client.post(DISABLE_URL, data="", content_type="application/json")

        assert response.status_code == 200
        assert BiometricCredential.objects.active_for(member_user).count() == 0

    def it_accepts_the_superseded_secret_so_a_logout_still_lands(client, member_user):
        credential, secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        BiometricCredential.objects.redeem(secret)
        client.force_login(member_user)

        _post(client, DISABLE_URL, {"secret": secret})

        credential.refresh_from_db()
        assert credential.revoked_at is not None

    def it_revokes_exactly_the_credential_named_by_credential_id(client, member_user):
        """App logout uses the id: reading the secret back would raise a Face ID prompt."""
        phone, _phone_secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        tablet, _tablet_secret = BiometricCredential.objects.issue(
            member_user, device_label="iPad", platform=BiometricCredential.Platform.IOS
        )
        client.force_login(member_user)

        response = _post(client, DISABLE_URL, {"credential_id": phone.pk})

        assert response.status_code == 200
        phone.refresh_from_db()
        tablet.refresh_from_db()
        assert phone.revoked_at is not None
        assert tablet.revoked_at is None

    def it_cannot_revoke_another_members_credential_by_id(client, member_user, other_user):
        theirs, _their_secret = BiometricCredential.objects.issue(
            other_user, device_label="Their Phone", platform=BiometricCredential.Platform.IOS
        )
        client.force_login(member_user)

        response = _post(client, DISABLE_URL, {"credential_id": theirs.pk})

        assert response.status_code == 200
        theirs.refresh_from_db()
        assert theirs.revoked_at is None

    def it_succeeds_silently_on_an_unknown_credential_id(client, member_user):
        credential, _secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        client.force_login(member_user)

        response = _post(client, DISABLE_URL, {"credential_id": credential.pk + 5000})

        assert response.status_code == 200
        assert BiometricCredential.objects.active_for(member_user).count() == 1

    def it_treats_a_zero_credential_id_as_a_targeted_revoke_not_revoke_everything(client, member_user):
        """`credential_id is not None`, not a truthiness test. Under `if credential_id:` a
        zero id falls through to the empty-body branch and silently revokes every device the
        member owns — one phone logging out taking the rest of their phones with it."""
        phone, _phone_secret = BiometricCredential.objects.issue(
            member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )
        client.force_login(member_user)

        response = _post(client, DISABLE_URL, {"credential_id": 0})

        assert response.status_code == 200
        phone.refresh_from_db()
        assert phone.revoked_at is None

    def it_rejects_a_credential_id_that_is_not_a_number(client, member_user):
        client.force_login(member_user)

        response = _post(client, DISABLE_URL, {"credential_id": "not-a-number"})

        assert response.status_code == 400

    def it_succeeds_silently_on_an_unknown_secret(client, member_user):
        BiometricCredential.objects.issue(member_user, device_label="iPhone", platform=BiometricCredential.Platform.IOS)
        client.force_login(member_user)

        response = _post(client, DISABLE_URL, {"secret": "never-issued"})

        assert response.status_code == 200
        assert BiometricCredential.objects.active_for(member_user).count() == 1

    def it_cannot_revoke_another_members_credential(client, member_user, other_user):
        theirs, their_secret = BiometricCredential.objects.issue(
            other_user, device_label="Their Phone", platform=BiometricCredential.Platform.IOS
        )
        client.force_login(member_user)

        response = _post(client, DISABLE_URL, {"secret": their_secret})

        assert response.status_code == 200
        theirs.refresh_from_db()
        assert theirs.revoked_at is None

    def it_rejects_a_malformed_body(client, member_user):
        client.force_login(member_user)

        response = client.post(DISABLE_URL, data="not json", content_type="application/json")

        assert response.status_code == 400

    def it_refuses_a_GET(client, member_user):
        client.force_login(member_user)

        assert client.get(DISABLE_URL).status_code == 405
