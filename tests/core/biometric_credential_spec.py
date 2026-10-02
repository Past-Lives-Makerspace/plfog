"""BDD-style tests for the BiometricCredential model and its manager.

This is the whole security surface of biometric login: the server trusts the secret and
nothing else, so the state machine in `redeem` is what stands between a member's account
and anyone holding a copy of their credential. The Keychain/Keystore side of the feature
cannot be exercised here at all.

Time is moved by writing timestamps onto the row rather than sleeping, so the grace-window
tests are exact and instant.
"""

from __future__ import annotations

import inspect
import logging
from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from sentry_sdk.scrubber import DEFAULT_DENYLIST

from core import views as core_views
from core.models import (
    BIOMETRIC_ROTATION_GRACE_SECONDS,
    BIOMETRIC_SECRET_BYTES,
    BIOMETRIC_TOKEN_SEPARATOR,
    BIOMETRIC_TTL_DAYS,
    BiometricCredential,
    BiometricCredentialManager,
    InvalidBiometricCredential,
    _split_biometric_secret,
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


def _verifier_of(secret: str) -> str:
    """The verifier half of a device token."""
    return secret.split(BIOMETRIC_TOKEN_SEPARATOR, 1)[1]


def _token_for(selector: str, secret: str) -> str:
    """Build a device token by hand, so a test can aim one half at a mismatched other."""
    return f"{selector}{BIOMETRIC_TOKEN_SEPARATOR}{secret}"


def describe_the_secret_itself():
    """The size of both halves and the hash over the verifier are the numbers the whole
    feature rests on, and none of them is visible in any behavioral test — every other spec
    passes just as happily with an 8-character token or an MD5 digest."""

    def it_mints_secrets_of_the_documented_size():
        assert BIOMETRIC_SECRET_BYTES == 48

    def it_issues_a_token_of_two_full_entropy_halves(member_user):
        _credential, secret = _issue(member_user)

        selector, separator, verifier = secret.partition(BIOMETRIC_TOKEN_SEPARATOR)

        # 48 bytes of urlsafe base64, unpadded, EACH. Shrinking BIOMETRIC_SECRET_BYTES makes
        # a guessable bearer token, and nothing else in the suite would notice.
        assert separator == BIOMETRIC_TOKEN_SEPARATOR
        assert len(selector) == 64
        assert len(verifier) == 64

    def it_gives_the_selector_the_same_entropy_as_the_verifier(member_user):
        """A mismatched verifier against a KNOWN selector revokes, so a short selector is a
        denial of service: enumerate the values, send one wrong verifier each, and every
        member's biometric sign in dies. The behavioral half of this is
        `describe_when_the_selector_is_guessed_at`, below."""
        credential, _secret = _issue(member_user)

        assert len(credential.selector) == 64

    def it_never_puts_the_separator_inside_either_half(member_user):
        """token_urlsafe emits only A-Za-z0-9-_, which is what makes the dot unambiguous."""
        _credential, secret = _issue(member_user)

        assert secret.count(BIOMETRIC_TOKEN_SEPARATOR) == 1

    def it_hashes_with_sha256_and_not_some_other_digest():
        # A published SHA-256 vector. Asserting the function against itself would accept
        # MD5, SHA-1, or any other digest swapped in underneath it.
        assert hash_biometric_secret("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    def it_produces_a_64_character_hex_digest():
        digest = hash_biometric_secret("anything")

        assert len(digest) == 64
        assert digest == digest.lower()
        assert all(character in "0123456789abcdef" for character in digest)

    def it_gives_different_secrets_different_hashes():
        assert hash_biometric_secret("one") != hash_biometric_secret("two")


def describe_the_secret_variable_naming():
    """Sentry runs with send_default_pii=True, so a 500 anywhere in the biometric call
    stack ships that frame's locals. The scrubber matches denylist entries against the
    WHOLE key, so a local named `secret` is redacted and one named `raw_secret` is
    transmitted in full. The protection is a naming convention with nothing enforcing it,
    which is exactly the kind of thing a readability refactor undoes by accident."""

    # Names that hold a HASH rather than a live verifier, and so are safe to transmit.
    _HASH_HOLDERS = {"verifier_hash", "previous_verifier_hash"}
    # A local whose name contains either word is presumed to hold token material.
    _SENSITIVE_WORDS = ("secret", "verifier")

    def it_keeps_secret_in_sentrys_default_denylist():
        # If a Sentry upgrade ever drops this entry, every name below stops being scrubbed
        # and this test is the only thing that would say so.
        assert "secret" in DEFAULT_DENYLIST

    def it_scrubs_by_whole_key_so_a_prefixed_name_would_not_match():
        assert "raw_secret" not in DEFAULT_DENYLIST
        assert "new_secret" not in DEFAULT_DENYLIST

    def it_adds_selector_to_the_denylist_because_sentry_does_not_carry_it():
        """The selector is not secret material — it identifies, it does not authenticate —
        so the `secret` naming rule does not reach it. It still must not travel: a wrong
        verifier against a KNOWN selector revokes that credential, so a selector in a Sentry
        event lets whoever reads it push one member's phone back to emailed codes."""
        assert "selector" not in DEFAULT_DENYLIST
        assert "selector" in settings.SENTRY_SCRUB_DENYLIST

    def it_keeps_everything_sentry_already_scrubbed():
        """The custom denylist must be additive. Replacing it would silently stop scrubbing
        passwords, cookies, and auth headers across the whole app, not just here."""
        assert set(DEFAULT_DENYLIST) <= set(settings.SENTRY_SCRUB_DENYLIST)

    @pytest.mark.parametrize(
        "func",
        [
            hash_biometric_secret,
            _split_biometric_secret,
            BiometricCredentialManager.issue,
            BiometricCredentialManager.redeem,
            BiometricCredential.rotate,
            core_views.biometric_enroll,
            core_views.biometric_unlock,
            core_views.biometric_disable,
        ],
    )
    def it_names_every_secret_bearing_local_something_sentry_scrubs(func):
        unwrapped = inspect.unwrap(func)
        offenders = [
            name
            for name in unwrapped.__code__.co_varnames
            if any(word in name for word in _SENSITIVE_WORDS) and name != "secret" and name not in _HASH_HOLDERS
        ]

        assert offenders == [], (
            f"{unwrapped.__qualname__} has local(s) {offenders} holding token material under a "
            "name Sentry's denylist does not match. Call it exactly `secret`."
        )


def describe_issue():
    def it_returns_a_verifier_that_is_not_stored_on_the_row(member_user):
        credential, secret = _issue(member_user)

        assert secret
        stored = BiometricCredential.objects.get(pk=credential.pk)
        row_values = [
            stored.selector,
            stored.verifier_hash,
            stored.previous_verifier_hash,
            stored.device_label,
            stored.platform,
        ]
        assert _verifier_of(secret) not in row_values
        assert secret not in row_values
        assert stored.verifier_hash == hash_biometric_secret(_verifier_of(secret))

    def it_stores_the_selector_in_the_clear_because_it_identifies_rather_than_authenticates(member_user):
        credential, secret = _issue(member_user)

        assert secret.startswith(f"{credential.selector}{BIOMETRIC_TOKEN_SEPARATOR}")

    def it_gives_every_device_a_different_secret(member_user):
        _first, first_secret = _issue(member_user, label="iPhone")
        _second, second_secret = _issue(member_user, label="iPad")

        assert first_secret != second_secret

    def it_gives_every_device_a_different_selector(member_user):
        first, _first_secret = _issue(member_user, label="iPhone")
        second, _second_secret = _issue(member_user, label="iPad")

        assert first.selector != second.selector

    def it_starts_the_credential_with_no_previous_verifier(member_user):
        credential, _secret = _issue(member_user)

        assert credential.previous_verifier_hash == ""
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

    def it_stores_the_hash_of_the_new_verifier(member_user):
        credential, secret = _issue(member_user)

        _user, new_secret = BiometricCredential.objects.redeem(secret)

        credential.refresh_from_db()
        assert credential.verifier_hash == hash_biometric_secret(_verifier_of(new_secret))

    def it_keeps_the_selector_unchanged_so_the_credential_stays_identifiable(member_user):
        """The point of the whole design. If the selector rotated with the verifier, the
        lookup key would be back on the rotating half and a stale copy would once again
        read as an unknown stranger instead of a replay."""
        credential, secret = _issue(member_user)
        original_selector = credential.selector

        _user, new_secret = BiometricCredential.objects.redeem(secret)

        credential.refresh_from_db()
        assert credential.selector == original_selector
        assert new_secret.startswith(f"{original_selector}{BIOMETRIC_TOKEN_SEPARATOR}")

    def it_keeps_the_spent_hash_as_the_previous_one(member_user):
        credential, secret = _issue(member_user)
        original_hash = credential.verifier_hash

        BiometricCredential.objects.redeem(secret)

        credential.refresh_from_db()
        assert credential.previous_verifier_hash == original_hash
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
        their_hash = theirs.verifier_hash

        BiometricCredential.objects.redeem(my_secret)

        theirs.refresh_from_db()
        assert theirs.verifier_hash == their_hash
        assert theirs.last_used_at is None

    def describe_with_an_unknown_selector():
        def it_raises(member_user):
            _issue(member_user)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(_token_for("no-such-selector", "not-a-real-secret"))

        def it_revokes_nothing(member_user):
            """Branch 1 must stay inert. An unknown selector that revoked anything, or that
            answered differently from a known one, would turn this endpoint into a way to
            ask which credentials exist."""
            credential, _secret = _issue(member_user)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(_token_for("no-such-selector", "not-a-real-secret"))

            credential.refresh_from_db()
            assert credential.revoked_at is None
            assert credential.is_active is True

    def describe_with_a_token_that_has_no_separator():
        """What a device enrolled before selectors existed sends. The migration revoked
        those rows, so there is nothing for it to match; it must not be a 500."""

        def it_raises(member_user):
            _credential, secret = _issue(member_user)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(_verifier_of(secret))

        def it_revokes_nothing(member_user):
            credential, secret = _issue(member_user)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(_verifier_of(secret))

            credential.refresh_from_db()
            assert credential.revoked_at is None

    def describe_with_a_token_missing_one_half():
        @pytest.mark.parametrize("secret", ["", ".", "selector-only.", ".verifier-only"])
        def it_raises(member_user, secret):
            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

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

        def it_accepts_the_previous_verifier_inside_the_grace_window(member_user):
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

        def it_refuses_a_previous_verifier_on_a_revoked_credential(member_user):
            credential, secret = _issue(member_user)
            BiometricCredential.objects.redeem(secret)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)
            BiometricCredential.objects.revoke(credential)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

        def it_refuses_a_previous_verifier_on_an_expired_credential(member_user):
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

            # The WHOLE rendered line, not "both values appear somewhere": asserting
            # membership would pass just as well with the user and the device swapped into
            # each other's slots, which is a log that names the wrong thing in an incident.
            assert caplog.messages == [
                f"Biometric credential replay: verifier did not match for user pk={member_user.pk}, "
                f"device 'Stolen Phone'. Credential revoked."
            ]
            assert secret not in caplog.text
            assert credential.selector not in caplog.text

    def describe_when_the_selector_is_guessed_at():
        """THE trap in this design, and the reason the selector is 48 bytes of entropy
        rather than the primary key.

        Because a wrong verifier against a known credential REVOKES it, an attacker who can
        guess selectors does not need a single valid token to kill biometric sign in for
        the whole membership — they just count. Every value an attacker can enumerate must
        therefore identify nothing at all.
        """

        def it_finds_nothing_when_the_selector_is_a_primary_key(member_user, other_user):
            phone, _phone_secret = _issue(member_user, label="iPhone")
            tablet, _tablet_secret = _issue(member_user, label="iPad")
            theirs, _their_secret = _issue(other_user, label="Their Phone")
            live = [phone, tablet, theirs]

            # The whole attack, in three lines: walk the integers, send junk, revoke the world.
            for pk in range(0, max(credential.pk for credential in live) + 5):
                with pytest.raises(InvalidBiometricCredential):
                    BiometricCredential.objects.redeem(_token_for(str(pk), "wrong-verifier"))

            for credential in live:
                credential.refresh_from_db()
                assert credential.revoked_at is None, f"pk {credential.pk} was revoked by an enumerable selector"

        @pytest.mark.parametrize("guess", ["1", "0", "", "iPhone", "bio@example.com", "android"])
        def it_finds_nothing_for_any_other_enumerable_value(member_user, guess):
            credential, _secret = _issue(member_user)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(_token_for(guess, "wrong-verifier"))

            credential.refresh_from_db()
            assert credential.revoked_at is None

    def describe_when_a_verifier_is_wrong_but_the_selector_is_real():
        """Branch 5. This is the branch the selector exists to make reachable, and it is
        also the branch that makes an unguessable selector mandatory — see the model
        docstring for the denial of service a walkable one would open."""

        def it_revokes_the_credential(member_user):
            credential, _secret = _issue(member_user)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(_token_for(credential.selector, "wrong-verifier"))

            credential.refresh_from_db()
            assert credential.revoked_at is not None

        def it_kills_the_verifier_the_real_device_is_holding(member_user):
            credential, secret = _issue(member_user)
            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(_token_for(credential.selector, "wrong-verifier"))

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

        def it_does_not_touch_the_members_other_devices(member_user):
            phone, _phone_secret = _issue(member_user, label="iPhone")
            tablet, _tablet_secret = _issue(member_user, label="iPad")

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(_token_for(phone.selector, "wrong-verifier"))

            tablet.refresh_from_db()
            assert tablet.revoked_at is None

    def describe_when_a_thief_redeems_a_stolen_token_and_the_real_device_comes_back():
        """THE regression for this whole change, and the reason it exists.

        Under the single-slot design the server remembered one generation of secret and
        found a credential BY that secret, so a thief defeated replay detection by simply
        using the stolen copy twice: the original fell out of the one remembered slot, the
        real device's return matched no column at all, and the server called it unknown.
        Nothing was revoked, nothing was logged, and the thief kept redeeming — renewing
        the ninety day expiry on every use.

        A stable selector removes staleness from the question entirely. The row is found
        no matter which generation arrives, so the mismatch is a replay at any depth.
        """

        @pytest.mark.parametrize("generations", [2, 3, 5, 10])
        def it_revokes_however_stale_the_stolen_copy_has_become(member_user, generations):
            credential, stolen = _issue(member_user)
            secret = stolen
            for _ in range(generations):
                _user, secret = BiometricCredential.objects.redeem(secret)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(stolen)  # the real device returns

            credential.refresh_from_db()
            assert credential.revoked_at is not None

        @pytest.mark.parametrize("generations", [2, 3, 5, 10])
        def it_kills_the_token_the_thief_was_left_holding(member_user, generations):
            _credential, stolen = _issue(member_user)
            secret = stolen
            for _ in range(generations):
                _user, secret = BiometricCredential.objects.redeem(secret)
            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(stolen)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(secret)

        def it_logs_the_replay_instead_of_passing_it_off_as_an_unknown_credential(member_user, caplog):
            """The old failure was silent as well as harmless-looking: the incident left no
            trace at all, because an unknown credential is not worth logging."""
            _credential, stolen = _issue(member_user, label="Stolen Phone")
            secret = stolen
            for _ in range(3):
                _user, secret = BiometricCredential.objects.redeem(secret)

            with caplog.at_level(logging.WARNING, logger="core.models"):
                with pytest.raises(InvalidBiometricCredential):
                    BiometricCredential.objects.redeem(stolen)

            assert caplog.messages == [
                f"Biometric credential replay: verifier did not match for user pk={member_user.pk}, "
                f"device 'Stolen Phone'. Credential revoked."
            ]

        def it_stops_the_thief_renewing_the_expiry_indefinitely(member_user):
            """What the old behavior actually cost: the stolen credential stayed live and
            pushed its own ninety day expiry forward on every use, forever."""
            _credential, stolen = _issue(member_user)
            secret = stolen
            for _ in range(5):
                _user, secret = BiometricCredential.objects.redeem(secret)
            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(stolen)

            assert list(BiometricCredential.objects.active_for(member_user)) == []

    def describe_when_a_thief_redeems_a_stolen_secret_twice_inside_the_grace_window():
        """Regression: the grace branch must NOT slide previous_verifier_hash forward.

        Rotating normally there would overwrite the spent hash with the live one, so a
        later honest retry from the real device would no longer match the previous slot and
        would be read as a replay. Pinning the window to the original rotation is what
        keeps the dropped-reply case and the theft case telling themselves apart. This
        shipped broken once.
        """

        def it_keeps_the_spent_verifier_recognizable(member_user):
            credential, stolen = _issue(member_user)
            spent_hash = credential.verifier_hash
            BiometricCredential.objects.redeem(stolen)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)

            BiometricCredential.objects.redeem(stolen)

            credential.refresh_from_db()
            assert credential.previous_verifier_hash == spent_hash

        def it_does_not_slide_the_grace_window_forward(member_user):
            credential, stolen = _issue(member_user)
            BiometricCredential.objects.redeem(stolen)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)
            pinned_rotated_at = BiometricCredential.objects.get(pk=credential.pk).rotated_at

            BiometricCredential.objects.redeem(stolen)

            credential.refresh_from_db()
            assert credential.rotated_at == pinned_rotated_at

        def it_still_revokes_when_the_real_device_comes_back(member_user):
            credential, stolen = _issue(member_user)
            BiometricCredential.objects.redeem(stolen)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)
            BiometricCredential.objects.redeem(stolen)  # the thief's second redeem
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS + 1)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(stolen)  # the real device returns

            credential.refresh_from_db()
            assert credential.revoked_at is not None

        def it_kills_the_secret_the_thief_was_left_holding(member_user):
            credential, stolen = _issue(member_user)
            BiometricCredential.objects.redeem(stolen)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS - 1)
            _user, thiefs_secret = BiometricCredential.objects.redeem(stolen)
            _age_rotation(credential, BIOMETRIC_ROTATION_GRACE_SECONDS + 1)
            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(stolen)

            with pytest.raises(InvalidBiometricCredential):
                BiometricCredential.objects.redeem(thiefs_secret)


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

    def it_keeps_the_original_timestamp_on_an_already_revoked_credential(member_user):
        """The revoked_at__isnull guard. Without it a later revoke_all restamps rows that
        were revoked long ago, rewriting when a device actually lost access."""
        credential, _secret = _issue(member_user)
        revoked_long_ago = timezone.now() - timedelta(days=30)
        BiometricCredential.objects.filter(pk=credential.pk).update(revoked_at=revoked_long_ago)

        BiometricCredential.objects.revoke_all(member_user)

        credential.refresh_from_db()
        assert credential.revoked_at == revoked_long_ago


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


def describe_model_defaults():
    def it_defaults_the_platform_to_android(member_user):
        """A credential written without an explicit platform must not land as iOS and tell
        the member's settings card the wrong thing about their phone."""
        credential = BiometricCredential.objects.create(
            user=member_user,
            selector="a-selector",
            verifier_hash=hash_biometric_secret("some-secret"),
            device_label="Unspecified",
            expires_at=timezone.now() + timedelta(days=BIOMETRIC_TTL_DAYS),
        )

        assert credential.platform == BiometricCredential.Platform.ANDROID

    def it_orders_newest_first_by_default(member_user):
        """Meta.ordering, exercised through a plain queryset rather than asserted as
        metadata, so dropping it actually fails here."""
        older = _issue(member_user, label="Old Phone")[0]
        newer = _issue(member_user, label="New Phone")[0]
        BiometricCredential.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=3))

        assert list(BiometricCredential.objects.all()) == [newer, older]


def describe_str():
    def it_names_the_device_platform_and_state(member_user):
        credential, _secret = _issue(member_user, label="iPhone 15", platform=BiometricCredential.Platform.IOS)

        assert str(credential) == "iPhone 15 - iOS - active"

    def it_reports_an_inactive_credential(member_user):
        credential, _secret = _issue(member_user, label="iPhone 15", platform=BiometricCredential.Platform.IOS)
        BiometricCredential.objects.revoke(credential)

        assert str(credential) == "iPhone 15 - iOS - inactive"
