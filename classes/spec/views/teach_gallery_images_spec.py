"""BDD specs for the instructor-scoped hero + gallery image endpoints and the edit pages that use them."""

from __future__ import annotations

import json
from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import resolve, reverse
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
from classes.views import teach_class_image_delete, teach_image_url_base
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


def _teach_image_base() -> str:
    """The per-image route prefix, from the route itself — never a retyped literal."""
    return reverse("classes:teach_class_image_delete", kwargs={"pk": 0}).removesuffix("0/delete/")


def describe_the_per_image_url_base_round_trips():
    def it_resolves_back_to_the_delete_route(instructor_fixture, db):
        # ``removesuffix`` is a silent no-op if the route verb ever changes, and both the view
        # and a recomputed expectation would move together — so assert the round trip instead.
        offering = _own(instructor_fixture, Status.DRAFT)
        image = ClassImageFactory(class_offering=offering)
        match = resolve(f"{teach_image_url_base()}{image.pk}/delete/")
        assert match.func is teach_class_image_delete
        assert match.kwargs == {"pk": image.pk}


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
        assert f'data-image-url-base="{_teach_image_base()}"' in html
        assert f'data-upload-url="{reverse("classes:teach_class_hero_upload", kwargs={"pk": offering.pk})}"' in html
        assert "/classes/admin/images/" not in html
        assert reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk}) not in html

    def it_wires_the_published_edit_page(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.PUBLISHED)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert f'data-upload-url="{reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk})}"' in html
        assert f'data-image-url-base="{_teach_image_base()}"' in html
        assert "/classes/admin/images/" not in html

    def it_keeps_the_admin_edit_page_on_the_admin_endpoints(admin_user, client):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert f'data-upload-url="{reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk})}"' in html
        admin_base = reverse("classes:admin_class_image_delete", kwargs={"pk": 0}).removesuffix("0/delete/")
        assert f'data-image-url-base="{admin_base}"' in html
        assert f'data-upload-url="{reverse("classes:admin_class_hero_upload", kwargs={"pk": offering.pk})}"' in html


def describe_instructor_image_routes_validate_their_input():
    def it_rejects_an_oversize_hero_upload(instructor_fixture, client, settings):
        # The hero path never reaches full_clean(), so the model field's validate_image_size
        # does not run: the cap has to be enforced in the view or it is not enforced at all.
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024 * 1024
        offering = _own(instructor_fixture, Status.DRAFT)
        was = offering.image.name
        client.force_login(instructor_fixture.user)
        big = SimpleUploadedFile("big.png", b"\x89PNG" + b"\x00" * (1024 * 1024 + 1), content_type="image/png")
        resp = client.post(reverse("classes:teach_class_hero_upload", kwargs={"pk": offering.pk}), {"image": big})
        assert resp.status_code == 400
        assert "1 MB" in resp.json()["error"]
        offering.refresh_from_db()
        assert offering.image.name == was

    def it_rejects_an_oversize_gallery_upload(instructor_fixture, client, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024 * 1024
        offering = _own(instructor_fixture, Status.DRAFT)
        before = offering.gallery_images.count()
        client.force_login(instructor_fixture.user)
        big = SimpleUploadedFile("big.png", b"\x89PNG" + b"\x00" * (1024 * 1024 + 1), content_type="image/png")
        resp = client.post(reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk}), {"image": big})
        assert resp.status_code == 400
        assert offering.gallery_images.count() == before

    def it_rejects_alt_text_that_is_not_a_string(instructor_fixture, client):
        offering = _own(instructor_fixture, Status.DRAFT)
        image = ClassImageFactory(class_offering=offering, alt_text="kept")
        client.force_login(instructor_fixture.user)
        for payload in ({"alt_text": 42}, {"alt_text": None}, {"alt_text": ["a", "b"]}):
            resp = client.post(
                reverse("classes:teach_class_image_alt", kwargs={"pk": image.pk}),
                json.dumps(payload),
                content_type="application/json",
            )
            assert resp.status_code == 400, payload
        image.refresh_from_db()
        assert image.alt_text == "kept"


def describe_instructor_image_routes_follow_the_edit_pages_status_gate():
    """teach_class_edit bounces cancelled and archived classes to an admin; so do these."""

    @pytest.mark.parametrize("closed_status", [Status.CANCELLED, Status.ARCHIVED])
    def it_404s_every_route_on_a_closed_class(instructor_fixture, client, closed_status):
        offering = _own(instructor_fixture, Status.PUBLISHED)
        image = ClassImageFactory(class_offering=offering, alt_text="kept")
        offering.status = closed_status
        offering.save(update_fields=["status"])
        client.force_login(instructor_fixture.user)
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
                json.dumps({"alt_text": "changed"}),
                "application/json",
            ),
            (reverse("classes:teach_class_image_delete", kwargs={"pk": image.pk}), {}, None),
        ]
        for url, data, content_type in posts:
            resp = client.post(url, data, content_type=content_type) if content_type else client.post(url, data)
            assert resp.status_code == 404, url
        image.refresh_from_db()
        assert image.alt_text == "kept"
        assert ClassImage.objects.filter(pk=image.pk).exists()
