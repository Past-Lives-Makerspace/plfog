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
        assert b"Past Lives Makerspace" in response.content


def describe_home_page_hero():
    def it_contains_past_lives_in_hero_title(client):
        response = client.get("/")
        content = response.content.decode()
        assert 'class="hero__title">Past Lives<' in content

    def it_contains_makerspace_subtitle(client):
        response = client.get("/")
        content = response.content.decode()
        assert 'class="hero__subtitle">Makerspace<' in content


def describe_nav_anonymous():
    def it_does_not_show_log_in_link(client):
        response = client.get("/")
        content = response.content.decode()
        assert "Log in" not in content

    def it_does_not_show_sign_up_link(client):
        response = client.get("/")
        content = response.content.decode()
        assert "Sign up" not in content


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

    def it_shows_user_email(logged_in_client):
        response = logged_in_client.get("/")
        assert b"test@example.com" in response.content

    def it_shows_log_out_link(logged_in_client):
        response = logged_in_client.get("/")
        assert b"Log out" in response.content


def describe_base_template_meta():
    def it_includes_meta_description(client):
        response = client.get("/")
        content = response.content.decode()
        assert '<meta name="description"' in content

    def it_includes_footer(client):
        response = client.get("/")
        content = response.content.decode()
        assert '<footer class="site-footer">' in content
