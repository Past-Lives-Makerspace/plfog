"""Image normalization helpers — resize, EXIF strip, format conversion.

Pure functions; no Django coupling beyond ContentFile so they can be unit
tested without DB setup. Pillow + pillow-heif do the work. HEIC support is
registered at import so ``PIL.Image.open()`` handles ``.heic`` transparently.
"""

from __future__ import annotations

import io
from pathlib import Path

import logging

from django.core.files.base import ContentFile
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
    if max(w, h) > max_long_edge:
        img.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    save_kwargs: dict = {"format": format, "optimize": True}
    if format in ("JPEG", "WEBP"):
        save_kwargs["quality"] = quality
    img.save(buffer, **save_kwargs)
    buffer.seek(0)
    base = Path(getattr(source, "name", "image")).stem or "image"
    ext = "jpg" if format == "JPEG" else format.lower()
    return ContentFile(buffer.read(), name=f"{base}.{ext}")


def normalize_field_if_uploaded(instance, field_name: str, max_long_edge: int) -> None:
    """If the named ImageField holds a fresh upload, replace it with a normalized version.

    Call from ``Model.save()`` BEFORE ``super().save()``. Detects fresh uploads
    via the Django convention that uncommitted field files have
    ``_committed == False``. Already-stored files (loaded from storage) keep
    ``_committed == True`` and are left alone, so re-saving an existing row
    does not re-normalize and re-encode the file.
    """
    file = getattr(instance, field_name)
    if not file:
        return
    # FieldFile: _committed=False means an UploadedFile was just assigned via
    # the form/descriptor and not yet written to storage. Already-stored files
    # have _committed=True. Raw UploadedFile (no descriptor) has no _committed
    # attribute at all and is always a fresh upload.
    is_raw_upload = isinstance(file, UploadedFile)
    is_uncommitted_field = hasattr(file, "_committed") and not file._committed
    if not (is_raw_upload or is_uncommitted_field):
        return
    try:
        new = normalize_image(file, max_long_edge=max_long_edge)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        # Pillow couldn't read the bytes (corrupt, unsupported format, or a
        # test using a header-only stub). Leave the field alone; the actual
        # ImageField/form validators will reject genuinely bad uploads.
        logger.warning("normalize_image skipped for %s.%s: %s", type(instance).__name__, field_name, exc)
        return
    setattr(instance, field_name, new)
