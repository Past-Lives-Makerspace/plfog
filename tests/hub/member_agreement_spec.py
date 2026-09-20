import pytest
from django.urls import reverse
from core.models import SiteConfiguration, SiteActivity
from membership.models import MemberAgreementAcceptance, Member

pytestmark = pytest.mark.django_db


class DescribeHubMemberAgreement:
    @pytest.fixture
    def active_member(self):
        from tests.membership.factories import UserFactory

        user = UserFactory()
        member = user.member
        member.status = member.Status.ACTIVE
        member.save()
        return member

    @pytest.fixture
    def inactive_member(self):
        from tests.membership.factories import MemberFactory

        return MemberFactory(status="former")

    def test_redirects_when_not_required(self, client, active_member: Member) -> None:
        client.force_login(active_member.user)
        response = client.get(reverse("hub_member_agreement"))
        assert response.status_code == 302
        assert response.url == reverse("hub_home")

    def test_renders_when_required(self, client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.get(reverse("hub_member_agreement"))
        assert response.status_code == 200
        assert b"https://example.com" in response.content

    def test_post_accepts_agreement(self, client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.post(
            reverse("hub_member_agreement"), {"agree": "1", "next": reverse("hub_home")}, HTTP_X_FORWARDED_FOR="1.2.3.4"
        )

        assert response.status_code == 302
        assert response.url == reverse("hub_home")

        acceptance = MemberAgreementAcceptance.objects.get(member=active_member)
        assert acceptance.agreement_url == "https://example.com"
        assert acceptance.ip_address == "1.2.3.4"

        assert SiteActivity.objects.filter(
            kind=SiteActivity.Kind.ACCEPTED_MEMBER_AGREEMENT, actor=active_member.user
        ).exists()
