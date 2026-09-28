"""BDD-style tests for core.validators."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from core.validators import validate_image_content, validate_image_size, validate_wiki_upload


def describe_validate_image_size():
    def it_accepts_images_under_the_limit(settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024
        upload = SimpleUploadedFile("small.jpg", b"x" * 512, content_type="image/jpeg")

        assert validate_image_size(upload) is None

    def it_accepts_images_exactly_at_the_limit(settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024
        upload = SimpleUploadedFile("exact.jpg", b"x" * 1024, content_type="image/jpeg")

        assert validate_image_size(upload) is None

    def it_rejects_images_over_the_limit(settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024
        upload = SimpleUploadedFile("big.jpg", b"x" * 2048, content_type="image/jpeg")

        with pytest.raises(ValidationError) as exc_info:
            validate_image_size(upload)

        assert "MB or smaller" in str(exc_info.value)

    def it_accepts_objects_without_a_size_attribute():
        class Sizeless:
            name = "no_size.jpg"

        assert validate_image_size(Sizeless()) is None  # type: ignore[arg-type]


def _image_bytes(fmt: str) -> bytes:
    import core.images  # noqa: F401  registers the HEIF encoder the HEIC case needs

    buf = BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, fmt)
    return buf.getvalue()


def describe_validate_image_content():
    @pytest.mark.parametrize(
        ("name", "fmt", "content_type"),
        [
            ("a.png", "PNG", "image/png"),
            ("a.jpg", "JPEG", "image/jpeg"),
            ("a.webp", "WEBP", "image/webp"),
            ("a.heic", "HEIF", "image/heic"),
        ],
    )
    def it_accepts_a_real_image_and_leaves_it_rewound(name, fmt, content_type):
        data = _image_bytes(fmt)
        upload = SimpleUploadedFile(name, data, content_type=content_type)

        assert validate_image_content(upload) is None
        assert upload.read() == data

    def it_refuses_a_text_file():
        upload = SimpleUploadedFile("bad.txt", b"just some notes", content_type="text/plain")

        with pytest.raises(ValidationError, match="not a photo we can open"):
            validate_image_content(upload)

    def it_reads_the_bytes_not_the_name_or_declared_type():
        upload = SimpleUploadedFile("bad.png", b"just some notes", content_type="image/png")

        with pytest.raises(ValidationError, match="not a photo we can open"):
            validate_image_content(upload)


def describe_validate_wiki_upload():
    """One field carries both documents and phone photos, so this is the union of the
    two allowlists with each half sized against its own cap."""

    def it_accepts_a_document_under_the_document_cap(settings):
        settings.MAX_UPLOAD_DOCUMENT_BYTES = 1024
        upload = SimpleUploadedFile("manual.pdf", b"x" * 512, content_type="application/pdf")

        assert validate_wiki_upload(upload) is None

    def it_rejects_a_document_over_the_document_cap(settings):
        settings.MAX_UPLOAD_DOCUMENT_BYTES = 1024
        upload = SimpleUploadedFile("manual.pdf", b"x" * 2048, content_type="application/pdf")

        with pytest.raises(ValidationError) as exc_info:
            validate_wiki_upload(upload)

        assert "MB or smaller" in str(exc_info.value)

    def it_accepts_a_photo_a_member_took(settings):
        # validate_document alone would reject every one of these, which is the whole
        # reason this validator exists: the quick-photo flow writes an attachment.
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024
        upload = SimpleUploadedFile("shot.jpg", b"x" * 512, content_type="image/jpeg")

        assert validate_wiki_upload(upload) is None

    def it_accepts_a_phone_heic_photo(settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024
        upload = SimpleUploadedFile("IMG_0001.HEIC", b"x" * 512, content_type="image/heic")

        assert validate_wiki_upload(upload) is None

    def it_sizes_a_photo_against_the_image_cap(settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024
        settings.MAX_UPLOAD_DOCUMENT_BYTES = 10 * 1024 * 1024
        upload = SimpleUploadedFile("shot.jpg", b"x" * 2048, content_type="image/jpeg")

        with pytest.raises(ValidationError):
            validate_wiki_upload(upload)

    def it_rejects_an_unsupported_type():
        upload = SimpleUploadedFile("payload.exe", b"MZ", content_type="application/octet-stream")

        with pytest.raises(ValidationError) as exc_info:
            validate_wiki_upload(upload)

        assert "Unsupported file type" in str(exc_info.value)

    def it_lists_both_halves_of_the_allowlist_in_the_error():
        upload = SimpleUploadedFile("payload.exe", b"MZ", content_type="application/octet-stream")

        with pytest.raises(ValidationError) as exc_info:
            validate_wiki_upload(upload)

        message = str(exc_info.value)
        assert "pdf" in message
        assert "jpg" in message

    def it_rejects_a_file_with_no_extension():
        upload = SimpleUploadedFile("noextension", b"data", content_type="application/octet-stream")

        with pytest.raises(ValidationError):
            validate_wiki_upload(upload)

    def it_accepts_an_object_without_a_size_attribute():
        class Sizeless:
            name = "manual.pdf"

        assert validate_wiki_upload(Sizeless()) is None  # type: ignore[arg-type]
