# Mailchimp Implementation Plan

**Date:** 2026-05-05
**Owner:** TBD
**Audience:** the engineer implementing this — read top to bottom before touching code.

This plan turns the Mailchimp config scaffolding (already merged) into a working
integration covering three jobs:

1. **Auto-subscribe class registrants** to a Mailchimp audience (with opt-in).
2. **Standalone newsletter signup** so anyone (member or not) can subscribe from
   the public site.
3. **Member auto-subscribe** when a Past Lives member account is created or
   activated.

It deliberately does **not** propose moving transactional emails (registration
confirmation, reminders) into Mailchimp. Those stay in Anymail/Resend — see
*"What this plan does not do"* at the bottom.

---

## 1. Background — what already exists

| Piece | Location | State |
|---|---|---|
| `SiteConfiguration.mailchimp_api_key` | `core/models.py:78` | ✅ field + admin |
| `SiteConfiguration.mailchimp_list_id` | `core/models.py:84` | ✅ field + admin |
| `Registration.subscribed_to_mailchimp` (bool) | `classes/models.py:415` | ✅ field, never written |
| Subscribe call | nowhere | ❌ to build |
| Mailchimp SDK in requirements | n/a | ❌ to add (or use plain `requests`) |
| Newsletter opt-in checkbox on registration form | n/a | ❌ to build |
| Standalone newsletter signup | n/a | ❌ to build |

**Registration model fields available** (`classes/models.py:380-419`):
`first_name`, `last_name`, `email`, `phone`, `pronouns`, `prior_experience`,
`looking_for`, plus a `member` FK that's resolved by email match.

**Registration creation paths** (`classes/views.py`):
- Free class → `Registration.Status.CONFIRMED` immediately at `views.py:228-240`.
- Paid class → Stripe Checkout, confirmed in `webhook_handlers.handle_checkout_session_completed:77`.

Both call `send_registration_confirmation(registration)` after confirmation.
**That's the natural insertion point for the subscribe call.**

---

## 2. Decisions to lock in before coding

### 2a. Client choice

**Recommendation: plain `requests`.** Mailchimp's v3 REST API is small, well
documented, and we'd touch maybe 2 endpoints. Avoiding `mailchimp-marketing`
saves us a transitive dep chain and gives us total control over error handling.

Auth: HTTP Basic with username `anystring` and password = the API key.
Datacenter: parse from API key suffix (`abc123-us17` → `us17`) and build
`https://us17.api.mailchimp.com/3.0/...`.

If the team prefers an SDK, swap in `mailchimp-marketing>=3.0`. The shape of
the wrapper module below stays the same either way.

### 2b. Opt-in vs opt-out (consent)

**Recommendation: explicit opt-in checkbox, default unchecked.** GDPR/CASL
land hates default-checked. Past Lives is small enough today that this
doesn't matter legally, but unsubscribe-pain is real and the cost of being
explicit is one extra click.

Checkbox label: *"Keep me in the loop — email me about future classes,
events, and what's happening at Past Lives."*

### 2c. Single vs double opt-in

**Recommendation: single opt-in (`status: "subscribed"`).** The user already
gave us their email and a deliberate checkbox click on a Past Lives form;
sending a "confirm your subscription" email afterward feels like spam. If
deliverability becomes a problem we revisit.

(Mailchimp lets you choose per-call: `status: "pending"` for double opt-in,
`status: "subscribed"` for single.)

### 2d. Where the audience lives

One **single audience** in Mailchimp configured via `mailchimp_list_id`.
Use **tags** to segment downstream:

| Source | Tag |
|---|---|
| Class registration auto-subscribe | `class-registrant` |
| Standalone newsletter signup | `newsletter` |
| Member auto-subscribe | `member` |

Tags are cheap, easy to query in Mailchimp, and let Lyle send "hey class
registrants" or "hey members" emails without juggling multiple audiences.

### 2e. Failure mode

Mailchimp must **never block** registration confirmation. The flow is:

> save Registration → confirm payment → send confirmation email → *try* to
> subscribe → if it fails, log and move on.

The user's transactional flow is sacred. Logging + a `subscribed_to_mailchimp`
flag set only on success means we can backfill later.

---

## 3. Architecture

```
┌─────────────────────────┐       ┌────────────────────────┐
│ classes/views.py        │       │ classes/webhook_       │
│ (free class path)       │       │ handlers.py            │
│                         │       │ (paid class path)      │
└────────────┬────────────┘       └────────────┬───────────┘
             │ on confirm                       │ on confirm
             ▼                                  ▼
        ┌────────────────────────────────────────────┐
        │ classes/services/mailchimp_subscribe.py    │
        │   def subscribe_registration(reg)          │
        └────────────────────────┬───────────────────┘
                                 │
                                 ▼
                ┌─────────────────────────────────┐
                │ core/integrations/mailchimp.py  │
                │   class MailchimpClient         │
                │     .subscribe(email, ...)      │
                │     .add_tags(email, tags)      │
                └─────────────────────────────────┘

   ┌────────────────────────┐
   │ hub/views/newsletter.py│
   │ (standalone signup)    │
   └────────┬───────────────┘
            │
            ▼ same client
            
   ┌──────────────────────────────────────────┐
   │ membership/signals.py                    │
   │ post_save Member → enqueue subscribe(    │
   │   email, tags=["member"])                │
   └──────────────────────────────────────────┘
```

**Layering:** `core/integrations/mailchimp.py` is the *only* place that
speaks HTTP. Everything else is a thin orchestrator — matches the
"fat models, skinny views" rule and keeps the HTTP mock surface small for
tests.

---

## 4. Build steps

### Step 1 — `core/integrations/mailchimp.py` (new)

```python
"""Mailchimp v3 REST client.

Reads credentials from `core.models.SiteConfiguration`. All public methods
return `bool` and never raise — failures are logged, the caller decides
what to do. We deliberately do NOT pull in the official SDK; the v3 API
surface we need is tiny and a bare client keeps tests fast.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MailchimpConfig:
    api_key: str
    list_id: str

    @property
    def datacenter(self) -> str:
        # API keys look like 'abc123def-us17' — datacenter is the suffix.
        if "-" not in self.api_key:
            raise ValueError("Mailchimp API key is malformed (missing datacenter suffix)")
        return self.api_key.rsplit("-", 1)[1]

    @property
    def base_url(self) -> str:
        return f"https://{self.datacenter}.api.mailchimp.com/3.0"


class MailchimpClient:
    """Minimal Mailchimp v3 client. Disabled when config is missing."""

    def __init__(self, config: MailchimpConfig | None) -> None:
        self.config = config

    @classmethod
    def from_site_config(cls) -> "MailchimpClient":
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        if not site.mailchimp_api_key or not site.mailchimp_list_id:
            return cls(config=None)
        return cls(config=MailchimpConfig(
            api_key=site.mailchimp_api_key,
            list_id=site.mailchimp_list_id,
        ))

    @property
    def enabled(self) -> bool:
        return self.config is not None

    def subscribe(
        self,
        *,
        email: str,
        first_name: str = "",
        last_name: str = "",
        tags: list[str] | None = None,
    ) -> bool:
        """Add (or update) a subscriber. Idempotent — re-subscribing succeeds."""
        if not self.enabled:
            return False
        assert self.config is not None
        subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
        url = f"{self.config.base_url}/lists/{self.config.list_id}/members/{subscriber_hash}"
        payload: dict[str, Any] = {
            "email_address": email,
            "status_if_new": "subscribed",  # single opt-in
            "merge_fields": {"FNAME": first_name, "LNAME": last_name},
        }
        if tags is not None:
            payload["tags"] = tags
        try:
            response = requests.put(
                url,
                json=payload,
                auth=("anystring", self.config.api_key),
                timeout=5.0,
            )
        except requests.RequestException as exc:
            logger.warning("Mailchimp subscribe network error for %s: %s", email, exc)
            return False
        if response.ok:
            return True
        logger.warning(
            "Mailchimp subscribe failed for %s: %s %s",
            email, response.status_code, response.text[:300],
        )
        return False
```

Key design choices:
- **`PUT /lists/{id}/members/{hash}`** is the upsert endpoint — idempotent,
  no "Member Exists" 400 to special-case. (`POST /members` would 400 on
  re-subscribe.)
- **5-second timeout**: the registration confirmation user is waiting for a
  page redirect; we cannot afford to hang.
- **`status_if_new: "subscribed"`** only sets status on first creation —
  doesn't re-subscribe people who unsubscribed (respects their choice).
- **No retry loop**: if it fails, the caller logs and moves on. We can build
  a backfill management command later (see Step 6).

### Step 2 — `classes/services/mailchimp_subscribe.py` (new)

```python
"""Bridge between Registration and the Mailchimp client."""

from __future__ import annotations

import logging

from classes.models import Registration

logger = logging.getLogger(__name__)


def subscribe_registration(registration: Registration) -> None:
    """Subscribe a confirmed registrant if they opted in.

    Called from the free-class flow and the Stripe webhook handler.
    Sets `subscribed_to_mailchimp=True` on success. Never raises.
    """
    if not registration.wants_newsletter:
        return
    if registration.subscribed_to_mailchimp:
        return  # already done — webhook redelivery, etc.

    from core.integrations.mailchimp import MailchimpClient

    client = MailchimpClient.from_site_config()
    if not client.enabled:
        return
    success = client.subscribe(
        email=registration.email,
        first_name=registration.first_name,
        last_name=registration.last_name,
        tags=["class-registrant"],
    )
    if success:
        registration.subscribed_to_mailchimp = True
        registration.save(update_fields=["subscribed_to_mailchimp"])
```

### Step 3 — Add the opt-in field

**Migration** — `Registration.wants_newsletter` boolean (default False):

```python
wants_newsletter = models.BooleanField(
    default=False,
    help_text="Did the registrant tick the newsletter opt-in box at signup?",
)
```

**Form change** in `classes/forms.py:RegistrationForm`:

```python
wants_newsletter = forms.BooleanField(
    required=False,
    initial=False,
    label="Keep me in the loop — email me about future classes, events, and what's happening at Past Lives.",
)

class Meta:
    model = Registration
    fields = [
        "first_name", "last_name", "pronouns", "email", "phone",
        "prior_experience", "looking_for", "wants_newsletter",
    ]
```

**Template:** Add the checkbox to `templates/classes/public/register.html`,
near the bottom of the form just above the waiver section. Use the same
`{% include "components/form_field.html" %}` pattern other fields use.

### Step 4 — Wire it into both confirmation paths

**Free class** (`classes/views.py:239` — right after `send_registration_confirmation(registration)`):

```python
from classes.services.mailchimp_subscribe import subscribe_registration
send_registration_confirmation(registration)
subscribe_registration(registration)
return redirect("classes:register_success", slug=offering.slug)
```

**Paid class** (`classes/webhook_handlers.py:77`):

```python
from classes.services.mailchimp_subscribe import subscribe_registration
send_registration_confirmation(registration)
subscribe_registration(registration)
```

### Step 5 — Standalone newsletter signup

**View + form + URL** (new — pick one of two patterns):

**Pattern A — public page at `/newsletter/`.** Add to `core/urls.py` since
this isn't classes-specific. New `core/forms.py:NewsletterSignupForm` with
`email`, `first_name`, optional `last_name`. View posts to it, calls
`MailchimpClient.from_site_config().subscribe(..., tags=["newsletter"])`,
shows a success/failure page.

**Pattern B — embed-only.** Add an inline newsletter widget to the home
page footer + the classes public list page (above-the-fold). Submit posts
via fetch to a small JSON endpoint, returns `{"ok": true|false}`, JS swaps
in a thank-you message.

Recommend **A first** (single dedicated page is easier to test and link to)
**then layer B** as time permits.

### Step 6 — Member auto-subscribe (lower priority)

In `membership/signals.py`, on `Member.post_save`, when status flips to
`Member.Status.ACTIVE`, fire-and-forget call to `MailchimpClient.subscribe`
with `tags=["member"]`. This keeps Mailchimp roughly in sync with active
membership without a one-shot backfill.

**One-shot backfill** management command (`membership/management/commands/sync_members_to_mailchimp.py`):

```bash
python manage.py sync_members_to_mailchimp --status=active --dry-run
python manage.py sync_members_to_mailchimp --status=active
```

Idempotent (the upsert endpoint handles it). Useful as a one-time runner
after the integration first goes live.

### Step 7 — Tests

New `classes/spec/services/mailchimp_subscribe_spec.py` and
`tests/core/integrations/mailchimp_spec.py`. Use `respx` (already the
project standard for HTTP mocks).

```python
# tests/core/integrations/mailchimp_spec.py
import pytest
import respx
from httpx import Response

from core.integrations.mailchimp import MailchimpClient, MailchimpConfig

def describe_MailchimpClient():
    def describe_subscribe():
        @respx.mock
        def it_returns_false_when_disabled():
            client = MailchimpClient(config=None)
            assert client.subscribe(email="a@b.com") is False

        @respx.mock
        def it_puts_subscriber_to_correct_url(respx_mock):
            cfg = MailchimpConfig(api_key="abc-us17", list_id="LISTID")
            client = MailchimpClient(config=cfg)
            route = respx_mock.put(
                "https://us17.api.mailchimp.com/3.0/lists/LISTID/members/"
                "0c30407d8db4d6ad62c3fe83cd0a6c2c"  # md5("a@b.com")
            ).mock(return_value=Response(200, json={"id": "x"}))
            assert client.subscribe(email="a@b.com") is True
            assert route.called

        @respx.mock
        def it_returns_false_on_5xx(respx_mock):
            cfg = MailchimpConfig(api_key="abc-us17", list_id="LISTID")
            respx_mock.put(url__regex=r".*").mock(return_value=Response(500))
            assert MailchimpClient(config=cfg).subscribe(email="a@b.com") is False

        @respx.mock
        def it_returns_false_on_network_error(respx_mock):
            cfg = MailchimpConfig(api_key="abc-us17", list_id="LISTID")
            respx_mock.put(url__regex=r".*").mock(side_effect=Exception("boom"))
            assert MailchimpClient(config=cfg).subscribe(email="a@b.com") is False
```

```python
# classes/spec/services/mailchimp_subscribe_spec.py
def describe_subscribe_registration():
    def it_does_nothing_when_user_did_not_opt_in(db, mocker):
        spy = mocker.patch("core.integrations.mailchimp.MailchimpClient.subscribe")
        reg = RegistrationFactory(wants_newsletter=False)
        subscribe_registration(reg)
        spy.assert_not_called()

    def it_skips_already_subscribed(db, mocker):
        spy = mocker.patch("core.integrations.mailchimp.MailchimpClient.subscribe")
        reg = RegistrationFactory(wants_newsletter=True, subscribed_to_mailchimp=True)
        subscribe_registration(reg)
        spy.assert_not_called()

    def it_calls_client_and_sets_flag_on_success(db, mocker, site_with_mailchimp):
        mocker.patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        )
        reg = RegistrationFactory(wants_newsletter=True)
        subscribe_registration(reg)
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is True

    def it_does_not_set_flag_on_failure(db, mocker, site_with_mailchimp):
        mocker.patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=False,
        )
        reg = RegistrationFactory(wants_newsletter=True)
        subscribe_registration(reg)
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is False
```

Plus end-to-end webhook test: confirm a paid registration → assert subscribe
was called with `tags=["class-registrant"]`.

---

## 5. Rollout

| Step | Risk | Rollback |
|---|---|---|
| Merge with `mailchimp_api_key` blank | None — disabled state is no-op | n/a |
| Set staging API key + list ID | Low — only sub'd test addresses | Clear the field |
| Run a real registration on staging | Low — one address joins audience | Manual unsubscribe in Mailchimp |
| Set prod API key + list ID | Medium — first real subscribers | Clear the field; Mailchimp keeps the audience |
| Backfill existing members | Medium — sends a wave of welcome emails (depends on your Mailchimp automations) | Disable Mailchimp automations during backfill |

**Pre-prod checklist:**

- [ ] Test API key is for a test audience — never the real one.
- [ ] Mailchimp "Welcome email" automation set up the way you want before flipping the switch.
- [ ] GDPR fields populated in Mailchimp (privacy policy URL, etc.).
- [ ] One real round-trip on staging confirmed: registration → audience grows by 1 → `subscribed_to_mailchimp = True`.
- [ ] Failure path verified: temporarily set a bogus API key, register, confirm registration succeeds and the app does not error.

---

## 6. What this plan does *not* do

- **Does not move transactional emails into Mailchimp.** Class
  confirmations and reminders stay in Anymail/Resend (`classes/emails.py`).
  Reasons: deliverability, simpler templating, no extra paid Mailchimp
  feature (Transactional/Mandrill is a separate product), and we already
  control transactional formatting in templates.
- **Does not build interest groups / preference centers.** Tags cover the
  current need.
- **Does not auto-unsubscribe** when a member is deactivated. People who
  signed up explicitly should keep getting newsletter content unless they
  unsubscribe themselves — which Mailchimp's footer link already supports.
- **Does not replace the existing Site Settings UI.** The api_key + list_id
  inputs already exist; we just start using them.

---

## 7. Effort estimate

| Step | Estimate |
|---|---|
| 1. `core/integrations/mailchimp.py` + tests | 2 hr |
| 2. `classes/services/mailchimp_subscribe.py` + tests | 1 hr |
| 3. Migration + form opt-in + template | 1 hr |
| 4. Wire into both confirmation paths + integration tests | 1 hr |
| 5. Standalone newsletter signup page (Pattern A) | 2 hr |
| 6. Member auto-subscribe signal + backfill command | 2 hr |
| 7. Manual end-to-end on staging | 1 hr |
| **Total** | **~10 hr** |

Steps 1-4 are the must-have (matches the admin help_text promise).
Steps 5-6 are nice-to-have that round out the feature.

---

## 8. Files this plan will create / modify

**New:**
- `core/integrations/__init__.py`
- `core/integrations/mailchimp.py`
- `classes/services/__init__.py`
- `classes/services/mailchimp_subscribe.py`
- `classes/migrations/00XX_registration_wants_newsletter.py`
- `tests/core/integrations/mailchimp_spec.py`
- `classes/spec/services/mailchimp_subscribe_spec.py`
- `core/views.py` — `newsletter_signup` view (Pattern A)
- `core/forms.py` — `NewsletterSignupForm`
- `templates/core/newsletter_signup.html`
- `membership/management/commands/sync_members_to_mailchimp.py`

**Modified:**
- `classes/models.py` — add `wants_newsletter`
- `classes/forms.py:RegistrationForm` — add opt-in + Meta.fields
- `classes/views.py:register` — call `subscribe_registration` after free-class confirm
- `classes/webhook_handlers.py:handle_checkout_session_completed` — call after paid confirm
- `templates/classes/public/register.html` — render new field
- `core/urls.py` — `/newsletter/` route
- `membership/signals.py` — Member→Mailchimp sync
- `requirements.txt` — no change (using `requests`, already present transitively via `pyairtable`/`stripe`; if not, add `requests>=2.31`)
- `CODEBASE_INDEX.md` — add Mailchimp row to External Integrations

---

## 9. Open questions for the product owner

1. **Default-checked or default-unchecked?** Plan assumes unchecked. Confirm.
2. **Single audience + tags, or one audience per source?** Plan assumes single. Confirm.
3. **Should the existing class-emails (confirmation/reminder) move to Mailchimp?** Plan says no.
4. **Welcome automation in Mailchimp** — does Lyle want one set up before this ships, so new subscribers get an immediate "thanks for signing up" message?
5. **Backfill members** — yes/no on the one-shot command in step 6?
