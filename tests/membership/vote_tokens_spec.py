"""Tests for vote_tokens module."""

import pytest
from django.core.signing import BadSignature, SignatureExpired
from django.test import override_settings

from membership.vote_tokens import DEFAULT_MAX_AGE, generate_vote_token, verify_vote_token


def describe_default_max_age():
    def it_equals_30_days_in_seconds():
        assert DEFAULT_MAX_AGE == 60 * 60 * 24 * 30


def describe_generate_vote_token():
    def it_returns_a_string():
        token = generate_vote_token("recABC123", 42)
        assert isinstance(token, str)
        assert len(token) > 0

    def it_generates_different_tokens_for_different_inputs():
        t1 = generate_vote_token("recAAA", 1)
        t2 = generate_vote_token("recBBB", 1)
        t3 = generate_vote_token("recAAA", 2)
        assert t1 != t2
        assert t1 != t3


def describe_verify_vote_token():
    def it_round_trips_member_and_session():
        token = generate_vote_token("recXYZ789", 99)
        data = verify_vote_token(token)
        assert data["member_record_id"] == "recXYZ789"
        assert data["session_id"] == 99

    def it_raises_bad_signature_for_tampered_token():
        token = generate_vote_token("recABC", 1)
        with pytest.raises(BadSignature):
            verify_vote_token(token + "tampered")

    def it_raises_bad_signature_for_garbage():
        with pytest.raises(BadSignature):
            verify_vote_token("not-a-valid-token")

    @override_settings(VOTE_TOKEN_MAX_AGE=0)
    def it_raises_signature_expired_for_old_token():
        token = generate_vote_token("recOLD", 1)
        with pytest.raises(SignatureExpired):
            verify_vote_token(token)

    def it_handles_record_id_with_special_chars():
        token = generate_vote_token("rec123ABCxyz", 5)
        data = verify_vote_token(token)
        assert data["member_record_id"] == "rec123ABCxyz"
        assert data["session_id"] == 5

    def it_raises_bad_signature_for_payload_without_separator():
        """Token with valid signature but no | separator in payload."""
        from django.core.signing import TimestampSigner

        signer = TimestampSigner()
        # Sign a payload that has no | separator
        token = signer.sign("noseparator")
        with pytest.raises(BadSignature, match="Invalid token payload"):
            verify_vote_token(token)
