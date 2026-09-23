# Past Lives Makerspace — plfog

Django web app for membership, studio booking, and class management at
[Past Lives Makerspace](https://pastlives.space) in Portland, OR.

Repo: <https://github.com/Past-Lives-Makerspace/plfog>

It runs as **two surfaces** that mirror production:

| Surface | Local host | What it is |
|---|---|---|
| **Members** | `pastlives.test:8000` | Member dashboard, membership, billing, studio rentals |
| **Book** | `book.pastlives.test:8000` | Public-facing classes & workshop catalog and booking |

**Stack:** Django (Python 3.13) · PostgreSQL · server-rendered templates · Stripe billing ·
Airtable sync · web push. Hosted on [Render](https://render.com).

---

## Getting started (local dev)

Local development runs entirely in **Docker Compose** — you do not need Python, Postgres, or
anything else installed on your host. There are two containers: `db` (Postgres) and `web` (Django).

### 1. Map the dev hostnames

The app uses real hostnames locally (not `localhost`) so cookies and the two surfaces behave like
production. Add this line to your hosts file (`/etc/hosts` on Linux/macOS, or
`C:\Windows\System32\drivers\etc\hosts` on Windows):

```
127.0.0.1 pastlives.test book.pastlives.test
```

### 2. Create your `.env`

Copy the example and fill in any secrets you need (it works out of the box for most local work):

```bash
cp .env.example .env   # if present; otherwise ask a maintainer for a starter .env
```

### 3. Start the stack

```bash
docker compose up -d
```

When both containers report healthy (`docker compose ps`), open:

- **Members:** <http://pastlives.test:8000>
- **Book:** <http://book.pastlives.test:8000>

### 4. Log in

Login is passwordless — you request a one-time code by email. In local `DEBUG` mode the code is
shown on-screen (and captured by Mailpit, below), so you never need a real inbox to sign in.

### Reloading after `.env` changes

A plain `docker compose restart web` does **not** re-read `.env`. To pick up env changes:

```bash
docker compose up -d --force-recreate --no-deps web
```

### Handy commands

```bash
docker compose logs web -f     # tail the web logs
docker compose ps              # container status
docker compose down            # stop everything
```

---

## Local email (Mailpit)

The app sends email (login codes, membership confirmations, class-registration notices). In
production that goes out through [Resend](https://resend.com). **Locally, all outbound mail is
caught by [Mailpit](https://github.com/axllent/mailpit)** — a tiny, free, self-hosted inbox so you
can read and click through real emails without sending anything to the outside world.

Mailpit is part of the Compose stack, so it starts automatically with `docker compose up -d`. No
separate launch, no account, nothing to install.

**Open the inbox at <http://localhost:8025>.** Trigger a login code or a booking, and the email
lands there with HTML rendered and links live.

How it's wired:

- `docker-compose.yml` defines the `mailpit` service (SMTP on `1025`, web inbox on `8025`).
- `.env` points Django at it:

  ```
  EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
  EMAIL_HOST=mailpit
  EMAIL_PORT=1025
  ```

`EMAIL_HOST=mailpit` is the container's name on the Compose network — that's how the `web` container
finds it. Production sets none of these vars, so it keeps using Resend untouched.

---

## Testing

Tests are **BDD/spec style** using [`pytest-describe`](https://pypi.org/project/pytest-describe/):
files are named `*_spec.py`, with `describe_*` blocks and `it_*` functions. Test data comes from
[`factory-boy`](https://factoryboy.readthedocs.io/) factories.

```bash
pytest                       # run the suite (unit tests; e2e deselected by default)
pytest path/to/foo_spec.py   # run one file
pytest -m e2e                # run the Playwright browser tests
```

- **Coverage gate: 98%.** CI fails under it (`fail_under = 98`). Branch coverage is on.
- **Mutation testing** runs in CI (via `pytest --leela`) as a non-blocking quality signal.
- Every code path should be covered — mock external services (Stripe, Airtable, Resend), never the
  database or models.

### Lint & types

```bash
ruff format .        # auto-format
ruff check --fix .   # lint
mypy plfog/ core/ membership/ hub/
```

CI runs `ruff check`, `ruff format --check`, and `mypy` on every pull request, alongside the test
suite. All of it must be green to merge.

---

## Deployment

**Production is [Render](https://render.com).** Deployment is automatic:

> **Merging to `main` auto-deploys to production.** There is no manual deploy step.

- The `plfog` web service redeploys on every push to `main`.
- **Pull requests get their own preview environment** automatically (Render PR previews), so you can
  click around a branch before it merges.
- Two scheduled **cron services** run alongside the web app: a nightly Airtable → Django pull and a
  15-minute dispatcher for background tasks (see `render.yaml`).
- On merge to `main`, a GitHub Action reads the latest changelog entry and posts a release
  announcement to the Past Lives **Discord**.

> ⚠️ `staging.pastlives.space` (public catalog at `book.staging.pastlives.space`) is **staging, not
> production**: a clone of production on the Hetzner VPS that tracks `main` and is contained so nothing
> there reaches a real member or the real Discord. Render is production. See
> [`deploy/staging/README.md`](deploy/staging/README.md).

---

## Versioning & changelog

**Every PR adds one file to [`changelog.d/`](changelog.d/) and never touches a version number.**

```toml
# changelog.d/394-composer-drafts.toml
bump = "minor"          # patch | minor | major
date = "2026-09-13"
title = "The class composer keeps what you typed"
changes = [
  "If the page reloads while writing a class, your next visit offers back what you had typed.",
]
```

[`changelog.d/README.md`](changelog.d/README.md) is the authoring contract. Entries are the
**source of truth for the Discord release announcement**, so write them in plain, member-friendly
language — no jargon, PR numbers, or commit hashes. Members read these. A tooling or test change
members will never see takes `audience = "internal"` and nothing else: it moves the version and
announces nothing, which is correct.

`VERSION` and `CHANGELOG` in [`plfog/version.py`](plfog/version.py) are **computed at import**,
folded from the fragments over [`changelog/base.json`](changelog/base.json) plus the frozen
[`changelog/history.json`](changelog/history.json). Nothing writes the number down, so two PRs
open at once cannot collide over it and a rebase cannot leave one stale. The machinery and the
reasoning are in [`plfog/changelog.py`](plfog/changelog.py).

**A pull request with no fragment and no `no-changelog` label fails CI.** That check replaced a
post-merge guard that could only catch a narrower mistake, and only after Render had already
deployed it.

On merge, [`release.yml`](.github/workflows/release.yml) folds the version, pushes the tag, and
announces **the fragments that push added** — so editing one that already shipped re-announces
nothing. To re-send a post that failed, `gh workflow run release.yml`; check first whether members
have already seen it, because a Discord post cannot be unsent. `python manage.py announce_release`
is **not** a companion to it: `release.published` is registered on Discord as well as email, so
running both announces the same release twice. See [`AGENTS.md`](AGENTS.md) under "Versioning &
Changelog".

---

## Contributing

**Contributions are welcome — this project is open to PRs from anyone.** If you use the makerspace,
spot a bug, or want to add something, please open an issue or send a pull request.

A good PR:

1. **Branches off `main`** and targets it with a pull request.
2. **Keeps the suite green** — tests, coverage (98%), `ruff`, and `mypy` all pass in CI.
3. **Adds or updates tests** for the behavior you change.
4. **Bumps `plfog/version.py`** and adds a member-friendly `CHANGELOG` entry (see above), if the
   change is something members would notice.
5. **Follows the house style** — fat models / skinny views, full type annotations, `help_text` on
   model fields. The conventions live in [`STANDARDS.md`](STANDARDS.md).

Not sure where something lives? Start with the map below.

---

## Project layout & docs

- **[CODEBASE_INDEX.md](CODEBASE_INDEX.md)** — full app map: models, URLs, integrations.
- **[FRONTEND.md](FRONTEND.md)** — component library, design system, page-building rules.
- **[STANDARDS.md](STANDARDS.md)** — coding standards, testing rules and known traps, for every contributor.
- **[AGENTS.md](AGENTS.md)** — project operations: releases, changelog, automated review. `CLAUDE.md` and `.cursorrules` link to it.
- Each Django app (`core/`, `membership/`, `hub/`, …) has its own `AGENTS.md` with per-app detail.

---

## Versioning scheme

The project is pre-1.0, so releases use **`0.X.Y`**: `X` increments for each notable
feature release, and `Y` for the fixes and small things in between. It resets to `0.1.0` at the
start and counts up — no jump straight to a `1.0`/`2.0` until the app is officially "done."

## License

[MIT](LICENSE) — free to use, modify, and share. Contributions are welcome under the same license.
