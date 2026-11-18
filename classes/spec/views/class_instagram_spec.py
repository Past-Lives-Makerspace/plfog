"""BDD specs for the Instagram story and post downloads of a class (#1254).

The publish lock, the retired states, the admin draft override, view-as and the stranger's
refusal are held for these two downloads beside the flyer and QR in class_flyer_spec.py's
ARTIFACTS, so the four marketing downloads cannot drift apart.
"""

from __future__ import annotations

import io

import pytest
from django.urls import reverse
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering


def describe_class_instagram_download():
    @pytest.mark.parametrize(("fmt", "size"), [("story", (1080, 1920)), ("post", (1080, 1350))])
    def it_downloads_the_png_of_a_live_class_to_its_instructor(fmt, size, member_user, client, db):
        offering = ClassOfferingFactory(
            slug="glaze-lab", instructor=member_user.member, status=ClassOffering.Status.PUBLISHED
        )
        client.force_login(member_user)
        resp = client.get(reverse("classes:class_instagram", args=[offering.pk, fmt]))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"
        assert resp["Content-Disposition"] == f'attachment; filename="glaze-lab-instagram-{fmt}.png"'
        assert Image.open(io.BytesIO(resp.content)).size == size

    def it_downloads_a_class_with_no_photo(member_user, client, db):
        offering = ClassOfferingFactory(
            image="", legacy_image_url="", instructor=member_user.member, status=ClassOffering.Status.PUBLISHED
        )
        client.force_login(member_user)
        resp = client.get(reverse("classes:class_instagram", args=[offering.pk, "story"]))
        assert resp.status_code == 200
        assert Image.open(io.BytesIO(resp.content)).size == (1080, 1920)

    def it_404s_an_unknown_format(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:class_instagram", args=[ClassOfferingFactory().pk, "reel"]))
        assert resp.status_code == 404

    def it_404s_an_unknown_class(admin_user, client, db):
        client.force_login(admin_user)
        assert client.get(reverse("classes:class_instagram", args=[999999, "post"])).status_code == 404
