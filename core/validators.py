"""Reusable model-field validators."""

from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile


def validate_image_size(image: UploadedFile) -> None:
    """Reject uploaded images larger than ``settings.MAX_UPLOAD_IMAGE_BYTES``."""
    limit = settings.MAX_UPLOAD_IMAGE_BYTES
    size = getattr(image, "size", None)
    if size is None or size <= limit:
        return
    limit_mb = limit / (1024 * 1024)
    size_mb = size / (1024 * 1024)
    raise ValidationError(f"Image must be {limit_mb:.1f} MB or smaller (got {size_mb:.1f} MB).")


def validate_hex_color(value: str) -> None:
    """Reject a color that is not a six digit hex code with a leading #."""
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValidationError(f"Enter a six digit hex color like #092E4C (got '{value}').")


# Document upload allowlist — extensions accepted for guild meeting-note attachments.
ALLOWED_DOCUMENT_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "odt",
    "ppt",
    "pptx",
    "xls",
    "xlsx",
    "csv",
    "txt",
    "md",
    "rtf",
}


def validate_document(upload: UploadedFile) -> None:
    """Reject documents over the size cap or with a disallowed extension.

    Enforces ``settings.MAX_UPLOAD_DOCUMENT_BYTES`` and the
    ``ALLOWED_DOCUMENT_EXTENSIONS`` allowlist, mirroring ``validate_image_size``.
    """
    name = (getattr(upload, "name", "") or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))
        raise ValidationError(f"Unsupported file type '.{ext}'. Allowed: {allowed}.")
    limit = settings.MAX_UPLOAD_DOCUMENT_BYTES
    size = getattr(upload, "size", None)
    if size is not None and size > limit:
        raise ValidationError(f"File must be {limit / (1024 * 1024):.0f} MB or smaller.")


# Wiki attachments accept documents plus photos taken on a phone — a quick-photo
# upload has no image-specific field of its own (WikiAttachment.file is one FileField
# shared by both), so this allowlist is the union rather than a second field.
ALLOWED_WIKI_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic"}


def validate_wiki_upload(upload: UploadedFile) -> None:
    """Reject a wiki attachment over its size cap or with a disallowed extension.

    A wiki attachment is a document OR a photo through one field, so this validator
    is the union of :func:`validate_document`'s allowlist and the image extensions —
    each checked against its own size cap (``MAX_UPLOAD_IMAGE_BYTES`` for a photo,
    ``MAX_UPLOAD_DOCUMENT_BYTES`` for everything else). ``validate_document`` alone
    would reject every photo a member takes, since it carries no image extensions.
    """
    name = (getattr(upload, "name", "") or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in ALLOWED_WIKI_IMAGE_EXTENSIONS:
        validate_image_size(upload)
        return
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_DOCUMENT_EXTENSIONS | ALLOWED_WIKI_IMAGE_EXTENSIONS))
        raise ValidationError(f"Unsupported file type '.{ext}'. Allowed: {allowed}.")
    limit = settings.MAX_UPLOAD_DOCUMENT_BYTES
    size = getattr(upload, "size", None)
    if size is not None and size > limit:
        raise ValidationError(f"File must be {limit / (1024 * 1024):.0f} MB or smaller.")
