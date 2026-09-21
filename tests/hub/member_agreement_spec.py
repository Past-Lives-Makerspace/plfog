import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.test import override_settings
from django.test.html import parse_html
from django.urls import reverse
from core.models import SiteConfiguration, SiteActivity
from membership.models import MemberAgreementAcceptance, Member

pytestmark = pytest.mark.django_db


def describe_hub_member_agreement() -> None:
    @pytest.fixture
    def active_member() -> Member:
        from tests.membership.factories import UserFactory

        user = UserFactory()
        member = user.member
        member.status = member.Status.ACTIVE
        member.save()
        return member

    @pytest.fixture
    def inactive_member(active_member: Member) -> Member:
        active_member.status = Member.Status.FORMER
        active_member.save(update_fields=["status"])
        return active_member

    @pytest.mark.parametrize("method", ["get", "post"])
    def it_redirects_inactive_members(client: Client, inactive_member: Member, method: str) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()
        client.force_login(inactive_member.user)

        response = getattr(client, method)(reverse("hub_member_agreement"), {"agree": "1"})

        assert response.status_code == 302
        assert response.url == reverse("hub_home")
        assert not MemberAgreementAcceptance.objects.exists()
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ACCEPTED_MEMBER_AGREEMENT).exists()

    @pytest.mark.parametrize("method", ["get", "post"])
    def it_redirects_users_without_a_member(client: Client, active_member: Member, method: str) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()
        client.force_login(active_member.user)
        active_member.delete()

        response = getattr(client, method)(reverse("hub_member_agreement"), {"agree": "1"})

        assert response.status_code == 302
        assert response.url == reverse("hub_home")
        assert not MemberAgreementAcceptance.objects.exists()
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ACCEPTED_MEMBER_AGREEMENT).exists()

    def it_redirects_when_not_required(client: Client, active_member: Member) -> None:
        client.force_login(active_member.user)
        response = client.get(reverse("hub_member_agreement"))
        assert response.status_code == 302
        assert response.url == reverse("hub_home")

    def it_renders_when_required(client: Client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.get(reverse("hub_member_agreement"))
        assert response.status_code == 200
        link = (
            '<a id="member-agreement-document" href="https://example.com" target="_blank" '
            'rel="noopener" hx-boost="false">Read the Member Agreement (opens in a new tab)</a>'
        )
        assert parse_html(link) in parse_html(response.content.decode())
        assert b"<iframe" not in response.content

    @override_settings(X_FRAME_OPTIONS="DENY")
    def it_can_read_the_knowledge_base_agreement_before_accepting(client: Client, active_member: Member) -> None:
        from tests.membership.factories import WikiPageFactory

        document = WikiPageFactory(
            title="Member Agreement For Reading Test",
            body="Keep the shared work areas clear after every project.",
        )
        document_url = f"http://testserver{document.get_absolute_url()}"
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = document_url
        config.save()
        client.force_login(active_member.user)

        prompt = client.get(reverse("hub_member_agreement"))
        link = (
            f'<a id="member-agreement-document" href="{document_url}" target="_blank" '
            'rel="noopener" hx-boost="false">Read the Member Agreement (opens in a new tab)</a>'
        )
        assert parse_html(link) in parse_html(prompt.content.decode())
        reading = client.get(document_url)
        assert reading.status_code == 200
        assert reading["X-Frame-Options"] == "DENY"
        assert document.body.encode() in reading.content
        assert not MemberAgreementAcceptance.objects.filter(member=active_member).exists()

        blocked = client.get(reverse("hub_member_directory"))
        assert blocked.status_code == 302
        assert blocked.url.startswith(reverse("hub_member_agreement"))

        accepted = client.post(reverse("hub_member_agreement"), {"agree": "on"})
        assert accepted.status_code == 302
        assert MemberAgreementAcceptance.objects.get(member=active_member).agreement_url == document_url
        assert client.get(reverse("hub_member_directory")).status_code == 200

    @pytest.mark.parametrize("next_url", [None, "https://outside.example/", "/members/"])
    @pytest.mark.parametrize(
        ("ip_headers", "expected_ip"),
        [
            ({"HTTP_X_FORWARDED_FOR": "192.0.2.1, 192.0.2.2", "REMOTE_ADDR": "192.0.2.3"}, "192.0.2.1"),
            ({"REMOTE_ADDR": "192.0.2.3"}, "192.0.2.3"),
        ],
    )
    def it_post_accepts_agreement(
        client: Client, active_member: Member, next_url: str | None, ip_headers: dict[str, str], expected_ip: str
    ) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        data = {"agree": "1"}
        if next_url is not None:
            data["next"] = next_url
        response = client.post(reverse("hub_member_agreement"), data, **ip_headers)

        assert response.status_code == 302
        assert response.url == ("/members/" if next_url == "/members/" else reverse("hub_home"))

        acceptance = MemberAgreementAcceptance.objects.get(member=active_member)
        assert acceptance.agreement_url == "https://example.com"
        assert acceptance.ip_address == expected_ip

        assert SiteActivity.objects.filter(
            kind=SiteActivity.Kind.ACCEPTED_MEMBER_AGREEMENT, actor=active_member.user
        ).exists()

    @pytest.mark.parametrize("agreement_data", [{}, {"agree": ""}, {"agree": "false"}, {"agree": "False"}])
    def it_post_fails_when_unchecked(client: Client, active_member: Member, agreement_data: dict[str, str]) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.post(reverse("hub_member_agreement"), {"next": reverse("hub_home"), **agreement_data})

        assert response.status_code == 200
        messages = list(get_messages(response.wsgi_request))
        assert [message.message for message in messages] == ["You must check the box to agree."]
        assert not MemberAgreementAcceptance.objects.filter(member=active_member).exists()
        assert not SiteActivity.objects.filter(
            kind=SiteActivity.Kind.ACCEPTED_MEMBER_AGREEMENT, actor=active_member.user
        ).exists()
