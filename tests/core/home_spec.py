import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


def describe_home_page():
    def it_returns_200(client):
        response = client.get("/")
        assert response.status_code == 200

    def it_uses_home_template(client):
        response = client.get("/")
        assert "home.html" in [t.name for t in response.templates]

    def it_uses_base_template(client):
        response = client.get("/")
        assert "base.html" in [t.name for t in response.templates]

    def it_contains_past_lives_text(client):
        response = client.get("/")
        assert b"Past Lives Federation of Guilds" in response.content


def describe_home_page_hero():
    def it_contains_past_lives_in_hero_title(client):
        response = client.get("/")
        content = response.content.decode()
        assert 'class="hero__title">Past Lives<' in content

    def it_contains_makerspace_subtitle(client):
        response = client.get("/")
        content = response.content.decode()
        assert 'class="hero__subtitle">Federation of Guilds<' in content


def describe_nav_anonymous():
    def it_shows_log_in_cta(client):
        # The home hero now exposes a "Log In" CTA for anonymous visitors.
        # Assert the actual link, not the substring — the changelog modal text
        # also contains the literal phrase "Log in" and would yield a false positive.
        response = client.get("/")
        content = response.content.decode()
        assert 'href="/accounts/login/"' in content

    def it_shows_join_the_community_cta(client):
        # The home hero now exposes a signup CTA ("Join the Community") next to Log In.
        response = client.get("/")
        content = response.content.decode()
        assert 'href="/accounts/signup/"' in content


def describe_nav_authenticated():
    @pytest.fixture()
    def logged_in_client(client):
        User = get_user_model()
        user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )
        client.force_login(user)
        return client

    def it_redirects_authenticated_users_to_hub_home(logged_in_client):
        response = logged_in_client.get("/")
        assert response.status_code == 302
        assert response.url == "/home/"


def describe_base_template_meta():
    def it_includes_meta_description(client):
        response = client.get("/")
        content = response.content.decode()
        assert '<meta name="description"' in content

    def it_includes_footer(client):
        response = client.get("/")
        content = response.content.decode()
        assert '<footer class="site-footer">' in content


def describe_google_analytics_on_main_site():
    def it_omits_ga_tag_when_id_not_set(client):
        response = client.get("/")
        assert b"googletagmanager.com" not in response.content

    def it_injects_ga_tag_on_home_when_configured(client):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-MAIN123"
        config.save()
        response = client.get("/")
        assert b"googletagmanager.com" in response.content
        assert b"G-MAIN123" in response.content
