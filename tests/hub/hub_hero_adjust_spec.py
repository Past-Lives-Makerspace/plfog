import pytest
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import User
from tests.membership.factories import GuildFactory
from classes.factories import ClassOfferingFactory, CategoryFactory


def login_member(client, username="u1", view_as="member"):
    user = User.objects.create_user(username=username, password="password", is_superuser=view_as == "admin")
    member = user.member
    client.login(username=username, password="password")
    session = client.session
    session["view_as"] = view_as
    session.save()
    return member


@pytest.mark.django_db
def describe_hub_hero_adjust():
    def it_rejects_unauthenticated(client):
        response = client.post("/hero-adjust/", {}, content_type="application/json")
        assert response.status_code == 302
        assert "/login" in response.url

    def it_rejects_invalid_json(client):
        login_member(client, "u2")
        response = client.post("/hero-adjust/", "invalid", content_type="application/json")
        assert response.status_code == 400

    def it_rejects_unsupported_model(client):
        member = login_member(client, "u3")
        ct = ContentType.objects.get_for_model(member)
        data = {"content_type_id": ct.id, "object_id": member.id, "crop": {"x": 0, "y": 0, "w": 10, "h": 10}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 400

    def it_forbids_non_lead_member(client):
        login_member(client, "u4")
        guild = GuildFactory()
        ct = ContentType.objects.get_for_model(guild)
        data = {"content_type_id": ct.id, "object_id": guild.id, "crop": {"x": 0, "y": 0, "w": 10, "h": 10}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 403

    def it_allows_guild_lead(client):
        member = login_member(client, "u5")
        guild = GuildFactory(guild_lead=member)
        ct = ContentType.objects.get_for_model(guild)
        data = {"content_type_id": ct.id, "object_id": guild.id, "crop": {"x": 10, "y": 20, "w": 30, "h": 40}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 200
        guild.refresh_from_db()
        assert guild.hero_crop_x == 10
        assert guild.hero_crop_y == 20
        assert guild.hero_crop_w == 30
        assert guild.hero_crop_h == 40

    def it_allows_class_instructor(client):
        member = login_member(client, "u6")
        offering = ClassOfferingFactory(instructor=member)
        ct = ContentType.objects.get_for_model(offering)
        data = {"content_type_id": ct.id, "object_id": offering.id, "crop": {"x": 5, "y": 5, "w": 50, "h": 50}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 200
        offering.refresh_from_db()
        assert offering.hero_crop_x == 5

    def it_allows_admin_for_category(client):
        login_member(client, "u7", view_as="admin")
        category = CategoryFactory(guild=None)
        ct = ContentType.objects.get_for_model(category)
        data = {"content_type_id": ct.id, "object_id": category.id, "crop": {"x": 1, "y": 1, "w": 1, "h": 1}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 200
        category.refresh_from_db()
        assert category.hero_crop_x == 1

    def it_allows_admin_for_class_offering(client):
        login_member(client, "u8", view_as="admin")
        offering = ClassOfferingFactory()
        ct = ContentType.objects.get_for_model(offering)
        data = {"content_type_id": ct.id, "object_id": offering.id, "crop": {"x": 1, "y": 1, "w": 1, "h": 1}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 200

    def it_allows_guild_lead_for_class_offering(client):
        member = login_member(client, "u9")
        guild = GuildFactory(guild_lead=member)
        category = CategoryFactory(guild=guild)
        offering = ClassOfferingFactory(category=category)
        ct = ContentType.objects.get_for_model(offering)
        data = {"content_type_id": ct.id, "object_id": offering.id, "crop": {"x": 1, "y": 1, "w": 1, "h": 1}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 200

    def it_forbids_non_member_view_for_class_offering(client):
        login_member(client, "u10", view_as="guest")
        offering = ClassOfferingFactory()
        ct = ContentType.objects.get_for_model(offering)
        data = {"content_type_id": ct.id, "object_id": offering.id, "crop": {"x": 1, "y": 1, "w": 1, "h": 1}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 403

    def it_forbids_a_lead_of_a_different_guild_for_class_offering(client):
        member = login_member(client, "u11")
        GuildFactory(guild_lead=member)  # leads a different guild
        offering = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
        ct = ContentType.objects.get_for_model(offering)
        data = {"content_type_id": ct.id, "object_id": offering.id, "crop": {"x": 1, "y": 1, "w": 1, "h": 1}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 403

    def it_forbids_a_plain_member_for_class_offering(client):
        login_member(client, "u12")
        offering = ClassOfferingFactory()
        ct = ContentType.objects.get_for_model(offering)
        data = {"content_type_id": ct.id, "object_id": offering.id, "crop": {"x": 1, "y": 1, "w": 1, "h": 1}}
        response = client.post("/hero-adjust/", data, content_type="application/json")
        assert response.status_code == 403
