"""BDD specs for the editor-gated class QR download (SVG default + PNG)."""

from __future__ import annotations

from django.urls import reverse


def describe_class_qr_download():
    def it_serves_a_scalable_svg_to_an_admin(admin_user, client, free_offering, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:class_qr", args=[free_offering.pk, "svg"]))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/svg+xml"
        assert resp["Content-Disposition"] == f'attachment; filename="{free_offering.slug}-qr.svg"'
        assert b"<svg" in resp.content
        assert b"viewBox" in resp.content

    def it_serves_a_png_to_an_admin(admin_user, client, free_offering, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:class_qr", args=[free_offering.pk, "png"]))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"
        assert resp["Content-Disposition"] == f'attachment; filename="{free_offering.slug}-qr.png"'
        assert resp.content.startswith(b"\x89PNG")

    def it_forbids_a_non_editor_member(member_user, client, free_offering, db):
        client.force_login(member_user)
        resp = client.get(reverse("classes:class_qr", args=[free_offering.pk, "svg"]))
        assert resp.status_code == 403

    def it_404s_an_unknown_format(admin_user, client, free_offering, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:class_qr", args=[free_offering.pk, "gif"]))
        assert resp.status_code == 404


def describe_class_permalink():
    def it_redirects_to_the_current_public_page(free_offering, client, db):
        resp = client.get(reverse("classes:class_permalink", args=[free_offering.pk]))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:public_class_detail", args=[free_offering.slug])

    def it_follows_a_slug_change_so_a_printed_qr_survives(free_offering, client, db):
        permalink = reverse("classes:class_permalink", args=[free_offering.pk])
        free_offering.slug = "renamed-demo"
        free_offering.save(update_fields=["slug"])
        resp = client.get(permalink)
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:public_class_detail", args=["renamed-demo"])
