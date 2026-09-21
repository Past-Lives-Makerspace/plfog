import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from core.middleware import MemberAgreementMiddleware
from core.models import SiteConfiguration
from membership.models import Member

pytestmark = pytest.mark.django_db


def describe_member_agreement_middleware() -> None:
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

    @pytest.fixture
    def enabled_agreement() -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

    @pytest.fixture
    def middleware() -> MemberAgreementMiddleware:
        def downstream(request: HttpRequest) -> HttpResponse:
            return HttpResponse("Reached the requested page")

        return MemberAgreementMiddleware(downstream)

    @pytest.mark.parametrize(
        ("document_url", "request_path", "method", "allowed"),
        [
            ("http://testserver/wiki/p/agreement/", "/wiki/p/agreement/", "GET", True),
            ("http://testserver/wiki/p/agreement/", "/wiki/p/agreement/", "HEAD", True),
            ("http://testserver/wiki/p/agreement/?edition=1#terms", "/wiki/p/agreement/?edition=1", "GET", True),
            ("http://testserver", "/", "GET", True),
            ("http://testserver/wiki/p/agreement/", "/wiki/p/agreement/", "POST", False),
            ("http://testserver/wiki/p/agreement/", "/wiki/p/agreement/edit/", "GET", False),
            ("http://elsewhere.example/wiki/p/agreement/", "/wiki/p/agreement/", "GET", False),
            ("https://testserver/wiki/p/agreement/", "/wiki/p/agreement/", "GET", False),
            ("http://testserver/wiki/p/agreement/?edition=1", "/wiki/p/agreement/?edition=2", "GET", False),
        ],
    )
    def it_exempts_only_reading_the_configured_document(
        rf: RequestFactory,
        middleware: MemberAgreementMiddleware,
        active_member: Member,
        document_url: str,
        request_path: str,
        method: str,
        allowed: bool,
    ) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = document_url
        config.save()
        request = rf.generic(method, request_path)
        request.user = active_member.user
        request.surface = "members"

        response = middleware(request)

        if allowed:
            assert response.status_code == 200
            assert response.content == b"Reached the requested page"
        else:
            assert response.status_code == 302
            assert response["Location"].startswith(reverse("hub_member_agreement"))

    def it_allows_anonymous_requests(
        rf: RequestFactory, middleware: MemberAgreementMiddleware, enabled_agreement: None
    ) -> None:
        request = rf.get("/members/")
        request.user = AnonymousUser()
        request.surface = "members"

        response = middleware(request)

        assert response.status_code == 200
        assert response.content == b"Reached the requested page"

    @pytest.mark.parametrize("surface", ["public", None])
    def it_allows_requests_outside_the_members_surface(
        rf: RequestFactory,
        middleware: MemberAgreementMiddleware,
        enabled_agreement: None,
        active_member: Member,
        surface: str | None,
    ) -> None:
        request = rf.get("/members/")
        request.user = active_member.user
        if surface is not None:
            request.surface = surface

        response = middleware(request)

        assert response.status_code == 200
        assert response.content == b"Reached the requested page"

    def it_allows_users_without_a_member(
        rf: RequestFactory,
        middleware: MemberAgreementMiddleware,
        enabled_agreement: None,
        active_member: Member,
    ) -> None:
        user = active_member.user
        active_member.delete()
        user.refresh_from_db()
        request = rf.get("/members/")
        request.user = user
        request.surface = "members"

        response = middleware(request)

        assert response.status_code == 200
        assert response.content == b"Reached the requested page"

    def it_allows_inactive_members(
        rf: RequestFactory,
        middleware: MemberAgreementMiddleware,
        enabled_agreement: None,
        inactive_member: Member,
    ) -> None:
        request = rf.get("/members/")
        request.user = inactive_member.user
        request.surface = "members"

        response = middleware(request)

        assert response.status_code == 200
        assert response.content == b"Reached the requested page"

    def it_bypasses_when_off(client: Client, active_member: Member) -> None:
        client.force_login(active_member.user)
        response = client.get(reverse("hub_home"))
        assert response.status_code == 200

    def it_redirects_when_on_and_unaccepted(client: Client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.get(reverse("hub_home"))

        assert response.status_code == 302
        assert reverse("hub_member_agreement") in response.url

    def it_hx_redirect_when_wants_fragment(client: Client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.get(reverse("hub_home"), HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        assert reverse("hub_member_agreement") in response["HX-Redirect"]

    def it_bypasses_when_on_and_accepted(client: Client, active_member: Member) -> None:
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

    def it_does_not_redirect_on_ignored_paths(client: Client, active_member: Member) -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com"
        config.save()

        client.force_login(active_member.user)
        response = client.get("/accounts/logout/")
        assert reverse("hub_member_agreement") not in response.get("Location", "")
