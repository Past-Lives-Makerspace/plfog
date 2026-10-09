"""BDD specs for the announce_live_requests job: "you asked for it, it's live" (#693 part 2)."""

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from io import StringIO

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command

from core.events.registry import FEEDBACK_REQUEST_LIVE
from core.management.commands import announce_live_requests as job
from core.models import FeedbackRequest, Notification
from core.scheduled_jobs import SCHEDULED_JOBS, Cadence
from tests.core.factories import FeedbackRequestFactory

pytestmark = pytest.mark.django_db

Status = FeedbackRequest.Status
HEALTH = "https://members.example/health/"
OWN_VERSION = "2.60.0"


@pytest.fixture(autouse=True)
def fragments(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, settings) -> pathlib.Path:
    """A changelog.d of the spec's own, this process at OWN_VERSION, and the web on a known host."""
    settings.MEMBER_BASE_URL = "https://members.example"
    monkeypatch.setattr(job, "FRAGMENTS_PATH", tmp_path)
    monkeypatch.setattr(job, "VERSION", OWN_VERSION)
    return tmp_path


def _fragment(directory: pathlib.Path, requests: list[int], *, name: str = "701-phone-booking") -> None:
    numbers = ", ".join(str(pk) for pk in requests)
    (directory / f"{name}.toml").write_text(
        'bump = "minor"\ndate = "2026-10-07"\ntitle = "Book equipment from your phone"\n'
        f'changes = ["Booking works on phones."]\nrequests = [{numbers}]\n',
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def web() -> Iterator[respx.MockRouter]:
    """Every HTTP call goes through this router; a spec says what the web's /health/ answers."""
    with respx.mock(assert_all_called=False) as router:
        yield router


def _web_serves(web: respx.MockRouter, version: str) -> respx.Route:
    return web.get(HEALTH).mock(return_value=httpx.Response(200, json={"status": "ok", "version": version}))


def _run() -> str:
    out = StringIO()
    call_command("announce_live_requests", stdout=out)
    return out.getvalue()


def _live_bells(request: FeedbackRequest) -> list[Notification]:
    return list(Notification.objects.filter(user=request.user, trigger=FEEDBACK_REQUEST_LIVE))


def describe_registration():
    def it_runs_every_tick_from_the_dispatcher():
        [entry] = [j for j in SCHEDULED_JOBS if j.key == "announce_live_requests"]

        assert (entry.command, entry.cadence) == ("announce_live_requests", Cadence.ALWAYS)


def describe_when_the_web_serves_the_release():
    def it_marks_the_request_live_and_tells_the_sender_with_the_changelog_link(
        fragments: pathlib.Path, web: respx.MockRouter
    ):
        request = FeedbackRequestFactory(subject="Phone booking", status=Status.BUILDING)
        _fragment(fragments, [request.pk])
        _web_serves(web, OWN_VERSION)

        out = _run()

        request.refresh_from_db()
        assert request.status == Status.LIVE
        assert request.live_notified_at is not None
        [bell] = _live_bells(request)
        assert bell.title == "You asked for this. It's live now."
        assert bell.body == "Phone booking"
        [message] = mail.outbox
        assert message.subject == "You asked for Phone booking. It's live now."
        link = "https://members.example/home/#changelog-701-phone-booking"
        assert link in message.body
        assert "Book equipment from your phone" in message.body
        assert f"Request #{request.pk} is live" in out

    def it_says_fixed_for_a_bug_report(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory(category="bug")
        _fragment(fragments, [request.pk])
        _web_serves(web, OWN_VERSION)

        _run()

        assert mail.outbox[0].subject.endswith("It's fixed now.")

    def it_acts_when_the_web_is_already_newer(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory()
        _fragment(fragments, [request.pk])
        _web_serves(web, "2.61.0")

        _run()

        request.refresh_from_db()
        assert request.status == Status.LIVE

    def it_notifies_once_across_repeated_runs(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory()
        _fragment(fragments, [request.pk])
        _web_serves(web, OWN_VERSION)

        _run()
        out = _run()

        assert len(_live_bells(request)) == 1
        assert len(mail.outbox) == 1
        assert "already Live" in out

    def it_sends_nothing_once_the_fragment_is_swept(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory()
        _fragment(fragments, [request.pk])
        route = _web_serves(web, OWN_VERSION)
        _run()
        (fragments / "701-phone-booking.toml").unlink()  # the sweep freezes it into history.json

        out = _run()

        assert "No changelog fragment lists a request." in out
        assert len(mail.outbox) == 1
        assert route.call_count == 1

    def it_skips_a_request_already_marked_live_by_hand(fragments: pathlib.Path, web: respx.MockRouter):
        admin = User.objects.create_superuser(username="handlive", password="p", email="handlive@example.com")
        request = FeedbackRequestFactory()
        request.mark_live(actor=admin)
        mail.outbox.clear()
        _fragment(fragments, [request.pk])
        _web_serves(web, OWN_VERSION)

        out = _run()

        assert _live_bells(request) == []
        assert mail.outbox == []
        assert "already Live" in out

    def it_does_not_fight_an_admin_who_moved_it_back(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory()
        _fragment(fragments, [request.pk])
        _web_serves(web, OWN_VERSION)
        _run()
        FeedbackRequest.objects.filter(pk=request.pk).update(status=Status.BUILDING)

        out = _run()

        request.refresh_from_db()
        assert request.status == Status.BUILDING
        assert "already told it is live" in out
        assert len(mail.outbox) == 1

    def it_logs_and_skips_an_unknown_number_and_still_does_the_rest(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory()
        _fragment(fragments, [999999, request.pk])
        _web_serves(web, OWN_VERSION)

        out = _run()

        assert "Request #999999 in 701-phone-booking.toml skipped: it does not exist." in out
        request.refresh_from_db()
        assert request.status == Status.LIVE

    def it_skips_general_feedback(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory(category="feedback")
        _fragment(fragments, [request.pk])
        _web_serves(web, OWN_VERSION)

        out = _run()

        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert "general feedback" in out
        assert mail.outbox == []

    def it_clears_a_not_planned_reason_on_the_way(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory(status=Status.NOT_PLANNED, staff_note="Zzno room")
        _fragment(fragments, [request.pk])
        _web_serves(web, OWN_VERSION)

        _run()

        request.refresh_from_db()
        assert (request.status, request.staff_note) == (Status.LIVE, "")
        assert "Zzno room" not in mail.outbox[0].body


def describe_when_the_web_is_not_serving_it_yet():
    def it_waits_while_the_web_serves_an_older_version(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory()
        _fragment(fragments, [request.pk])
        _web_serves(web, "2.59.3")

        out = _run()

        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert "Web serves 2.59.3, not yet 2.60.0" in out

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(503),
            httpx.Response(200, json={"status": "ok"}),
            httpx.Response(200, json={"status": "ok", "version": "next"}),
            httpx.Response(200, text="<html>not json</html>"),
        ],
    )
    def it_skips_the_tick_when_the_web_version_cannot_be_read(
        fragments: pathlib.Path, response: httpx.Response, web: respx.MockRouter
    ):
        request = FeedbackRequestFactory()
        _fragment(fragments, [request.pk])
        web.get(HEALTH).mock(return_value=response)

        out = _run()

        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert "Web version unknown" in out

    def it_skips_the_tick_when_the_web_cannot_be_reached(fragments: pathlib.Path, web: respx.MockRouter):
        request = FeedbackRequestFactory()
        _fragment(fragments, [request.pk])
        web.get(HEALTH).mock(side_effect=httpx.ConnectError("down"))

        out = _run()

        assert "Web version unknown" in out
        assert mail.outbox == []


def describe_when_no_fragment_lists_a_request():
    def it_does_not_ask_the_web_service_at_all(fragments: pathlib.Path, web: respx.MockRouter):
        (fragments / "1-plain.toml").write_text(
            'bump = "patch"\ndate = "2026-10-07"\ntitle = "A fix"\nchanges = ["Fixed."]\n', encoding="utf-8"
        )
        route = web.get(HEALTH)

        out = _run()

        assert not route.called
        assert "No changelog fragment lists a request." in out


def describe_announce_live():
    def it_announces_once_from_two_stale_copies():
        request = FeedbackRequestFactory()
        first = FeedbackRequest.objects.get(pk=request.pk)
        second = FeedbackRequest.objects.get(pk=request.pk)

        assert first.announce_live(release_title="T", changelog_url="https://x/#changelog-a") is None
        assert second.announce_live(release_title="T", changelog_url="https://x/#changelog-a") == "is already Live"

        assert len(_live_bells(request)) == 1


def describe_the_changelog_modal():
    def it_gives_each_fragment_entry_an_anchor_and_opens_on_one(client):
        from plfog.version import CHANGELOG

        User.objects.create_user(username="modalreader", password="p", email="modalreader@example.com")
        client.login(username="modalreader", password="p")
        slugged = [entry for entry in CHANGELOG if "slug" in entry]

        body = client.get("/feedback/").content.decode()

        assert slugged, "the repo has at least this PR's own fragment"
        assert f'id="changelog-{slugged[0]["slug"]}"' in body
        # Since #709 a member's hub page opens the link in the Spotlight's Expanded panel.
        assert "location.hash.indexOf('#changelog-')" in body
        assert "plShowEntry(location.hash.slice(1))" in body
