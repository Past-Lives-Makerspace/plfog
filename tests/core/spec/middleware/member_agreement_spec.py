import pytest
from django.urls import reverse
from core.models import SiteConfiguration
from membership.models import Member

pytestmark = pytest.mark.django_db


class DescribeMemberAgreementMiddleware:
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

    def test_bypasses_when_off(self, client, active_member: Member) -> None:
        client.force_login(active_member.user)
        response = client.get(reverse("hub_home"))
        assert response.status_code == 200

    def test_redirects_when_on_and_unaccepted(self, client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.get(reverse("hub_home"))

        assert response.status_code == 302
        assert reverse("hub_member_agreement") in response.url

    def test_bypasses_when_on_and_accepted(self, client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        from membership.models import MemberAgreementAcceptance

        MemberAgreementAcceptance.objects.create(
            member=active_member, agreement_url="https://example.com", ip_address="127.0.0.1"
        )

        client.force_login(active_member.user)
        response = client.get(reverse("hub_home"))

        assert response.status_code == 200

    def test_does_not_redirect_on_ignored_paths(self, client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.get("/accounts/logout/")
        assert reverse("hub_member_agreement") not in response.url if response.status_code == 302 else True
