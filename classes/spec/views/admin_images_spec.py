"""BDD specs for admin AJAX image endpoints (hero upload, gallery CRUD, reorder)."""

from __future__ import annotations

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from classes.factories import ClassImageFactory, ClassOfferingFactory
from classes.models import ClassImage, ClassOffering


def _tiny_gif() -> SimpleUploadedFile:
    gif_bytes = (
        b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x21\xf9\x04"
        b"\x01\x0a\x00\x01\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02"
        b"\x02\x4c\x01\x00\x3b"
    )
    return SimpleUploadedFile("test.gif", gif_bytes, content_type="image/gif")


def describe_admin_class_hero_upload():
    def it_uploads_and_returns_url(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        url = reverse("classes:admin_class_hero_upload", kwargs={"pk": offering.pk})

        response = client.post(url, {"image": _tiny_gif()})

        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        offering.refresh_from_db()
        assert offering.image

    def it_returns_400_without_a_file(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        url = reverse("classes:admin_class_hero_upload", kwargs={"pk": offering.pk})

        response = client.post(url)

        assert response.status_code == 400
        assert "No file" in response.json()["error"]

    def it_rejects_get_requests(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        url = reverse("classes:admin_class_hero_upload", kwargs={"pk": offering.pk})

        response = client.get(url)

        assert response.status_code == 405

    def it_requires_admin_access(client, member_user, db):
        client.force_login(member_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        url = reverse("classes:admin_class_hero_upload", kwargs={"pk": offering.pk})

        response = client.post(url, {"image": _tiny_gif()})

        assert response.status_code == 403


def describe_admin_class_image_upload():
    def it_creates_a_gallery_image(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, gallery=0)
        url = reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk})

        response = client.post(url, {"image": _tiny_gif()})

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "url" in data
        assert ClassImage.objects.filter(class_offering=offering).count() == 1

    def it_enforces_10_image_limit(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        for i in range(10):
            ClassImageFactory(class_offering=offering, sort_order=i)
        url = reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk})

        response = client.post(url, {"image": _tiny_gif()})

        assert response.status_code == 400
        assert "at most 10 images" in response.json()["error"]

    def it_allows_adding_again_after_a_delete(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, gallery=0)
        imgs = [ClassImageFactory(class_offering=offering, sort_order=i) for i in range(10)]
        ClassImage.objects.filter(pk=imgs[0].pk).delete()
        url = reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk})

        response = client.post(url, {"image": _tiny_gif()})

        assert response.status_code == 200
        assert offering.gallery_images.count() == 10

    def it_returns_400_without_a_file(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        url = reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk})

        response = client.post(url)

        assert response.status_code == 400

    def it_returns_400_when_file_exceeds_the_limit(admin_user, client, db, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024 * 1024  # 1 MB, so the file below is over the limit
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        url = reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk})
        big_file = SimpleUploadedFile("big.png", b"\x89PNG" + b"\x00" * (1024 * 1024 + 1), content_type="image/png")

        response = client.post(url, {"image": big_file})

        assert response.status_code == 400
        assert "1 MB" in response.json()["error"]


def describe_admin_class_image_reorder():
    def it_updates_sort_order(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        img_a = ClassImageFactory(class_offering=offering, sort_order=0)
        img_b = ClassImageFactory(class_offering=offering, sort_order=1)
        url = reverse("classes:admin_class_image_reorder", kwargs={"pk": offering.pk})

        response = client.post(
            url,
            json.dumps({"order": [img_b.pk, img_a.pk]}),
            content_type="application/json",
        )

        assert response.status_code == 200
        img_a.refresh_from_db()
        img_b.refresh_from_db()
        assert img_b.sort_order == 0
        assert img_a.sort_order == 1

    def it_returns_400_for_invalid_json(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        url = reverse("classes:admin_class_image_reorder", kwargs={"pk": offering.pk})

        response = client.post(url, "not json", content_type="application/json")

        assert response.status_code == 400


def describe_admin_class_image_delete():
    def it_removes_the_image(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        img = ClassImageFactory(class_offering=offering)
        url = reverse("classes:admin_class_image_delete", kwargs={"pk": img.pk})

        response = client.post(url)

        assert response.status_code == 200
        assert not ClassImage.objects.filter(pk=img.pk).exists()

    def it_returns_404_for_nonexistent_image(admin_user, client, db):
        client.force_login(admin_user)
        url = reverse("classes:admin_class_image_delete", kwargs={"pk": 99999})

        response = client.post(url)

        assert response.status_code == 404


def describe_admin_class_image_alt():
    def it_updates_alt_text(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        img = ClassImageFactory(class_offering=offering, alt_text="")
        url = reverse("classes:admin_class_image_alt", kwargs={"pk": img.pk})

        response = client.post(
            url,
            json.dumps({"alt_text": "A beautiful pot"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        img.refresh_from_db()
        assert img.alt_text == "A beautiful pot"

    def it_truncates_alt_text_at_255_chars(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        img = ClassImageFactory(class_offering=offering)
        url = reverse("classes:admin_class_image_alt", kwargs={"pk": img.pk})

        response = client.post(
            url,
            json.dumps({"alt_text": "x" * 300}),
            content_type="application/json",
        )

        assert response.status_code == 200
        img.refresh_from_db()
        assert len(img.alt_text) == 255

    def it_returns_400_for_missing_key(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        img = ClassImageFactory(class_offering=offering)
        url = reverse("classes:admin_class_image_alt", kwargs={"pk": img.pk})

        response = client.post(url, json.dumps({"wrong": "key"}), content_type="application/json")

        assert response.status_code == 400
