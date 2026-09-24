"""What an acceptance record proves — the version, the text, the device.

A click-accept is only worth something later if it says WHAT was agreed to, not just that
something was. These cover the three things that were missing.
"""

import hashlib

import httpx
import pytest
from core.models import SiteConfiguration
from django.test import RequestFactory
from membership.models import Member, MemberAgreementAcceptance
from membership.services.consent import fingerprint_agreement

pytestmark = pytest.mark.django_db

AGREEMENT_URL = "https://kb.example.test/member-agreement/"
BODY = b"<h1>Member Agreement</h1><p>v2.0.0</p>"


def _response(content: bytes, status: int = 200) -> httpx.Response:
    """A Response with its request attached — `raise_for_status()` needs one to exist."""
    return httpx.Response(status, content=content, request=httpx.Request("GET", AGREEMENT_URL))


def _response(content: bytes, status: int = 200) -> httpx.Response:
    """A response with its request attached — raise_for_status() needs one."""
    return httpx.Response(status, content=content, request=httpx.Request("GET", AGREEMENT_URL))


def _configure(version: str = "") -> SiteConfiguration:
    config = SiteConfiguration.load()
    config.member_agreement_required = True
    config.member_agreement_url = AGREEMENT_URL
    config.member_agreement_version = version
    config.save()
    return config


def describe_fingerprint_agreement() -> None:
    def it_hashes_what_was_published(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(BODY))

        digest, length = fingerprint_agreement(AGREEMENT_URL)

        assert digest == hashlib.sha256(BODY).hexdigest()
        assert length == len(BODY)

    def it_returns_blank_when_the_document_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("kb is down")

        monkeypatch.setattr(httpx, "get", boom)

        assert fingerprint_agreement(AGREEMENT_URL) == ("", None)

    def it_returns_blank_for_a_blank_url() -> None:
        assert fingerprint_agreement("") == ("", None)

    def it_refuses_a_document_too_large_to_be_one(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(b"x" * 5_000_001))

        assert fingerprint_agreement(AGREEMENT_URL) == ("", None)


def describe_accept_member_agreement() -> None:
    @pytest.fixture
    def member() -> Member:
        from tests.membership.factories import MemberFactory

        return MemberFactory(status="active")

    @pytest.fixture
    def request_(member: Member) -> object:
        req = RequestFactory().post("/agreement/", HTTP_USER_AGENT="Mozilla/5.0 (TestDevice)")
        req.user = member.user
        return req

    def it_records_the_version_the_text_and_the_device(
        member: Member, request_: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(version="2.0.0")
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(BODY))

        member.accept_member_agreement(request_, AGREEMENT_URL)  # type: ignore[arg-type]

        row = MemberAgreementAcceptance.objects.get(member=member)
        assert row.document_version == "2.0.0"
        assert row.content_sha256 == hashlib.sha256(BODY).hexdigest()
        assert row.content_length == len(BODY)
        assert row.user_agent == "Mozilla/5.0 (TestDevice)"
        assert row.ip_address

    def it_still_accepts_when_the_document_cannot_be_fetched(
        member: Member, request_: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A member is never locked out of the hub because a fingerprint fetch failed."""
        _configure(version="2.0.0")

        def boom(*args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("kb is down")

        monkeypatch.setattr(httpx, "get", boom)

        member.accept_member_agreement(request_, AGREEMENT_URL)  # type: ignore[arg-type]

        row = MemberAgreementAcceptance.objects.get(member=member)
        assert row.content_sha256 == ""
        assert row.content_length is None
        assert row.document_version == "2.0.0"

    def it_truncates_an_overlong_user_agent(member: Member, request_: object, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure()
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(BODY))
        req = RequestFactory().post("/agreement/", HTTP_USER_AGENT="U" * 2000)
        req.user = member.user

        member.accept_member_agreement(req, AGREEMENT_URL)

        assert len(MemberAgreementAcceptance.objects.get(member=member).user_agent) == 1000


def describe_re_acceptance() -> None:
    @pytest.fixture
    def member() -> Member:
        from tests.membership.factories import MemberFactory

        return MemberFactory(status="active")

    def it_keeps_asking_nobody_when_no_version_is_configured(member: Member) -> None:
        """Shipping this must not re-prompt a single member until someone decides to."""
        _configure(version="")
        MemberAgreementAcceptance.objects.create(member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1")

        assert not member.needs_member_agreement

    def it_asks_again_when_the_released_version_moves_on(member: Member) -> None:
        _configure(version="1.0.0")
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version="1.0.0"
        )
        assert not member.needs_member_agreement

        _configure(version="2.0.0")
        assert member.needs_member_agreement

    def it_does_not_ask_again_once_the_new_version_is_accepted(member: Member) -> None:
        _configure(version="2.0.0")
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version="1.0.0"
        )
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version="2.0.0"
        )

        assert not member.needs_member_agreement

    def it_asks_a_member_whose_only_acceptance_predates_versions(member: Member) -> None:
        """A blank version means unknown, never "the current one". Those members re-accept."""
        _configure(version="2.0.0")
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version=""
        )

        assert member.needs_member_agreement

    def it_keeps_the_older_acceptance_intact(member: Member) -> None:
        """The history is the point: the old row still says what they agreed to back then."""
        _configure(version="2.0.0")
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version="1.0.0"
        )
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version="2.0.0"
        )

        versions = set(
            MemberAgreementAcceptance.objects.filter(member=member).values_list("document_version", flat=True)
        )
        assert versions == {"1.0.0", "2.0.0"}
        assert member.agreement_acceptance is not None
        assert member.agreement_acceptance.document_version == "2.0.0"

    def it_counts_a_twice_accepting_member_once(member: Member) -> None:
        """Without .distinct() the join returns one row per acceptance and inflates every total."""
        _configure(version="2.0.0")
        for version in ("1.0.0", "2.0.0"):
            MemberAgreementAcceptance.objects.create(
                member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version=version
            )

        assert Member.objects.active().accepted_agreement().count() == 1
