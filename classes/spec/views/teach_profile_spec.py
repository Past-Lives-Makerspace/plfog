"""BDD specs for the teaching portal's Instructor Profile tab.

The tab used to be a signpost pointing at Profile settings. It now edits the three things the
public instructor page actually shows — photo, bio, and the links flagged for that page — while
the settings page keeps editing the same fields from its own side.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from classes.factories import InstructorFactory, UserFactory
from membership.models import MemberContact

# A real 2x2 PNG: small enough to pass the size validator, real enough to pass Pillow's sniff.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000020802000000fdd49a73"
    "0000001649444154789c633c51a1c1c0c0c0c4c0c0c0c0c0000011aa016c79c676"
    "260000000049454e44ae426082"
)


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


def _contact(member, **kwargs):
    defaults = {"label": "Website", "value": "https://example.test", "show_on_instructor_page": True}
    return MemberContact.objects.create(member=member, **{**defaults, **kwargs})


def _formset_data(prefix="contacts", total=0, initial=0):
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def describe_teach_profile():
    def describe_the_page():
        def it_renders_the_editable_fields(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            html = client.get(reverse("classes:teach_profile")).content.decode()
            assert 'name="instructor_bio"' in html
            assert 'name="profile_photo"' in html
            assert "Your Public Instructor Page" in html

        def it_keeps_the_link_across_to_profile_settings(instructor_fixture, client):
            """This is a second door onto the fields, not a move — settings still owns the rest."""
            client.force_login(instructor_fixture.user)
            html = client.get(reverse("classes:teach_profile")).content.decode()
            assert f"{reverse('hub_user_settings')}?tab=profile" in html

        def it_lists_only_the_links_flagged_for_the_instructor_page(instructor_fixture, client):
            _contact(instructor_fixture, label="On My Page", show_on_instructor_page=True)
            _contact(instructor_fixture, label="Directory Only", show_on_instructor_page=False)
            client.force_login(instructor_fixture.user)
            html = client.get(reverse("classes:teach_profile")).content.decode()
            assert "On My Page" in html
            assert "Directory Only" not in html

        def it_says_so_when_there_are_no_links_yet(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            html = client.get(reverse("classes:teach_profile")).content.decode()
            assert "No links yet." in html

    def describe_saving():
        def it_saves_the_bio(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            response = client.post(
                reverse("classes:teach_profile"),
                data={"instructor_bio": "I throw pots.", **_formset_data()},
            )
            assert response.status_code == 302
            instructor_fixture.refresh_from_db()
            assert instructor_fixture.instructor_bio == "I throw pots."

        def it_saves_the_photo(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            photo = SimpleUploadedFile("me.png", _TINY_PNG, content_type="image/png")
            client.post(
                reverse("classes:teach_profile"),
                data={"instructor_bio": "Hi.", "profile_photo": photo, **_formset_data()},
            )
            instructor_fixture.refresh_from_db()
            assert instructor_fixture.profile_photo

        def it_adds_a_link(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            client.post(
                reverse("classes:teach_profile"),
                data={
                    "instructor_bio": "",
                    **_formset_data(total=1),
                    "contacts-0-id": "",
                    "contacts-0-label": "Shop",
                    "contacts-0-value": "https://shop.example.test",
                    "contacts-0-kind": "website",
                    "contacts-0-sort_order": "0",
                    "contacts-0-show_on_instructor_page": "on",
                },
            )
            contact = MemberContact.objects.get(member=instructor_fixture)
            assert contact.label == "Shop"
            assert contact.show_on_instructor_page is True

        def it_deletes_a_link(instructor_fixture, client):
            contact = _contact(instructor_fixture, label="Old Site")
            client.force_login(instructor_fixture.user)
            client.post(
                reverse("classes:teach_profile"),
                data={
                    "instructor_bio": "",
                    **_formset_data(total=1, initial=1),
                    "contacts-0-id": str(contact.pk),
                    "contacts-0-label": "Old Site",
                    "contacts-0-value": "https://example.test",
                    "contacts-0-kind": "website",
                    "contacts-0-sort_order": "0",
                    "contacts-0-show_on_instructor_page": "on",
                    "contacts-0-DELETE": "on",
                },
            )
            assert not MemberContact.objects.filter(pk=contact.pk).exists()

    def describe_when_the_photo_is_rejected():
        def it_keeps_the_bio_and_flags_only_the_photo(instructor_fixture, client, settings):
            """A 9 MB photo must not cost the instructor the bio they just wrote."""
            settings.MAX_UPLOAD_IMAGE_BYTES = 10
            client.force_login(instructor_fixture.user)
            photo = SimpleUploadedFile("huge.png", _TINY_PNG, content_type="image/png")
            response = client.post(
                reverse("classes:teach_profile"),
                data={"instructor_bio": "Kept this.", "profile_photo": photo, **_formset_data()},
                follow=True,
            )
            instructor_fixture.refresh_from_db()
            assert instructor_fixture.instructor_bio == "Kept this."
            assert not instructor_fixture.profile_photo
            messages = [str(m) for m in response.context["messages"]]
            assert any("photo wasn't" in m for m in messages)

    def describe_when_the_photo_is_rejected_and_a_link_is_invalid():
        def it_saves_nothing_and_re_renders(instructor_fixture, client, settings):
            """Both halves have to be good before the photo-rescue path runs.

            The rescue writes the bio while dropping the photo; doing that with a broken link
            row would save half a page the instructor never got to fix.
            """
            settings.MAX_UPLOAD_IMAGE_BYTES = 10
            client.force_login(instructor_fixture.user)
            photo = SimpleUploadedFile("huge.png", _TINY_PNG, content_type="image/png")
            response = client.post(
                reverse("classes:teach_profile"),
                data={
                    "instructor_bio": "Should not stick.",
                    "profile_photo": photo,
                    **_formset_data(total=1),
                    "contacts-0-id": "",
                    "contacts-0-label": "",
                    "contacts-0-value": "https://shop.example.test",
                    "contacts-0-kind": "website",
                    "contacts-0-sort_order": "0",
                },
            )
            assert response.status_code == 200
            instructor_fixture.refresh_from_db()
            assert instructor_fixture.instructor_bio == ""
            assert not instructor_fixture.profile_photo
            assert not MemberContact.objects.filter(member=instructor_fixture).exists()

    def describe_deleting_the_photo():
        def it_returns_to_the_instructor_profile_tab(instructor_fixture, client):
            """The shared delete endpoint used to eject them onto the settings page."""
            instructor_fixture.profile_photo = SimpleUploadedFile("me.png", _TINY_PNG, content_type="image/png")
            instructor_fixture.save()
            client.force_login(instructor_fixture.user)
            response = client.post(reverse("hub_profile_photo_delete"), data={"next": reverse("classes:teach_profile")})
            assert response.status_code == 302
            assert response["Location"] == reverse("classes:teach_profile")
            instructor_fixture.refresh_from_db()
            assert not instructor_fixture.profile_photo

        def it_posts_the_tab_as_the_return_target(instructor_fixture, client):
            instructor_fixture.profile_photo = SimpleUploadedFile("me.png", _TINY_PNG, content_type="image/png")
            instructor_fixture.save()
            client.force_login(instructor_fixture.user)
            html = client.get(reverse("classes:teach_profile")).content.decode()
            assert f'name="next" value="{reverse("classes:teach_profile")}"' in html

        def it_offers_no_delete_control_when_there_is_no_photo(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            html = client.get(reverse("classes:teach_profile")).content.decode()
            assert "delete-instructor-photo" not in html

    def describe_when_a_link_is_invalid():
        def it_re_renders_without_saving_the_bio(instructor_fixture, client):
            """A blank label on a link is a form error, so nothing on the page is written."""
            client.force_login(instructor_fixture.user)
            response = client.post(
                reverse("classes:teach_profile"),
                data={
                    "instructor_bio": "Should not stick.",
                    **_formset_data(total=1),
                    "contacts-0-id": "",
                    "contacts-0-label": "",
                    "contacts-0-value": "https://shop.example.test",
                    "contacts-0-kind": "website",
                    "contacts-0-sort_order": "0",
                },
            )
            assert response.status_code == 200
            instructor_fixture.refresh_from_db()
            assert instructor_fixture.instructor_bio == ""
