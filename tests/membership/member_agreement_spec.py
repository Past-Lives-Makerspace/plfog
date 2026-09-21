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

    def it_returns_agreement_acceptance_when_accepted(active_member: Member) -> None:
        acceptance = MemberAgreementAcceptance.objects.create(
            member=active_member, agreement_url="https://example.com", ip_address="127.0.0.1"
        )
        assert active_member.agreement_acceptance == acceptance

    def it_returns_none_for_agreement_acceptance_when_not_accepted(active_member: Member) -> None:
        assert active_member.agreement_acceptance is None


def describe_MemberQuerySet() -> None:
    def describe_accepted_agreement() -> None:
        def it_filters_members_who_have_accepted() -> None:
            from tests.membership.factories import MemberFactory

            accepted_member = MemberFactory(status="active")
            unaccepted_member = MemberFactory(status="active")
            MemberAgreementAcceptance.objects.create(
                member=accepted_member, agreement_url="https://example.com", ip_address="127.0.0.1"
            )

            results = Member.objects.accepted_agreement()
            assert accepted_member in results
            assert unaccepted_member not in results

    def describe_missing_agreement() -> None:
        def it_filters_members_who_have_not_accepted() -> None:
            from tests.membership.factories import MemberFactory

            accepted_member = MemberFactory(status="active")
            unaccepted_member = MemberFactory(status="active")
            MemberAgreementAcceptance.objects.create(
                member=accepted_member, agreement_url="https://example.com", ip_address="127.0.0.1"
            )

            results = Member.objects.missing_agreement()
            assert unaccepted_member in results
            assert accepted_member not in results

    def it_chains_with_active() -> None:
        from tests.membership.factories import MemberFactory

        active_accepted = MemberFactory(status="active")
        former_accepted = MemberFactory(status="former")
        MemberAgreementAcceptance.objects.create(
            member=active_accepted, agreement_url="https://example.com", ip_address="127.0.0.1"
        )
        MemberAgreementAcceptance.objects.create(
            member=former_accepted, agreement_url="https://example.com", ip_address="127.0.0.1"
        )

        active_results = list(Member.objects.active().accepted_agreement())
        assert active_accepted in active_results
        assert former_accepted not in active_results
