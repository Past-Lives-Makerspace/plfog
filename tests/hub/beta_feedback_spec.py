"""BDD specs for beta feedback form and view."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils.datastructures import MultiValueDict
from PIL import Image

from django.contrib.auth.models import User

from core.models import FeedbackRequest
from hub.forms import MAX_FEEDBACK_PHOTOS, BetaFeedbackForm
from membership.models import Member
from tests.core.factories import FeedbackRequestFactory

VALID_DATA = {"category": "bug", "subject": "Broken page", "message": "Something is wrong"}


def _photo(name: str = "shot.png") -> SimpleUploadedFile:
    """A tiny but real PNG wrapped as an upload."""
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _form_with_photos(photos: list[SimpleUploadedFile]) -> BetaFeedbackForm:
    return BetaFeedbackForm(dict(VALID_DATA), MultiValueDict({"photos": photos}))


def describe_BetaFeedbackForm():
    def it_accepts_valid_data():
        form = BetaFeedbackForm({"category": "bug", "subject": "Broken page", "message": "Something is wrong"})

        assert form.is_valid()

    def it_requires_category():
        form = BetaFeedbackForm({"category": "", "subject": "Test", "message": "Test"})

        assert not form.is_valid()
        assert "category" in form.errors

    def it_requires_subject():
        form = BetaFeedbackForm({"category": "bug", "subject": "", "message": "Test"})

        assert not form.is_valid()
        assert "subject" in form.errors

    def it_requires_message():
        form = BetaFeedbackForm({"category": "bug", "subject": "Test", "message": ""})

        assert not form.is_valid()
        assert "message" in form.errors

    def it_rejects_invalid_category():
        form = BetaFeedbackForm({"category": "invalid", "subject": "Test", "message": "Test"})

        assert not form.is_valid()
        assert "category" in form.errors

    def it_accepts_all_valid_categories():
        for value, _label in BetaFeedbackForm.CATEGORY_CHOICES:
            form = BetaFeedbackForm({"category": value, "subject": "Test", "message": "Test"})
            assert form.is_valid(), f"Category '{value}' should be valid"

    def describe_photos():
        def it_is_valid_with_no_photos():
            form = BetaFeedbackForm(dict(VALID_DATA))

            assert form.is_valid()
            assert form.cleaned_data["photos"] == []

        def it_accepts_one_photo():
            form = _form_with_photos([_photo()])

            assert form.is_valid()
            assert len(form.cleaned_data["photos"]) == 1

        def it_accepts_five_photos():
            form = _form_with_photos([_photo(f"shot{n}.png") for n in range(MAX_FEEDBACK_PHOTOS)])

            assert form.is_valid()
            assert len(form.cleaned_data["photos"]) == MAX_FEEDBACK_PHOTOS

        def it_rejects_six_photos():
            form = _form_with_photos([_photo(f"shot{n}.png") for n in range(MAX_FEEDBACK_PHOTOS + 1)])

            assert not form.is_valid()
            assert "photos" in form.errors
            assert str(MAX_FEEDBACK_PHOTOS) in form.errors["photos"][0]

        def it_rejects_a_non_image_file():
            fake = SimpleUploadedFile("notes.txt", b"just some text", content_type="text/plain")
            form = _form_with_photos([fake])

            assert not form.is_valid()
            assert "photos" in form.errors

        def it_names_every_bad_file_in_one_round_trip():
            bad_one = SimpleUploadedFile("notes.txt", b"just some text", content_type="text/plain")
            bad_two = SimpleUploadedFile("recording.mp4", b"not an image either", content_type="video/mp4")
            form = _form_with_photos([bad_one, _photo("fine.png"), bad_two])

            assert not form.is_valid()
            joined = " ".join(form.errors["photos"])
            assert "notes.txt:" in joined
            assert "recording.mp4:" in joined
            assert "fine.png" not in joined

        def it_derives_help_text_from_the_real_caps():
            with override_settings(MAX_UPLOAD_IMAGE_BYTES=3 * 1024 * 1024):
                form = BetaFeedbackForm()

            assert "3 MB each, 15 MB total" in form.fields["photos"].help_text

        def it_rejects_an_oversize_photo():
            with override_settings(MAX_UPLOAD_IMAGE_BYTES=10):
                form = _form_with_photos([_photo()])  # a real PNG is comfortably over 10 bytes

                assert not form.is_valid()
            assert "photos" in form.errors

        def it_rejects_photos_over_the_combined_cap(monkeypatch: pytest.MonkeyPatch):
            monkeypatch.setattr("hub.forms.MAX_FEEDBACK_PHOTO_TOTAL_BYTES", 100)
            form = _form_with_photos([_photo("a.png"), _photo("b.png")])  # each passes solo, together > 100 bytes

            assert not form.is_valid()
            assert "photos" in form.errors
            assert "total" in form.errors["photos"][0]

    def describe_send():
        def it_attaches_photos_and_notes_the_count(db):
            user = User.objects.create_user(username="attacher", password="pass", email="attacher@example.com")
            form = _form_with_photos([_photo("first.png"), _photo("second.png")])
            assert form.is_valid()

            form.submit(user=user)

            assert len(mail.outbox) == 1
            sent = mail.outbox[0]
            assert "Photos attached: 2" in sent.body
            assert len(sent.attachments) == 2
            filenames = [attachment[0] for attachment in sent.attachments]
            assert filenames == ["first.png", "second.png"]
            for _name, content, mimetype in sent.attachments:
                assert content  # the bytes actually made it in
                assert mimetype == "image/png"

        def it_sends_without_attachments_when_no_photos(db):
            user = User.objects.create_user(username="plain", password="pass", email="plain@example.com")
            form = BetaFeedbackForm(dict(VALID_DATA))
            assert form.is_valid()

            form.submit(user=user)

            assert len(mail.outbox) == 1
            sent = mail.outbox[0]
            assert sent.attachments == []
            assert "Photos attached" not in sent.body


@pytest.mark.django_db
def describe_beta_feedback_view():
    def it_requires_login(client: Client):
        response = client.get("/feedback/")

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_renders_feedback_form(client: Client):
        User.objects.create_user(username="feedbacker", password="pass")
        client.login(username="feedbacker", password="pass")

        response = client.get("/feedback/")

        assert response.status_code == 200
        assert isinstance(response.context["form"], BetaFeedbackForm)

    def it_sends_email_on_valid_post(client: Client):
        User.objects.create_user(username="reporter", password="pass", email="reporter@example.com")
        client.login(username="reporter", password="pass")

        response = client.post(
            "/feedback/",
            {"category": "bug", "subject": "Button broken", "message": "The submit button does not work"},
        )

        assert response.status_code == 302
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert "[Bug Report]" in sent.subject
        assert "Button broken" in sent.subject
        assert "reporter@example.com" in sent.body
        assert "The submit button does not work" in sent.body

        # Decision 8: the send is now audited through the choke-point.
        from core.models import TransactionalEmailLog

        log = TransactionalEmailLog.objects.get()
        assert log.trigger_kind == "hub.beta_feedback"
        assert log.status == TransactionalEmailLog.Status.SENT

    def it_shows_success_message_on_valid_post(client: Client):
        User.objects.create_user(username="msguser", password="pass")
        client.login(username="msguser", password="pass")

        response = client.post(
            "/feedback/",
            {"category": "feature", "subject": "Add dark mode", "message": "Would be nice"},
            follow=True,
        )

        assert response.status_code == 200
        messages_list = list(response.context["messages"])
        assert len(messages_list) == 1
        assert str(messages_list[0]) == "Thanks! You can follow it below."

    def it_renders_the_multipart_enctype(client: Client):
        User.objects.create_user(username="enctyper", password="pass")
        client.login(username="enctyper", password="pass")

        response = client.get("/feedback/")

        assert response.status_code == 200
        assert b'enctype="multipart/form-data"' in response.content
        assert b'accept="image/*"' in response.content

    def it_accepts_a_photo_upload(client: Client):
        User.objects.create_user(username="shutterbug", password="pass", email="shutterbug@example.com")
        client.login(username="shutterbug", password="pass")

        response = client.post(
            "/feedback/",
            {"category": "bug", "subject": "See screenshot", "message": "Attached", "photos": _photo("bug.png")},
        )

        assert response.status_code == 302
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert len(sent.attachments) == 1
        assert sent.attachments[0][0] == "bug.png"
        assert "Photos attached: 1" in sent.body

    def it_re_renders_form_on_invalid_post(client: Client):
        User.objects.create_user(username="badpost", password="pass")
        client.login(username="badpost", password="pass")

        response = client.post("/feedback/", {"category": "bug", "subject": "", "message": ""})

        assert response.status_code == 200
        assert response.context["form"].errors
        assert len(mail.outbox) == 0

    def it_keeps_typed_text_when_a_photo_is_rejected(client: Client):
        User.objects.create_user(username="retyper", password="pass")
        client.login(username="retyper", password="pass")
        fake = SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain")

        response = client.post(
            "/feedback/",
            {"category": "bug", "subject": "My subject stays", "message": "My message stays", "photos": fake},
        )

        assert response.status_code == 200
        assert "photos" in response.context["form"].errors
        assert b"My subject stays" in response.content
        assert b"My message stays" in response.content
        assert len(mail.outbox) == 0


@pytest.mark.django_db
def describe_saving_a_request():
    def it_saves_one_received_request_with_sender_and_photos(client: Client):
        user = User.objects.create_user(username="saver", password="pass", email="saver@example.com")
        client.login(username="saver", password="pass")

        client.post(
            "/feedback/",
            {"category": "feature", "subject": "Kiln log", "message": "Track firings", "photos": _photo("kiln.png")},
        )

        request = FeedbackRequest.objects.get()
        assert (request.user, request.category, request.subject, request.message) == (
            user,
            "feature",
            "Kiln log",
            "Track firings",
        )
        assert request.status == FeedbackRequest.Status.RECEIVED
        assert request.photos.count() == 1

    def it_links_the_saved_request_in_the_email_to_the_admins(client: Client, settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        User.objects.create_user(username="linker", password="pass", email="linker@example.com")
        client.login(username="linker", password="pass")

        client.post("/feedback/", dict(VALID_DATA))

        request = FeedbackRequest.objects.get()
        assert f"In the Feedback inbox: https://members.example/manage/feedback/{request.pk}/" in mail.outbox[0].body

    def it_lands_back_with_the_new_request_open(client: Client):
        User.objects.create_user(username="lander", password="pass")
        client.login(username="lander", password="pass")

        response = client.post("/feedback/", dict(VALID_DATA))

        request = FeedbackRequest.objects.get()
        assert response["Location"] == f"/feedback/?sent={request.pk}#request-{request.pk}"
        body = client.get(response["Location"]).content.decode()
        assert f'id="request-{request.pk}" data-feedback-request="{request.pk}" open' in body

    def it_saves_nothing_on_an_invalid_post(client: Client):
        User.objects.create_user(username="nothing", password="pass")
        client.login(username="nothing", password="pass")

        client.post("/feedback/", {"category": "bug", "subject": "", "message": ""})

        assert not FeedbackRequest.objects.exists()

    def it_lets_an_account_with_no_membership_send_and_follow(client: Client):
        user = User.objects.create_user(username="nomember", password="pass", email="nomember@example.com")
        Member.objects.filter(user=user).delete()
        client.login(username="nomember", password="pass")

        client.post("/feedback/", {"category": "bug", "subject": "Unlinked report", "message": "Hi"})

        request = FeedbackRequest.objects.get(user=user)
        body = client.get("/feedback/").content.decode()
        assert f'data-feedback-request="{request.pk}"' in body


@pytest.mark.django_db
def describe_your_requests():
    def _login(client: Client, username: str) -> User:
        user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
        client.login(username=username, password="pass")
        return user

    def it_says_nothing_is_sent_yet_when_empty(client: Client):
        _login(client, "empty")

        body = client.get("/feedback/").content.decode()

        assert "data-feedback-requests-empty" in body

    def it_lists_only_the_viewers_requests_newest_first(client: Client):
        user = _login(client, "lister")
        older = FeedbackRequestFactory(user=user, subject="Zzolder request")
        newer = FeedbackRequestFactory(user=user, subject="Zznewer request")
        other = FeedbackRequestFactory(subject="Zzsomeone else")
        FeedbackRequest.objects.filter(pk=older.pk).update(created_at=older.created_at.replace(year=2025))

        response = client.get("/feedback/")

        assert list(response.context["feedback_requests"]) == [newer, older]
        body = response.content.decode()
        assert body.index("Zznewer request") < body.index("Zzolder request")
        assert f'data-feedback-request="{other.pk}"' not in body

    def it_shows_status_last_changed_date_and_the_staff_note(client: Client):
        user = _login(client, "detail")
        FeedbackRequestFactory(user=user, status=FeedbackRequest.Status.PLANNED, staff_note="Zznote from the admins")

        body = client.get("/feedback/").content.decode()

        assert 'data-feedback-status="planned">Planned<' in body
        assert "Zznote from the admins" in body
        assert "Updated " in body

    def it_shows_fixed_for_a_bug_and_live_for_a_feature(client: Client):
        user = _login(client, "fixer")
        FeedbackRequestFactory(user=user, category="bug", status=FeedbackRequest.Status.LIVE)
        FeedbackRequestFactory(user=user, category="feature", status=FeedbackRequest.Status.LIVE)

        body = client.get("/feedback/").content.decode()

        assert 'data-feedback-status="live">Fixed<' in body
        assert 'data-feedback-status="live">Live<' in body

    def it_never_shows_the_github_issue_link(client: Client):
        user = _login(client, "nogithub")
        FeedbackRequestFactory(user=user, github_issue_url="https://github.com/o/r/issues/987654")

        body = client.get("/feedback/").content.decode()

        assert "issues/987654" not in body

    def it_shows_the_photos_sent_with_a_request(client: Client):
        user = _login(client, "photos")
        request = FeedbackRequest.submit(user=user, category="bug", subject="s", message="m", photos=[_photo()])

        body = client.get("/feedback/").content.decode()

        assert request.photos.get().image.url in body

    def it_ignores_a_sent_value_that_is_not_a_number(client: Client):
        _login(client, "badsent")

        response = client.get("/feedback/?sent=abc")

        assert response.context["open_request_pk"] is None


@pytest.mark.django_db
def describe_beta_feedback_category_preselect():
    """The Spotlight's Suggest a feature and Report a bug link here with ``?category=`` (#709)."""

    @pytest.mark.parametrize("category", ["feature", "bug"])
    def it_preselects_the_category_from_the_query_string(client: Client, category: str):
        User.objects.create_user(username=f"asker-{category}", password="pass")
        client.login(username=f"asker-{category}", password="pass")

        response = client.get(f"/feedback/?category={category}")

        assert response.context["form"]["category"].value() == category
        assert f'<option value="{category}" selected>' in response.content.decode()

    def it_ignores_a_category_that_does_not_exist(client: Client):
        User.objects.create_user(username="guesser", password="pass")
        client.login(username="guesser", password="pass")

        response = client.get("/feedback/?category=zorblax")

        assert response.context["form"]["category"].value() is None

    def it_builds_an_unbound_form_with_the_category_chosen():
        form = BetaFeedbackForm.preselected("feature")

        assert not form.is_bound
        assert form.initial == {"category": "feature"}
