"""Build the copy-review Email Copy Gallery (and prove every email renders).

This is the capture spec ``copy-review.yml`` runs: it seeds sample data, renders
every registered email through its real send-path renderer, and writes the static
gallery site to ``SHOT_DIR``. Browserless — email HTML is pure Python — so it
needs no Chromium or ``live_server``; the transactional ``db`` fixture rolls back,
so running it locally never touches a dev database.

Dormant by default — it only runs when ``BUILD_EMAIL_GALLERY`` is set:

    scripts/build-email-gallery.sh

or directly:

    BUILD_EMAIL_GALLERY=1 SHOT_DIR=email-gallery pytest -m e2e --no-cov tests/e2e/email_gallery_spec.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILD_EMAIL_GALLERY"),
    reason="email-gallery builder — run scripts/build-email-gallery.sh (sets BUILD_EMAIL_GALLERY=1)",
)


def describe_email_gallery():
    def it_builds_the_gallery(db):
        from tests.e2e.email_gallery.build import build_site
        from tests.e2e.email_gallery.context import build_sample_data
        from tests.e2e.email_gallery.registry import gallery_emails

        out_dir = Path(os.environ.get("SHOT_DIR", "email-gallery")).resolve()
        data = build_sample_data()
        rendered = build_site(out_dir, data)

        index = out_dir / "index.html"
        assert index.exists(), "build_site wrote no index.html"
        page = index.read_text(encoding="utf-8")

        emails = gallery_emails()
        # A green run proves 100% rendered — build_site raises on any failure.
        assert len(rendered) == len(emails)
        for email in emails:
            assert f'id="{email.key}"' in page, f"no card anchor for {email.key}"
        assert 'id="section-no-email"' in page

        print(f"\nRendered {len(rendered)} email cards to {out_dir}\nOpen {index}")

    def describe_render_one():
        @pytest.fixture
        def data(db):
            from tests.e2e.email_gallery.context import build_sample_data

            return build_sample_data()

        def _by_key(key):
            from tests.e2e.email_gallery.registry import gallery_emails

            return next(e for e in gallery_emails() if e.key == key)

        def _assert_branded(rendered):
            assert rendered.subject.strip()
            assert "Past Lives" in rendered.html
            assert "[missing:" not in rendered.html
            assert "[missing:" not in rendered.text

        def it_renders_a_spine_copy_email(data):
            from tests.e2e.email_gallery.build import render_one

            rendered = render_one(_by_key("guild_announcement"), data)
            _assert_branded(rendered)

        def it_renders_a_shell_template_email(data):
            from tests.e2e.email_gallery.build import render_one

            rendered = render_one(_by_key("confirmation"), data)
            _assert_branded(rendered)
            assert "Intro to Lost-Wax Casting" in rendered.html

        def it_renders_the_instructor_welcome_email(data):
            from tests.e2e.email_gallery.build import render_one

            rendered = render_one(_by_key("welcome"), data)
            _assert_branded(rendered)
            assert "closed-toe shoes" in rendered.html  # the authored body, not a blank (M4)

        def it_renders_the_release_email(data):
            from tests.e2e.email_gallery.build import render_one

            rendered = render_one(_by_key("release_update"), data)
            _assert_branded(rendered)

        def it_renders_the_announcement_email(data):
            from tests.e2e.email_gallery.build import render_one

            rendered = render_one(_by_key("announcement"), data)
            _assert_branded(rendered)

        def it_renders_an_allauth_email(data):
            from tests.e2e.email_gallery.build import render_one

            rendered = render_one(_by_key("login_code"), data)
            _assert_branded(rendered)
            assert "824113" in rendered.html

        def it_renders_the_find_account_email_text_only(data):
            from tests.e2e.email_gallery.build import render_one

            rendered = render_one(_by_key("find_account"), data)
            assert rendered.subject == "Your Past Lives Account"
            assert rendered.html == ""  # plain-text only — the card shows the text directly
            assert "robin.vale@example.com" in rendered.text

    def it_dedups_shell_backed_events(db):
        """An event whose email ships via a structural template appears once — as that card."""
        from tests.e2e.email_gallery.registry import Renderer, gallery_emails

        emails = gallery_emails()
        assert not any(e.renderer is Renderer.SPINE_COPY and e.key == "registration_confirmed" for e in emails)
        owners = [e for e in emails if "registration_confirmed" in e.event_keys]
        assert [e.key for e in owners] == ["confirmation"]
