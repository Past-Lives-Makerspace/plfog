"""BDD-style tests for core.images.normalize_image."""

from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from core.images import normalize_field_if_uploaded, normalize_image


def _png_bytes(size: tuple[int, int], mode: str = "RGB", color=(255, 0, 0)) -> bytes:
    """Build a real PNG of the given size in-memory."""
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpg_with_exif_orientation(size: tuple[int, int]) -> bytes:
    """Build a JPEG carrying an orientation EXIF tag (6 = rotate 90 CW)."""
    img = Image.new("RGB", size, (0, 128, 255))
    buf = io.BytesIO()
    exif = img.getexif()
    exif[274] = 6  # Orientation: rotate 90° clockwise
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def describe_normalize_image():
    def it_resizes_when_over_max():
        src = SimpleUploadedFile("big.png", _png_bytes((3000, 1500)), content_type="image/png")

        out = normalize_image(src, max_long_edge=1200)

        img = Image.open(out)
        assert max(img.size) == 1200
        # Aspect preserved.
        assert img.size == (1200, 600)

    def it_passes_through_when_smaller_than_max():
        src = SimpleUploadedFile("small.png", _png_bytes((400, 300)), content_type="image/png")

        out = normalize_image(src, max_long_edge=1200)

        img = Image.open(out)
        # No upscale; original dimensions preserved.
        assert img.size == (400, 300)

    def it_returns_jpeg_extension_for_jpeg_target():
        src = SimpleUploadedFile("photo.png", _png_bytes((100, 100)), content_type="image/png")

        out = normalize_image(src, max_long_edge=1200, format="JPEG")

        assert out.name.endswith(".jpg")
        img = Image.open(out)
        assert img.format == "JPEG"

    def it_flattens_rgba_over_white():
        # A transparent corner becomes white, not black.
        src = SimpleUploadedFile(
            "alpha.png", _png_bytes((10, 10), mode="RGBA", color=(255, 0, 0, 0)), content_type="image/png"
        )

        out = normalize_image(src, max_long_edge=1200)

        img = Image.open(out)
        assert img.mode == "RGB"
        # Transparent pixel becomes white background.
        assert img.getpixel((0, 0)) == (255, 255, 255)

    def it_applies_exif_rotation_and_strips_exif():
        # Source is 200x100 with orientation=6 (rotate 90 CW); output should be 100x200.
        src = SimpleUploadedFile("rot.jpg", _jpg_with_exif_orientation((200, 100)), content_type="image/jpeg")

        out = normalize_image(src, max_long_edge=2000)

        img = Image.open(out)
        assert img.size == (100, 200)
        # EXIF stripped.
        assert not img.getexif()


def describe_normalize_field_if_uploaded():
    def it_replaces_fresh_uploads_with_normalized_version():
        class Holder:
            field = None

        # Mimic a fresh ImageFieldFile: an UploadedFile is uncommitted by default.
        upload = SimpleUploadedFile("hero.png", _png_bytes((3000, 1500)), content_type="image/png")
        holder = Holder()
        holder.field = upload

        normalize_field_if_uploaded(holder, "field", max_long_edge=1200)

        img = Image.open(holder.field)
        assert max(img.size) == 1200

    def it_leaves_committed_files_alone():
        class FakeStoredFile:
            _committed = True
            name = "stored.png"

        class Holder:
            field = FakeStoredFile()

        holder = Holder()
        original = holder.field

        normalize_field_if_uploaded(holder, "field", max_long_edge=100)

        assert holder.field is original

    def it_no_ops_when_field_is_falsy():
        class Holder:
            field = None

        holder = Holder()

        normalize_field_if_uploaded(holder, "field", max_long_edge=100)

        assert holder.field is None
