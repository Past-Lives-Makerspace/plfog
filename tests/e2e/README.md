# Browser QA

Two layers, both driving a real Chromium against the app.

## 1. Committed end-to-end tests (deterministic, gated in CI)

Specs in this directory run through pytest-django's `live_server` +
pytest-playwright. They drive the genuine flows a member sees — e.g. the
allauth login-by-code screens (the code is read out of the email outbox) and
class registration.

They are marked `e2e` and **deselected from the default `pytest` run** (see
`addopts` in `pyproject.toml`). Run them explicitly:

```bash
# first time only — install the browser
python -m playwright install chromium

# the host reaches the Docker Postgres on :5433 (the override-mapped port)
DATABASE_URL=postgres://plfog:plfog@localhost:5433/plfog pytest -m e2e
```

The harness talks to `live_server` on `localhost` and forces non-secure,
host-scoped cookies, so it is self-contained — it does **not** use the
`pastlives.test` dev surfaces. CI: `.github/workflows/playwright.yml`
(Postgres service, runs on PRs to `main` and pushes to `release-**`).

## 2. Agent walkthroughs (on-demand, Playwright MCP)

`/.mcp.json` (repo root) wires the [Playwright MCP](https://github.com/microsoft/playwright-mcp)
server. Claude Code prompts to enable it the first time the project loads —
**reload Claude Code** to pick it up. Once enabled, you can ask Claude to walk
a flow against the running dev app and report what's broken, e.g.:

> "Sign in at book.pastlives.test:8000, register for a class as a guest, and
>  tell me anything broken or ugly in dark **and** light mode."

Start the dev server first (Docker Compose). Target the dev surfaces, **not**
localhost (localhost is out of `ALLOWED_HOSTS` in dev):

- `http://pastlives.test:8000` — members / FOG hub
- `http://book.pastlives.test:8000` — public classes & workshops (CMS)

These hostnames need a hosts-file entry the WSL-side Chromium can resolve.
Findings worth keeping become new specs in layer 1.

First MCP run downloads its browser; if it complains, run
`npx playwright install chromium`.
