import pytest
from django.contrib.auth.models import User

pytestmark = pytest.mark.django_db


def describe_admin_login():
    def it_redirects_anonymous_to_the_email_code_login(client):
        resp = client.get("/admin/login/")
        assert resp.status_code == 302
        assert resp["Location"] == "/accounts/login/code/?next=/admin/"

    def it_preserves_the_next_param_on_redirect(client):
        resp = client.get("/admin/login/?next=/admin/membership/member/")
        assert resp.status_code == 302
        assert resp["Location"] == "/accounts/login/code/?next=/admin/membership/member/"

    def it_403s_an_authenticated_non_staff_member(client):
        User.objects.create_user(username="m", email="m@x.com", password="p")
        client.login(username="m", password="p")
        resp = client.get("/admin/login/")
        assert resp.status_code == 403

    def it_sends_authenticated_staff_to_the_admin_index(client):
        User.objects.create_superuser(username="a", email="a@x.com", password="p")
        client.login(username="a", password="p")
        resp = client.get("/admin/login/")
        assert resp.status_code == 302
        assert resp["Location"].endswith("/admin/")

    def it_never_serves_a_password_form(client):
        # The whole point: no username/password form is reachable anymore.
        resp = client.get("/admin/login/", follow=False)
        assert resp.status_code in (302, 403)


def describe_admin_index_for_non_staff():
    def it_403s_a_non_staff_member_hitting_the_admin_index(client):
        User.objects.create_user(username="m", email="m@x.com", password="p")
        client.login(username="m", password="p")
        resp = client.get("/admin/", follow=True)
        assert resp.status_code == 403
        assert 'type="password"' not in resp.content.decode()
