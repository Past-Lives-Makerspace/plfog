"""BDD specs for the Instagram story and post images of a class (#1254)."""

from __future__ import annotations

import io
from datetime import date, timedelta

import pytest
from django.template.defaultfilters import date as date_filter
from django.utils import timezone
from django.utils.timezone import localtime
from PIL import Image, ImageDraw

from classes import instagram
from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    SeriesClassOfferingFactory,
)
from classes.instagram import (
    BRAND,
    GOLD,
    LAYOUTS,
    NAVY,
    Face,
    InstagramCopy,
    InstagramFormat,
    fit_text,
    instagram_copy,
    render_png,
    when_lines,
)
from classes.models import ClassOffering

FORMATS = list(InstagramFormat)


@pytest.fixture
def drawn(monkeypatch):
    """Every (x, text, font) the renderer draws, in order, so a spec can read what a PNG says."""
    calls: list[tuple[float, str, object]] = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        calls.append((xy[0], text, kwargs["font"]))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    return calls


def _image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _texts(drawn) -> list[str]:
    return [text for _, text, _ in drawn]


def describe_render_png():
    @pytest.mark.parametrize(
        ("fmt", "size"), [(InstagramFormat.STORY, (1080, 1920)), (InstagramFormat.POST, (1080, 1350))]
    )
    def it_draws_a_png_of_the_exact_frame(fmt, size, db):
        data = render_png(ClassOfferingFactory(), fmt)
        assert data.startswith(b"\x89PNG")
        assert Image.open(io.BytesIO(data)).size == size

    @pytest.mark.parametrize("fmt", FORMATS)
    def it_carries_every_fact_of_the_class(fmt, drawn, db):
        teacher = InstructorFactory(full_legal_name="Rowan Ashby")
        offering = ClassOfferingFactory(
            title="Bronze Ring Casting",
            category=CategoryFactory(name="Metalsmithing"),
            instructor=teacher,
            price_cents=8500,
        )
        start = timezone.now() + timedelta(days=4)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        render_png(offering, fmt)
        texts = " ".join(_texts(drawn))
        for fact in (
            "METALSMITHING",
            "Bronze Ring Casting",
            "with Rowan Ashby",
            date_filter(localtime(start), "l, F j, Y"),
            "$85",
            BRAND,
        ):
            assert fact in texts
        assert "/classes/" in texts  # the class page's address

    @pytest.mark.parametrize("fmt", FORMATS)
    def it_wraps_or_shrinks_a_long_title_so_nothing_runs_off_the_edge(fmt, drawn, db):
        title = "A Very Long Weekend Intensive Introduction to Lost Wax Casting for Jewelry Makers and Curious Beginners Alike"
        offering = ClassOfferingFactory(title=title, slug="long-title")
        render_png(offering, fmt)
        layout = LAYOUTS[fmt]
        right_edge = layout.size[0] - layout.margin
        for x, text, font in drawn:
            assert x + font.getlength(text) <= right_edge, text
        title_lines = [text for _, text, font in drawn if font.size < layout.title_size and text in title]
        assert len(title_lines) >= 2

    @pytest.mark.parametrize("fmt", FORMATS)
    def it_shows_the_class_photo_on_top(fmt, db):
        offering = ClassOfferingFactory(image__width=40, image__height=40, image__color="red")
        pixel = _image(render_png(offering, fmt)).getpixel((540, 200))
        assert pixel[0] > 200 and pixel[1] < 40 and pixel[2] < 40

    @pytest.mark.parametrize("fmt", FORMATS)
    def it_shows_the_copy_cut_to_the_crop_box_when_there_is_one(fmt, monkeypatch, db):
        offering = ClassOfferingFactory(
            image__width=1000,
            image__height=600,
            image__color="green",
            hero_crop_x=100,
            hero_crop_y=50,
            hero_crop_w=400,
            hero_crop_h=225,
        )
        assert offering.hero_cropped
        opened: list[str] = []
        original = type(offering.hero_cropped).open

        def spy(self, mode="rb"):
            opened.append(self.name)
            return original(self, mode)

        monkeypatch.setattr(type(offering.hero_cropped), "open", spy)
        pixel = _image(render_png(offering, fmt)).getpixel((540, 200))
        assert opened == [offering.hero_cropped.name]
        assert pixel[1] > 100 and pixel[0] < 40

    @pytest.mark.parametrize("fmt", FORMATS)
    def it_draws_the_branded_placeholder_for_a_class_with_no_photo(fmt, drawn, db):
        offering = ClassOfferingFactory(title="Zebra Craft", image="", legacy_image_url="")
        image = _image(render_png(offering, fmt))
        assert image.getpixel((40, 40)) == NAVY
        assert drawn[0][1] == "Z"
        assert drawn[0][2].size == LAYOUTS[fmt].initial_size
        assert GOLD in {color for _, color in image.crop((0, 0, 1080, 560)).getcolors(1_000_000)}

    def it_uses_the_placeholder_for_a_legacy_cms_photo(drawn, db):
        offering = ClassOfferingFactory(title="Old Class", image="", legacy_image_url="https://legacy.example/hero.jpg")
        render_png(offering, InstagramFormat.STORY)
        assert drawn[0][1] == "O"

    def it_uses_the_placeholder_when_the_photo_cannot_be_read(drawn, caplog, db):
        offering = ClassOfferingFactory(title="Missing Photo")
        offering.image.name = "classes/images/gone-from-storage.jpg"
        data = render_png(offering, InstagramFormat.POST)
        assert Image.open(io.BytesIO(data)).size == (1080, 1350)
        assert drawn[0][1] == "M"
        assert "drew the placeholder" in caplog.text


def describe_when_lines():
    def it_gives_a_one_off_its_date_and_time(db):
        offering = ClassOfferingFactory()
        start = localtime(timezone.now() + timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        assert when_lines(offering) == (date_filter(start, "l, F j, Y"), "6–8 PM")

    def it_gives_a_series_its_session_count_and_first_date(db):
        offering = SeriesClassOfferingFactory(session_count=3)
        first = offering.sessions.order_by("starts_at").first()
        lines = when_lines(offering)
        assert lines[0] == "Series · 3 sessions"
        assert lines[1].startswith(f"Starts {date_filter(localtime(first.starts_at), 'D, M j')} · ")

    def it_says_session_for_a_series_of_one(db):
        assert when_lines(SeriesClassOfferingFactory(session_count=1))[0] == "Series · 1 session"

    def it_gives_a_flexible_class_its_window_whatever_sessions_it_carries(db):
        offering = ClassOfferingFactory(
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_starts_on=date(2026, 11, 2),
            flexible_ends_on=date(2026, 12, 1),
        )
        ClassSessionFactory(class_offering=offering)
        assert when_lines(offering) == (
            "Flexible: arrange dates directly with the instructor.",
            "Nov 2 to Dec 1, 2026",
        )

    def it_gives_a_flexible_class_with_no_window_the_one_line(db):
        offering = ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        assert when_lines(offering) == ("Flexible: arrange dates directly with the instructor.",)

    def it_says_dates_to_be_announced_with_no_sessions(db):
        offering = ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FIXED)
        assert when_lines(offering) == ("Dates to be announced.",)


def describe_instagram_copy():
    def it_reads_free_for_a_free_class(db):
        assert instagram_copy(ClassOfferingFactory(price_cents=0)).price == "Free"

    def it_drops_an_imported_date_suffix_from_the_title_and_prints_the_bare_address(db):
        offering = ClassOfferingFactory(title="Pottery Night - 6/5/26", slug="pottery-night")
        copy = instagram_copy(offering)
        assert copy.title == "Pottery Night"
        assert copy.initial == "P"
        assert "://" not in copy.address and copy.address.endswith("/classes/pottery-night")

    def it_leaves_the_byline_out_when_nobody_is_listed(db, monkeypatch):
        monkeypatch.setattr(ClassOffering, "instructor_names", property(lambda self: ""))
        assert instagram_copy(ClassOfferingFactory()).byline == ""


def describe_fit_text():
    def it_shrinks_before_it_wraps_past_the_line_limit():
        fitted = fit_text("Two words", Face.DISPLAY, 300, 1, 120, 40)
        assert len(fitted.lines) == 1
        assert fitted.font.size < 120
        assert fitted.font.getlength(fitted.lines[0]) <= 300

    def it_ends_in_an_ellipsis_when_even_the_floor_size_overflows():
        fitted = fit_text("word " * 60, Face.TEXT, 300, 2, 40, 30)
        assert fitted.font.size == 30
        assert len(fitted.lines) == 2
        assert fitted.lines[-1].endswith("…")
        assert all(fitted.font.getlength(line) <= 300 for line in fitted.lines)

    def it_breaks_an_overlong_address_after_a_hyphen_or_slash():
        fitted = fit_text("classes.example/classes/lost-wax-casting-for-beginners", Face.TEXT, 260, 4, 30, 30)
        assert len(fitted.lines) > 1
        assert all(line[-1] in "-/" for line in fitted.lines[:-1])

    def it_gives_empty_text_no_lines():
        assert fit_text("", Face.TEXT, 200, 1, 30, 30).lines == ()

    def it_breaks_a_word_with_no_joint_between_characters():
        fitted = fit_text("before " + "x" * 80, Face.TEXT, 200, 6, 30, 30)
        assert fitted.lines[0] == "before"
        assert all(fitted.font.getlength(line) <= 200 for line in fitted.lines)


def describe_fit_body():
    def it_falls_back_to_the_floor_title_when_nothing_fits_the_room():
        layout = LAYOUTS[InstagramFormat.POST]
        copy = InstagramCopy(
            category="CERAMICS",
            title="Wheel",
            byline="",
            when=("Dates to be announced.",),
            price="Free",
            address="x",
            initial="W",
        )
        blocks = instagram._fit_body(copy, layout, room=10)
        assert blocks[1][0].font.size == layout.title_floor
        assert len(blocks) == 4  # category, title, one When line, price: no byline


def describe_filename():
    def it_names_the_download_after_the_slug(db):
        offering = ClassOfferingFactory(slug="glaze-lab")
        assert instagram.filename(offering, InstagramFormat.STORY) == "glaze-lab-instagram-story.png"
        assert instagram.filename(offering, InstagramFormat.POST) == "glaze-lab-instagram-post.png"
