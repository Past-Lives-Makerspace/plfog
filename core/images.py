"""Image normalization helpers — resize, EXIF strip, format conversion.

Pure functions; no Django coupling beyond ContentFile so they can be unit
tested without DB setup. Pillow + pillow-heif do the work. HEIC support is
registered at import so ``PIL.Image.open()`` handles ``.heic`` transparently.
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False


def normalize_image(
    source,
    *,
    max_long_edge: int,
    format: str = "JPEG",
    quality: int = 85,
) -> ContentFile:
    """Return a fresh ContentFile with the image normalized.

    Applies EXIF rotation, strips EXIF, converts to RGB (handles RGBA/PNG by
    flattening over white), downscales so the longest edge fits within
    ``max_long_edge`` (smaller images pass through untouched), and re-encodes
    to ``format`` at ``quality``. The returned file's name preserves the
    source basename with the new extension.
    """
    return _normalize(source, max_long_edge=max_long_edge, format=format, quality=quality)[0]


def _normalize(
    source,
    *,
    max_long_edge: int,
    format: str = "JPEG",
    quality: int = 85,
) -> tuple[ContentFile, float]:
    """:func:`normalize_image`, plus the factor the pixel dimensions were multiplied by.

    The factor is ``1.0`` when the image already fit within ``max_long_edge`` and
    below ``1.0`` when it was downscaled, so a caller holding pixel coordinates
    measured on the original (a hero crop box) can scale them the same way.
    """
    if hasattr(source, "seek"):
        source.seek(0)
    opened = Image.open(source)
    img: Image.Image = ImageOps.exif_transpose(opened) or opened
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    scale = 1.0
    if max(w, h) > max_long_edge:
        img.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
        scale = max(img.size) / max(w, h)
    buffer = io.BytesIO()
    save_kwargs: dict = {"format": format, "optimize": True}
    if format in ("JPEG", "WEBP"):
        save_kwargs["quality"] = quality
    img.save(buffer, **save_kwargs)
    buffer.seek(0)
    base = Path(getattr(source, "name", "image")).stem or "image"
    ext = "jpg" if format == "JPEG" else format.lower()
    return ContentFile(buffer.read(), name=f"{base}.{ext}"), scale


def normalize_field_if_uploaded(instance, field_name: str, max_long_edge: int) -> float:
    """If the named ImageField holds a fresh upload, replace it with a normalized version.

    Call from ``Model.save()`` BEFORE ``super().save()``. Detects fresh uploads
    via the Django convention that uncommitted field files have
    ``_committed == False``. Already-stored files (loaded from storage) keep
    ``_committed == True`` and are left alone, so re-saving an existing row
    does not re-normalize and re-encode the file.

    Returns the factor the image's pixel dimensions were multiplied by: ``1.0``
    when the file was left alone (already stored, unreadable, or within the
    cap) and below ``1.0`` when a fresh upload was downscaled to fit
    ``max_long_edge``. A caller that stores pixel coordinates measured on the
    original file (the hero crop box) scales them by it.
    """
    file = getattr(instance, field_name)
    if not file:
        return 1.0
    # FieldFile: _committed=False means an UploadedFile was just assigned via
    # the form/descriptor and not yet written to storage. Already-stored files
    # have _committed=True. Raw UploadedFile (no descriptor) has no _committed
    # attribute at all and is always a fresh upload.
    is_raw_upload = isinstance(file, UploadedFile)
    is_uncommitted_field = hasattr(file, "_committed") and not file._committed
    if not (is_raw_upload or is_uncommitted_field):
        return 1.0
    try:
        new, scale = _normalize(file, max_long_edge=max_long_edge)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        # Pillow couldn't read the bytes (corrupt, unsupported format, or a
        # test using a header-only stub). Leave the field alone. A form
        # ImageField, or core.validators.validate_image_content on the routes
        # that save without a form, is what refuses such a file; a caller
        # that does neither saves it as it came.
        logger.warning("normalize_image skipped for %s.%s: %s", type(instance).__name__, field_name, exc)
        return 1.0
    setattr(instance, field_name, new)
    return scale


def cut_hero_crop(source_bytes: bytes, box: tuple[int, int, int, int], *, stem: str) -> ContentFile | None:
    """Cut ``box`` (``x, y, w, h`` in source pixels) out of an image's bytes, ready to store.

    The composer stores the box as pixels on the uploaded file, so this cuts exactly that
    rectangle (clamped to the image's edges) after applying the EXIF orientation, keeps the
    source format (JPEG at quality 88, PNG as is, anything else as JPEG) and names the result
    ``<stem>-crop.<ext>``. Shared by every model with a cropped hero copy so the cut can never
    drift between them; the model decides what to do with the file.

    Args:
        source_bytes: The whole source image.
        box: The crop box's left, top, width and height in source pixels.
        stem: The file name without its extension (the source's stem).

    Returns:
        The cut copy, or None when the box lies off the image (nothing to cut).

    Raises:
        UnidentifiedImageError, OSError, ValueError: Pillow could not read ``source_bytes``.
    """
    opened = Image.open(io.BytesIO(source_bytes))
    source_format = opened.format
    img: Image.Image = ImageOps.exif_transpose(opened) or opened
    src_w, src_h = img.size
    x, y, w, h = box
    left = min(x, src_w)
    top = min(y, src_h)
    right = min(left + w, src_w)
    bottom = min(top + h, src_h)
    if right <= left or bottom <= top:
        return None
    cropped = img.crop((left, top, right, bottom))
    buffer = io.BytesIO()
    if source_format == "PNG":
        cropped.save(buffer, format="PNG")
        ext = "png"
    else:
        if cropped.mode != "RGB":
            cropped = cropped.convert("RGB")
        cropped.save(buffer, format="JPEG", quality=88, optimize=True)
        ext = "jpg"
    return ContentFile(buffer.getvalue(), name=f"{stem}-crop.{ext}")


def image_upload_refusal(file: UploadedFile) -> str | None:
    """Why an upload to a photo route is refused, or None when it may be stored.

    The byte cap first (``MAX_UPLOAD_IMAGE_BYTES``), then whether Pillow can open the bytes.
    One answer for every hero and gallery route, because none of them builds a form: the
    model field's ``validate_image_size`` runs only under ``full_clean()``, which a hero save
    never reaches, and the field never reads the bytes, so without this a text file would be
    stored as a photo.
    """
    # Lazy: core.validators imports this module at load time to register the HEIC opener.
    from core.validators import validate_image_content

    assert file.size is not None  # an uploaded file always reports its size
    if file.size > settings.MAX_UPLOAD_IMAGE_BYTES:
        limit_mb = settings.MAX_UPLOAD_IMAGE_BYTES / (1024 * 1024)
        return f"Image must be {limit_mb:.0f} MB or smaller."
    try:
        validate_image_content(file)
    except ValidationError as exc:
        return exc.messages[0]
    return None


_CONTENT_ADDRESSED_RE = re.compile(r"^[0-9a-f]{64}\.[A-Za-z0-9]+$")


def content_addressed_name(content: bytes, *, prefix: str, ext: str = "jpg") -> str:
    """Return the deterministic storage key for ``content``.

    Args:
        content: The exact bytes that will be stored.
        prefix: Storage folder including its trailing slash (e.g. ``classes/images/``).
        ext: File extension without the leading dot.

    Returns:
        ``<prefix><sha256-hex>.<ext>``.
    """
    return f"{prefix}{hashlib.sha256(content).hexdigest()}.{ext}"


def is_content_addressed(name: str, *, prefix: str) -> bool:
    """Whether ``name`` is already a key produced by :func:`content_addressed_name`."""
    if not name.startswith(prefix):
        return False
    return bool(_CONTENT_ADDRESSED_RE.match(name[len(prefix) :]))


def store_content_addressed(content: bytes, *, prefix: str, ext: str = "jpg") -> str:
    """Store ``content`` under a key derived from its SHA-256 digest.

    Identical bytes always map to the same key, so rows carrying the same picture
    share a single stored object. Without this the default storage backend
    (``file_overwrite=False`` on R2) appends a random suffix on every name
    collision and each row gets its own copy of the same image.

    Storing bytes that are already present is a no-op, which is what makes the
    callers idempotent and safe to resume after an interruption.

    Args:
        content: The bytes to store.
        prefix: Storage folder including its trailing slash.
        ext: File extension without the leading dot.

    Returns:
        The storage key the content is available under.
    """
    name = content_addressed_name(content, prefix=prefix, ext=ext)
    if default_storage.exists(name):
        return name
    # A concurrent writer winning the race would make the backend append a
    # suffix, so trust whatever key storage actually used.
    return default_storage.save(name, ContentFile(content))
