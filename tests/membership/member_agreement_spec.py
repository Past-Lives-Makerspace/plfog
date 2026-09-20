import pytest
from core.models import SiteConfiguration
from django.test import RequestFactory
from membership.models import Member, MemberAgreementAcceptance

pytestmark = pytest.mark.django_db


def describe_member_agreement() -> None:
    @pytest.fixture
    def active_member() -> Member:
        from tests.membership.factories import MemberFactory

        return MemberFactory(status="active")

    @pytest.fixture
    def inactive_member() -> Member:
        from tests.membership.factories import MemberFactory

        return MemberFactory(status="former")

    def it_needs_member_agreement_when_off(active_member: Member) -> None:
        assert not active_member.needs_member_agreement

    def it_needs_member_agreement_when_on_and_unaccepted(active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        assert active_member.needs_member_agreement

    def it_needs_member_agreement_when_on_and_accepted(active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        MemberAgreementAcceptance.objects.create(
            member=active_member, agreement_url="https://example.com", ip_address="127.0.0.1"
        )
        assert not active_member.needs_member_agreement

    def it_needs_member_agreement_when_inactive(inactive_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        assert not inactive_member.needs_member_agreement

    def it_requires_a_remote_address_when_there_is_no_forwarded_address(rf: RequestFactory) -> None:
        from core.models import SiteActivity
        from tests.membership.factories import UserFactory

        user = UserFactory()
        request = rf.post("/agreement/")
        request.user = user
        del request.META["REMOTE_ADDR"]

        with pytest.raises(KeyError, match="REMOTE_ADDR"):
            user.member.accept_member_agreement(request, "https://example.com/agreement")

        assert not MemberAgreementAcceptance.objects.exists()
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ACCEPTED_MEMBER_AGREEMENT).exists()
