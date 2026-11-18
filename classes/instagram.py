"""Instagram story and post images of one class, drawn on request with Pillow (#1254).

Each image is drawn from the class as it is now and never stored, so an edited class never
serves a stale picture. The look is the printable flyer's (``templates/classes/class_flyer.html``):
the class photo on top, then a navy panel with the Class Type, the title in Playfair Display,
"with" the instructors, the When lines, the price, and Past Lives Makerspace with the class
page's address. The fonts ship in ``classes/fonts/`` (SIL Open Font License, beside them), so
the images look the same on every host and no request reaches Google Fonts.

The photo is read through Django storage, never by fetching its public URL. A class with no
uploaded photo, a legacy CMS image only, or a file that cannot be read gets the flyer's
placeholder: navy with the title's first letter in gold.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import models
from django.utils.formats import date_format
from django.utils.timezone import localtime
from PIL import Image, ImageDraw, ImageFont, ImageOps

from classes.templatetags.classes_tags import cents_as_price, session_time_range, strip_date_suffix

if TYPE_CHECKING:
    from classes.models import ClassOffering

logger = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

NAVY = (9, 46, 76)
GOLD = (238, 180, 75)
WHITE = (255, 255, 255)
MIST = (206, 216, 226)

BRAND = "Past Lives Makerspace"
ELLIPSIS = "…"


class InstagramFormat(models.TextChoices):
    """The two Instagram frames a class can be downloaded in."""

    STORY = "story", "Instagram story"
    POST = "post", "Instagram post"


class Face(Enum):
    """A bundled typeface: its file and, for the variable Playfair Display, the named weight."""

    DISPLAY = ("PlayfairDisplay-Variable.ttf", "Bold")
    TEXT = ("Lato-Regular.ttf", "")
    TEXT_BOLD = ("Lato-Bold.ttf", "")

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        """This face at ``size`` pixels, loaded fresh (a FreeType face is not shared across threads)."""
        filename, variation = self.value
        font = ImageFont.truetype(str(FONTS_DIR / filename), size)
        if variation:
            font.set_variation_by_name(variation)
        return font


@dataclass(frozen=True)
class Layout:
    """Where everything sits on one frame. Sizes are pixels at the frame's full size."""

    size: tuple[int, int]
    photo_height: int
    margin: int
    content_top: int
    footer_bottom: int
    title_size: int
    title_floor: int
    body_size: int
    category_size: int
    price_size: int
    brand_size: int
    address_size: int
    initial_size: int


# The story keeps everything that matters between y=250 and y=1670: Instagram draws its own
# controls over the top and bottom 250 px. Only the photo reaches into the top band.
LAYOUTS: dict[InstagramFormat, Layout] = {
    InstagramFormat.STORY: Layout(
        size=(1080, 1920),
        photo_height=880,
        margin=80,
        content_top=936,
        footer_bottom=1660,
        title_size=88,
        title_floor=52,
        body_size=40,
        category_size=32,
        price_size=50,
        brand_size=42,
        address_size=30,
        initial_size=420,
    ),
    InstagramFormat.POST: Layout(
        size=(1080, 1350),
        photo_height=560,
        margin=72,
        content_top=604,
        footer_bottom=1300,
        title_size=72,
        title_floor=44,
        body_size=34,
        category_size=28,
        price_size=42,
        brand_size=36,
        address_size=26,
        initial_size=300,
    ),
}

TITLE_MAX_LINES = 3
GOLD_RULE = 8
FOOTER_GAP = 28


@dataclass(frozen=True)
class InstagramCopy:
    """Every line of text an image carries, in reading order."""

    category: str
    title: str
    byline: str
    when: tuple[str, ...]
    price: str
    address: str
    initial: str


@dataclass(frozen=True)
class FittedText:
    """Text wrapped to a width: the font it fits at and its lines."""

    font: ImageFont.FreeTypeFont
    lines: tuple[str, ...]

    @property
    def line_height(self) -> int:
        return round(self.font.size * 1.2)

    @property
    def height(self) -> int:
        return self.line_height * len(self.lines)


def when_lines(offering: ClassOffering) -> tuple[str, ...]:
    """The When wording, as the flyer's When section reads it.

    A flexible class reads its window whatever session rows it still carries; a series its
    session count and first date; a one-off its date and time; a class with no dates yet,
    "Dates to be announced."
    """
    if offering.is_flexible:
        lines = ["Flexible: arrange dates directly with the instructor."]
        if offering.has_flexible_window:
            lines.append(offering.flexible_window_label)
        return tuple(lines)
    sessions = list(offering.sessions.all())
    if not sessions:
        return ("Dates to be announced.",)
    first = sessions[0]
    if offering.is_series:
        count = len(sessions)
        return (
            f"Series · {count} session{'' if count == 1 else 's'}",
            f"Starts {date_format(localtime(first.starts_at), 'D, M j')} · {session_time_range(first)}",
        )
    return (date_format(localtime(first.starts_at), "l, F j, Y"), session_time_range(first))


def instagram_copy(offering: ClassOffering) -> InstagramCopy:
    """The text both images carry, read from the class as it is now."""
    title = strip_date_suffix(offering.title)
    names = offering.public_instructor_names
    return InstagramCopy(
        category=offering.category.name.upper(),
        title=title,
        byline=f"with {names}" if names else "",
        when=when_lines(offering),
        price=cents_as_price(offering.price_cents),
        address=offering.public_url.split("://", 1)[-1].rstrip("/"),
        initial=offering.title[:1].upper(),
    )


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word wrap; a single word wider than the line is broken between characters."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if font.getlength(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        while font.getlength(word) > max_width:
            cut = len(word)
            while cut > 1 and font.getlength(word[:cut]) > max_width:
                cut -= 1
            # Prefer to break an address after a hyphen or slash, where a reader expects it.
            joint = max(word.rfind("-", 0, cut), word.rfind("/", 0, cut))
            if joint > 0:
                cut = joint + 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines


def _ellipsize(line: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """``line`` cut short with an ellipsis so it fits ``max_width``."""
    while line and font.getlength(line.rstrip() + ELLIPSIS) > max_width:
        line = line[:-1]
    return line.rstrip() + ELLIPSIS


def fit_text(text: str, face: Face, max_width: int, max_lines: int, start: int, floor: int) -> FittedText:
    """Wrap ``text`` to ``max_width``, shrinking from ``start`` toward ``floor`` until it fits ``max_lines``.

    At the floor size a text still too long keeps its first ``max_lines`` lines and the last
    ends in an ellipsis, so nothing ever runs off the edge.
    """
    for size in range(start, floor - 1, -2):
        font = face.font(size)
        lines = _wrap(text, font, max_width)
        if len(lines) <= max_lines:
            return FittedText(font, tuple(lines))
    font = face.font(floor)
    lines = _wrap(text, font, max_width)[:max_lines]
    lines[-1] = _ellipsize(lines[-1], font, max_width)
    return FittedText(font, tuple(lines))


def _photo(offering: ClassOffering, box: tuple[int, int]) -> Image.Image | None:
    """The class's own photo cover-cropped to ``box``, or None when there is none or it cannot be read.

    The copy cut to the composer's crop box wins and is centred, since it is the box; the
    original is centred on its focal point, as the flyer frames it. Read through storage.
    """
    photo = offering.hero_cropped or offering.image
    if not photo:
        return None
    x_pct, y_pct = (float(part.rstrip("%")) / 100 for part in offering.hero_object_position.split())
    centering = (min(max(x_pct, 0.0), 1.0), min(max(y_pct, 0.0), 1.0))
    try:
        with photo.open("rb") as handle, Image.open(handle) as source:
            source.draft("RGB", box)
            upright = ImageOps.exif_transpose(source).convert("RGB")
            return ImageOps.fit(upright, box, method=Image.Resampling.LANCZOS, centering=centering)
    except (OSError, ValueError, Image.DecompressionBombError):
        logger.warning("Instagram image for class %s drew the placeholder: its photo could not be read.", offering.pk)
        return None


def _placeholder(initial: str, box: tuple[int, int], size: int) -> Image.Image:
    """The flyer's no-photo panel: navy, with the title's first letter in gold."""
    panel = Image.new("RGB", box, NAVY)
    draw = ImageDraw.Draw(panel)
    draw.text((box[0] / 2, box[1] / 2), initial, font=Face.DISPLAY.font(size), fill=GOLD, anchor="mm")
    return panel


def _body_blocks(
    copy: InstagramCopy, layout: Layout, title_size: int
) -> list[tuple[FittedText, tuple[int, int, int], int]]:
    """The panel's text blocks at one title size: (text, colour, gap after it)."""
    width = layout.size[0] - 2 * layout.margin
    body_floor = round(layout.body_size * 0.75)
    blocks: list[tuple[FittedText, tuple[int, int, int], int]] = [
        (
            fit_text(copy.category, Face.TEXT_BOLD, width, 1, layout.category_size, round(layout.category_size * 0.7)),
            GOLD,
            layout.category_size // 2,
        ),
        (
            fit_text(copy.title, Face.DISPLAY, width, TITLE_MAX_LINES, title_size, layout.title_floor),
            WHITE,
            layout.body_size // 2,
        ),
    ]
    if copy.byline:
        blocks.append(
            (fit_text(copy.byline, Face.TEXT, width, 2, layout.body_size, body_floor), MIST, layout.body_size)
        )
    for index, line in enumerate(copy.when):
        face = Face.TEXT_BOLD if index == 0 else Face.TEXT
        last = index == len(copy.when) - 1
        gap = layout.body_size // 2 if last else layout.body_size // 8
        blocks.append((fit_text(line, face, width, 2, layout.body_size, body_floor), WHITE, gap))
    blocks.append((fit_text(copy.price, Face.TEXT_BOLD, width, 1, layout.price_size, layout.body_size), GOLD, 0))
    return blocks


def _fit_body(copy: InstagramCopy, layout: Layout, room: int) -> list[tuple[FittedText, tuple[int, int, int], int]]:
    """The panel's blocks with the largest title that lets them all fit in ``room`` pixels above the footer."""
    for title_size in range(layout.title_size, layout.title_floor - 1, -4):
        blocks = _body_blocks(copy, layout, title_size)
        if sum(text.height + gap for text, _, gap in blocks) <= room:
            return blocks
    return _body_blocks(copy, layout, layout.title_floor)


def _draw_lines(draw: ImageDraw.ImageDraw, text: FittedText, x: int, y: int, fill: tuple[int, int, int]) -> int:
    """Draw ``text`` from its top at ``y``; return the y just under it."""
    for line in text.lines:
        draw.text((x, y), line, font=text.font, fill=fill, anchor="la")
        y += text.line_height
    return y


def render_png(offering: ClassOffering, fmt: InstagramFormat) -> bytes:
    """PNG bytes of ``offering`` in the ``fmt`` frame, exactly ``LAYOUTS[fmt].size``."""
    layout = LAYOUTS[fmt]
    width, height = layout.size
    copy = instagram_copy(offering)
    canvas = Image.new("RGB", layout.size, NAVY)
    photo_box = (width, layout.photo_height)
    hero = _photo(offering, photo_box) or _placeholder(copy.initial, photo_box, layout.initial_size)
    canvas.paste(hero, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, layout.photo_height, width, layout.photo_height + GOLD_RULE - 1), fill=GOLD)

    # The footer sits on the bottom of the safe area; the body gets whatever is left above it.
    text_width = width - 2 * layout.margin
    brand = fit_text(BRAND, Face.DISPLAY, text_width, 1, layout.brand_size, layout.brand_size)
    address = fit_text(copy.address, Face.TEXT, text_width, 2, layout.address_size, round(layout.address_size * 0.8))
    footer_top = layout.footer_bottom - brand.height - address.height
    _draw_lines(draw, address, layout.margin, _draw_lines(draw, brand, layout.margin, footer_top, GOLD), MIST)

    y = layout.content_top
    for text, fill, gap in _fit_body(copy, layout, footer_top - FOOTER_GAP - layout.content_top):
        y = _draw_lines(draw, text, layout.margin, y, fill) + gap

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def filename(offering: ClassOffering, fmt: InstagramFormat) -> str:
    """The download's name: ``<slug>-instagram-story.png`` or ``<slug>-instagram-post.png``."""
    return f"{offering.slug}-instagram-{fmt.value}.png"
