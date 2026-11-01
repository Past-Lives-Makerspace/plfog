"""The shots stepper in a real browser (#1050): a What's New entry walks through captioned pictures.

A member opens What's New and an entry at level shots shows Step 1 of 3 with its caption; Next,
Previous, the arrow keys and the step list move it; a tap on the picture opens the lightbox,
whose arrows move it too, and Esc closes the lightbox alone, leaving the modal open. At phone
width the picture sits above the list. A picture that fails to load hides the picture block and
keeps the list, and under prefers-reduced-motion the track does not slide.

The last spec takes this PR's own fragment's shots (``changelog.d/1050-guidance-shots.toml``)
with ``tests/e2e/guidance.py``'s ``shoot`` at the standard frame, which is also what pins the
helper: 1280 by 800, dark, the gold ring on the named target, 390 wide for a phone. Set
``GUIDANCE_SHOTS_DIR`` to keep what it took (the three steps and the PR screenshots); they are
uploaded by hand with ``scripts/upload_screenshot.py``.

The changelog is patched for the live server (same process) and the pictures are written into
``MEDIA_ROOT``, where ``serve_media`` answers them from, so nothing here depends on the repo's
fragments, the calendar or the bucket. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from playwright.sync_api import expect

from membership.models import Member
from plfog.changelog import shot_key
from tests.e2e.guidance import FRAMES, MAX_BYTES, shoot
from tests.membership.factories import MembershipPlanFactory

EMAIL = "whats-new-shots@example.com"
SLUG = "e2e-shots"
CAPTIONS = (
    "Press Next to move on.",
    "The steps are listed beside the picture.",
    "Tap the picture to see it full size.",
)
STEPPER = f'[data-whats-new-entry="{SLUG}"] [data-whats-new-shots]'
GOLD = "rgb(255, 211, 106)"  # --pl-new-from, the ring's outline

#: This PR's own fragment, mirrored so the pictures show the real entry.
FRAGMENT = "1050-guidance-shots"
FRAGMENT_TITLE = "What's New walks you through a change in pictures"
FRAGMENT_CHANGES = (
    "An entry with pictures shows them one step at a time, with a caption under each. Press Next, "
    "the arrow keys or a step in the list to move.",
    "Tap a picture to see it full size.",
)
FRAGMENT_CAPTIONS = (
    "Open What's New from Home. An entry with pictures starts on Step 1; press Next or the right arrow key to move on.",
    "The steps are listed beside the picture, with the one you are on marked. Press a step to jump to it.",
    "Tap the picture to see it full size. Press Esc or the X to come back.",
)


def _entry(slug: str, title: str, changes: tuple[str, ...], captions: tuple[str, ...]) -> dict[str, Any]:
    """A changelog entry at level shots, as ``Fragment.as_entry`` writes one."""
    return {
        "date": timezone.localdate().isoformat(),
        "title": title,
        "changes": list(changes),
        "slug": slug,
        "audiences": ["members"],
        "feature": "home.whats-new",
        "guidance": "shots",
        "shots": [{"caption": caption, "frame": "desktop"} for caption in captions],
    }


def _write_pictures(slug: str, count: int) -> None:
    """Distinct solid pictures at the standard size, where the storage (and ``serve_media``) finds them."""
    for number in range(1, count + 1):
        path = Path(django_settings.MEDIA_ROOT) / shot_key(slug, number)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1280, 800), color=(40 + 60 * number, 90, 140)).save(path, "PNG")


@pytest.fixture
def shots_entry(monkeypatch) -> None:
    monkeypatch.setattr("plfog.version.CHANGELOG", [_entry(SLUG, "E2E shots entry", ("It has pictures.",), CAPTIONS)])
    _write_pictures(SLUG, len(CAPTIONS))


def _sign_in(login_via_code: Any) -> None:
    MembershipPlanFactory()
    login_via_code(EMAIL)
    member = Member.objects.get(user=get_user_model().objects.get(username=EMAIL))
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["welcome_dismissed_at", "guided_tours_enabled"])


def _open(page: Any, live_server: Any, login_via_code: Any, size: dict[str, int], slug: str = SLUG) -> Any:
    """Sign in, open What's New from Home and return the entry's stepper."""
    _sign_in(login_via_code)
    page.set_viewport_size(size)
    page.goto(f"{live_server.url}{reverse('hub_home')}")
    page.locator("[data-whats-new-button]").click()
    stepper = page.locator(f'[data-whats-new-entry="{slug}"] [data-whats-new-shots]')
    expect(stepper).to_be_visible()
    return stepper


def it_steps_with_next_previous_the_arrow_keys_and_the_list_and_opens_the_lightbox(
    page, live_server, login_via_code, serve_media, shots_entry
):
    stepper = _open(page, live_server, login_via_code, FRAMES["desktop"])
    count = stepper.locator("[data-whats-new-shots-count]")
    picture = stepper.locator("[data-whats-new-shots-picture]")
    steps = stepper.locator("[data-whats-new-shots-list]")
    expect(count).to_have_text("Step 1 of 3")
    expect(stepper.locator(".pl-wn-shots__text:visible")).to_have_text(CAPTIONS[0])
    expect(stepper.locator("[data-whats-new-shots-prev]")).to_be_disabled()
    expect(steps.locator("li.is-current button")).to_have_text(CAPTIONS[0])
    expect(steps.locator("li.is-current button")).to_have_attribute("aria-current", "step")
    picture_box, steps_box = picture.bounding_box(), steps.bounding_box()
    assert picture_box["x"] + picture_box["width"] <= steps_box["x"]  # side by side on a desktop

    stepper.locator("[data-whats-new-shots-next]").click()
    expect(count).to_have_text("Step 2 of 3")
    expect(stepper.locator(".pl-wn-shots__text:visible")).to_have_text(CAPTIONS[1])
    expect(steps.locator("li.is-current button")).to_have_text(CAPTIONS[1])
    page.keyboard.press("ArrowRight")  # the Next button has focus, inside the stepper
    expect(count).to_have_text("Step 3 of 3")
    expect(stepper.locator("[data-whats-new-shots-next]")).to_be_disabled()
    page.keyboard.press("ArrowLeft")
    expect(count).to_have_text("Step 2 of 3")
    stepper.locator("[data-whats-new-shots-prev]").click()
    expect(count).to_have_text("Step 1 of 3")
    steps.locator("button", has_text=CAPTIONS[2]).click()
    expect(count).to_have_text("Step 3 of 3")
    third = stepper.locator('[data-whats-new-shot="3"]')
    expect(third).to_have_attribute("alt", CAPTIONS[2])
    expect(third).to_have_attribute("loading", "lazy")
    expect(third).to_have_js_property("complete", True)

    lightbox = stepper.locator("[data-whats-new-lightbox]")
    expect(lightbox).to_be_hidden()
    stepper.locator(".pl-wn-shots__slide").nth(2).click()
    expect(lightbox).to_be_visible()
    expect(lightbox.locator("img:visible")).to_have_attribute("alt", CAPTIONS[2])
    lightbox.locator(".pl-wn-lightbox__nav--prev").click()
    expect(count).to_have_text("Step 2 of 3")
    expect(lightbox.locator("img:visible")).to_have_attribute("alt", CAPTIONS[1])
    page.keyboard.press("Escape")
    expect(lightbox).to_be_hidden()
    expect(page.locator("#whats-new-body")).to_be_visible()  # Esc closed the lightbox alone, not the modal
    expect(stepper).to_be_focused()


def it_puts_the_picture_above_the_list_on_a_phone(page, live_server, login_via_code, serve_media, shots_entry):
    stepper = _open(page, live_server, login_via_code, FRAMES["phone"])
    picture = stepper.locator("[data-whats-new-shots-picture]").bounding_box()
    steps = stepper.locator("[data-whats-new-shots-list]").bounding_box()
    assert picture["y"] + picture["height"] <= steps["y"]
    assert picture["width"] > 0 and steps["width"] > 0


def it_hides_the_picture_block_and_keeps_the_list_when_a_picture_fails(
    page, live_server, login_via_code, serve_media, shots_entry
):
    (Path(django_settings.MEDIA_ROOT) / shot_key(SLUG, 1)).unlink()  # serve_media answers a missing file with a 404
    stepper = _open(page, live_server, login_via_code, FRAMES["desktop"])
    expect(stepper.locator("[data-whats-new-shots-picture]")).to_be_hidden()
    expect(stepper.locator("[data-whats-new-shots-list]")).to_be_visible()
    expect(stepper.locator("[data-whats-new-shots-list] li")).to_have_count(3)


def it_does_not_slide_under_reduced_motion(page, live_server, login_via_code, serve_media, shots_entry):
    page.emulate_media(reduced_motion="reduce")
    stepper = _open(page, live_server, login_via_code, FRAMES["desktop"])
    track = stepper.locator(".pl-wn-shots__track")
    # hub.css clamps every duration under reduced motion; the property is the stepper's own rule (guidance.css).
    transition = "el => [getComputedStyle(el).transitionProperty, getComputedStyle(el).transitionDuration]"
    prop, duration = track.evaluate(transition)
    assert prop == "none" and float(duration.rstrip("s")) < 0.001
    page.emulate_media(reduced_motion="no-preference")
    assert track.evaluate(transition) == ["transform", "0.3s"]


def _ring(page: Any) -> str:
    """The one ring style the helper leaves on the page, so a spec can see what it rings right now."""
    styles = page.locator("style[data-guidance-ring]")
    expect(styles).to_have_count(1)
    return styles.first.evaluate("el => el.textContent")


def it_shoots_the_stepper_at_the_standard_frame_for_the_fragment(
    page, live_server, login_via_code, serve_media, monkeypatch, tmp_path
):
    """The fragment's own three shots, and the helper's contract: 1280 by 800, dark, ringed; 390 wide for a phone."""
    _sign_in(login_via_code)
    # The pictures inside the stepper: three hub pages at the standard frame, so the first example shows the portal.
    for number, name in enumerate(("hub_home", "hub_community_calendar", "hub_beta_feedback"), start=1):
        page.goto(f"{live_server.url}{reverse(name)}")
        shoot(page, Path(django_settings.MEDIA_ROOT) / shot_key(FRAGMENT, number))
    monkeypatch.setattr(
        "plfog.version.CHANGELOG", [_entry(FRAGMENT, FRAGMENT_TITLE, FRAGMENT_CHANGES, FRAGMENT_CAPTIONS)]
    )
    page.goto(f"{live_server.url}{reverse('hub_home')}")
    page.locator("[data-whats-new-button]").click()
    stepper = page.locator(f'[data-whats-new-entry="{FRAGMENT}"] [data-whats-new-shots]')
    expect(stepper).to_be_visible()
    expect(stepper.locator('[data-whats-new-shot="1"]')).to_have_js_property("complete", True)
    # The hooks #1051 adds to a walkthrough's controls, stamped here on the three this walkthrough is about.
    stamp = "(el, key) => el.setAttribute('data-guidance-target', key)"
    stepper.locator("[data-whats-new-shots-next]").evaluate(stamp, "whats-new.next")
    stepper.locator("[data-whats-new-shots-list]").evaluate(stamp, "whats-new.steps")
    stepper.locator(".pl-wn-shots__frame").evaluate(stamp, "whats-new.picture")

    one = shoot(page, tmp_path / "step-1.png", target="whats-new.next")
    assert 'data-guidance-target="whats-new.next"' in _ring(page)
    stepper.locator("[data-whats-new-shots-next]").click()
    two = shoot(page, tmp_path / "step-2.png", target="whats-new.steps")
    assert 'data-guidance-target="whats-new.steps"' in _ring(page) and "whats-new.next" not in _ring(
        page
    )  # the ring moved on
    stepper.locator("[data-whats-new-shots-next]").click()
    three = shoot(page, tmp_path / "step-3.png", target="whats-new.picture")
    frame = stepper.locator(".pl-wn-shots__frame")
    assert frame.evaluate("el => getComputedStyle(el).outlineWidth") == "3px"
    assert frame.evaluate("el => getComputedStyle(el).outlineColor") == GOLD

    assert page.evaluate("document.documentElement.classList.contains('dark')")
    for path in (one, two, three):
        with Image.open(path) as image:
            assert image.format == "PNG" and image.size == (1280, 800)
            assert max(image.getpixel((4, 4))[:3]) < 60  # a corner of the dark page, under the modal's backdrop
        assert path.stat().st_size <= MAX_BYTES

    with pytest.raises(ValueError, match="No \\[data-guidance-target='nowhere'\\]"):
        shoot(page, tmp_path / "nowhere.png", target="nowhere")
    with pytest.raises(ValueError, match="frame must be one of desktop, phone"):
        shoot(page, tmp_path / "tablet.png", frame="tablet")

    phone = shoot(page, tmp_path / "phone.png", frame="phone")
    with Image.open(phone) as image:
        assert image.size == (390, 844)

    keep = os.environ.get("GUIDANCE_SHOTS_DIR")
    if keep:
        out = Path(keep)
        out.mkdir(parents=True, exist_ok=True)
        for path in (one, two, three):
            shutil.copy(path, out / path.name)
        # The PR's evidence: dark and light at both widths, and the lightbox open. No ring on these.
        shoot(page, out / "1050-stepper-phone-dark.png", frame="phone")
        page.evaluate("window.__plSetTheme(true)")
        page.wait_for_timeout(350)
        page.screenshot(path=str(out / "1050-stepper-phone-light.png"))
        shoot(page, out / "1050-stepper-desktop-dark.png")
        page.evaluate("window.__plSetTheme(true)")
        page.wait_for_timeout(350)
        page.screenshot(path=str(out / "1050-stepper-desktop-light.png"))
        shoot(page, out / "1050-stepper-desktop-dark-step-1.png")
        stepper.locator(".pl-wn-shots__slide").nth(2).click()
        expect(stepper.locator("[data-whats-new-lightbox]")).to_be_visible()
        page.wait_for_timeout(350)
        page.screenshot(path=str(out / "1050-stepper-lightbox-dark.png"))
