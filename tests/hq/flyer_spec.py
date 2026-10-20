"""Event flyer and sharing (Track D, part 5): the door, who may open it and who may change it, this year's flyer as an
image or a PDF (or Coming soon), the upload's type and size refusals and its orphan deletion, Help spread the word with
its share link and Facebook's encoded share dialog, Find the event with blank links left out, the RSVP figure from the
event, the businesses with flyers from the Marketing Tracker, last year's flyer, the organisers' links form over htmx,
and the seed's adoption of the placeholder."""

from __future__ import annotations

import io
import os
from typing import Any
from urllib.parse import quote

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from hq import services
from hq.forms import FLYER_TYPE_ERROR, FlyerUploadForm
from hq.holiday_bizarre import FLYER_COMING_SOON, FLYER_LAST_YEAR_NOTE, FLYER_SHARE_LEDE, FLYER_SHARE_MESSAGE
from hq.models import BUILT_DOORS, DEFAULT_CARDS, EventHub, FlyerSlot, HubCard, HubFlyer, MarketingAction
from hub.view_as import ROLE_MEMBER
from membership.models import Member
from tests.hq.conftest import signed_in
from tests.hq.factories import EventHubFactory, HubCardFactory, MarketingActionFactory
from tests.membership.factories import CommunityEventFactory, EventRSVPFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
THIS_YEAR = FlyerSlot.THIS_YEAR.value
LAST_YEAR = FlyerSlot.LAST_YEAR.value
# A member's GET of the page, with both flyers up and an event: pinned so a figure that starts to query per row shows.
QUERIES_PER_PAGE = 28


def _page(hub: Any) -> str:
    return reverse("hq:flyer", args=[hub.slug])


def _upload(hub: Any, slot: str = THIS_YEAR) -> str:
    return reverse("hq:flyer_upload", args=[hub.slug, slot])


def _remove(hub: Any, slot: str = THIS_YEAR) -> str:
    return reverse("hq:flyer_remove", args=[hub.slug, slot])


def _download(hub: Any, slot: str = THIS_YEAR) -> str:
    return reverse("hq:flyer_download", args=[hub.slug, slot])


def _links(hub: Any) -> str:
    return reverse("hq:flyer_links", args=[hub.slug])


def _file_post(slot: str, upload: SimpleUploadedFile) -> dict[str, Any]:
    return {f"{slot}-file": upload}


def _links_post(**fields: str) -> dict[str, str]:
    data = {"website_url": "", "eventbrite_url": "", "share_message": "Come to the Bizarre!"}
    data.update(fields)
    return data


def _pdf(name: str = "flyer.pdf", size: int = 0) -> SimpleUploadedFile:
    body = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
    return SimpleUploadedFile(name, body + b"0" * size, content_type="application/pdf")


def _image(name: str = "flyer.png", fmt: str = "PNG", size: tuple[int, int] = (850, 1100)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, (30, 45, 78)).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def _messages(response: Any) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def _preview(client: Client, role: str) -> None:
    session = client.session
    session["view_as_role"] = role
    session.save()


@pytest.fixture
def lee(make_member) -> Member:
    return make_member(preferred_name="Lee Organiser")


@pytest.fixture
def card(open_hub, lee) -> HubCard:
    """The flyer door on the open hub, opened to members by a card edit (it is seeded admins only), with Lee named on it."""
    return HubCardFactory(
        hub=open_hub,
        kind=HubCard.Kind.FLYER,
        title="Event flyer and sharing",
        audience=HubCard.Audience.MEMBERS,
        named=[lee],
    )


@pytest.fixture
def hub(card) -> EventHub:
    return card.hub


@pytest.fixture
def lee_client(lee) -> Client:
    return signed_in(lee)


@pytest.fixture
def event(hub):
    event = CommunityEventFactory(community=True, title="Holiday Bizarre")
    hub.event = event
    hub.save(update_fields=["event"])
    return event


def _with_flyer(hub: EventHub, slot: str = THIS_YEAR, upload: SimpleUploadedFile | None = None) -> HubFlyer:
    return services.set_flyer(hub, slot, upload if upload is not None else _image())


def describe_the_door():
    def it_is_a_built_door_that_opens_the_page_from_the_hq_page(card, member_client):
        body = member_client.get(card.hub.get_absolute_url()).content.decode()

        assert HubCard.Kind.FLYER in BUILT_DOORS
        assert card.url == _page(card.hub) == f"/hq/{card.hub.slug}/flyer/"
        assert f'href="{_page(card.hub)}" class="pl-hq-card" data-hq-card-link' in body
        assert "M21 15l-5-5L5 21" in body  # the picture icon

    def it_carries_the_header_with_the_hubs_title_and_date_line_and_a_way_back(hub, event, member_client):
        body = member_client.get(_page(hub)).content.decode()

        assert '<h1 class="pl-hq-fl__title">Event flyer and sharing</h1>' in body
        assert f"data-hq-fl-subline>{hub.title} · {hub.date_line}</p>" in body
        assert f'href="{hub.get_absolute_url()}" class="pl-hq-fl__back" data-hq-fl-back>&larr; HQ</a>' in body

    def it_leaves_the_date_line_out_without_an_event(hub, member_client):
        body = member_client.get(_page(hub)).content.decode()

        assert f"data-hq-fl-subline>{hub.title}</p>" in body


def describe_who_may_open_it():
    def it_sends_a_signed_out_visitor_through_login_once_opened_to_members(hub):
        response = Client().get(_page(hub))

        assert response.status_code == 302
        assert response["Location"].startswith("/accounts/login/")

    def it_opens_to_an_active_member_without_the_organisers_box(hub, member_client):
        response = member_client.get(_page(hub))

        body = response.content.decode()
        assert response.status_code == 200
        assert "data-hq-fl-organizers" not in body
        assert _upload(hub) not in body and _links(hub) not in body and _remove(hub) not in body

    @pytest.mark.parametrize("status", [Member.Status.GUEST, Member.Status.INVITED])
    def it_is_a_404_for_an_account_that_is_not_active(hub, make_member, status):
        assert signed_in(make_member(status=status)).get(_page(hub)).status_code == 404

    def it_opens_to_everyone_when_public(card):
        card.audience = HubCard.Audience.PUBLIC
        card.save(update_fields=["audience"])

        assert Client().get(_page(card.hub)).status_code == 200

    def it_is_a_404_when_hidden_for_all_but_admins(card, member_client, lee_client, admin_client):
        card.is_hidden = True
        card.save(update_fields=["is_hidden"])

        assert member_client.get(_page(card.hub)).status_code == 404
        assert lee_client.get(_page(card.hub)).status_code == 404
        assert admin_client.get(_page(card.hub)).status_code == 200

    def it_gives_the_organisers_box_to_a_named_member_and_an_admin(hub, lee_client, admin_client):
        for client in (lee_client, admin_client):
            body = client.get(_page(hub)).content.decode()
            assert "data-hq-fl-organizers" in body
            assert f'action="{_upload(hub, THIS_YEAR)}"' in body
            assert f'action="{_upload(hub, LAST_YEAR)}"' in body
            assert f'hx-post="{_links(hub)}"' in body

    def it_renders_a_previewing_admin_as_the_member_but_lets_them_act(hub, admin_client):
        _preview(admin_client, ROLE_MEMBER)

        body = admin_client.get(_page(hub)).content.decode()

        assert "data-hq-fl-organizers" not in body
        assert admin_client.post(_links(hub), _links_post()).status_code == 302
        assert admin_client.post(_upload(hub), _file_post(THIS_YEAR, _image())).status_code == 302
        assert HubFlyer.objects.get(hub=hub).share_message == "Come to the Bizarre!"

    def it_answers_every_post_with_a_403_for_anyone_who_does_not_organise_it(hub, member_client):
        _with_flyer(hub)
        for client in (Client(), member_client):
            assert client.post(_upload(hub), _file_post(THIS_YEAR, _image())).status_code == 403
            assert client.post(_remove(hub)).status_code == 403
            assert client.post(_links(hub), _links_post()).status_code == 403
        page = HubFlyer.objects.get(hub=hub)
        assert page.flyer
        assert page.share_message == FLYER_SHARE_MESSAGE

    def it_takes_posts_only(hub, lee_client):
        for url in (_upload(hub), _remove(hub), _links(hub)):
            assert lee_client.get(url).status_code == 405, url

    def it_is_a_404_for_a_hub_with_no_flyer_door_and_an_unknown_slot(hub, lee_client, admin_client):
        other = EventHubFactory(is_open=False)
        HubCardFactory(hub=other, kind=HubCard.Kind.MARKETING)

        assert admin_client.get(_page(other)).status_code == 404
        assert admin_client.post(_upload(other), _file_post(THIS_YEAR, _image())).status_code == 404
        assert admin_client.post(_links(other), _links_post()).status_code == 404
        assert lee_client.post(_upload(hub, "poster"), {"poster-file": _image()}).status_code == 404
        assert lee_client.post(_remove(hub, "poster")).status_code == 404
        assert not HubFlyer.objects.exists()

    def it_never_lets_one_hubs_organiser_change_another_hub(hub, lee_client):
        elsewhere = EventHubFactory()
        HubCardFactory(hub=elsewhere, kind=HubCard.Kind.FLYER, audience=HubCard.Audience.PUBLIC)

        assert lee_client.post(_upload(elsewhere), _file_post(THIS_YEAR, _image())).status_code == 404
        assert lee_client.post(_links(elsewhere), _links_post()).status_code == 404
        elsewhere.is_open, hub.is_open = True, False
        hub.save(update_fields=["is_open"])
        elsewhere.save(update_fields=["is_open"])
        assert lee_client.post(_upload(elsewhere), _file_post(THIS_YEAR, _image())).status_code == 403
        assert lee_client.post(_links(elsewhere), _links_post()).status_code == 403
        assert not HubFlyer.objects.exists()


def describe_the_page_cost():
    def it_renders_for_a_member_in_a_fixed_number_of_queries(hub, event, member_client, django_assert_num_queries):
        _with_flyer(hub)
        _with_flyer(hub, LAST_YEAR, _pdf())
        MarketingActionFactory(hub=hub, kind=MarketingAction.Kind.FLYER)
        member_client.get(_page(hub))  # warm the session and the site configuration

        with django_assert_num_queries(QUERIES_PER_PAGE):
            response = member_client.get(_page(hub))

        assert response.status_code == 200

    def it_costs_the_same_with_more_rsvps_and_flyer_drop_offs(hub, event, member_client, django_assert_num_queries):
        _with_flyer(hub)
        for _ in range(3):
            EventRSVPFactory(event=event)
            MarketingActionFactory(hub=hub, kind=MarketingAction.Kind.FLYER)
        member_client.get(_page(hub))

        with django_assert_num_queries(QUERIES_PER_PAGE):
            member_client.get(_page(hub))


def describe_this_years_flyer():
    def it_says_coming_soon_with_no_flyer_and_offers_no_download(hub, member_client):
        body = member_client.get(_page(hub)).content.decode()

        assert "data-hq-fl-soon" in body
        assert ">Coming soon</span>" in body
        assert FLYER_COMING_SOON in body
        assert "data-hq-fl-flyer-actions" not in body
        assert "data-hq-fl-share-download" not in body

    def it_shows_an_image_large_with_view_download_and_print(hub, member_client):
        page = _with_flyer(hub)

        body = member_client.get(_page(hub)).content.decode()

        url = page.flyer.url
        assert f'<img src="{url}" alt="{hub.title} flyer" data-hq-fl-image>' in body
        assert f'href="{url}" class="pl-hq-fl__btn pl-hq-fl__btn--ghost" target="_blank"' in body
        assert (
            f'href="{_download(hub)}" class="pl-hq-fl__btn pl-hq-fl__btn--ghost" download hx-boost="false"'
            " data-pl-download data-hq-fl-download>Download</a>" in body
        )
        assert '@click="window.print()" data-hq-fl-print>Print</button>' in body
        assert (
            f'href="{_download(hub)}" class="pl-hq-fl__btn pl-hq-fl__btn--primary" download hx-boost="false"'
            " data-pl-download data-hq-fl-share-download>" in body
        )
        assert f'href="{url}" download' not in body
        assert "data-hq-fl-soon" not in body and "data-hq-fl-pdf" not in body

    def it_shows_a_pdf_as_a_card_that_opens_it(hub, member_client):
        page = _with_flyer(hub, upload=_pdf())

        body = member_client.get(_page(hub)).content.decode()

        assert page.flyer.name.endswith(".pdf")
        assert page.flyer_is_pdf
        assert f'href="{page.flyer.url}" class="pl-hq-fl__poster pl-hq-fl__poster--pdf"' in body
        assert ">Flyer ready</span>" in body
        assert 'aria-label="Open the PDF to print it" data-hq-fl-print>Print</a>' in body
        assert "data-hq-fl-image" not in body and "window.print()" not in body


def describe_downloading():
    def it_sends_this_years_flyer_as_an_attachment_named_for_the_hub_and_year(hub, member_client):
        page = _with_flyer(hub)

        response = member_client.get(_download(hub))

        assert response.status_code == 200
        assert response["Content-Disposition"] == 'attachment; filename="holiday-bizarre-2026-flyer.jpg"'
        assert response["Content-Type"] == "image/jpeg"
        with open(page.flyer.path, "rb") as stored:
            assert b"".join(response.streaming_content) == stored.read()

    def it_sends_last_years_pdf_with_its_own_name(hub, member_client):
        _with_flyer(hub, LAST_YEAR, _pdf())

        response = member_client.get(_download(hub, LAST_YEAR))

        assert response["Content-Disposition"] == 'attachment; filename="holiday-bizarre-2025-flyer.pdf"'
        assert response["Content-Type"] == "application/pdf"

    def it_is_a_404_with_no_flyer_up_or_an_unknown_slot(hub, member_client):
        assert member_client.get(_download(hub)).status_code == 404
        _with_flyer(hub)
        assert member_client.get(_download(hub, LAST_YEAR)).status_code == 404
        assert member_client.get(_download(hub, "poster")).status_code == 404

    def it_is_gated_like_the_page(card, member_client, admin_client):
        hub = card.hub
        _with_flyer(hub)

        signed_out = Client().get(_download(hub))
        assert signed_out.status_code == 302
        assert signed_out["Location"].startswith("/accounts/login/")

        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])
        assert member_client.get(_download(hub)).status_code == 404
        assert admin_client.get(_download(hub)).status_code == 200

        card.audience, card.is_hidden = HubCard.Audience.PUBLIC, True
        card.save(update_fields=["audience", "is_hidden"])
        assert Client().get(_download(hub)).status_code == 404
        assert member_client.get(_download(hub)).status_code == 404


def describe_uploading():
    def it_stores_an_image_resized_to_the_hero_edge_as_a_jpeg(hub, lee_client, settings):
        settings.IMAGE_MAX_LONG_EDGE_HERO = 400

        response = lee_client.post(_upload(hub), _file_post(THIS_YEAR, _image("Poster.png", size=(850, 1100))))

        page = HubFlyer.objects.get(hub=hub)
        assert response.status_code == 302
        assert response["Location"] == _page(hub)
        assert page.flyer.name.startswith("hq/flyers/Poster") and page.flyer.name.endswith(".jpg")
        with Image.open(page.flyer.path) as stored:
            assert max(stored.size) == 400
            assert stored.format == "JPEG"
        assert not page.flyer_is_pdf
        assert not page.last_year_flyer

    def it_stores_a_pdf_as_it_came_whatever_its_name(hub, lee_client):
        body = _pdf("flyer.png")

        lee_client.post(_upload(hub), _file_post(THIS_YEAR, body))

        page = HubFlyer.objects.get(hub=hub)
        assert page.flyer.name.endswith(".pdf")
        with open(page.flyer.path, "rb") as stored:
            assert stored.read().startswith(b"%PDF-1.4")

    def it_takes_a_jpeg_and_a_webp_and_names_them_by_their_bytes(hub):
        for fmt, extension in (("JPEG", "jpg"), ("WEBP", "webp")):
            form = FlyerUploadForm({}, {"flyer-file": _image("flyer.pdf", fmt=fmt)}, slot=THIS_YEAR)
            assert form.is_valid(), form.errors
            assert form.cleaned_data["file"].name == f"flyer.{extension}"

    def it_says_which_flyer_was_uploaded(hub, lee_client):
        response = lee_client.post(_upload(hub, LAST_YEAR), _file_post(LAST_YEAR, _image()), follow=True)

        assert _messages(response) == ["2025 flyer uploaded."]
        assert HubFlyer.objects.get(hub=hub).last_year_flyer

    @pytest.mark.parametrize(
        "upload",
        [
            SimpleUploadedFile("flyer.png", b"not an image at all", content_type="image/png"),
            SimpleUploadedFile("flyer.pdf", b"PDF but not really", content_type="application/pdf"),
        ],
    )
    def it_refuses_a_file_that_is_neither_an_image_nor_a_pdf(hub, lee_client, upload):
        response = lee_client.post(_upload(hub), _file_post(THIS_YEAR, upload))

        body = response.content.decode().replace("&#x27;", "'")
        assert response.status_code == 200
        assert f'<li class="pl-field-error">{FLYER_TYPE_ERROR}</li>' in body
        assert not HubFlyer.objects.exists()

    def it_refuses_an_image_type_that_is_not_jpg_png_or_webp(hub):
        form = FlyerUploadForm({}, {"flyer-file": _image("flyer.gif", fmt="GIF")}, slot=THIS_YEAR)

        assert not form.is_valid()
        assert form.errors["file"] == [FLYER_TYPE_ERROR]

    @pytest.mark.parametrize(
        "upload",
        [
            SimpleUploadedFile(
                "flyer.png",
                b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect width="10" height="10"/></svg>',
                content_type="image/png",
            ),
            SimpleUploadedFile(
                "flyer.png", b"<!doctype html><script>alert(document.cookie)</script>", content_type="image/png"
            ),
        ],
        ids=["svg", "html"],
    )
    def it_refuses_markup_renamed_as_a_png(hub, lee_client, upload):
        response = lee_client.post(_upload(hub), _file_post(THIS_YEAR, upload))

        assert response.status_code == 200
        assert FLYER_TYPE_ERROR in response.content.decode().replace("&#x27;", "'")
        assert not HubFlyer.objects.exists()

    def it_refuses_a_gif_with_html_after_it_whatever_its_name(hub):
        gif = io.BytesIO()
        Image.new("RGB", (4, 4)).save(gif, format="GIF")
        polyglot = gif.getvalue() + b"<html><script>alert(document.cookie)</script></html>"
        assert Image.open(io.BytesIO(polyglot)).format == "GIF"  # Pillow opens it: the type list refuses it

        form = FlyerUploadForm({}, {"flyer-file": SimpleUploadedFile("flyer.png", polyglot)}, slot=THIS_YEAR)

        assert not form.is_valid()
        assert form.errors["file"] == [FLYER_TYPE_ERROR]

    def it_refuses_an_image_over_fifty_million_pixels_before_decoding_it(hub, lee_client):
        # 8000 by 8000 one-bit pixels: a few kilobytes on disk, 64 million pixels once decoded.
        buffer = io.BytesIO()
        Image.new("1", (8000, 8000)).save(buffer, format="PNG")
        bomb = SimpleUploadedFile("flyer.png", buffer.getvalue(), content_type="image/png")
        assert bomb.size is not None and bomb.size < 100_000

        response = lee_client.post(_upload(hub), _file_post(THIS_YEAR, bomb))

        assert response.status_code == 200
        assert (
            "That image is 8,000 by 8,000 pixels, too large to open. Choose one under 50 million pixels."
            in response.content.decode()
        )
        assert not HubFlyer.objects.exists()

    def it_holds_an_image_to_the_image_cap_and_a_pdf_to_the_document_cap(hub, lee_client, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 1024 * 1024
        settings.MAX_UPLOAD_DOCUMENT_BYTES = 2 * 1024 * 1024
        big_image = SimpleUploadedFile("big.png", b"\x89PNG" + b"0" * (1024 * 1024), content_type="image/png")

        image_answer = lee_client.post(_upload(hub), _file_post(THIS_YEAR, big_image))
        pdf_answer = lee_client.post(_upload(hub), _file_post(THIS_YEAR, _pdf(size=2 * 1024 * 1024)))
        fits = lee_client.post(_upload(hub), _file_post(THIS_YEAR, _pdf(size=1024 * 1024)))

        assert "An image must be 1 MB or smaller." in image_answer.content.decode()
        assert "A PDF must be 2 MB or smaller." in pdf_answer.content.decode()
        assert fits.status_code == 302
        assert HubFlyer.objects.get(hub=hub).flyer_is_pdf

    def it_refuses_an_empty_post(hub, lee_client):
        response = lee_client.post(_upload(hub), {})

        assert response.status_code == 200
        assert "data-hq-fl-upload-errors" in response.content.decode()
        assert not HubFlyer.objects.exists()

    def it_keeps_the_other_forms_error_free_when_one_is_refused(hub, lee_client):
        response = lee_client.post(_upload(hub, LAST_YEAR), _file_post(LAST_YEAR, SimpleUploadedFile("x.txt", b"x")))

        body = response.content.decode()
        this_year = body.split('data-hq-fl-upload="flyer"')[1].split("</form>")[0]
        last_year = body.split('data-hq-fl-upload="last_year_flyer"')[1].split("</form>")[0]
        assert "data-hq-fl-upload-errors" in last_year
        assert "data-hq-fl-upload-errors" not in this_year

    def it_deletes_the_file_it_replaces_once_the_new_one_is_stored(hub, lee_client, django_capture_on_commit_callbacks):
        old = _with_flyer(hub).flyer.path
        assert os.path.exists(old)

        with django_capture_on_commit_callbacks() as pending:
            lee_client.post(_upload(hub), _file_post(THIS_YEAR, _pdf()))

        page = HubFlyer.objects.get(hub=hub)
        assert os.path.exists(page.flyer.path)
        assert os.path.exists(old)  # still there until the save has committed
        for callback in pending:
            callback()
        assert not os.path.exists(old)
        assert os.path.exists(page.flyer.path)

    def it_keeps_the_old_file_when_storing_the_new_one_fails(hub, monkeypatch, django_capture_on_commit_callbacks):
        old = _with_flyer(hub)
        old_name, old_path = old.flyer.name, old.flyer.path

        def full_disk(*args: Any, **kwargs: Any) -> str:
            raise OSError("No space left on device")

        monkeypatch.setattr(FileSystemStorage, "_save", full_disk)
        with django_capture_on_commit_callbacks(execute=True), pytest.raises(OSError):
            services.set_flyer(hub, THIS_YEAR, _pdf())

        assert os.path.exists(old_path)
        assert HubFlyer.objects.get(hub=hub).flyer.name == old_name

    def it_leaves_the_other_flyer_alone(hub, lee_client):
        last = _with_flyer(hub, LAST_YEAR).last_year_flyer.path

        lee_client.post(_upload(hub), _file_post(THIS_YEAR, _image()))

        assert os.path.exists(last)
        assert HubFlyer.objects.get(hub=hub).last_year_flyer.path == last


def describe_removing():
    def it_takes_the_flyer_off_the_page_and_out_of_storage(hub, lee_client, django_capture_on_commit_callbacks):
        path = _with_flyer(hub).flyer.path

        with django_capture_on_commit_callbacks(execute=True):
            response = lee_client.post(_remove(hub), follow=True)

        page = HubFlyer.objects.get(hub=hub)
        assert response.redirect_chain == [(_page(hub), 302)]
        assert _messages(response) == ["2026 flyer removed."]
        assert not page.flyer
        assert not os.path.exists(path)
        assert "data-hq-fl-soon" in response.content.decode()

    def it_says_so_when_there_is_nothing_to_remove(hub, lee_client):
        assert _messages(lee_client.post(_remove(hub, LAST_YEAR), follow=True)) == [
            "There was no 2025 flyer to remove."
        ]
        _with_flyer(hub)
        assert _messages(lee_client.post(_remove(hub, LAST_YEAR), follow=True)) == [
            "There was no 2025 flyer to remove."
        ]
        assert HubFlyer.objects.get(hub=hub).flyer

    def it_asks_first_with_labels_that_name_each_flyer(hub, lee_client):
        _with_flyer(hub)
        _with_flyer(hub, LAST_YEAR, _pdf())

        body = lee_client.get(_page(hub)).content.decode()

        assert 'aria-label="Remove the 2026 flyer" data-hq-fl-remove' in body
        assert 'aria-label="Remove the 2025 flyer" data-hq-fl-remove' in body
        assert 'aria-label="Upload the 2026 flyer"' in body and 'aria-label="Upload the 2025 flyer"' in body
        assert "open-confirm', 'hq-fl-remove-flyer'" in body
        assert "open-confirm', 'hq-fl-remove-last_year_flyer'" in body
        assert f'action="{_remove(hub, THIS_YEAR)}"' in body and f'action="{_remove(hub, LAST_YEAR)}"' in body
        assert ">the 2025 flyer (PDF)</a>" in body

    def it_offers_no_remove_before_a_flyer_is_up(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert "data-hq-fl-remove" not in body
        assert body.count("None up yet") == 2


def describe_help_spread_the_word():
    def it_shares_the_events_public_page(hub, event, member_client):
        body = member_client.get(_page(hub)).content.decode().replace("&#x27;", "'")

        link = event.public_url
        assert f'aria-label="The event link">{link}</textarea>' in body
        assert f"{FLYER_SHARE_MESSAGE} {link}" in body
        assert FLYER_SHARE_LEDE in body
        assert "Link copied. Paste it anywhere!" in body

    def it_shares_the_hq_page_with_no_event(hub, member_client, settings):
        settings.MEMBER_BASE_URL = "https://members.example.test"

        body = member_client.get(_page(hub)).content.decode()

        assert f'aria-label="The event link">https://members.example.test{hub.get_absolute_url()}</textarea>' in body

    def it_encodes_the_whole_link_into_facebooks_share_dialog(hub, event, member_client):
        body = member_client.get(_page(hub)).content.decode()

        encoded = quote(event.public_url, safe="")
        assert "%3A%2F%2F" in encoded and "/" not in encoded
        assert (
            f'href="https://www.facebook.com/sharer/sharer.php?u={encoded}" class="pl-hq-fl__btn pl-hq-fl__btn--ghost"'
            ' target="_blank" rel="noopener" hx-boost="false" data-hq-fl-facebook>Share on Facebook</a>'
        ) in body
        assert "connect.facebook.net" not in body

    def it_encodes_an_ampersand_and_a_query_so_facebook_keeps_them():
        assert services.facebook_share_url("https://x.test/a?b=1&c=2") == (
            "https://www.facebook.com/sharer/sharer.php?u=https%3A%2F%2Fx.test%2Fa%3Fb%3D1%26c%3D2"
        )

    def it_writes_the_message_then_the_link():
        assert services.share_text("  Come along!\n", "https://x.test/") == "Come along! https://x.test/"


def describe_find_the_event():
    def it_leaves_out_a_blank_link_and_always_offers_the_member_portal(hub, event, member_client):
        body = member_client.get(_page(hub)).content.decode()

        assert "data-hq-fl-website" not in body and "data-hq-fl-eventbrite" not in body
        assert f'href="{event.public_url}" class="pl-hq-fl__link" data-hq-fl-portal' in body

    def it_shows_the_links_that_are_set(hub, member_client):
        HubFlyer.objects.create(
            hub=hub, website_url="https://www.pastlives.space/bizarre", eventbrite_url="https://www.eventbrite.com/e/1"
        )

        body = member_client.get(_page(hub)).content.decode()

        website = body.split("data-hq-fl-website>")[1].split("</a>")[0]
        assert "<b>pastlives.space</b><span>See the event page</span>" in website
        assert 'href="https://www.eventbrite.com/e/1" class="pl-hq-fl__link"' in body
        assert "<b>Eventbrite</b><span>RSVP on Eventbrite</span>" in body

    def it_shows_only_the_eventbrite_link_when_the_website_is_blank(hub, member_client):
        HubFlyer.objects.create(hub=hub, eventbrite_url="https://www.eventbrite.com/e/1")

        body = member_client.get(_page(hub)).content.decode()

        assert "data-hq-fl-eventbrite" in body and "data-hq-fl-website" not in body

    def it_names_the_website_by_its_host():
        assert HubFlyer(website_url="https://www.pastlives.space/x").website_host == "pastlives.space"
        assert HubFlyer(website_url="https://example.org").website_host == "example.org"
        assert HubFlyer(website_url="").website_host == ""


def describe_the_figures():
    def it_reads_the_rsvps_from_the_event_and_links_to_whos_coming(hub, event, member_client):
        EventRSVPFactory(event=event, party_size=3)
        EventRSVPFactory(event=event, guest=True)

        body = member_client.get(_page(hub)).content.decode().replace("&#x27;", "'")

        rsvp = body.split("data-hq-fl-rsvp>")[1].split("</a>")[0]
        assert f'href="{reverse("hub_event_detail", args=[event.pk])}#whos-coming"' in body
        assert "data-hq-fl-figure>4</strong>" in rsvp
        assert "people have RSVP'd so far" in rsvp
        assert "See who's coming" in rsvp

    def it_says_person_for_one_rsvp(hub, event, member_client):
        EventRSVPFactory(event=event)

        body = member_client.get(_page(hub)).content.decode().replace("&#x27;", "'")

        rsvp = body.split("data-hq-fl-rsvp>")[1].split("</a>")[0]
        assert "data-hq-fl-figure>1</strong>" in rsvp
        assert ">person has RSVP'd so far</span>" in rsvp

    def it_hides_the_rsvp_figure_with_no_event(hub, member_client):
        assert "data-hq-fl-rsvp" not in member_client.get(_page(hub)).content.decode()

    def it_puts_the_whos_coming_anchor_on_the_event_page(event, member_client):
        body = member_client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()

        assert 'id="whos-coming" data-event-rsvps' in body

    def it_counts_the_marketing_trackers_flyer_drop_offs_on_this_hub(hub, member_client):
        MarketingActionFactory.create_batch(3, hub=hub)
        MarketingActionFactory(hub=hub, kind=MarketingAction.Kind.REEL)
        MarketingActionFactory(hub=EventHubFactory())

        body = member_client.get(_page(hub)).content.decode()

        flyers = body.split("data-hq-fl-flyers>")[1].split("</div>")[0]
        assert "data-hq-fl-figure>3</strong>" in flyers
        assert "businesses have the flyer up" in flyers
        assert services.marketing_totals(hub)["flyer"] == 3

    def it_says_business_for_one(hub, member_client):
        MarketingActionFactory(hub=hub)

        body = member_client.get(_page(hub)).content.decode()

        assert "business has the flyer up" in body

    def it_links_the_figure_to_the_marketing_tracker_only_when_that_card_is_open(hub, member_client, admin_client):
        marketing = HubCardFactory(hub=hub, kind=HubCard.Kind.MARKETING, audience=HubCard.Audience.ADMINS)
        tracker = reverse("hq:marketing", args=[hub.slug])

        member_body = member_client.get(_page(hub)).content.decode()
        admin_body = admin_client.get(_page(hub)).content.decode()

        assert '<div class="pl-hq-fl__figure" data-hq-fl-flyers>' in member_body
        assert tracker not in member_body
        assert f'<a href="{tracker}" class="pl-hq-fl__figure" data-hq-fl-flyers>' in admin_body
        marketing.audience = HubCard.Audience.MEMBERS
        marketing.save(update_fields=["audience"])
        assert f'<a href="{tracker}" class="pl-hq-fl__figure" data-hq-fl-flyers>' in (
            member_client.get(_page(hub)).content.decode()
        )
        marketing.is_hidden = True
        marketing.save(update_fields=["is_hidden"])
        assert tracker not in member_client.get(_page(hub)).content.decode()

    def it_never_stores_either_figure(hub):
        assert {f.name for f in HubFlyer._meta.get_fields()} == {
            "id",
            "hub",
            "flyer",
            "last_year_flyer",
            "website_url",
            "eventbrite_url",
            "share_message",
            "updated_at",
        }


def describe_last_year():
    def it_is_left_out_until_a_flyer_is_up(hub, member_client):
        assert "data-hq-fl-last-year" not in member_client.get(_page(hub)).content.decode()

    def it_shows_last_years_image_with_lees_line(hub, member_client):
        page = _with_flyer(hub, LAST_YEAR)

        body = member_client.get(_page(hub)).content.decode().replace("&#x27;", "'")

        assert 'id="hq-fl-last-title">Last Year: 2025</h2>' in body
        assert f'<img src="{page.last_year_flyer.url}" alt="2025 Holiday Bizarre flyer"' in body
        assert f"data-hq-fl-last-note>{FLYER_LAST_YEAR_NOTE}</p>" in body
        assert "data-hq-fl-soon" in body  # this year's is still coming

    def it_shows_last_years_pdf_as_a_link(hub, member_client):
        page = _with_flyer(hub, LAST_YEAR, _pdf())

        body = member_client.get(_page(hub)).content.decode()

        assert f'href="{page.last_year_flyer.url}" class="pl-hq-fl__thumb pl-hq-fl__thumb--pdf"' in body
        assert page.last_year_is_pdf


def describe_the_links_form():
    def it_saves_over_htmx_and_sends_the_share_and_find_sections_out_of_band(hub, event, lee_client):
        response = lee_client.post(
            _links(hub),
            _links_post(website_url="https://pastlives.space/b", share_message="See you there!"),
            **HTMX,
        )

        body = response.content.decode()
        page = HubFlyer.objects.get(hub=hub)
        assert response.status_code == 200
        assert response["HX-Trigger"]
        assert "Saved." in response["HX-Trigger"]
        assert (page.website_url, page.eventbrite_url, page.share_message) == (
            "https://pastlives.space/b",
            "",
            "See you there!",
        )
        assert body.lstrip().startswith("<form")
        assert 'id="hq-fl-share" data-hq-fl-share data-help-key="hq.flyer" hx-swap-oob="true"' in body
        assert 'id="hq-fl-find" data-hq-fl-find hx-swap-oob="true"' in body
        assert f"See you there! {event.public_url}" in body
        assert 'href="https://pastlives.space/b" class="pl-hq-fl__link"' in body

    def it_refuses_a_link_that_is_not_http_and_saves_nothing(hub, lee_client):
        for bad in ("javascript:alert(1)", "ftp://files.example.com/flyer"):
            response = lee_client.post(_links(hub), _links_post(website_url=bad), **HTMX)
            body = response.content.decode()
            assert response.status_code == 200
            assert "HX-Trigger" not in response
            assert "pl-field-error" in body
            assert "hx-swap-oob" not in body
        assert not HubFlyer.objects.exists()

    def it_refuses_a_blank_message(hub, lee_client):
        response = lee_client.post(_links(hub), _links_post(share_message=""), **HTMX)

        assert "This field is required." in response.content.decode()
        assert not HubFlyer.objects.exists()

    def it_refuses_a_message_over_600_characters(hub, lee_client):
        response = lee_client.post(_links(hub), _links_post(share_message="x" * 601), **HTMX)

        assert "pl-field-error" in response.content.decode()
        assert not HubFlyer.objects.exists()

    def it_lands_a_plain_post_back_on_the_page_with_a_message(hub, lee_client):
        response = lee_client.post(
            _links(hub), _links_post(eventbrite_url="https://www.eventbrite.com/e/9"), follow=True
        )

        assert response.redirect_chain == [(_page(hub), 302)]
        assert _messages(response) == ["Saved."]
        assert HubFlyer.objects.get(hub=hub).eventbrite_url == "https://www.eventbrite.com/e/9"

    def it_renders_the_whole_page_with_the_errors_for_a_plain_post_that_fails(hub, lee_client):
        response = lee_client.post(_links(hub), _links_post(website_url="nope nope"))

        body = response.content.decode()
        assert response.status_code == 200
        assert "data-hq-flyer" in body and "pl-field-error" in body

    def it_starts_from_lees_message_and_keeps_an_uploaded_flyer_on_save(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode().replace("&#x27;", "'")
        assert f"{FLYER_SHARE_MESSAGE}</textarea>" in body
        _with_flyer(hub)

        lee_client.post(_links(hub), _links_post())

        page = HubFlyer.objects.get(hub=hub)
        assert page.flyer and page.share_message == "Come to the Bizarre!"


def describe_the_services():
    def it_reads_defaults_without_writing_a_row(hub):
        page = services.flyer_page(hub)

        assert page.pk is None
        assert page.share_message == FLYER_SHARE_MESSAGE
        assert not HubFlyer.objects.exists()

    def it_refuses_an_unknown_slot(hub):
        with pytest.raises(ValueError, match="Unknown flyer slot 'poster'"):
            services.set_flyer(hub, "poster", _image())
        with pytest.raises(ValueError, match="Unknown flyer slot 'poster'"):
            services.remove_flyer(hub, "poster")

    def it_reports_nothing_removed_for_a_hub_with_no_row(hub):
        assert services.remove_flyer(hub, THIS_YEAR) is False

    def it_reads_as_the_hubs_flyer(hub):
        assert str(HubFlyer.objects.create(hub=hub)) == f"Flyer for {hub.title}"

    def it_leaves_a_stored_pdf_alone_on_a_later_save(hub):
        page = _with_flyer(hub, upload=_pdf())
        name = page.flyer.name

        page.website_url = "https://pastlives.space/"
        page.save()

        page.refresh_from_db()
        assert page.flyer.name == name


def describe_the_seed():
    def it_seeds_a_new_flyer_door_admins_only_like_every_door():
        door = next(door for door in DEFAULT_CARDS if door.door == HubCard.Kind.FLYER)

        assert "audience" not in door.fields()
        assert door.kind == HubCard.Kind.FLYER
        assert HubCard(**door.fields()).audience == HubCard.Audience.ADMINS

    def it_turns_the_flyer_placeholder_into_the_live_door_in_place(open_hub, member):
        placeholder = HubCardFactory(
            hub=open_hub,
            kind=HubCard.Kind.PLACEHOLDER,
            title="Event flyer and sharing",
            bullets=(
                "The flyer to print and post\nGraphics to share on Instagram and by email\n"
                "Where to drop flyers around town"
            ),
            button_text="Get the flyer",
            audience=HubCard.Audience.ADMINS,
            named=[member],
        )

        result = open_hub.seed_default_cards()

        card = HubCard.objects.get(pk=placeholder.pk)
        assert "Event flyer and sharing" in result.adopted
        assert "Event flyer and sharing" not in result.added_beside
        assert (card.kind, card.audience, card.button_text) == (
            HubCard.Kind.FLYER,
            HubCard.Audience.ADMINS,
            "See the flyer",
        )
        assert card.bullet_lines[0] == "This year's flyer to view, download and print"
        assert list(card.named_members.all()) == [member]
        assert card.url == _page(open_hub)

    def it_makes_the_flyer_door_admins_only_beside_other_cards_and_says_so(open_hub):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.PLACEHOLDER, title="Something else")

        result = open_hub.seed_default_cards()

        card = open_hub.cards.get(kind=HubCard.Kind.FLYER)
        assert card.audience == HubCard.Audience.ADMINS
        assert "Event flyer and sharing" in result.added_beside
        admins_only = result.summary.split(" New and admins only until you open it: ")[1].split(".")[0]
        assert "Event flyer and sharing" in admins_only.split(", ")
        assert "open to members" not in result.summary
