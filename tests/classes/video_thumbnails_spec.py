"""Specs for classes/video_thumbnails.py and the fetch_video_thumbnails job.

Every request is mocked with ``respx``; an unmocked request fails the spec, so nothing here
reaches Instagram.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from datetime import timedelta
from io import StringIO
from pathlib import Path

import httpx
import pytest
import respx
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from PIL import Image

from classes import video_thumbnails
from classes.factories import ClassOfferingFactory
from classes.models import VIDEO_THUMBNAIL_PREFIX, ClassOffering
from classes.video_providers import recognize
from classes.video_thumbnails import (
    ThumbnailFetchError,
    fetch_post_picture,
    post_page_url,
    refresh_video_thumbnails,
    validate_image_url,
)
from core.models import ScheduledTaskRun
from core.scheduled_jobs import JOBS_BY_KEY, Cadence, Trigger, record_run

POST_URL = "https://www.instagram.com/p/CvxovSSs8dw/"
PAGE_URL = POST_URL  # Already the rebuilt shape, so the link and the requested page coincide.
OTHER_POST_URL = "https://www.instagram.com/p/DIUkbpeuR-W/"
OTHER_PAGE_URL = OTHER_POST_URL
IMAGE_URL = "https://scontent-sea5-1.cdninstagram.com/v/t51/500661881_n.jpg?stp=dst-jpg&_nc_ht=x&oe=6ACEDE09"
OTHER_IMAGE_URL = "https://scontent-sea5-1.cdninstagram.com/v/t51/490659115_n.jpg?oe=6ACED11A"

pytestmark = pytest.mark.django_db

# A route registered for a request that must NOT happen is part of the assertion, so unused routes are fine.
_respx = respx.mock(assert_all_called=False)


def _jpeg(size: tuple[int, int] = (640, 640), color: tuple[int, int, int] = (200, 50, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _page(image_url: str = IMAGE_URL) -> str:
    """A post page shaped like Instagram's: other meta tags first, the URL entity escaped."""
    escaped = image_url.replace("&", "&amp;")
    return (
        "<!DOCTYPE html><html><head><title>Post</title>"
        '<meta property="og:title" content="Forge Night">'
        '<meta name="viewport" content="width=device-width">'
        f'<meta property="og:image" content="{escaped}" />'
        "</head><body>" + "x" * 5000 + "</body></html>"
    )


def _mock_post(page_url: str = PAGE_URL, image_url: str = IMAGE_URL, image: bytes | None = None) -> respx.Route:
    _respx.get(page_url).mock(return_value=httpx.Response(200, text=_page(image_url)))
    return _respx.get(image_url).mock(
        return_value=httpx.Response(200, content=image or _jpeg(), headers={"content-type": "image/jpeg"})
    )


def _link(url: str = POST_URL):
    link = recognize(url)
    assert link is not None
    return link


def _fetch(link_url: str = POST_URL) -> bytes:
    with httpx.Client(follow_redirects=False) as client:
        return fetch_post_picture(client, _link(link_url))


@pytest.fixture(autouse=True)
def _media(settings, tmp_path: Path) -> Iterator[None]:
    """Stored pictures land in a throwaway folder, not the checkout's media/."""
    settings.MEDIA_ROOT = str(tmp_path)
    yield


def describe_post_page_url():
    def it_rebuilds_the_post_page_from_the_id():
        assert post_page_url(_link("https://instagram.com/p/CvxovSSs8dw?igsh=abc")) == PAGE_URL

    def it_rebuilds_a_reel_or_a_profile_route_as_the_post_page():
        assert post_page_url(_link("https://www.instagram.com/reel/CvxovSSs8dw/")) == PAGE_URL
        assert post_page_url(_link("https://m.instagram.com/pastlives/p/CvxovSSs8dw/")) == PAGE_URL

    def it_refuses_a_link_from_another_provider():
        with pytest.raises(ValueError, match="Not an Instagram link"):
            post_page_url(_link("https://youtu.be/dQw4w9WgXcQ"))


def describe_validate_image_url():
    @pytest.mark.parametrize(
        "url",
        [
            IMAGE_URL,
            "https://instagram.fpdx1-1.fna.fbcdn.net/v/t51/1_n.jpg",
            "https://SCONTENT.CDNINSTAGRAM.COM:443/v/1.jpg",
        ],
    )
    def it_accepts_https_on_instagrams_cdn(url):
        assert validate_image_url(url) == url

    @pytest.mark.parametrize(
        ("url", "reason"),
        [
            ("http://scontent.cdninstagram.com/v/1.jpg", "not https: http"),
            ("//scontent.cdninstagram.com/v/1.jpg", "not https: no scheme"),
            ("https://evil.test/v/1.jpg", "not on Instagram's CDN: evil.test"),
            ("https://scontent.cdninstagram.com.evil.test/1.jpg", "not on Instagram's CDN"),
            ("https://cdninstagram.com/1.jpg", "not on Instagram's CDN"),
            ("https://scontent.cdninstagram.com@evil.test/1.jpg", "not on Instagram's CDN: evil.test"),
            ("https:///1.jpg", "not on Instagram's CDN: no host"),
            ("https://evil.test\\@scontent.cdninstagram.com/1.jpg", "malformed"),
            ("https://scontent.cdninstagram.com:8443/1.jpg", "names port 8443"),
            ("https://scontent.cdninstagram.com:99999/1.jpg", "malformed"),
            ("https://evil.com\uff0f.cdninstagram.com/x", "malformed"),
            ("https://[::1].cdninstagram.com/x", "malformed"),
            ("https://scontent.cdninstagram.com/a\tb.jpg", "malformed"),
            ("https://scontent.cdninstagram.com/a\nb.jpg", "malformed"),
            ("https://scontent.cdninstagram.com/a\x7fb.jpg", "malformed"),
        ],
    )
    def it_refuses_anything_else(url, reason):
        with pytest.raises(ThumbnailFetchError, match=reason):
            validate_image_url(url)


def describe_fetch_post_picture():
    @_respx
    def it_returns_the_picture_normalized_to_a_jpeg(settings):
        settings.IMAGE_MAX_LONG_EDGE_GALLERY = 320
        _mock_post(image=_jpeg((800, 1000)))
        data = _fetch()
        with Image.open(io.BytesIO(data)) as picture:
            assert picture.format == "JPEG"
            assert picture.size == (256, 320)

    @_respx
    def it_requests_only_the_rebuilt_page_never_the_stored_string():
        page = _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=_page()))
        _respx.get(IMAGE_URL).mock(
            return_value=httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})
        )
        _fetch("https://www.instagram.com/someone/reel/CvxovSSs8dw/?utm_source=ig")
        assert page.call_count == 1

    @_respx
    def it_finds_the_tag_split_across_chunks():
        html = _page().encode()
        cut = html.index(b"og:image") + 4
        _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, content=iter([html[:cut], html[cut:]])))
        _respx.get(IMAGE_URL).mock(
            return_value=httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})
        )
        assert _fetch()

    @_respx
    def it_fails_when_the_page_does_not_answer_200_and_never_follows_a_redirect():
        _respx.get(PAGE_URL).mock(
            return_value=httpx.Response(302, headers={"location": "https://www.instagram.com/accounts/login/"})
        )
        login = _respx.get("https://www.instagram.com/accounts/login/").mock(return_value=httpx.Response(200))
        with pytest.raises(ThumbnailFetchError, match="the post page answered 302"):
            _fetch()
        assert login.call_count == 0

    @_respx
    def it_fails_on_a_timeout():
        _respx.get(PAGE_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
        with pytest.raises(ThumbnailFetchError, match="ConnectTimeout: timed out"):
            _fetch()

    @_respx
    def it_fails_when_the_page_names_no_picture():
        _respx.get(PAGE_URL).mock(
            return_value=httpx.Response(200, text='<html><head><meta property="og:image" content=""></head></html>')
        )
        with pytest.raises(ThumbnailFetchError, match="names no og:image"):
            _fetch()

    @_respx
    def it_stops_reading_a_page_that_runs_past_the_cap(monkeypatch):
        monkeypatch.setattr(video_thumbnails, "PAGE_MAX_BYTES", 10)
        chunks = [b"<html><head>", b"<title>long</title>", _page().encode()]
        _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, content=iter(chunks)))
        with pytest.raises(ThumbnailFetchError, match="names no og:image"):
            _fetch()

    @_respx
    def it_fails_when_the_picture_is_off_instagrams_cdn():
        _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=_page("https://evil.test/1.jpg")))
        evil = _respx.get("https://evil.test/1.jpg").mock(return_value=httpx.Response(200))
        with pytest.raises(ThumbnailFetchError, match="not on Instagram's CDN"):
            _fetch()
        assert evil.call_count == 0

    @_respx
    def it_fails_when_the_picture_is_plain_http():
        _respx.get(PAGE_URL).mock(
            return_value=httpx.Response(200, text=_page("http://scontent.cdninstagram.com/1.jpg"))
        )
        with pytest.raises(ThumbnailFetchError, match="not https"):
            _fetch()

    @_respx
    def it_fails_when_the_picture_does_not_answer_200():
        _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=_page()))
        _respx.get(IMAGE_URL).mock(return_value=httpx.Response(403))
        with pytest.raises(ThumbnailFetchError, match="the picture answered 403"):
            _fetch()

    @_respx
    @pytest.mark.parametrize(
        ("headers", "reason"),
        [({"content-type": "text/html"}, "not an image: text/html"), ({}, "not an image: no content type")],
    )
    def it_fails_when_the_picture_is_not_an_image_type(headers, reason):
        _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=_page()))
        _respx.get(IMAGE_URL).mock(return_value=httpx.Response(200, content=b"<html></html>", headers=headers))
        with pytest.raises(ThumbnailFetchError, match=reason):
            _fetch()

    @_respx
    def it_fails_when_the_picture_is_too_large(monkeypatch):
        monkeypatch.setattr(video_thumbnails, "IMAGE_MAX_BYTES", 1024 * 1024)
        _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=_page()))
        chunks = [b"\xff" * (600 * 1024), b"\xff" * (600 * 1024)]
        _respx.get(IMAGE_URL).mock(
            return_value=httpx.Response(200, content=iter(chunks), headers={"content-type": "image/jpeg"})
        )
        with pytest.raises(ThumbnailFetchError, match="larger than 1 MB"):
            _fetch()

    @_respx
    def it_asks_for_the_page_uncompressed_so_the_cap_counts_real_bytes():
        _mock_post()
        _fetch()
        page_request = _respx.calls[0].request
        assert page_request.url == PAGE_URL
        assert page_request.headers["Accept-Encoding"] == "identity"

    @_respx
    def it_refuses_a_picture_url_with_a_tab_from_the_page():
        tabbed = "https://scontent.cdninstagram.com/a&#9;b.jpg"
        _respx.get(PAGE_URL).mock(
            return_value=httpx.Response(200, text=f'<html><head><meta property="og:image" content="{tabbed}"></head>')
        )
        with pytest.raises(ThumbnailFetchError, match="malformed"):
            _fetch()

    @_respx
    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (httpx.InvalidURL("Invalid non-printable ASCII character in URL"), "InvalidURL: Invalid non-printable"),
            (httpx.StreamConsumed(), "StreamConsumed"),
        ],
    )
    def it_fails_on_the_errors_httpx_raises_outside_http_error(error, reason):
        _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=_page()))
        _respx.get(IMAGE_URL).mock(side_effect=error)
        with pytest.raises(ThumbnailFetchError, match=reason):
            _fetch()

    def describe_the_deadline():
        @pytest.fixture
        def clock(monkeypatch):
            """A monotonic clock that reads each given value once, then stays on the last."""

            def _set(*readings: float) -> None:
                values = list(readings)
                monkeypatch.setattr(
                    video_thumbnails, "monotonic", lambda: values.pop(0) if len(values) > 1 else values[0]
                )

            return _set

        @_respx
        def it_gives_up_on_a_page_that_trickles_past_it(clock):
            clock(0.0, 20.5)
            _mock_post()
            with pytest.raises(ThumbnailFetchError, match="took longer than 20 seconds"):
                _fetch()

        @_respx
        def it_gives_up_on_a_picture_that_trickles_past_it(clock):
            clock(0.0, 5.0, 20.5)
            route = _mock_post()
            with pytest.raises(ThumbnailFetchError, match="took longer than 20 seconds"):
                _fetch()
            assert route.call_count == 1

        @_respx
        def it_finishes_a_fetch_inside_it(clock):
            clock(0.0, 19.9)
            _mock_post()
            assert _fetch()

    @_respx
    def it_fails_on_a_decompression_bomb(monkeypatch):
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
        _mock_post(image=_jpeg((640, 640)))
        with pytest.raises(ThumbnailFetchError, match="could not be read as an image"):
            _fetch()

    @_respx
    def it_fails_when_the_bytes_are_not_a_picture():
        _mock_post(image=b"not a jpeg at all")
        with pytest.raises(ThumbnailFetchError, match="could not be read as an image"):
            _fetch()


def describe_refresh_video_thumbnails():
    @_respx
    def it_stores_the_picture_for_an_instagram_class_with_none_yet():
        _mock_post()
        offering = ClassOfferingFactory(video_url=POST_URL)
        now = timezone.now()
        summary = refresh_video_thumbnails(now)
        offering.refresh_from_db()
        assert summary.fetched == [offering.pk]
        assert offering.video_thumbnail.name.startswith(VIDEO_THUMBNAIL_PREFIX)
        assert default_storage.exists(offering.video_thumbnail.name)
        assert offering.video_thumbnail_source_url == POST_URL
        assert offering.video_thumbnail_checked_at == now

    @_respx
    def it_stores_one_object_for_classes_linking_the_same_post():
        _mock_post()
        first = ClassOfferingFactory(video_url=POST_URL)
        second = ClassOfferingFactory(video_url=f" {POST_URL} ")
        refresh_video_thumbnails()
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.video_thumbnail.name == second.video_thumbnail.name
        assert second.video_thumbnail_source_url == POST_URL

    @_respx
    def it_leaves_a_class_alone_once_its_picture_matches_the_link():
        route = _mock_post()
        offering = ClassOfferingFactory(video_url=POST_URL)
        refresh_video_thumbnails()
        offering.refresh_from_db()
        stamped = offering.video_thumbnail_checked_at
        summary = refresh_video_thumbnails(timezone.now() + timedelta(days=3))
        offering.refresh_from_db()
        assert route.call_count == 1
        assert summary.fetched == summary.failed == summary.cleared == summary.waiting == []
        assert offering.video_thumbnail_checked_at == stamped

    @_respx
    def it_records_a_failure_logs_it_and_never_raises(caplog):
        _respx.get(PAGE_URL).mock(return_value=httpx.Response(429))
        offering = ClassOfferingFactory(video_url=POST_URL)
        now = timezone.now()
        with caplog.at_level(logging.WARNING, logger="classes.video_thumbnails"):
            summary = refresh_video_thumbnails(now)
        offering.refresh_from_db()
        assert summary.failed == [(offering.pk, "the post page answered 429")]
        assert not offering.video_thumbnail
        assert offering.video_thumbnail_source_url == POST_URL
        assert offering.video_thumbnail_checked_at == now
        assert f"class {offering.pk}" in caplog.text
        assert "the post page answered 429" in caplog.text

    def describe_after_a_failure():
        @pytest.fixture
        def failed(db) -> ClassOffering:
            return ClassOfferingFactory(
                video_url=POST_URL,
                video_thumbnail_source_url=POST_URL,
                video_thumbnail_checked_at=timezone.now(),
            )

        @_respx
        def it_waits_a_day_before_trying_again(failed):
            page = _respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=_page()))
            summary = refresh_video_thumbnails(failed.video_thumbnail_checked_at + timedelta(hours=23, minutes=59))
            assert summary.waiting == [failed.pk]
            assert page.call_count == 0

        @_respx
        def it_tries_again_once_the_day_is_up(failed):
            _mock_post()
            summary = refresh_video_thumbnails(failed.video_thumbnail_checked_at + timedelta(days=1))
            failed.refresh_from_db()
            assert summary.fetched == [failed.pk]
            assert failed.video_thumbnail

        @_respx
        def it_tries_a_row_with_no_checked_stamp_at_once(failed):
            ClassOffering.objects.filter(pk=failed.pk).update(video_thumbnail_checked_at=None)
            _mock_post()
            assert refresh_video_thumbnails().fetched == [failed.pk]

        @_respx
        def it_tries_a_changed_link_at_once(failed):
            ClassOffering.objects.filter(pk=failed.pk).update(video_url=OTHER_POST_URL)
            _mock_post(OTHER_PAGE_URL, OTHER_IMAGE_URL)
            assert refresh_video_thumbnails().fetched == [failed.pk]

    def describe_when_the_link_changes():
        @pytest.fixture
        def pictured(db) -> ClassOffering:
            with _respx:
                _mock_post()
                offering = ClassOfferingFactory(video_url=POST_URL)
                refresh_video_thumbnails()
            offering.refresh_from_db()
            ClassOffering.objects.filter(pk=offering.pk).update(video_url=OTHER_POST_URL)
            offering.refresh_from_db()
            return offering

        @_respx
        def it_replaces_the_picture_and_deletes_the_old_file(pictured):
            old = pictured.video_thumbnail.name
            _mock_post(OTHER_PAGE_URL, OTHER_IMAGE_URL, image=_jpeg(color=(10, 200, 10)))
            summary = refresh_video_thumbnails()
            pictured.refresh_from_db()
            assert summary.fetched == [pictured.pk]
            assert pictured.video_thumbnail.name != old
            assert pictured.video_thumbnail_source_url == OTHER_POST_URL
            assert not default_storage.exists(old)

        @_respx
        def it_keeps_the_old_file_while_a_clone_still_shows_it(pictured):
            old = pictured.video_thumbnail.name
            clone = ClassOfferingFactory(video_url=POST_URL, video_thumbnail=old, video_thumbnail_source_url=POST_URL)
            _mock_post(OTHER_PAGE_URL, OTHER_IMAGE_URL, image=_jpeg(color=(10, 200, 10)))
            refresh_video_thumbnails()
            clone.refresh_from_db()
            assert clone.video_thumbnail.name == old
            assert default_storage.exists(old)

        @_respx
        def it_drops_the_old_picture_when_the_new_post_cannot_be_fetched(pictured):
            old = pictured.video_thumbnail.name
            _respx.get(OTHER_PAGE_URL).mock(side_effect=httpx.ReadTimeout("slow"))
            summary = refresh_video_thumbnails()
            pictured.refresh_from_db()
            assert summary.failed == [(pictured.pk, "ReadTimeout: slow")]
            assert not pictured.video_thumbnail
            assert pictured.video_thumbnail_source_url == OTHER_POST_URL
            assert not default_storage.exists(old)

        @_respx
        @pytest.mark.parametrize("new_url", ["", "https://youtu.be/dQw4w9WgXcQ", "https://vimeo.com/123"])
        def it_clears_the_picture_when_the_link_is_removed_or_not_instagram(pictured, new_url):
            old = pictured.video_thumbnail.name
            ClassOffering.objects.filter(pk=pictured.pk).update(video_url=new_url)
            summary = refresh_video_thumbnails()
            pictured.refresh_from_db()
            assert summary.cleared == [pictured.pk]
            assert not pictured.video_thumbnail
            assert pictured.video_thumbnail_source_url == ""
            assert pictured.video_thumbnail_checked_at is None
            assert not default_storage.exists(old)

    @_respx
    def it_ignores_an_instagram_link_that_is_not_a_post(db):
        ClassOfferingFactory(video_url="https://www.instagram.com/pastlivesmakerspace/")
        summary = refresh_video_thumbnails()
        assert summary.fetched == summary.failed == summary.cleared == summary.waiting == []

    @_respx
    def it_clears_only_what_was_stored_after_a_failed_try(db):
        offering = ClassOfferingFactory(
            video_url="",
            video_thumbnail_source_url=POST_URL,
            video_thumbnail_checked_at=timezone.now(),
        )
        assert refresh_video_thumbnails().cleared == [offering.pk]


def describe_fetch_video_thumbnails_command():
    def it_is_registered_to_run_every_tick():
        job = JOBS_BY_KEY["fetch_video_thumbnails"]
        assert job.command == "fetch_video_thumbnails"
        assert job.cadence == Cadence.ALWAYS
        assert job.toggleable is True

    @_respx
    def it_prints_the_summary_and_each_failure():
        _mock_post()
        _respx.get(OTHER_PAGE_URL).mock(return_value=httpx.Response(404))
        ok = ClassOfferingFactory(video_url=POST_URL)
        bad = ClassOfferingFactory(video_url=OTHER_POST_URL)
        out, err = StringIO(), StringIO()
        call_command("fetch_video_thumbnails", stdout=out, stderr=err)
        assert "1 fetched, 1 failed, 0 cleared, 0 waiting a day to retry." in out.getvalue()
        assert f"Class {bad.pk}: could not fetch the Instagram picture: the post page answered 404" in err.getvalue()
        assert f"Class {ok.pk}" not in err.getvalue()

    def describe_the_run_reports_the_outcome():
        def _tick() -> tuple[ScheduledTaskRun, StringIO]:
            """One scheduled run, as the dispatcher wraps it; the run row is returned either way."""
            err = StringIO()
            try:
                with record_run("fetch_video_thumbnails", trigger=Trigger.SCHEDULED):
                    call_command("fetch_video_thumbnails", stdout=StringIO(), stderr=err)
            except CommandError:
                pass
            return ScheduledTaskRun.objects.filter(task_key="fetch_video_thumbnails").latest("started_at"), err

        @_respx
        def it_fails_the_run_when_it_tried_and_fetched_nothing():
            _respx.get(PAGE_URL).mock(side_effect=httpx.ConnectError("refused"))
            _respx.get(OTHER_PAGE_URL).mock(return_value=httpx.Response(429))
            first = ClassOfferingFactory(video_url=POST_URL)
            second = ClassOfferingFactory(video_url=OTHER_POST_URL)
            with pytest.raises(CommandError) as raised:
                call_command("fetch_video_thumbnails", stdout=StringIO(), stderr=StringIO())
            message = str(raised.value)
            assert message.startswith("Could not fetch any of 2 Instagram picture(s) tried; each is retried in a day.")
            assert f"class {first.pk}: ConnectError: refused" in message
            assert f"class {second.pk}: the post page answered 429" in message
            assert "\n" not in message

        @_respx
        def it_marks_the_run_failed_and_writes_each_wait_first():
            _respx.get(PAGE_URL).mock(side_effect=httpx.ConnectError("refused"))
            offering = ClassOfferingFactory(video_url=POST_URL)
            run, err = _tick()
            offering.refresh_from_db()
            assert run.status == ScheduledTaskRun.Status.FAILED
            assert "ConnectError: refused" in run.error
            assert "ConnectError: refused" in err.getvalue()
            assert offering.video_thumbnail_source_url == POST_URL
            assert offering.video_thumbnail_checked_at is not None

        @_respx
        def it_ends_the_next_ticks_ok_while_the_stuck_class_waits_then_fails_again_a_day_later():
            page = _respx.get(PAGE_URL).mock(side_effect=httpx.ConnectError("refused"))
            offering = ClassOfferingFactory(video_url=POST_URL)
            assert _tick()[0].status == ScheduledTaskRun.Status.FAILED
            assert _tick()[0].status == ScheduledTaskRun.Status.OK
            assert page.call_count == 1
            ClassOffering.objects.filter(pk=offering.pk).update(
                video_thumbnail_checked_at=timezone.now() - timedelta(days=1)
            )
            assert _tick()[0].status == ScheduledTaskRun.Status.FAILED
            assert page.call_count == 2

        @_respx
        def it_ends_ok_when_one_post_is_fetched_and_another_fails():
            _mock_post()
            _respx.get(OTHER_PAGE_URL).mock(return_value=httpx.Response(404))
            ClassOfferingFactory(video_url=POST_URL)
            ClassOfferingFactory(video_url=OTHER_POST_URL)
            assert _tick()[0].status == ScheduledTaskRun.Status.OK

        @_respx
        def it_ends_ok_with_nothing_to_try():
            ClassOfferingFactory(video_url="https://youtu.be/dQw4w9WgXcQ")
            assert _tick()[0].status == ScheduledTaskRun.Status.OK
