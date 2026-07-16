"""BDD specs for the inline edit affordances on the public class detail page.

Admins and the owning instructor see edit controls (and, when the class falls
back to its category hero image, admins can edit that too). Guests see none.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import ClassOffering
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

# Smallest valid GIF — enough for an ImageField to accept and store.
_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _published_with_session(**kwargs):
    kwargs.setdefault("status", ClassOffering.Status.PUBLISHED)
    offering = ClassOfferingFactory(**kwargs)
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _detail(client, offering):
    return client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))


def describe_detail_edit_affordances():
    def it_offers_admins_class_and_category_editing(admin_user, client):
        # No class image, but the category has a hero — so the admin can edit both.
        category = CategoryFactory()
        category.hero_image = SimpleUploadedFile("hero.gif", _GIF, content_type="image/gif")
        category.save()
        offering = _published_with_session(slug="admin-detail", category=category, image="")

        client.force_login(admin_user)
        response = _detail(client, offering)

        assert response.status_code == 200
        assert response.context["is_admin"] is True
        assert response.context["can_edit_offering"] is True
        assert response.context["can_edit_category"] is True
        assert response.context["edit_url"] == reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})

    def it_offers_the_owning_instructor_class_editing(db, client):
        user = UserFactory(username="owner-instructor@example.com")
        instructor = InstructorFactory(user=user, instructor_slug="owner-instructor")
        offering = _published_with_session(slug="instructor-detail", instructor=instructor)

        client.force_login(user)
        response = _detail(client, offering)

        assert response.status_code == 200
        assert response.context["is_admin"] is False
        assert response.context["is_instructor"] is True
        assert response.context["edit_url"] == reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})

    def it_offers_guests_no_edit_controls(db, client):
        offering = _published_with_session(slug="guest-detail")
        response = _detail(client, offering)

        assert response.status_code == 200
        assert response.context["can_edit_offering"] is False
        assert response.context["can_edit_category"] is False
        assert response.context["edit_url"] is None

    def it_offers_the_guild_lead_of_the_categorys_guild_class_editing(db, client):
        # A plain member (no FOG role) who leads the category's guild — the
        # production case that used to be blocked. They don't instruct the class.
        lead_user = UserFactory(username="cat-guild-lead@example.com")
        guild = GuildFactory(guild_lead=lead_user.member)
        category = CategoryFactory(guild=guild)
        offering = _published_with_session(
            slug="guild-lead-detail",
            category=category,
            instructor=InstructorFactory(instructor_slug="not-the-lead"),
        )

        client.force_login(lead_user)
        response = _detail(client, offering)

        assert response.status_code == 200
        assert response.context["is_admin"] is False
        assert response.context["is_instructor"] is False
        assert response.context["can_edit_offering"] is True
        assert response.context["edit_url"] == reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})

    def it_hides_class_editing_from_a_lead_of_a_different_guild(db, client):
        lead_user = UserFactory(username="other-guild-lead@example.com")
        GuildFactory(guild_lead=lead_user.member)  # leads some other guild
        offering = _published_with_session(
            slug="foreign-guild-detail",
            category=CategoryFactory(guild=GuildFactory()),
        )

        client.force_login(lead_user)
        response = _detail(client, offering)

        assert response.status_code == 200
        assert response.context["can_edit_offering"] is False
        assert response.context["edit_url"] is None

    def it_hides_class_editing_from_a_plain_member(db, client):
        plain_user = UserFactory(username="plain-member@example.com")
        offering = _published_with_session(slug="plain-member-detail")

        client.force_login(plain_user)
        response = _detail(client, offering)

        assert response.status_code == 200
        assert response.context["can_edit_offering"] is False
        assert response.context["edit_url"] is None
