import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db

HOSTS = dict(
    ALLOWED_HOSTS=[
        "classes.pastlives.space",
        "members.pastlives.space",
        "tours.pastlives.space",
        "guilds.pastlives.space",
        "testserver",
    ],
    PUBLIC_HOSTS=["classes.pastlives.space"],
    TOURS_HOSTS=["tours.pastlives.space"],
    GUILDS_HOSTS=["guilds.pastlives.space"],
    MEMBER_HOST="members.pastlives.space",
)


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

    @override_settings(**HOSTS)
    @pytest.mark.parametrize(
        "host",
        ["classes.pastlives.space", "members.pastlives.space", "tours.pastlives.space", "guilds.pastlives.space"],
    )
    def it_answers_on_every_host_with_that_hosts_sitemap(client, host):
        resp = client.get("/robots.txt", HTTP_HOST=host, secure=True)

        assert resp.status_code == 200
        assert f"Sitemap: https://{host}/sitemap.xml\n" in resp.content.decode()
