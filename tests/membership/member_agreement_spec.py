import pytest
from core.models import SiteConfiguration
from membership.models import Member, MemberAgreementAcceptance

pytestmark = pytest.mark.django_db


class DescribeMemberAgreement:
    @pytest.fixture
    def active_member(self):
        from tests.membership.factories import MemberFactory

        return MemberFactory(status="active")

    @pytest.fixture
    def inactive_member(self):
        from tests.membership.factories import MemberFactory

        return MemberFactory(status="former")

    def test_needs_member_agreement_when_off(self, active_member: Member) -> None:
        assert not active_member.needs_member_agreement

    def test_needs_member_agreement_when_on_and_unaccepted(self, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        assert active_member.needs_member_agreement

    def test_needs_member_agreement_when_on_and_accepted(self, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        MemberAgreementAcceptance.objects.create(
            member=active_member, agreement_url="https://example.com", ip_address="127.0.0.1"
        )
        assert not active_member.needs_member_agreement

    def test_needs_member_agreement_when_inactive(self, inactive_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        assert not inactive_member.needs_member_agreement
