"""BDD-style tests for the BiometricCredential model and its manager.

This is the whole security surface of biometric login: the server trusts the secret and
nothing else, so the state machine in `redeem` is what stands between a member's account
and anyone holding a copy of their credential. The Keychain/Keystore side of the feature
cannot be exercised here at all.

Time is moved by writing timestamps onto the row rather than sleeping, so the grace-window
tests are exact and instant.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import (
    BIOMETRIC_ROTATION_GRACE_SECONDS,
    BIOMETRIC_TTL_DAYS,
    BiometricCredential,
    InvalidBiometricCredential,
    hash_biometric_secret,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def member_user(db):
    return User.objects.create_user(username="bio", email="bio@example.com")


@pytest.fixture()
def other_user(db):
    return User.objects.create_user(username="other", email="other@example.com")


def _issue(user, label="iPhone", platform=BiometricCredential.Platform.IOS):
    return BiometricCredential.objects.issue(user, device_label=label, platform=platform)


def _age_rotation(credential: BiometricCredential, seconds: float) -> None:
    """Backdate the rotation so `seconds` appear to have passed since it happened."""
    credential.rotated_at = timezone.now() - timedelta(seconds=seconds)
    credential.save(update_fields=["rotated_at"])


def describe_issue():
    def it_returns_a_raw_secret_that_is_not_stored_on_the_row(member_user):
        credential, secret = _issue(member_user)

        assert secret
        stored = BiometricCredential.objects.get(pk=credential.pk)
        row_values = [
            stored.secret_hash,
            stored.previous_secret_hash,
            stored.device_label,
            stored.platform,
        ]
        assert secret not in row_values
        assert stored.secret_hash == hash_biometric_secret(secret)

    def it_gives_every_device_a_different_secret(member_user):
        _first, first_secret = _issue(member_user, label="iPhone")
        _second, second_secret = _issue(member_user, label="iPad")

        assert first_secret != second_secret

    def it_starts_the_credential_with_no_previous_secret(member_user):
        credential, _secret = _issue(member_user)

        assert credential.previous_secret_hash == ""
        assert credential.rotated_at is None
        assert credential.last_used_at is None

    def it_expires_the_credential_ninety_days_out(member_user):
        credential, _secret = _issue(member_user)

        expected = timezone.now() + timedelta(days=BIOMETRIC_TTL_DAYS)
        assert abs((credential.expires_at - expected).total_seconds()) < 5

    def it_records_the_device_label_and_platform(member_user):
        credential, _secret = _issue(member_user, label="Pixel 9", platform=BiometricCredential.Platform.ANDROID)

        assert credential.device_label == "Pixel 9"
        assert credential.platform == BiometricCredential.Platform.ANDROID


def describe_redeem():
    def it_returns_the_owning_user(member_user):
        _credential, secret = _issue(member_user)

        user, _new_secret = BiometricCredential.objects.redeem(secret)

        assert user == member_user

    def it_returns_a_different_secret_than_the_one_redeemed(member_user):
        _credential, secret = _issue(member_user)

        _user, new_secret = BiometricCredential.objects.redeem(secret)

        assert new_secret != secret

    def it_stores_the_hash_of_the_new_secret(member_user):
        credential, secret = _issue(member_user)

        _user, new_secret = BiometricCredential.objects.redeem(secret)

        credential.refresh_from_db()
        assert credential.secret_hash == hash_biometric_secret(new_secret)

    def it_keeps_the_spent_hash_as_the_previous_one(member_user):
        credential, secret = _issue(member_user)
        original_hash = credential.secret_hash

        BiometricCredential.objects.redeem(secret)

        credential.refresh_from_db()
        assert credential.previous_secret_hash == original_hash
        assert credential.rotated_at is not None

    def it_pushes_the_expiry_out(member_user):
        credential, secret = _issue(member_user)
        credential.expires_at = timezone.now() + timedelta(days=2)
        credential.save(update_fields=["expires_at"])
        before = credential.expires_at

        BiometricCredential.objects.redeem(secret)

        credential.refresh_from_db()
        assert credential.expires_at > before

    def it_records_when_the_credential_was_last_used(member_user):
        credential, secret = _issue(member_user)

        BiometricCredential.objects.redeem(secret)

        credential.refresh_from_db()
        assert credential.last_used_at is not None

    def it_does_not_touch_another_members_credential(member_user, other_user):
        _mine, my_secret = _issue(member_user)
        theirs, _their_secret = _issue(other_user)
        their_hash = theirs.secret_hash

        BiometricCredential.objects.redeem(my_secret)

        theirs.refresh_from_db()
        assert theirs.secret_hash == their_hash
        assert theirs.last_used_at is None

    def describe_with_an_unknown_secret():
        def it_raises(member_user):
            _issue(member_user)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem("not-a-real-secret")

    def describe_with_an_empty_secret():
        def it_raises(member_user):
            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem("")

    def describe_with_an_expired_secret():
        def it_raises(member_user):
            credential, secret = _issue(member_user)
            credential.expires_at = timezone.now() - timedelta(seconds=1)
            credential.save(update_fields=["expires_at"])

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

    def describe_with_a_revoked_secret():
        def it_raises(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.revoke(credential)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

    def describe_after_a_rotation():
        def it_accepts_the_new_secret(member_user):
            _credential, secret = _issue(member_user)
            _user, new_secret = BiometricCredential.objects.redeem(secret)

            user, _newer_secret = BiometricCredential.objects.redeem(new_secret)

            assert user == member_user

        def it_refuses_the_spent_secret_once_the_grace_window_has_passed(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS + 1)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

    def describe_when_the_reply_to_a_rotation_was_lost():
        """Branch 2: the app retried with the only secret it has. Not an attack."""

        def it_accepts_the_previous_secret_inside_the_grace_window(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)

            user, retry_secret = BiometricCredential.objects.redeem(secret)

            assert user == member_user
            assert retry_secret != secret

        def it_keeps_the_credential_alive(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)

            BiometricCredential.objects.redeem(secret)

            credential.refresh_from_db()
            assert credential.revoked_at is None
            assert credential.is_active is True

        def it_hands_back_a_secret_that_works_on_the_next_unlock(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)
            _user, retry_secret = BiometricCredential.objects.redeem(secret)

            user, _next_secret = BiometricCredential.objects.redeem(retry_secret)

            assert user == member_user

        def it_refuses_a_previous_secret_on_a_revoked_credential(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)
            BiometricCredential.objects.revoke(credential)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

        def it_refuses_a_previous_secret_on_an_expired_credential(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)
            credential.expires_at = timezone.now() - timedelta(seconds=1)
            credential.save(update_fields=["expires_at"])

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

    def describe_when_a_spent_secret_is_replayed():
        """Branch 3: past the grace window, a spent secret coming back is the signature of theft."""

        def it_revokes_the_credential(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS + 1)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

            credential.refresh_from_db()
            assert credential.revoked_at is not None

        def it_kills_the_secret_the_real_device_is_holding(member_user):
            credential, secret = _issue(member_user)
            _user, live_secret = BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS + 1)
            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(live_secret)

        def it_logs_a_warning_naming_the_user_and_device_but_never_the_secret(member_user, caplog):
            credential, secret = _issue(member_user, label="Stolen Phone")
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS + 1)

            with caplog.at_level(logging.WARNING, logger="core.models"):
                with pytest.raises(InvalidBiometricCredential):
                    BiometricCredential.objects.redeem(secret)

            assert "Stolen Phone" in caplog.text
            assert str(member_user.pk) in caplog.text
            assert secret not in caplog.text


def describe_active_for():
    def it_lists_only_this_members_credentials(member_user, other_user):
        mine, _secret = _issue(member_user)
        _issue(other_user)

        assert list(BiometricCredential.objects.active_for(member_user)) == [mine]

    def it_omits_revoked_credentials(member_user):
        credential, _secret = _issue(member_user)
        BiometricCredential.objects.revoke(credential)

        assert list(BiometricCredential.objects.active_for(member_user)) == []

    def it_omits_expired_credentials(member_user):
        credential, _secret = _issue(member_user)
        credential.expires_at = timezone.now() - timedelta(seconds=1)
        credential.save(update_fields=["expires_at"])

        assert list(BiometricCredential.objects.active_for(member_user)) == []

    def it_lists_the_newest_first(member_user):
        older, _first = _issue(member_user, label="Old Phone")
        newer, _second = _issue(member_user, label="New Phone")
        older.created_at = timezone.now() - timedelta(days=3)
        older.save(update_fields=["created_at"])

        assert list(BiometricCredential.objects.active_for(member_user)) == [newer, older]


def describe_revoke():
    def it_stops_the_secret_working(member_user):
        credential, secret = _issue(member_user)

        BiometricCredential.objects.revoke(credential)

        with pytest.raises(InvalidBiometricCredential):
            BiometricCredential.objects.redeem(secret)

    def it_keeps_the_original_timestamp_when_called_twice(member_user):
        credential, _secret = _issue(member_user)
        BiometricCredential.objects.revoke(credential)
        first_revoked_at = credential.revoked_at

        BiometricCredential.objects.revoke(credential)

        credential.refresh_from_db()
        assert credential.revoked_at == first_revoked_at


def describe_revoke_all():
    def it_revokes_every_credential_the_member_has(member_user):
        _first, first_secret = _issue(member_user, label="iPhone")
        _second, second_secret = _issue(member_user, label="iPad")

        BiometricCredential.objects.revoke_all(member_user)

        for secret in (first_secret, second_secret):
            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

    def it_leaves_other_members_credentials_alone(member_user, other_user):
        _issue(member_user)
        _theirs, their_secret = _issue(other_user)

        BiometricCredential.objects.revoke_all(member_user)

        user, _new_secret = BiometricCredential.objects.redeem(their_secret)
        assert user == other_user


def describe_is_active():
    def it_is_true_for_a_fresh_credential(member_user):
        credential, _secret = _issue(member_user)

        assert credential.is_active is True

    def it_is_false_once_revoked(member_user):
        credential, _secret = _issue(member_user)
        BiometricCredential.objects.revoke(credential)

        assert credential.is_active is False

    def it_is_false_once_expired(member_user):
        credential, _secret = _issue(member_user)
        credential.expires_at = timezone.now() - timedelta(seconds=1)

        assert credential.is_active is False


def describe_str():
    def it_names_the_device_platform_and_state(member_user):
        credential, _secret = _issue(member_user, label="iPhone 15", platform=BiometricCredential.Platform.IOS)

        assert str(credential) == "iPhone 15 - iOS - active"

    def it_reports_an_inactive_credential(member_user):
        credential, _secret = _issue(member_user, label="iPhone 15", platform=BiometricCredential.Platform.IOS)
        BiometricCredential.objects.revoke(credential)

        assert str(credential) == "iPhone 15 - iOS - inactive"
