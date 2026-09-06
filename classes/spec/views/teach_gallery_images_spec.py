"""BDD specs for the instructor-scoped hero + gallery image endpoints and the edit pages that use them."""

from __future__ import annotations

import json
from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassImageFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import ClassImage, ClassOffering
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

Status = ClassOffering.Status


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="gallery-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Gallery Teacher", instructor_slug="gallery-teacher")


@pytest.fixture
def stranger(db):
    user = UserFactory(username="gallery-stranger@example.com")
    return InstructorFactory(user=user, full_legal_name="Gallery Stranger", instructor_slug="gallery-stranger")


def _png(name: str = "shot.png") -> SimpleUploadedFile:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _own(instructor, status) -> ClassOffering:
    offering = ClassOfferingFactory(instructor=instructor, status=status, published_at=timezone.now())
    if status == Status.PUBLISHED:
        start = timezone.now() + timedelta(days=3)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _exercise_gallery(client, offering: ClassOffering) -> None:
    """Upload, reorder, edit alt text, and delete one gallery image on ``offering``."""
    upload = client.post(reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk}), {"image": _png()})
    assert upload.status_code == 200, upload.content
    new_id = upload.json()["id"]
    first = offering.gallery_images.exclude(pk=new_id).first()
    reorder = client.post(
        reverse("classes:teach_class_image_reorder", kwargs={"pk": offering.pk}),
        json.dumps({"order": [new_id, first.pk]}),
        content_type="application/json",
    )
    assert reorder.status_code == 200
    assert ClassImage.objects.get(pk=new_id).sort_order == 0
    alt = client.post(
        reverse("classes:teach_class_image_alt", kwargs={"pk": new_id}),
        json.dumps({"alt_text": "A finished bowl"}),
        content_type="application/json",
    )
    assert alt.status_code == 200
    assert ClassImage.objects.get(pk=new_id).alt_text == "A finished bowl"
    delete = client.post(reverse("classes:teach_class_image_delete", kwargs={"pk": new_id}))
    assert delete.status_code == 200
    assert not ClassImage.objects.filter(pk=new_id).exists()


def describe_instructor_gallery_endpoints():
    def it_lets_the_instructor_manage_the_gallery_on_a_draft(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.DRAFT)
        client.force_login(instructor_fixture.user)
        _exercise_gallery(client, offering)

    def it_lets_the_instructor_manage_the_gallery_on_a_live_class(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.PUBLISHED)
        client.force_login(instructor_fixture.user)
        _exercise_gallery(client, offering)

    def it_lets_the_instructor_replace_the_hero(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.DRAFT)
        offering.hero_crop_w = 3
        offering.save(update_fields=["hero_crop_w"])
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_hero_upload", kwargs={"pk": offering.pk}), {"image": _png("hero.png")}
        )
        assert resp.status_code == 200
        assert resp.json()["url"]
        offering.refresh_from_db()
        assert offering.hero_crop_w is None

    def it_lets_guild_staff_who_can_edit_the_class_manage_its_gallery(instructor_fixture, stranger, client):
        guild = GuildFactory(name="Gallery Guild")
        GuildStaffMembershipFactory(guild=guild, member=instructor_fixture)
        offering = ClassOfferingFactory(instructor=stranger, status=Status.DRAFT, category=CategoryFactory(guild=guild))
        client.force_login(instructor_fixture.user)
        _exercise_gallery(client, offering)

    def it_404s_a_stranger_on_every_endpoint(instructor_fixture, stranger, client):
        offering = _own(instructor_fixture, Status.PUBLISHED)
        image = ClassImageFactory(class_offering=offering)
        client.force_login(stranger.user)
        posts = [
            (reverse("classes:teach_class_hero_upload", kwargs={"pk": offering.pk}), {"image": _png()}, None),
            (reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk}), {"image": _png()}, None),
            (
                reverse("classes:teach_class_image_reorder", kwargs={"pk": offering.pk}),
                json.dumps({"order": [image.pk]}),
                "application/json",
            ),
            (
                reverse("classes:teach_class_image_alt", kwargs={"pk": image.pk}),
                json.dumps({"alt_text": "x"}),
                "application/json",
            ),
            (reverse("classes:teach_class_image_delete", kwargs={"pk": image.pk}), {}, None),
        ]
        for url, data, content_type in posts:
            resp = client.post(url, data, content_type=content_type) if content_type else client.post(url, data)
            assert resp.status_code == 404, url
        image.refresh_from_db()
        assert image.alt_text == ""
        assert offering.gallery_images.count() == 2

    def it_rejects_get_and_anonymous(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.DRAFT)
        assert client.post(reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk})).status_code == 302
        client.force_login(instructor_fixture.user)
        assert client.get(reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk})).status_code == 405

    def it_keeps_the_admin_endpoints_admin_only(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk}), {"image": _png()})
        assert resp.status_code == 403


def describe_edit_pages_point_at_the_instructor_endpoints():
    def it_wires_the_draft_edit_page(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert f'data-upload-url="{reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk})}"' in html
        assert f'data-reorder-url="{reverse("classes:teach_class_image_reorder", kwargs={"pk": offering.pk})}"' in html
        assert 'data-image-url-base="/classes/teach/images/"' in html
        assert f'data-upload-url="{reverse("classes:teach_class_hero_upload", kwargs={"pk": offering.pk})}"' in html
        assert "/classes/admin/images/" not in html
        assert reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk}) not in html

    def it_wires_the_published_edit_page(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.PUBLISHED)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert f'data-upload-url="{reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk})}"' in html
        assert 'data-image-url-base="/classes/teach/images/"' in html
        assert "/classes/admin/images/" not in html

    def it_keeps_the_admin_edit_page_on_the_admin_endpoints(admin_user, client):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert f'data-upload-url="{reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk})}"' in html
        assert 'data-image-url-base="/classes/admin/images/"' in html
        assert f'data-upload-url="{reverse("classes:admin_class_hero_upload", kwargs={"pk": offering.pk})}"' in html
