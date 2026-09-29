"""What an acceptance record proves — the version, the text, the device.

A click-accept is only worth something later if it says WHAT was agreed to, not just that
something was. These cover the three things that were missing.
"""

import hashlib

import httpx
import pytest
import respx
from core.models import SiteConfiguration
from django.test import RequestFactory
from membership.models import Member, MemberAgreementAcceptance
from membership.services.consent import fingerprint_agreement

pytestmark = pytest.mark.django_db

AGREEMENT_URL = "https://kb.example.test/member-agreement/"
BODY = b"<h1>Member Agreement</h1><p>v2.0.0</p>"


def _publish(content: bytes = BODY, status: int = 200) -> None:
    """Serve `content` at the agreement URL.

    respx intercepts at the transport, which is what STANDARDS asks for and what this needs:
    `fingerprint_agreement` streams the body, so patching `httpx.get` would no longer intercept
    anything — the spec would pass while the code under test made a real request.
    """
    respx.get(AGREEMENT_URL).mock(return_value=httpx.Response(status, content=content))


def _unreachable() -> None:
    respx.get(AGREEMENT_URL).mock(side_effect=httpx.ConnectError("kb is down"))


def _configure(version: str = "") -> SiteConfiguration:
    config = SiteConfiguration.load()
    config.member_agreement_required = True
    config.member_agreement_url = AGREEMENT_URL
    config.member_agreement_version = version
    config.save()
    return config


def describe_fingerprint_agreement() -> None:
    @respx.mock
    def it_hashes_what_was_published() -> None:
        _publish()

        digest, length = fingerprint_agreement(AGREEMENT_URL)

        assert digest == hashlib.sha256(BODY).hexdigest()
        assert length == len(BODY)

    @respx.mock
    def it_returns_blank_when_the_document_is_unreachable() -> None:
        _unreachable()

        assert fingerprint_agreement(AGREEMENT_URL) == ("", None)

    def it_returns_blank_for_a_blank_url() -> None:
        assert fingerprint_agreement("") == ("", None)

    @respx.mock
    def it_refuses_a_document_too_large_to_be_one() -> None:
        _publish(b"x" * 5_000_001)

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

    @respx.mock
    def it_records_the_version_the_text_and_the_device(member: Member, request_: object) -> None:
        _configure(version="2.0.0")
        _publish()

        member.accept_member_agreement(request_, AGREEMENT_URL)  # type: ignore[arg-type]

        row = MemberAgreementAcceptance.objects.get(member=member)
        assert row.document_version == "2.0.0"
        assert row.content_sha256 == hashlib.sha256(BODY).hexdigest()
        assert row.content_length == len(BODY)
        assert row.user_agent == "Mozilla/5.0 (TestDevice)"
        assert row.ip_address

    @respx.mock
    def it_still_accepts_when_the_document_cannot_be_fetched(member: Member, request_: object) -> None:
        """A member is never locked out of the hub because a fingerprint fetch failed."""
        _configure(version="2.0.0")
        _unreachable()

        member.accept_member_agreement(request_, AGREEMENT_URL)  # type: ignore[arg-type]

        row = MemberAgreementAcceptance.objects.get(member=member)
        assert row.content_sha256 == ""
        assert row.content_length is None
        assert row.document_version == "2.0.0"

    @respx.mock
    def it_truncates_an_overlong_user_agent(member: Member, request_: object) -> None:
        _configure()
        _publish()
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


def describe_owes_agreement() -> None:
    """The list an admin reads must match the prompt a member is shown.

    `missing_agreement` asks "has this member ever accepted anything?". Once a version is
    released that stops being the same question, and the gap is invisible in the worst way: the
    member is re-prompted on every page load while the admin's "missing" filter passes over them
    (PastLivesReviewBot, #493).
    """

    @pytest.fixture
    def member() -> Member:
        from tests.membership.factories import MemberFactory

        return MemberFactory(status="active")

    def it_lists_a_member_who_accepted_only_an_older_edition(member: Member) -> None:
        _configure(version="2.0.0")
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version="1.0.0"
        )

        # The bug, stated as an assertion: the old filter cannot see them...
        assert member not in Member.objects.missing_agreement()
        # ...while the member is genuinely being re-prompted.
        assert member.needs_member_agreement
        # The new one agrees with the product.
        assert member in Member.objects.owes_agreement()

    def it_leaves_out_a_member_who_accepted_the_current_edition(member: Member) -> None:
        _configure(version="2.0.0")
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version="2.0.0"
        )

        assert member not in Member.objects.owes_agreement()
        assert not member.needs_member_agreement

    def it_lists_a_member_who_never_accepted_anything(member: Member) -> None:
        _configure(version="2.0.0")

        assert member in Member.objects.owes_agreement()

    def it_matches_the_old_filter_when_no_version_is_configured(member: Member) -> None:
        """One-time behaviour: any acceptance settles it, so the two questions coincide."""
        _configure(version="")
        MemberAgreementAcceptance.objects.create(
            member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version="1.0.0"
        )

        assert member not in Member.objects.owes_agreement()
        assert not member.needs_member_agreement

    def it_counts_a_member_once_despite_several_old_acceptances(member: Member) -> None:
        """A NOT IN subquery, not a join — several non-matching rows must not duplicate the member."""
        _configure(version="3.0.0")
        for version in ("1.0.0", "2.0.0"):
            MemberAgreementAcceptance.objects.create(
                member=member, agreement_url=AGREEMENT_URL, ip_address="127.0.0.1", document_version=version
            )

        assert list(Member.objects.owes_agreement()).count(member) == 1
