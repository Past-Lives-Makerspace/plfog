# plfog Codebase Index

## Project Structure

```
plfog/
├── plfog/                  # Django project config
│   ├── settings.py         # All config via env vars
│   ├── urls.py             # Root URL routing
│   ├── adapters.py         # Allauth adapters (auto-admin by email domain)
│   └── wsgi.py
├── core/                   # Core app (home, health check)
│   ├── urls.py             # / and /health/
│   └── views.py            # home, health_check
├── membership/             # Main business logic app
│   ├── models.py           # All domain models (see Models section)
│   ├── views.py            # All membership views (see Views section)
│   ├── urls.py             # Guild & buyable URL routes
│   ├── forms.py            # MemberProfileForm, BuyableForm, OrderNoteForm
│   ├── admin.py            # Unfold admin config for all models
│   ├── stripe_utils.py     # Stripe checkout session helpers
│   └── management/commands/
├── templates/
│   ├── base.html           # Site-wide layout (nav, Alpine.js dropdown)
│   ├── core/               # home.html
│   ├── membership/         # All membership templates
│   │   ├── guild_list.html, guild_detail.html          # Public guild pages
│   │   ├── buyable_detail.html                         # Public buyable page
│   │   ├── guild_manage.html, buyable_form.html        # Guild lead management
│   │   ├── guild_orders.html, order_detail.html        # Guild lead orders
│   │   ├── member_directory.html                       # Member directory
│   │   ├── profile_edit.html                           # Profile edit
│   │   ├── user_orders.html                            # User order history
│   │   └── checkout_success.html, checkout_cancel.html # Stripe callbacks
│   └── admin/login.html    # Custom admin login (Unfold)
├── static/
│   ├── css/style.css       # All styles
│   └── img/                # Static images
├── tests/                  # pytest-describe BDD tests
│   ├── membership/
│   │   ├── views_spec.py         # Guild & buyable view tests
│   │   ├── member_views_spec.py  # Member directory & profile tests
│   │   ├── models_spec.py        # Model method tests
│   │   ├── querysets_spec.py     # Queryset annotation tests
│   │   ├── guild_spec.py         # Guild model tests
│   │   ├── buyable_spec.py       # Buyable model tests
│   │   ├── stripe_spec.py        # Stripe integration tests
│   │   └── admin_spec.py         # Admin interface tests
│   ├── core/                     # Home, health tests
│   ├── plfog/                    # Settings, adapter tests
│   └── auth/                     # Allauth tests
└── conftest.py             # Shared pytest fixtures
```

## Models (membership/models.py)

| Model | Purpose | Key Relations |
|-------|---------|---------------|
| MembershipPlan | Pricing tiers (Standard, Premium) | → Member (1:many) |
| Member | Makerspace member, links to User 1:1 | → User, → MembershipPlan, GenericRelation → Lease |
| Guild | Groups/teams within the makerspace | → Member (guild_lead), GenericRelation → Lease |
| GuildMembership | M2M through: User ↔ Guild | → Guild, → User |
| GuildVote | Members vote for 3 guilds by priority | → Member, → Guild |
| GuildWishlistItem | Items a guild wants to acquire | → Guild |
| Buyable | Products a guild sells (Stripe) | → Guild |
| Order | Purchase of a Buyable | → Buyable, → User |
| Space | Physical spaces (studio, storage, desk) | → Guild (sublet), GenericRelation → Lease |
| Lease | Rental agreement (generic FK to Member or Guild) | GenericFK → Member/Guild, → Space |

### Key Model Patterns
- **GenericFK on Lease**: `content_type` + `object_id` allows both Member and Guild as tenants
- **Active lease filter**: `_active_lease_q()` helper builds Q objects for date-range filtering
- **QuerySet annotations**: `MemberQuerySet.with_lease_totals()`, `SpaceQuerySet.with_revenue()`
- **Auto-slugify**: Guild and Buyable auto-generate slugs from name on save
- **Member.Status**: ACTIVE, FORMER, SUSPENDED
- **Member.Role**: STANDARD, GUILD_LEAD, WORK_TRADE, EMPLOYEE, CONTRACTOR, VOLUNTEER

## URL Routing

### Root (plfog/urls.py)
| URL | View | Auth | Purpose |
|-----|------|------|---------|
| `/admin/` | Django admin | Staff | Unfold admin dashboard |
| `/accounts/` | allauth | — | Login, signup, OAuth |
| `/guilds/` | include(membership.urls) | — | Guild routes |
| `/checkout/success/` | checkout_success | — | Stripe callback |
| `/checkout/cancel/` | checkout_cancel | — | Stripe callback |
| `/members/` | member_directory | Active member | Member directory |
| `/account/orders/` | user_orders | Login | Order history |
| `/account/profile/` | profile_edit | Active member | Edit profile |
| `/` | home | — | Landing page |
| `/health/` | health_check | — | JSON health check |

### Guild Routes (membership/urls.py)
| URL | View | Auth | Purpose |
|-----|------|------|---------|
| `/guilds/` | guild_list | — | List active guilds |
| `/guilds/<slug>/` | guild_detail | — | Guild page |
| `/guilds/<slug>/buy/<slug>/` | buyable_detail | — | Product page |
| `/guilds/<slug>/buy/<slug>/checkout/` | buyable_checkout | — | Stripe checkout |
| `/guilds/<slug>/buy/<slug>/qr/` | buyable_qr | — | QR code SVG |
| `/guilds/<slug>/manage/` | guild_manage | Lead/Staff | Manage buyables |
| `/guilds/<slug>/manage/add/` | buyable_add | Lead/Staff | Add buyable |
| `/guilds/<slug>/manage/<slug>/edit/` | buyable_edit | Lead/Staff | Edit buyable |
| `/guilds/<slug>/manage/orders/` | guild_orders | Lead/Staff | Guild orders |
| `/guilds/<slug>/manage/orders/<pk>/` | order_detail | Lead/Staff | Order detail |

## Auth & Permissions (membership/views.py)

### Helper Functions
- `_get_active_member(request)` → Returns Member or raises 403. Checks: authenticated + Member exists + status ACTIVE
- `_get_lead_guild(request, slug)` → Returns Guild or raises 403. Checks: authenticated + (GuildMembership.is_lead OR user.is_staff)

### Auto-Admin (plfog/adapters.py)
- `ADMIN_DOMAINS` env var: comma-separated email domains
- Users with matching email domain get `is_staff=True` + `is_superuser=True` on social login
- Staff users redirect to `/admin/` after login

## Templates

### Base Layout (templates/base.html)
- Nav: Brand, Guilds, Members (auth), User dropdown (Profile, Orders, Admin if staff, Logout)
- Alpine.js for dropdown interactivity
- Django messages display
- Footer: "Do It Together" tagline

### Template Naming Convention
All membership templates in `templates/membership/`. Named to match their view function.

## Testing

- Framework: pytest + pytest-describe (BDD style)
- Pattern: `describe_*` blocks with `it_*` test functions
- Files: `tests/**/*_spec.py`
- Key test files:
  - `views_spec.py` — Guild/buyable view tests (largest)
  - `member_views_spec.py` — Member directory, profile
  - `models_spec.py` — Model properties and methods
  - `querysets_spec.py` — Annotation tests

## Tech Stack
- Django 5.1+, Python 3.13
- django-allauth (email + Google/GitHub/Discord OAuth)
- django-unfold (admin theme)
- Stripe (checkout sessions)
- Alpine.js (frontend interactivity)
- WhiteNoise (static files)
- SQLite (dev) / PostgreSQL (prod via DATABASE_URL)
- Sentry (error tracking via SENTRY_DSN)
