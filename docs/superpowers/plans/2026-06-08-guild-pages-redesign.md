# Guild Pages Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the guild page to match the polish of the class detail page — a hero, image gallery (up to 10), YouTube embed, meeting schedule, FAQ, announcements, links, and an opt-in members roster — all managed by guild leads/admins from a full edit page. Also adds an admin "site announcement" poster.

**Architecture:** Five new `membership` models (`GuildImage`, `GuildFAQItem`, `GuildLink`, `GuildAnnouncement`, `GuildMembership`). Four new `Guild` fields. The current edit *modal* becomes a full edit *page* (`guild_edit` gains a GET branch). FAQ and links use Django `inlineformset_factory` (the codebase's existing pattern); gallery images mirror `ClassImage.add_gallery_images`. The detail page is rebuilt with `pl-guild-*` CSS modeled on the class detail layout. Publishing an announcement dispatches `guild_announcement`; a small admin form dispatches `site_announcement`.

**Tech Stack:** Django 5, pytest + pytest-describe, factory-boy, HTMX + Alpine. Reuses `core.files.delete_orphan_on_replace`, `core.images.normalize_field_if_uploaded`, `core.validators.validate_image_size`, and the `youtube_embed_id` filter from `classes/templatetags/classes_tags.py`. Python 3.13, ruff (120, mccabe 10), mypy, coverage `fail_under = 98`.

**Depends on Plan 2 (notifications):** `core.notifications.dispatch()` and `core.notifications.active_member_users()` must exist. This plan **completes the two triggers Plan 2 left without callers** — `guild_announcement` and `site_announcement`.

**Conventions for every task:**
- Tests are `tests/<app>/<name>_spec.py`, `it_*` inside `describe_*`, `@pytest.mark.django_db` (the guild specs use the decorator form).
- Run one test: `set -a && source .env && set +a && pytest tests/hub/guild_pages_spec.py -v`.
- Before each commit: `ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/`.
- Image-field models import: `from core.files import delete_orphan_on_replace`, `from core.images import normalize_field_if_uploaded`, `from core.validators import validate_image_size`.

---

## File Structure

**Create:**
- `templates/hub/guild_edit.html` — the full edit page (replaces the modal).
- `templates/hub/_guild_gallery.html` — lightweight gallery + lightbox partial for the detail page.
- Tests: `tests/membership/guild_content_models_spec.py`, `tests/membership/guild_membership_spec.py`, `tests/hub/guild_edit_spec.py`, `tests/hub/guild_roster_spec.py`, `tests/hub/guild_announcements_spec.py`, `tests/hub/site_announcement_spec.py`.

**Modify:**
- `membership/models.py` — add 5 models + 4 Guild fields + `Guild.add_gallery_images()` + `Guild.roster_members()`.
- `membership/factories` (`tests/membership/factories.py`) — add factories for the new models.
- `hub/forms.py` — expand `GuildEditForm`; add `GuildFAQItemFormSet`, `GuildLinkFormSet`, `GuildAnnouncementForm`, `SiteAnnouncementForm`.
- `hub/views.py` — rewrite `guild_edit` (GET+POST); add `guild_image_delete`, `guild_announcement_create`, `guild_announcement_delete`, `guild_join`, `guild_leave`; extend `guild_detail` context; add `site_announcement` post handler in `admin_site_settings`.
- `hub/urls.py` — add the new routes.
- `templates/hub/guild_detail.html` — full rebuild.
- `templates/hub/admin/site_settings.html` — add the site-announcement form.
- `static/css/hub.css` — `pl-guild-*` classes.
- Delete usage of `templates/hub/_modal_edit_guild.html` from the detail page (the file can remain unused or be removed).

---

## Task 1: `GuildImage` model + `Guild.add_gallery_images()`

**Files:**
- Modify: `membership/models.py` (add `GuildImage` after `Guild`; add method + new fields to `Guild`)
- Test: `tests/membership/guild_content_models_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/membership/guild_content_models_spec.py
"""BDD-style tests for guild content models."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from membership.models import GuildImage
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def describe_GuildImage():
    def it_orders_by_sort_order_then_created():
        guild = GuildFactory()
        b = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("b.png", _PNG), sort_order=2)
        a = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("a.png", _PNG), sort_order=1)
        assert list(guild.gallery_images.all()) == [a, b]


def describe_add_gallery_images():
    def it_creates_rows_with_incrementing_sort_order():
        guild = GuildFactory()
        guild.add_gallery_images([
            SimpleUploadedFile("1.png", _PNG),
            SimpleUploadedFile("2.png", _PNG),
        ])
        assert guild.gallery_images.count() == 2
        assert [g.sort_order for g in guild.gallery_images.all()] == [0, 1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/membership/guild_content_models_spec.py -v`
Expected: FAIL — `cannot import name 'GuildImage'`.

- [ ] **Step 3: Add the model + method**

In `membership/models.py`, add after the `Guild` class:

```python
class GuildImage(models.Model):
    """Gallery image for a guild page. Up to 10, enforced in the form."""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="gallery_images", help_text="Parent guild.")
    image = models.ImageField(
        upload_to="guilds/images/", validators=[validate_image_size], help_text="Gallery photo."
    )
    alt_text = models.CharField(max_length=255, blank=True, help_text="Accessibility description.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at"]

    def __str__(self) -> str:
        return f"Image #{self.pk} for {self.guild.name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "image")
        normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_GALLERY)
        super().save(*args, **kwargs)
```

Add this method inside the `Guild` class (after `save`):

```python
    def add_gallery_images(self, files: list[Any]) -> None:
        """Create GuildImage rows from uploaded files, appending after existing ones."""
        start = self.gallery_images.count()
        for i, img_file in enumerate(files):
            GuildImage.objects.create(guild=self, image=img_file, sort_order=start + i)
```

Confirm the helper imports exist at the top of `membership/models.py` (the `Guild.banner_image` field already uses `validate_image_size`, so `validate_image_size` is imported; add the other two if missing):

```python
from core.files import delete_orphan_on_replace  # likely already imported (Guild.save uses it)
from core.images import normalize_field_if_uploaded  # add if missing
```

- [ ] **Step 4: Migrate + run**

Run: `set -a && source .env && set +a && python manage.py makemigrations membership && pytest tests/membership/guild_content_models_spec.py -v`
Expected: migration created; tests PASS.

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check membership/models.py && mypy membership/
git add membership/models.py membership/migrations/ tests/membership/guild_content_models_spec.py
git commit -m "feat(membership): add GuildImage gallery model"
```

---

## Task 2: `GuildFAQItem` + `GuildLink` models

**Files:**
- Modify: `membership/models.py`
- Test: extend `tests/membership/guild_content_models_spec.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/membership/guild_content_models_spec.py
from membership.models import GuildFAQItem, GuildLink


def describe_GuildFAQItem():
    def it_orders_by_sort_order():
        guild = GuildFactory()
        q2 = GuildFAQItem.objects.create(guild=guild, question="Second?", answer="A", sort_order=2)
        q1 = GuildFAQItem.objects.create(guild=guild, question="First?", answer="A", sort_order=1)
        assert list(guild.faq_items.all()) == [q1, q2]


def describe_GuildLink():
    def it_orders_by_sort_order():
        guild = GuildFactory()
        l2 = GuildLink.objects.create(guild=guild, label="Wiki", url="https://w.example", sort_order=2)
        l1 = GuildLink.objects.create(guild=guild, label="Discord", url="https://d.example", sort_order=1)
        assert list(guild.links.all()) == [l1, l2]
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/membership/guild_content_models_spec.py -k "FAQ or Link" -v`
Expected: FAIL — import error.

- [ ] **Step 3: Add the models**

```python
class GuildFAQItem(models.Model):
    """A question/answer pair shown in the guild page FAQ section."""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="faq_items", help_text="Parent guild.")
    question = models.CharField(max_length=500, help_text="The question.")
    answer = models.TextField(help_text="The answer.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order"]

    def __str__(self) -> str:
        return self.question


class GuildLink(models.Model):
    """A named external link shown in the guild page sidebar."""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="links", help_text="Parent guild.")
    label = models.CharField(max_length=100, help_text="Display text, e.g. 'Discord'.")
    url = models.URLField(help_text="Destination URL.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order"]

    def __str__(self) -> str:
        return f"{self.label} ({self.guild.name})"
```

- [ ] **Step 4: Migrate + run + commit**

Run: `set -a && source .env && set +a && python manage.py makemigrations membership && pytest tests/membership/guild_content_models_spec.py -v`
Expected: PASS.

```bash
ruff format . && ruff check membership/models.py && mypy membership/
git add membership/models.py membership/migrations/ tests/membership/guild_content_models_spec.py
git commit -m "feat(membership): add GuildFAQItem + GuildLink models"
```

---

## Task 3: `GuildAnnouncement` model (fires `guild_announcement`)

**Files:**
- Modify: `membership/models.py`
- Test: extend `tests/membership/guild_content_models_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/membership/guild_content_models_spec.py
from django.contrib.auth.models import User

from membership.models import GuildAnnouncement, GuildMembership
from core.models import Notification


def describe_GuildAnnouncement():
    def it_orders_newest_first():
        guild = GuildFactory()
        a1 = GuildAnnouncement.objects.create(guild=guild, title="Old", body="b")
        a2 = GuildAnnouncement.objects.create(guild=guild, title="New", body="b")
        assert list(guild.announcements.all()) == [a2, a1]

    def it_notifies_guild_members_when_published_via_publish(db):
        from tests.membership.factories import MemberFactory

        guild = GuildFactory()
        user = User.objects.create_user(username="gm", email="gm@example.com")
        member = MemberFactory(user=user)
        GuildMembership.objects.create(guild=guild, member=member)

        GuildAnnouncement.publish(guild=guild, author=None, title="Hi", body="News")

        assert Notification.objects.filter(trigger="guild_announcement", user=user).count() == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/membership/guild_content_models_spec.py -k Announcement -v`
Expected: FAIL.

- [ ] **Step 3: Add the model with a `publish` classmethod**

```python
class GuildAnnouncement(models.Model):
    """A news post on a guild page. Publishing notifies the guild's members."""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="announcements", help_text="Parent guild.")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Who posted it.",
    )
    title = models.CharField(max_length=300, help_text="Announcement headline.")
    body = models.TextField(help_text="Announcement body.")
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.guild.name})"

    @classmethod
    def publish(cls, *, guild: "Guild", author: Any | None, title: str, body: str) -> "GuildAnnouncement":
        """Create an announcement and notify the guild's members."""
        from django.contrib.auth.models import User

        from core import notifications

        announcement = cls.objects.create(guild=guild, author=author, title=title, body=body)
        member_user_ids = guild.memberships.filter(member__user__isnull=False).values_list("member__user_id", flat=True)
        users = User.objects.filter(pk__in=list(member_user_ids))
        notifications.dispatch(
            "guild_announcement", users,
            title=f"{guild.name}: {title}", body=body[:200], url=f"/guilds/{guild.pk}/",
        )
        return announcement
```

> `GuildMembership` is referenced here but defined in Task 4. Since both land before the test for `publish` runs in this task, **do Task 4's model addition first if executing strictly in order**, or add `GuildMembership` (Task 4) and this model together. They are split only for review clarity.

- [ ] **Step 4: Migrate + run + commit**

Run: `set -a && source .env && set +a && python manage.py makemigrations membership && pytest tests/membership/guild_content_models_spec.py -k Announcement -v`
Expected: PASS (after Task 4's model exists).

```bash
ruff format . && ruff check membership/models.py && mypy membership/
git add membership/models.py membership/migrations/ tests/membership/guild_content_models_spec.py
git commit -m "feat(membership): add GuildAnnouncement with member notification"
```

---

## Task 4: `GuildMembership` model + `Guild.roster_members()`

**Files:**
- Modify: `membership/models.py`
- Test: `tests/membership/guild_membership_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/membership/guild_membership_spec.py
"""Opt-in guild membership + privacy-respecting roster."""

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError

from membership.models import GuildMembership, Member
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_GuildMembership():
    def it_is_unique_per_guild_and_member():
        guild = GuildFactory()
        member = MemberFactory()
        GuildMembership.objects.create(guild=guild, member=member)
        with pytest.raises(IntegrityError):
            GuildMembership.objects.create(guild=guild, member=member)


def describe_roster_members():
    def it_includes_listed_members_only():
        guild = GuildFactory()
        shown = MemberFactory(show_in_directory=True)
        hidden = MemberFactory(show_in_directory=False)
        GuildMembership.objects.create(guild=guild, member=shown)
        GuildMembership.objects.create(guild=guild, member=hidden)
        roster = list(guild.roster_members())
        assert shown in roster
        assert hidden not in roster

    def it_includes_public_role_members_even_if_hidden():
        guild = GuildFactory()
        lead_member = MemberFactory(show_in_directory=False)
        guild.guild_lead = lead_member
        guild.save()
        GuildMembership.objects.create(guild=guild, member=lead_member)
        assert lead_member in list(guild.roster_members())
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/membership/guild_membership_spec.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the model + roster helper**

```python
class GuildMembership(models.Model):
    """Explicit opt-in affiliation between a Member and a Guild."""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="memberships", help_text="The guild.")
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="guild_memberships", help_text="The member."
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["guild", "member"], name="uq_guildmembership_guild_member"),
        ]

    def __str__(self) -> str:
        return f"{self.member} in {self.guild.name}"
```

Add to the `Guild` class:

```python
    def roster_members(self) -> models.QuerySet[Member]:
        """Active joined members, filtered by directory privacy (mirrors member_directory)."""
        must_show = (
            models.Q(fog_role=Member.FogRole.ADMIN)
            | models.Q(fog_role=Member.FogRole.GUILD_OFFICER)
            | models.Q(led_guilds__isnull=False)
            | models.Q(instructor_slug__gt="")
        )
        return (
            Member.objects.filter(guild_memberships__guild=self, status=Member.Status.ACTIVE)
            .filter(models.Q(show_in_directory=True) | must_show)
            .distinct()
        )
```

- [ ] **Step 4: Migrate + run the membership + announcement specs + commit**

Run: `set -a && source .env && set +a && python manage.py makemigrations membership && pytest tests/membership/guild_membership_spec.py tests/membership/guild_content_models_spec.py -v`
Expected: PASS (including Task 3's `publish` test, now that `GuildMembership` exists).

```bash
ruff format . && ruff check membership/models.py && mypy membership/
git add membership/models.py membership/migrations/ tests/membership/guild_membership_spec.py
git commit -m "feat(membership): add GuildMembership + privacy-aware roster"
```

---

## Task 5: New `Guild` fields

**Files:**
- Modify: `membership/models.py` (`Guild`)
- Test: extend `tests/membership/guild_content_models_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/membership/guild_content_models_spec.py
def describe_guild_new_fields():
    def it_defaults_the_new_fields_blank():
        guild = GuildFactory()
        assert guild.youtube_url == ""
        assert guild.meeting_schedule == ""
        assert guild.contact_email == ""
        assert guild.show_members is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/membership/guild_content_models_spec.py -k new_fields -v`
Expected: FAIL — `AttributeError: youtube_url`.

- [ ] **Step 3: Add the fields**

In `Guild`, after `calendar_color` (keeping field grouping sensible):

```python
    youtube_url = models.URLField(
        blank=True, default="", help_text="Optional YouTube video shown on the guild page."
    )
    meeting_schedule = models.TextField(
        blank=True, default="", help_text="When/where the guild meets, e.g. 'Tuesdays 6pm, Studio B'."
    )
    contact_email = models.EmailField(
        blank=True, default="", help_text="Optional guild contact email shown on the page."
    )
    show_members = models.BooleanField(
        default=False, help_text="Show the opt-in members roster on the public guild page."
    )
```

- [ ] **Step 4: Migrate + run + commit**

Run: `set -a && source .env && set +a && python manage.py makemigrations membership && pytest tests/membership/guild_content_models_spec.py -k new_fields -v`
Expected: PASS.

```bash
ruff format . && ruff check membership/models.py && mypy membership/
git add membership/models.py membership/migrations/ tests/membership/guild_content_models_spec.py
git commit -m "feat(membership): add youtube_url, meeting_schedule, contact_email, show_members to Guild"
```

---

## Task 6: Add factories for the new models

**Files:**
- Modify: `tests/membership/factories.py`

- [ ] **Step 1: Add factories**

```python
# in tests/membership/factories.py
class GuildFAQItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "membership.GuildFAQItem"

    guild = factory.SubFactory(GuildFactory)
    question = factory.Sequence(lambda n: f"Question {n}?")
    answer = "An answer."


class GuildLinkFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "membership.GuildLink"

    guild = factory.SubFactory(GuildFactory)
    label = factory.Sequence(lambda n: f"Link {n}")
    url = "https://example.com"


class GuildAnnouncementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "membership.GuildAnnouncement"

    guild = factory.SubFactory(GuildFactory)
    title = factory.Sequence(lambda n: f"Announcement {n}")
    body = "Body text."


class GuildMembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "membership.GuildMembership"

    guild = factory.SubFactory(GuildFactory)
    member = factory.SubFactory(MemberFactory)
```

(Use the string `"membership.ModelName"` Meta form to avoid import churn, matching factory-boy's lazy model resolution. If the file imports models directly elsewhere, follow that style instead.)

- [ ] **Step 2: Smoke-check the factories import**

Run: `set -a && source .env && set +a && pytest tests/membership/guild_content_models_spec.py -v`
Expected: still PASS (factories just need to import cleanly).

- [ ] **Step 3: Commit**

```bash
ruff format . && ruff check tests/membership/factories.py
git add tests/membership/factories.py
git commit -m "test(membership): add factories for guild content models"
```

---

## Task 7: Expand forms (`GuildEditForm` + FAQ/Link formsets + announcement forms)

**Files:**
- Modify: `hub/forms.py`
- Test: `tests/hub/guild_edit_spec.py` (form-level cases)

- [ ] **Step 1: Expand `GuildEditForm`**

Change `GuildEditForm.Meta.fields` to add the new fields and widgets:

```python
        fields = [
            "name", "about", "banner_image", "calendar_url", "calendar_color",
            "youtube_url", "meeting_schedule", "contact_email", "show_members",
        ]
```

Add to `widgets`:

```python
            "youtube_url": forms.URLInput(attrs={"placeholder": "https://youtube.com/watch?v=..."}),
            "meeting_schedule": forms.Textarea(attrs={"rows": 2, "placeholder": "Tuesdays 6pm, Studio B"}),
            "contact_email": forms.EmailInput(attrs={"placeholder": "guild@example.com"}),
```

Add to `labels`:

```python
            "youtube_url": "YouTube video",
            "meeting_schedule": "Meeting schedule",
            "contact_email": "Contact email",
            "show_members": "Show members roster",
```

- [ ] **Step 2: Add the inline formsets + forms**

At the end of `hub/forms.py`:

```python
from django import forms as _forms  # if `forms` already imported, reuse it
from membership.models import Guild, GuildAnnouncement, GuildFAQItem, GuildLink


class GuildFAQItemForm(forms.ModelForm):
    class Meta:
        model = GuildFAQItem
        fields = ["question", "answer", "sort_order"]
        widgets = {
            "answer": forms.Textarea(attrs={"rows": 3}),
            "sort_order": forms.HiddenInput(),
        }


GuildFAQItemFormSet = forms.inlineformset_factory(
    Guild, GuildFAQItem, form=GuildFAQItemForm, extra=1, can_delete=True
)


class GuildLinkForm(forms.ModelForm):
    class Meta:
        model = GuildLink
        fields = ["label", "url", "sort_order"]
        widgets = {"sort_order": forms.HiddenInput()}


GuildLinkFormSet = forms.inlineformset_factory(
    Guild, GuildLink, form=GuildLinkForm, extra=1, can_delete=True
)


class GuildAnnouncementForm(forms.ModelForm):
    class Meta:
        model = GuildAnnouncement
        fields = ["title", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}


class SiteAnnouncementForm(forms.Form):
    """Admin form to broadcast a site-wide announcement to all members."""

    title = forms.CharField(max_length=300, label="Title")
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), label="Message")
```

- [ ] **Step 3: Write a form-level test**

```python
# tests/hub/guild_edit_spec.py
"""Guild edit form + page."""

import pytest

from hub.forms import GuildEditForm

pytestmark = pytest.mark.django_db


def describe_GuildEditForm():
    def it_accepts_the_new_fields():
        form = GuildEditForm(data={
            "name": "Painters", "about": "We paint", "calendar_color": "#4B9FEE",
            "youtube_url": "https://youtube.com/watch?v=abc12345678",
            "meeting_schedule": "Tuesdays 6pm", "contact_email": "p@example.com",
            "show_members": "on",
        })
        assert form.is_valid(), form.errors
```

- [ ] **Step 4: Run + commit**

Run: `set -a && source .env && set +a && pytest tests/hub/guild_edit_spec.py -v`
Expected: PASS.

```bash
ruff format . && ruff check hub/forms.py && mypy hub/
git add hub/forms.py tests/hub/guild_edit_spec.py
git commit -m "feat(hub): expand GuildEditForm + add FAQ/link/announcement forms"
```

---

## Task 8: Rewrite `guild_edit` as a full page (GET + POST)

**Files:**
- Modify: `hub/views.py` (`guild_edit`), `hub/urls.py` (already has the route; no change needed)
- Test: `tests/hub/guild_edit_spec.py` (view cases)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/hub/guild_edit_spec.py
from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import GuildFAQItem
from tests.membership.factories import GuildFactory, MemberFactory


def _admin_login(client, username="admin"):
    user = User.objects.create_user(username=username, password="pw", is_staff=True, is_superuser=True)
    client.login(username=username, password="pw")
    return user


def describe_guild_edit_page():
    def it_renders_the_edit_form_for_an_editor(client):
        _admin_login(client)
        guild = GuildFactory()
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 200
        assert b"Meeting schedule" in resp.content

    def it_forbids_non_editors(client):
        User.objects.create_user(username="plain", password="pw")
        client.login(username="plain", password="pw")
        guild = GuildFactory()
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 403

    def it_saves_basic_fields_and_faq_on_post(client):
        _admin_login(client)
        guild = GuildFactory(name="Old")
        resp = client.post(reverse("hub_guild_edit", args=[guild.pk]), {
            "name": "New Name", "about": "About", "calendar_color": "#4B9FEE",
            "youtube_url": "", "meeting_schedule": "Tuesdays", "contact_email": "",
            # FAQ formset (one row)
            "faq-TOTAL_FORMS": "1", "faq-INITIAL_FORMS": "0", "faq-MIN_NUM_FORMS": "0", "faq-MAX_NUM_FORMS": "1000",
            "faq-0-question": "Why?", "faq-0-answer": "Because", "faq-0-sort_order": "0",
            # Link formset (zero rows)
            "links-TOTAL_FORMS": "0", "links-INITIAL_FORMS": "0", "links-MIN_NUM_FORMS": "0", "links-MAX_NUM_FORMS": "1000",
        })
        assert resp.status_code == 302
        guild.refresh_from_db()
        assert guild.name == "New Name"
        assert GuildFAQItem.objects.filter(guild=guild, question="Why?").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/hub/guild_edit_spec.py -k edit_page -v`
Expected: FAIL (the view is POST-only and has no template).

- [ ] **Step 3: Rewrite the view**

Replace `guild_edit` in `hub/views.py`:

```python
@login_required
def guild_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Full guild edit page (GET) + handler (POST). Admin, officer, or this guild's lead only."""
    from hub.forms import GuildEditForm, GuildFAQItemFormSet, GuildLinkFormSet

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    if request.method == "POST":
        form = GuildEditForm(request.POST, request.FILES, instance=guild)
        faq_formset = GuildFAQItemFormSet(request.POST, instance=guild, prefix="faq")
        link_formset = GuildLinkFormSet(request.POST, instance=guild, prefix="links")
        if form.is_valid() and faq_formset.is_valid() and link_formset.is_valid():
            form.save()
            faq_formset.save()
            link_formset.save()
            guild.add_gallery_images(request.FILES.getlist("gallery_images"))
            messages.success(request, "Guild page updated.")
            return redirect("hub_guild_detail", pk=guild.pk)
    else:
        form = GuildEditForm(instance=guild)
        faq_formset = GuildFAQItemFormSet(instance=guild, prefix="faq")
        link_formset = GuildLinkFormSet(instance=guild, prefix="links")

    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/guild_edit.html",
        {
            **ctx,
            "guild": guild,
            "form": form,
            "faq_formset": faq_formset,
            "link_formset": link_formset,
            "announcement_form": __import__("hub.forms", fromlist=["GuildAnnouncementForm"]).GuildAnnouncementForm(),
        },
    )
```

Remove the now-obsolete `@require_POST` decorator from `guild_edit`. The `guild_banner_delete`, `guild_product_*` views are unchanged.

- [ ] **Step 4: Create a minimal edit template (so render tests pass)**

Create `templates/hub/guild_edit.html` (the full version is fleshed out in Task 10, but a working version is needed now):

```html
{% extends "hub/base.html" %}
{% block title %}Edit {{ guild.name }}{% endblock %}
{% block content %}
<h1 class="hub-page-title">Edit {{ guild.name }}</h1>
<form method="post" enctype="multipart/form-data" class="hub-form">
  {% csrf_token %}
  {% include "components/form_field.html" with field=form.name %}
  {% include "components/form_field.html" with field=form.about %}
  {% include "components/form_field.html" with field=form.meeting_schedule field_label="Meeting schedule" %}
  {% include "components/form_field.html" with field=form.youtube_url %}
  {% include "components/form_field.html" with field=form.contact_email %}
  {% include "components/form_field.html" with field=form.show_members %}

  <h3 class="hub-detail-label" style="margin-top:1.5rem;">Gallery images</h3>
  <input type="file" name="gallery_images" accept="image/*" multiple>

  <h3 class="hub-detail-label" style="margin-top:1.5rem;">FAQ</h3>
  {{ faq_formset.management_form }}
  {% for f in faq_formset %}
    <div class="hub-card">{{ f.as_p }}</div>
  {% endfor %}

  <h3 class="hub-detail-label" style="margin-top:1.5rem;">Links</h3>
  {{ link_formset.management_form }}
  {% for f in link_formset %}
    <div class="hub-card">{{ f.as_p }}</div>
  {% endfor %}

  <div style="margin-top:1.5rem;">
    <button type="submit" class="hub-btn hub-btn--primary">Save</button>
    <a href="{% url 'hub_guild_detail' guild.pk %}" class="pl-btn pl-btn--secondary">Cancel</a>
  </div>
</form>
{% endblock %}
```

- [ ] **Step 5: Run to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/hub/guild_edit_spec.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
ruff format . && ruff check hub/views.py && mypy hub/
git add hub/views.py templates/hub/guild_edit.html tests/hub/guild_edit_spec.py
git commit -m "feat(hub): turn guild edit into a full page with FAQ/link/gallery management"
```

---

## Task 9: Image delete + join/leave + announcement endpoints

**Files:**
- Modify: `hub/views.py`, `hub/urls.py`
- Test: `tests/hub/guild_roster_spec.py`, `tests/hub/guild_announcements_spec.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/hub/guild_roster_spec.py
"""Join/leave a guild + image delete."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import GuildImage, GuildMembership
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_join_leave():
    def it_lets_a_member_join_and_leave(client):
        user = User.objects.create_user(username="m", password="pw")
        MemberFactory(user=user)
        client.login(username="m", password="pw")
        guild = GuildFactory()

        client.post(reverse("hub_guild_join", args=[guild.pk]))
        assert GuildMembership.objects.filter(guild=guild, member__user=user).exists()

        client.post(reverse("hub_guild_leave", args=[guild.pk]))
        assert not GuildMembership.objects.filter(guild=guild, member__user=user).exists()


def describe_image_delete():
    def it_deletes_an_image_for_an_editor(client):
        admin = User.objects.create_user(username="a", password="pw", is_staff=True, is_superuser=True)
        client.login(username="a", password="pw")
        guild = GuildFactory()
        from django.core.files.uploadedfile import SimpleUploadedFile
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        img = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("x.png", png))
        client.post(reverse("hub_guild_image_delete", args=[guild.pk, img.pk]))
        assert not GuildImage.objects.filter(pk=img.pk).exists()
```

```python
# tests/hub/guild_announcements_spec.py
"""Posting + deleting guild announcements."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import GuildAnnouncement
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def describe_announcement_create():
    def it_posts_an_announcement_for_an_editor(client):
        User.objects.create_user(username="a", password="pw", is_staff=True, is_superuser=True)
        client.login(username="a", password="pw")
        guild = GuildFactory()
        client.post(reverse("hub_guild_announcement_create", args=[guild.pk]),
                    {"title": "Big news", "body": "We meet Friday"})
        assert GuildAnnouncement.objects.filter(guild=guild, title="Big news").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/hub/guild_roster_spec.py tests/hub/guild_announcements_spec.py -v`
Expected: FAIL — `NoReverseMatch`.

- [ ] **Step 3: Add the views**

In `hub/views.py`:

```python
@login_required
@require_POST
def guild_join(request: HttpRequest, pk: int) -> HttpResponse:
    """Current member joins this guild (idempotent)."""
    from membership.models import GuildMembership

    guild = get_object_or_404(Guild, pk=pk)
    member = _get_member(request)
    if member is not None:
        GuildMembership.objects.get_or_create(guild=guild, member=member)
        messages.success(request, f"You joined {guild.name}.")
    return redirect("hub_guild_detail", pk=guild.pk)


@login_required
@require_POST
def guild_leave(request: HttpRequest, pk: int) -> HttpResponse:
    """Current member leaves this guild."""
    from membership.models import GuildMembership

    guild = get_object_or_404(Guild, pk=pk)
    member = _get_member(request)
    if member is not None:
        GuildMembership.objects.filter(guild=guild, member=member).delete()
        messages.success(request, f"You left {guild.name}.")
    return redirect("hub_guild_detail", pk=guild.pk)


@login_required
@require_POST
def guild_image_delete(request: HttpRequest, pk: int, image_pk: int) -> HttpResponse:
    """Delete a gallery image. Editor only."""
    from membership.models import GuildImage

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    image = get_object_or_404(GuildImage, pk=image_pk, guild=guild)
    image.image.delete(save=False)
    image.delete()
    messages.success(request, "Image removed.")
    return redirect("hub_guild_edit", pk=guild.pk)


@login_required
@require_POST
def guild_announcement_create(request: HttpRequest, pk: int) -> HttpResponse:
    """Post a guild announcement (notifies members). Editor only."""
    from hub.forms import GuildAnnouncementForm
    from membership.models import GuildAnnouncement

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    form = GuildAnnouncementForm(request.POST)
    if form.is_valid():
        GuildAnnouncement.publish(
            guild=guild, author=request.user,
            title=form.cleaned_data["title"], body=form.cleaned_data["body"],
        )
        messages.success(request, "Announcement posted.")
    else:
        messages.error(request, "Could not post announcement.")
    return redirect("hub_guild_edit", pk=guild.pk)


@login_required
@require_POST
def guild_announcement_delete(request: HttpRequest, pk: int, announcement_pk: int) -> HttpResponse:
    """Delete a guild announcement. Editor only."""
    from membership.models import GuildAnnouncement

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    get_object_or_404(GuildAnnouncement, pk=announcement_pk, guild=guild).delete()
    messages.success(request, "Announcement deleted.")
    return redirect("hub_guild_edit", pk=guild.pk)
```

- [ ] **Step 4: Add the URLs**

In `hub/urls.py`, near the other guild routes:

```python
    path("guilds/<int:pk>/join/", views.guild_join, name="hub_guild_join"),
    path("guilds/<int:pk>/leave/", views.guild_leave, name="hub_guild_leave"),
    path("guilds/<int:pk>/images/<int:image_pk>/delete/", views.guild_image_delete, name="hub_guild_image_delete"),
    path("guilds/<int:pk>/announcements/", views.guild_announcement_create, name="hub_guild_announcement_create"),
    path(
        "guilds/<int:pk>/announcements/<int:announcement_pk>/delete/",
        views.guild_announcement_delete, name="hub_guild_announcement_delete",
    ),
```

- [ ] **Step 5: Run + commit**

Run: `set -a && source .env && set +a && pytest tests/hub/guild_roster_spec.py tests/hub/guild_announcements_spec.py -v`
Expected: PASS.

```bash
ruff format . && ruff check hub/ && mypy hub/
git add hub/views.py hub/urls.py tests/hub/guild_roster_spec.py tests/hub/guild_announcements_spec.py
git commit -m "feat(hub): add guild join/leave, image delete, announcement endpoints"
```

---

## Task 10: Redesigned detail page + edit page templates + CSS

**Files:**
- Modify: `templates/hub/guild_detail.html` (full rebuild), `templates/hub/guild_edit.html` (flesh out), `static/css/hub.css`
- Create: `templates/hub/_guild_gallery.html`
- Modify: `hub/views.py` `guild_detail` (add gallery/faq/links/announcements/roster to context)

- [ ] **Step 1: Extend `guild_detail` context**

In `guild_detail`, before the `return render(...)`, add:

```python
    gallery_images = guild.gallery_images.all()
    faq_items = guild.faq_items.all()
    links = guild.links.all()
    announcements = guild.announcements.all()[:5]
    roster = guild.roster_members() if guild.show_members else None
    is_member_of_guild = (
        member is not None and guild.memberships.filter(member=member).exists()
    )
```

Add these to the render context dict:
`"gallery_images": gallery_images, "faq_items": faq_items, "links": links, "announcements": announcements, "roster": roster, "is_member_of_guild": is_member_of_guild,`.

- [ ] **Step 2: Add `pl-guild-*` CSS**

Append to `static/css/hub.css` (modeled on the class detail `.cp-detail` hero + grid):

```css
.pl-guild-hero { position: relative; min-height: clamp(220px, 32vw, 380px); border-radius: 8px; overflow: hidden; margin-bottom: 1.5rem; display: flex; align-items: flex-end; }
.pl-guild-hero__img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.pl-guild-hero__overlay { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(9,46,76,0) 0%, rgba(9,46,76,0.85) 100%); }
.pl-guild-hero__content { position: relative; z-index: 1; padding: 1.5rem; color: #F4EFDD; }
.pl-guild-hero__title { font-family: Lato, sans-serif; font-weight: 900; font-size: clamp(1.75rem, 4vw, 2.75rem); margin: 0; }
.pl-guild-hero__lead { font-size: 0.9375rem; opacity: 0.9; margin-top: 0.25rem; }
.pl-guild-grid { display: grid; grid-template-columns: 1fr; gap: 1.5rem; }
@media (min-width: 1024px) { .pl-guild-grid { grid-template-columns: 1fr 340px; } }
.pl-guild-section { padding: 1.25rem 0; border-top: 1px solid var(--hub-border); }
.pl-guild-section:first-child { border-top: none; padding-top: 0; }
.pl-guild-section__h2 { font-family: Lato, sans-serif; font-weight: 700; font-size: 1.375rem; margin: 0 0 0.75rem; }
.pl-guild-video { position: relative; aspect-ratio: 16/9; border-radius: 6px; overflow: hidden; background: #000; }
.pl-guild-video iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
.pl-guild-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 0.5rem; }
.pl-guild-gallery__item { aspect-ratio: 1; border-radius: 6px; overflow: hidden; cursor: pointer; border: none; padding: 0; background: none; }
.pl-guild-gallery__item img { width: 100%; height: 100%; object-fit: cover; }
.pl-guild-lightbox { position: fixed; inset: 0; background: rgba(0,0,0,0.9); display: flex; align-items: center; justify-content: center; z-index: 100; }
.pl-guild-lightbox img { max-width: 90vw; max-height: 90vh; border-radius: 6px; }
.pl-guild-faq__q { font-weight: 600; cursor: pointer; padding: 0.75rem 0; border-top: 1px solid var(--hub-border); }
.pl-guild-faq__a { padding-bottom: 0.75rem; color: var(--hub-text-muted); }
.pl-guild-roster { display: flex; flex-direction: column; gap: 0.5rem; }
.pl-guild-links a { display: block; padding: 0.5rem 0; color: var(--color-tuscan-yellow); }
```

- [ ] **Step 3: Create the gallery partial**

```html
{# templates/hub/_guild_gallery.html #}
<div x-data="{ open: false, src: '' }">
  <div class="pl-guild-gallery">
    {% for img in gallery_images %}
    <button type="button" class="pl-guild-gallery__item" @click="src = '{{ img.image.url }}'; open = true">
      <img src="{{ img.image.url }}" alt="{{ img.alt_text }}" loading="lazy">
    </button>
    {% endfor %}
  </div>
  <div class="pl-guild-lightbox" x-show="open" x-cloak @click="open = false" @keydown.escape.window="open = false">
    <img :src="src" alt="">
  </div>
</div>
```

- [ ] **Step 4: Rebuild `guild_detail.html`**

Replace the guild info card + about with the hero + two-column grid. Keep the existing products/cart Alpine wrapper (`guildCart`) and the EYOP/cart/product modals as they are — only the *presentation* of the top of the page changes. Insert at the top of `{% block content %}` (after `{% load static %}`, add `{% load classes_tags %}`):

```html
{# ── HERO ── #}
<div class="pl-guild-hero">
  {% if guild.banner_image %}<img class="pl-guild-hero__img" src="{{ guild.banner_image.url }}" alt="{{ guild.name }}">{% endif %}
  <div class="pl-guild-hero__overlay"></div>
  <div class="pl-guild-hero__content">
    <h1 class="pl-guild-hero__title">{{ guild.name }}</h1>
    {% if guild.guild_lead %}<div class="pl-guild-hero__lead">Led by {{ guild.guild_lead.display_name }}</div>{% endif %}
    {% if can_edit_this_guild %}
      <a href="{% url 'hub_guild_edit' guild.pk %}" class="pl-btn pl-btn--secondary" style="margin-top:0.75rem;">Edit Guild Page</a>
    {% endif %}
  </div>
</div>

{# ── JOIN / LEAVE ── #}
{% if member %}
<form method="post" action="{% if is_member_of_guild %}{% url 'hub_guild_leave' guild.pk %}{% else %}{% url 'hub_guild_join' guild.pk %}{% endif %}" style="margin-bottom:1rem;">
  {% csrf_token %}
  <button type="submit" class="pl-btn {% if is_member_of_guild %}pl-btn--secondary{% else %}pl-btn--primary{% endif %}">
    {% if is_member_of_guild %}Leave this guild{% else %}Join this guild{% endif %}
  </button>
</form>
{% endif %}

<div class="pl-guild-grid">
  <main>
    {% if guild.about %}
    <section class="pl-guild-section"><h2 class="pl-guild-section__h2">About</h2><p>{{ guild.about|linebreaksbr }}</p></section>
    {% endif %}

    {% if guild.youtube_url %}{% with vid=guild.youtube_url|youtube_embed_id %}{% if vid %}
    <section class="pl-guild-section"><h2 class="pl-guild-section__h2">Watch</h2>
      <div class="pl-guild-video"><iframe src="https://www.youtube-nocookie.com/embed/{{ vid }}" title="{{ guild.name }} video" allowfullscreen></iframe></div>
    </section>
    {% endif %}{% endwith %}{% endif %}

    {% if gallery_images %}
    <section class="pl-guild-section"><h2 class="pl-guild-section__h2">Gallery</h2>
      {% include "hub/_guild_gallery.html" %}
    </section>
    {% endif %}

    {% if guild.meeting_schedule %}
    <section class="pl-guild-section"><h2 class="pl-guild-section__h2">Meetings</h2><p>{{ guild.meeting_schedule|linebreaksbr }}</p></section>
    {% endif %}

    {% if announcements %}
    <section class="pl-guild-section"><h2 class="pl-guild-section__h2">Announcements</h2>
      {% for a in announcements %}
        <div class="hub-card"><strong>{{ a.title }}</strong><div class="hub-text-muted" style="font-size:0.8125rem;">{{ a.published_at|date:"M j, Y" }}</div><p>{{ a.body|linebreaksbr }}</p></div>
      {% endfor %}
    </section>
    {% endif %}

    {% if faq_items %}
    <section class="pl-guild-section"><h2 class="pl-guild-section__h2">FAQ</h2>
      {% for item in faq_items %}
      <div x-data="{ open: false }">
        <div class="pl-guild-faq__q" @click="open = !open">{{ item.question }}</div>
        <div class="pl-guild-faq__a" x-show="open" x-cloak>{{ item.answer|linebreaksbr }}</div>
      </div>
      {% endfor %}
    </section>
    {% endif %}
  </main>

  <aside>
    {% if guild.guild_lead %}
    <div class="hub-card"><h3 class="hub-detail-label">Guild Lead</h3><div class="hub-member-name">{{ guild.guild_lead.display_name }}</div></div>
    {% endif %}

    {% if guild.show_members and roster %}
    <div class="hub-card"><h3 class="hub-detail-label">Members</h3>
      <div class="pl-guild-roster">
        {% for m in roster %}<div class="hub-member-row"><div class="hub-member-avatar">{{ m.display_name|make_list|first|upper }}</div><span class="hub-member-name">{{ m.display_name }}</span></div>{% endfor %}
      </div>
    </div>
    {% endif %}

    {% if links %}
    <div class="hub-card"><h3 class="hub-detail-label">Links</h3>
      <div class="pl-guild-links">{% for link in links %}<a href="{{ link.url }}" target="_blank" rel="noopener">{{ link.label }}</a>{% endfor %}</div>
    </div>
    {% endif %}

    {% if guild.contact_email %}
    <div class="hub-card"><h3 class="hub-detail-label">Contact</h3><a href="mailto:{{ guild.contact_email }}" style="color:var(--color-tuscan-yellow);">{{ guild.contact_email }}</a></div>
    {% endif %}
  </aside>
</div>
```

The existing **Products / Store** card and all the existing modals (`guildCart`, add-to-cart, product modal, EYOP, delete-product) stay below this grid, unchanged — move them inside or after the grid as fits. Remove the old `{% include "hub/_modal_edit_guild.html" %}` include (the modal is replaced by the edit page).

- [ ] **Step 5: Flesh out the edit page**

Extend `templates/hub/guild_edit.html` to include: the announcements manager (a "Post Announcement" form posting to `hub_guild_announcement_create`, then a list of existing announcements each with a delete button posting to `hub_guild_announcement_delete`), a banner upload/delete (reuse `components/image_field.html` like the old modal did), and per-image delete buttons for existing `guild.gallery_images` (POST to `hub_guild_image_delete`). Use `components/confirm_modal.html` for the deletes per the project's button standards.

- [ ] **Step 6: Manual smoke test**

`make server`, open a guild page as an admin: confirm the hero, sections render only when populated, gallery lightbox opens, FAQ accordions toggle, Join/Leave works, and the edit page saves everything.

- [ ] **Step 7: Run the guild specs + commit**

Run: `set -a && source .env && set +a && pytest tests/hub/ -k guild -v`
Expected: PASS.

```bash
ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/
git add templates/hub/guild_detail.html templates/hub/guild_edit.html templates/hub/_guild_gallery.html static/css/hub.css hub/views.py
git commit -m "feat(hub): redesign guild detail + edit pages"
```

---

## Task 11: Site announcement poster (closes `site_announcement`)

**Files:**
- Modify: `hub/views.py` (`admin_site_settings`), `templates/hub/admin/site_settings.html`
- Test: `tests/hub/site_announcement_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/hub/site_announcement_spec.py
"""Admin can broadcast a site-wide announcement."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Notification
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_site_announcement():
    def it_broadcasts_to_all_active_members(client):
        member_user = User.objects.create_user(username="m", email="m@example.com")
        MemberFactory(user=member_user)
        admin = User.objects.create_user(username="a", password="pw", is_staff=True, is_superuser=True)
        client.login(username="a", password="pw")
        client.post(reverse("hub_admin_site_settings"), {
            "form_id": "site_announcement", "title": "Closed Monday", "body": "We're closed for the holiday.",
        })
        assert Notification.objects.filter(trigger="site_announcement", user=member_user).exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/hub/site_announcement_spec.py -v`
Expected: FAIL.

- [ ] **Step 3: Handle the form in `admin_site_settings`**

In `hub/views.py` `admin_site_settings`, add a `form_id == "site_announcement"` POST branch (mirror how that view already disambiguates its other forms — read it first with `grep -n "def admin_site_settings" hub/views.py`):

```python
    if request.method == "POST" and request.POST.get("form_id") == "site_announcement":
        from core import notifications
        from hub.forms import SiteAnnouncementForm

        form = SiteAnnouncementForm(request.POST)
        if form.is_valid():
            notifications.dispatch(
                "site_announcement",
                notifications.active_member_users(),
                title=form.cleaned_data["title"],
                body=form.cleaned_data["body"],
                url="/",
            )
            messages.success(request, "Announcement sent to all members.")
        return redirect("hub_admin_site_settings")
```

Pass a `SiteAnnouncementForm()` instance into the template context for GET rendering.

- [ ] **Step 4: Add the form to the settings template**

In `templates/hub/admin/site_settings.html`, add a card:

```html
<div class="hub-card">
  <h2>Broadcast Announcement</h2>
  <p class="hub-text-muted">Send a notification to every active member.</p>
  <form method="post">
    {% csrf_token %}
    <input type="hidden" name="form_id" value="site_announcement">
    {% include "components/form_field.html" with field=site_announcement_form.title %}
    {% include "components/form_field.html" with field=site_announcement_form.body %}
    <button type="submit" class="hub-btn hub-btn--primary">Send to all members</button>
  </form>
</div>
```

Add `"site_announcement_form": SiteAnnouncementForm()` to the view's render context (import it at the top of the branch or function).

- [ ] **Step 5: Run + commit**

Run: `set -a && source .env && set +a && pytest tests/hub/site_announcement_spec.py -v`
Expected: PASS.

```bash
ruff format . && ruff check hub/ && mypy hub/
git add hub/views.py templates/hub/admin/site_settings.html tests/hub/site_announcement_spec.py
git commit -m "feat(hub): add admin site-wide announcement broadcast"
```

---

## Task 12: Admin registration + full suite + coverage

**Files:**
- Modify: `membership/admin.py` (if it auto-registers, the new models appear automatically; otherwise register them)

- [ ] **Step 1: Confirm the new models are admin-registered**

Check `membership/admin.py` (`grep -n "register\|get_models" membership/admin.py`). The project auto-registers app models (see CLAUDE.md "Admin Auto-Registration"). Confirm `GuildImage`, `GuildFAQItem`, `GuildLink`, `GuildAnnouncement`, `GuildMembership` show in Django admin via `manage.py check` + a quick admin GET in a test, or add explicit registrations if the app doesn't auto-register.

- [ ] **Step 2: Run the whole suite with coverage**

Run: `set -a && source .env && set +a && pytest`
Expected: all green; coverage ≥ 98%. Likely gaps: the `guild_join`/`guild_leave` `member is None` branches, `roster_members` with no members, the announcement-form-invalid branch, the gallery-empty render path. Add `it_*` cases for any uncovered lines.

- [ ] **Step 3: Lint + type**

Run: `ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/`
Expected: clean.

- [ ] **Step 4: Final commit**

```bash
git add tests/ membership/admin.py
git commit -m "test(hub): cover guild-page edge branches to 98%"
```

---

## Self-Review Checklist (run before handing off)

- [ ] **Spec coverage:** GuildImage gallery ✓ (T1), FAQ + Links ✓ (T2), Announcements ✓ (T3), GuildMembership + roster ✓ (T4), new Guild fields ✓ (T5), factories ✓ (T6), expanded forms ✓ (T7), full edit page ✓ (T8), join/leave + image-delete + announcement endpoints ✓ (T9), redesigned detail + edit templates + CSS ✓ (T10), site announcement ✓ (T11).
- [ ] **Closes Plan 2's deferred triggers:** `guild_announcement` fires from `GuildAnnouncement.publish` (T3); `site_announcement` fires from the admin broadcast (T11). After this plan, every key in `core/triggers.py` has a caller.
- [ ] **Privacy:** the roster derives from explicit `GuildMembership` (not votes) and filters through the same directory-visibility rule as `member_directory` (T4/T10). `guild_detail` stays public; join/leave are `@login_required`.
- [ ] **Reuse:** GuildImage mirrors ClassImage exactly (same helpers, same `add_gallery_images` shape); FAQ/Links use the existing `inlineformset_factory` pattern; the YouTube embed reuses `youtube_embed_id`.
- [ ] **Type/name consistency:** `Guild.add_gallery_images(files)`, `Guild.roster_members()`, `GuildAnnouncement.publish(*, guild, author, title, body)`, formset prefixes `faq` / `links` are used identically in views, templates, and tests.
- [ ] **Placeholder scan:** every code step shows real code; the only "read it first" instructions (T11 `admin_site_settings`) include the exact grep to locate the existing form-dispatch pattern to mirror.

---

## Execution Handoff

Plan complete. Run it **in a fresh window**, after Plans 1 and 2 are merged — `core.email.send` (Plan 1) and `core.notifications.dispatch` + `active_member_users` (Plan 2) must exist first.

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — one session with checkpoints.

This is the last of the three v2.4.0 plans. With all three executed, the spec at `docs/superpowers/specs/2026-06-08-notifications-guilds-activity-design.md` is fully implemented.
