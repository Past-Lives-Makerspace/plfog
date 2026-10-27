"""BDD specs for core.healthchecks: reporting a cron run to a Healthchecks check."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx
import pytest
import respx

from core.healthchecks import BODY_LIMIT_BYTES, USER_AGENT, HealthcheckRun, ping, report_to_healthchecks

# Not a real check: the repository is public, so no real ping URL appears anywhere in it.
PING_URL = "https://hc.example.test/ping/00000000-0000-0000-0000-000000000000"


@pytest.fixture()
def hc(settings) -> Iterator[respx.MockRouter]:
    """A configured ping URL with every Healthchecks endpoint answering 200."""
    settings.HEALTHCHECKS_PING_URL = PING_URL
    with respx.mock(assert_all_called=False) as router:
        router.post(PING_URL).respond(200)
        router.post(f"{PING_URL}/start").respond(200)
        router.post(f"{PING_URL}/fail").respond(200)
        yield router


def _pinged(router: respx.MockRouter) -> list[str]:
    """The signal of each ping sent, in order: ``start``, ``fail`` or ``success``."""
    signals = []
    for call in router.calls:
        url = str(call.request.url)
        signals.append(url.rsplit("/", 1)[-1] if url != PING_URL else "success")
    return signals


def _body(router: respx.MockRouter) -> str:
    """The body of the last ping sent."""
    return router.calls.last.request.content.decode()


def describe_report_to_healthchecks():
    def describe_when_no_ping_url_is_set():
        def it_runs_the_body_and_pings_nothing(settings):
            settings.HEALTHCHECKS_PING_URL = ""
            ran = []
            with respx.mock(assert_all_mocked=True) as router, report_to_healthchecks() as run:
                ran.append(True)
                run.fail("ignored without a check")
            assert ran == [True]
            assert not router.calls

        def it_still_raises_the_bodys_error(settings):
            settings.HEALTHCHECKS_PING_URL = ""
            with pytest.raises(RuntimeError, match="kaboom"), report_to_healthchecks():
                raise RuntimeError("kaboom")

    def describe_when_the_run_succeeds():
        def it_pings_start_then_success_with_the_noted_lines(hc: respx.MockRouter):
            with report_to_healthchecks() as run:
                run.note("ok first_job")
                run.note("ok second_job")
            assert _pinged(hc) == ["start", "success"]
            assert _body(hc) == "ok first_job\nok second_job"

        def it_ignores_a_trailing_slash_on_the_url(hc: respx.MockRouter, settings):
            settings.HEALTHCHECKS_PING_URL = f"{PING_URL}/"
            with report_to_healthchecks():
                pass
            assert _pinged(hc) == ["start", "success"]

        def it_sends_its_own_user_agent(hc: respx.MockRouter):
            # Cloudflare in front of the Healthchecks host answers default library agents with a 403.
            with report_to_healthchecks():
                pass
            assert {call.request.headers["User-Agent"] for call in hc.calls} == {USER_AGENT}

    def describe_when_the_command_marks_a_failure():
        def it_pings_fail_with_the_lines_and_the_failures(hc: respx.MockRouter):
            with report_to_healthchecks() as run:
                run.note("ok first_job")
                run.fail("second_job: kaboom")
            assert _pinged(hc) == ["start", "fail"]
            assert _body(hc) == "ok first_job\nFAILED: second_job: kaboom"

    def describe_when_the_body_raises():
        def it_pings_fail_with_the_traceback_and_re_raises(hc: respx.MockRouter):
            with pytest.raises(RuntimeError, match="kaboom"), report_to_healthchecks() as run:
                run.note("ok first_job")
                raise RuntimeError("kaboom")
            assert _pinged(hc) == ["start", "fail"]
            body = _body(hc)
            assert body.startswith("ok first_job\nTraceback (most recent call last):")
            assert body.rstrip().endswith("RuntimeError: kaboom")

        def it_starts_the_body_with_the_traceback_when_nothing_was_noted(hc: respx.MockRouter):
            with pytest.raises(RuntimeError), report_to_healthchecks():
                raise RuntimeError("kaboom")
            assert _body(hc).startswith("Traceback (most recent call last):")

    def describe_when_healthchecks_is_unreachable():
        def it_logs_a_warning_and_the_run_still_succeeds(settings, caplog):
            settings.HEALTHCHECKS_PING_URL = PING_URL
            ran = []
            with respx.mock() as router, caplog.at_level(logging.WARNING, logger="core.healthchecks"):
                router.route().mock(side_effect=httpx.ConnectTimeout("timed out"))
                with report_to_healthchecks():
                    ran.append(True)
            assert ran == [True]
            assert [r.getMessage() for r in caplog.records] == [
                "Healthchecks start ping failed: ConnectTimeout ",
                "Healthchecks success ping failed: ConnectTimeout ",
            ]

        def it_keeps_the_bodys_own_error(settings):
            settings.HEALTHCHECKS_PING_URL = PING_URL
            with respx.mock() as router:
                router.route().mock(side_effect=httpx.ConnectError("refused"))
                with pytest.raises(RuntimeError, match="kaboom"), report_to_healthchecks():
                    raise RuntimeError("kaboom")
                assert len(router.calls) == 2


def describe_ping():
    def it_sends_only_the_tail_of_a_long_body(hc: respx.MockRouter):
        ping(PING_URL, "fail", "x" * BODY_LIMIT_BYTES + "the error at the end")
        content = hc.calls.last.request.content
        assert len(content) == BODY_LIMIT_BYTES
        assert content.endswith(b"the error at the end")

    def describe_when_healthchecks_refuses():
        def it_logs_the_status_and_never_the_secret_url(caplog):
            with respx.mock() as router, caplog.at_level(logging.WARNING, logger="core.healthchecks"):
                router.post(f"{PING_URL}/fail").respond(403)
                ping(PING_URL, "fail", "body")
            messages = [r.getMessage() for r in caplog.records]
            assert messages == ["Healthchecks fail ping failed: HTTPStatusError 403"]
            assert "00000000" not in messages[0]

    def describe_when_the_url_is_malformed():
        def it_logs_a_warning_instead_of_raising(caplog):
            with caplog.at_level(logging.WARNING, logger="core.healthchecks"):
                ping("not a url", "start")
            assert len(caplog.records) == 1
            assert caplog.records[0].getMessage().startswith("Healthchecks start ping failed:")


def describe_healthcheck_run():
    def it_has_an_empty_body_when_nothing_was_noted():
        assert HealthcheckRun().body() == ""
