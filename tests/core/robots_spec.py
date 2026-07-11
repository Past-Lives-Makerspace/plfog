import pytest

pytestmark = pytest.mark.django_db


def describe_robots_txt():
    def it_returns_200(client):
        resp = client.get("/robots.txt")
        assert resp.status_code == 200

    def it_is_served_as_plain_text(client):
        resp = client.get("/robots.txt")
        assert resp["Content-Type"].startswith("text/plain")

    def it_disallows_the_admin(client):
        resp = client.get("/robots.txt")
        assert "Disallow: /admin/" in resp.content.decode()
