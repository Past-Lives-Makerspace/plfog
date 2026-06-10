import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from tests.membership.factories import GuildFactory
from membership.models import GuildImage

def login_member(client, username="u1", view_as="member"):
    user = User.objects.create_user(username=username, password="password", is_superuser=view_as == "admin")
    member = user.member
    client.login(username=username, password="password")
    session = client.session
    session["view_as"] = view_as
    session.save()
    return member

@pytest.fixture
def image_file():
    return SimpleUploadedFile(
        "test.jpg", b"file_content", content_type="image/jpeg"
    )

@pytest.mark.django_db
def describe_guild_image_views():
    def it_uploads_image_for_lead(client, image_file):
        member = login_member(client, "u1")
        guild = GuildFactory(guild_lead=member)
        response = client.post(f"/guilds/{guild.pk}/images/upload/", {"image": image_file})
        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert guild.gallery_images.count() == 1

    def it_rejects_upload_without_file(client):
        member = login_member(client, "u2")
        guild = GuildFactory(guild_lead=member)
        response = client.post(f"/guilds/{guild.pk}/images/upload/", {})
        assert response.status_code == 400

    def it_rejects_upload_large_file(client):
        member = login_member(client, "u3")
        guild = GuildFactory(guild_lead=member)
        large_file = SimpleUploadedFile("large.jpg", b"x" * (4 * 1024 * 1024), content_type="image/jpeg")
        response = client.post(f"/guilds/{guild.pk}/images/upload/", {"image": large_file})
        assert response.status_code == 400

    def it_rejects_upload_over_10_images(client, image_file):
        member = login_member(client, "u4")
        guild = GuildFactory(guild_lead=member)
        for i in range(10):
            GuildImage.objects.create(guild=guild, image=SimpleUploadedFile(f"test{i}.jpg", b"x"), sort_order=i)
        response = client.post(f"/guilds/{guild.pk}/images/upload/", {"image": image_file})
        assert response.status_code == 400

    def it_rejects_upload_non_lead(client, image_file):
        login_member(client, "u5")
        guild = GuildFactory()
        response = client.post(f"/guilds/{guild.pk}/images/upload/", {"image": image_file})
        assert response.status_code == 403

    def it_deletes_image_for_lead(client, image_file):
        member = login_member(client, "u6")
        guild = GuildFactory(guild_lead=member)
        img = GuildImage.objects.create(guild=guild, image=image_file, sort_order=1)
        response = client.post(f"/guilds/{guild.pk}/images/{img.pk}/delete/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 204
        assert guild.gallery_images.count() == 0

    def it_deletes_image_for_lead_non_ajax(client, image_file):
        member = login_member(client, "u7")
        guild = GuildFactory(guild_lead=member)
        img = GuildImage.objects.create(guild=guild, image=image_file, sort_order=1)
        response = client.post(f"/guilds/{guild.pk}/images/{img.pk}/delete/")
        assert response.status_code == 302
        assert guild.gallery_images.count() == 0

    def it_reorders_images_for_lead(client, image_file):
        member = login_member(client, "u8")
        guild = GuildFactory(guild_lead=member)
        img1 = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("1.jpg", b"x"), sort_order=1)
        img2 = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("2.jpg", b"x"), sort_order=2)
        
        data = {"order": [img2.pk, img1.pk]}
        response = client.post(f"/guilds/{guild.pk}/images/reorder/", data, content_type="application/json")
        assert response.status_code == 204
        
        img1.refresh_from_db()
        img2.refresh_from_db()
        assert img2.sort_order == 0
        assert img1.sort_order == 1

    def it_rejects_reorder_invalid_json(client):
        member = login_member(client, "u9")
        guild = GuildFactory(guild_lead=member)
        response = client.post(f"/guilds/{guild.pk}/images/reorder/", "invalid", content_type="application/json")
        assert response.status_code == 400

    def it_rejects_reorder_empty_order(client):
        member = login_member(client, "u10")
        guild = GuildFactory(guild_lead=member)
        response = client.post(f"/guilds/{guild.pk}/images/reorder/", {"order": []}, content_type="application/json")
        assert response.status_code == 400

    def it_updates_alt_text_for_lead(client, image_file):
        member = login_member(client, "u11")
        guild = GuildFactory(guild_lead=member)
        img = GuildImage.objects.create(guild=guild, image=image_file, sort_order=1)
        
        data = {"alt_text": "New alt text"}
        response = client.post(f"/guilds/{guild.pk}/images/{img.pk}/alt/", data, content_type="application/json")
        assert response.status_code == 204
        img.refresh_from_db()
        assert img.alt_text == "New alt text"

    def it_rejects_alt_update_invalid_json(client, image_file):
        member = login_member(client, "u12")
        guild = GuildFactory(guild_lead=member)
        img = GuildImage.objects.create(guild=guild, image=image_file, sort_order=1)
        
        response = client.post(f"/guilds/{guild.pk}/images/{img.pk}/alt/", "invalid", content_type="application/json")
        assert response.status_code == 400