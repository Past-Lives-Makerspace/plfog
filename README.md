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

> ⚠️ The Hetzner box at `pastlives.plaza.codes` is **QA/staging only — not production.** Render is
> production.

---

## Versioning & changelog

Every change that ships bumps the version in [`plfog/version.py`](plfog/version.py) (`VERSION`) and
adds a `CHANGELOG` entry. That changelog is the **source of truth for the Discord release
announcement**, so write entries in plain, member-friendly language — no jargon, PR numbers, or
commit hashes. Members read these.

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
   model fields. The conventions live in [`CLAUDE.md`](CLAUDE.md).

Not sure where something lives? Start with the map below.

---

## Project layout & docs

- **[CODEBASE_INDEX.md](CODEBASE_INDEX.md)** — full app map: models, URLs, integrations.
- **[FRONTEND.md](FRONTEND.md)** — component library, design system, page-building rules.
- **[CLAUDE.md](CLAUDE.md)** — coding standards (also guides AI assistants on the project).
- Each Django app (`core/`, `membership/`, `hub/`, …) has its own `CLAUDE.md` with per-app detail.

---

## Versioning scheme

The project is pre-1.0, so releases use **`0.X.Y`**: `X` increments for each notable
feature release, and `Y` for the fixes and small things in between. It resets to `0.1.0` at the
start and counts up — no jump straight to a `1.0`/`2.0` until the app is officially "done."

## License

[MIT](LICENSE) — free to use, modify, and share. Contributions are welcome under the same license.
