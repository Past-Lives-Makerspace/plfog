"""Seed (and remove) demo personas + classes for product demos.

The three personas:

1. **Returning student (non-member)** — has a past confirmed registration and an
   upcoming one. Logs into ``book.pastlives.space``.
2. **Instructor** — owns three classes: one in the past (with confirmed
   registrants), one current free class (with two registrants), one upcoming
   paid class (with one confirmed + one pending registration). Logs into the
   instructor portal.
3. **Guest** — no User account, just a single confirmed registration with a
   known order number + last name so the guest-lookup flow can be demoed.

Safety design (so this is safe to run on prod):
- Every demo email ends in ``@pastlives.demo`` — an unregistered TLD that will
  never collide with a real address.
- Every demo class/category/instructor has a slug starting with ``demo-``.
- ``--remove`` cleanly tears everything down by querying those two patterns.
- ``get_or_create`` everywhere — re-running ``seed`` is idempotent.
- Direct ORM creation bypasses Mailchimp/Stripe/email side-effects entirely,
  so a prod seed does not pollute external services.
- **Registration questions are seeded only when ``DEBUG`` is on.** They are
  *global* (no ``demo-`` scoping is possible), so on a real environment they
  would surface on every registrant's form. Questions are CMS-managed via the
  admin in real environments; demo seeding stays in local dev. ``--remove``
  still tears them down anywhere so a stale dev seed can always be cleaned up.

Usage:

    python manage.py demo_data                # create / refresh
    python manage.py demo_data --status       # show what's currently seeded
    python manage.py demo_data --remove       # tear down

Default password for all logins is ``demo-pass-2026`` — override with
``--password`` if you need something stronger for a prod demo.
"""

from __future__ import annotations

from datetime import time, timedelta
from pathlib import Path
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from classes.models import (
    Category,
    ClassApproval,
    ClassImage,
    ClassOffering,
    ClassSession,
    DiscountCode,
    Registration,
    RegistrationQuestion,
    Waiver,
)
from core.models import UserProfile

DEMO_EMAIL_DOMAIN = "pastlives.demo"
DEMO_SLUG_PREFIX = "demo-"
DEMO_PASSWORD_DEFAULT = "demo-pass-2026"

# Persona identifiers — these double as the email local-part so cleanup is mechanical.
PERSONA_STUDENT_EMAIL = f"student@{DEMO_EMAIL_DOMAIN}"
PERSONA_INSTRUCTOR_EMAIL = f"instructor@{DEMO_EMAIL_DOMAIN}"
PERSONA_GUEST_EMAIL = f"guest@{DEMO_EMAIL_DOMAIN}"

# Predictable order numbers for the guest-lookup demo.
# Characters must be in the unambiguous alphabet [A-HJ-NP-Z2-9] (no I, O, 0, 1).
GUEST_ORDER_NUMBER = "PL-DEM2-26"
STUDENT_PAST_ORDER_NUMBER = "PL-DMP2-26"
STUDENT_FUTURE_ORDER_NUMBER = "PL-DMF2-26"

# --- Local-dev-only personas + data (gated on settings.DEBUG) ----------------
# The blocks below create ACTIVE demo Members, push orientation config onto real
# guilds, and trigger no external side effects. They run on local dev only so a
# real environment's active-member count and live guild config stay untouched.

# The "test member" the QA flow logs in as to browse classes and book orientations.
PERSONA_MEMBER_EMAIL = f"member@{DEMO_EMAIL_DOMAIN}"

# Extra members who populate orientation rosters (so a slot shows more than one
# name and there's a completed/oriented record to look at). (local-part, first, last)
ORIENTATION_BOOKER_PERSONAS: list[tuple[str, str, str]] = [
    ("orient-ana", "Ana", "Rivera"),
    ("orient-ben", "Ben", "Cho"),
    ("orient-cara", "Cara", "Nguyen"),
    ("orient-dev", "Dev", "Patel"),
]

# Real guilds we switch orientation on for the demo (chosen because they already
# carry GuildOrientationSettings rows in the pulled data). `--remove` re-disables
# only the ones it left empty, so a guild with its own (non-demo) rules is spared.
DEMO_ORIENTATION_GUILDS = ["Ceramics Guild", "Gardeners Guild", "Jewelry Guild"]

# Embedded in every demo availability rule + slot `location` so teardown can find
# exactly our rows and never touch a guild's pre-existing orientation data.
DEMO_ORIENTATION_MARKER = "[DEMO]"

# Source images for class hero/gallery attachment live here, under MEDIA_ROOT.
# Populate with scripts/fetch_demo_images.sh; the seed skips attachment if absent.
DEMO_IMAGE_DIRNAME = "demo_seed"


class Command(BaseCommand):
    help = "Create or remove demo personas + classes for product demos."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--remove", action="store_true", help="Tear down the demo data instead of creating it.")
        parser.add_argument("--status", action="store_true", help="Show what's currently seeded; make no changes.")
        parser.add_argument(
            "--password",
            default=DEMO_PASSWORD_DEFAULT,
            help=f"Login password for all demo users (default: {DEMO_PASSWORD_DEFAULT}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["status"]:
            self._print_status()
            return
        if options["remove"]:
            self._remove()
            return
        self._seed(password=options["password"])

    # --- Seed ---------------------------------------------------------------

    @transaction.atomic
    def _seed(self, *, password: str) -> None:
        self.stdout.write(self.style.NOTICE("Seeding demo data..."))
        category = self._ensure_category()
        instructor = self._ensure_instructor(password=password)
        past_class, current_free_class, future_paid_class = self._ensure_classes(category, instructor)
        student = self._ensure_student(password=password)
        self._ensure_student_registrations(student, past_class, future_paid_class)
        self._ensure_instructor_class_rosters(past_class, current_free_class, future_paid_class)
        self._ensure_guest_registration(current_free_class)
        self._ensure_discount_codes()

        # Everything below is local-dev only. Registration questions are global
        # (no demo- scoping), demo Members are ACTIVE, and the orientation seed
        # pushes config onto real guilds — none of which should touch a real env.
        member = None
        pending_class = None
        orientation_summary: list[str] = []
        if settings.DEBUG:
            self._ensure_registration_questions()
            self._attach_images(
                current_free_class,
                hero="hero_intro.jpg",
                gallery=("gallery_1.jpg", "gallery_2.jpg", "gallery_3.jpg", "gallery_4.jpg"),
            )
            self._attach_images(future_paid_class, hero="hero_advanced.jpg")
            member = self._ensure_member_persona(password=password)
            pending_class = self._ensure_pending_class(category, instructor)
            self._attach_images(pending_class, hero="hero_pending.jpg", gallery=("gallery_4.jpg", "gallery_3.jpg"))
            orientation_summary = self._ensure_orientations(member, password=password)
        else:
            self.stdout.write(
                self.style.WARNING(
                    "  Local-dev extras skipped (DEBUG off): registration questions, demo member, "
                    "class images, pending-approval class, orientations."
                )
            )

        self.stdout.write(self.style.SUCCESS("\nDemo data ready. Log in details:"))
        self.stdout.write(f"  Student (non-member): {PERSONA_STUDENT_EMAIL}  /  password: {password}")
        self.stdout.write(f"  Instructor:           {PERSONA_INSTRUCTOR_EMAIL}  /  password: {password}")
        if member is not None:
            self.stdout.write(f"  Member (orientations): {PERSONA_MEMBER_EMAIL}  /  password: {password}")
        self.stdout.write("\nGuest lookup (no login):")
        self.stdout.write(f"  Last name: Guest    Order #: {GUEST_ORDER_NUMBER}")
        if pending_class is not None:
            self.stdout.write(f"\nPending admin approval: {pending_class.title}  (slug: {pending_class.slug})")
        if orientation_summary:
            self.stdout.write("\nOrientations (enabled on real guilds — book/confirm in the hub to fire emails):")
            for line in orientation_summary:
                self.stdout.write(f"  • {line}")
        self.stdout.write("\nRun `python manage.py demo_data --remove` when you're done.")

    def _ensure_category(self) -> Category:
        category, _ = Category.objects.get_or_create(
            slug=f"{DEMO_SLUG_PREFIX}glassblowing",
            defaults={"name": "[DEMO] Glassblowing", "sort_order": 999},
        )
        return category

    def _ensure_instructor(self, *, password: str) -> Any:
        """Create (or refresh) the demo instructor persona.

        Unlike _ensure_user, this method does NOT delete the auto-created Member —
        the instructor IS a Member in the new data model, and the Member row is
        referenced by ClassOffering via a PROTECT FK so we must keep it alive
        across idempotent re-seeding runs.
        """
        from allauth.account.models import EmailAddress as AllauthEmailAddress

        from membership.models import Member, MembershipPlan

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username=PERSONA_INSTRUCTOR_EMAIL,
            defaults={"email": PERSONA_INSTRUCTOR_EMAIL, "first_name": "Demo", "last_name": "Instructor"},
        )
        user.set_password(password)
        if not user.email:
            user.email = PERSONA_INSTRUCTOR_EMAIL
        user.first_name = "Demo"
        user.last_name = "Instructor"
        user.save()
        AllauthEmailAddress.objects.update_or_create(
            user=user,
            email=PERSONA_INSTRUCTOR_EMAIL,
            defaults={"verified": True, "primary": True},
        )
        plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
        member, _ = Member.objects.update_or_create(
            user=user,
            defaults={
                "full_legal_name": "Demo Instructor",
                "instructor_slug": f"{DEMO_SLUG_PREFIX}instructor",
                "about_me": "Seeded demo instructor — safe to delete with `manage.py demo_data --remove`.",
                "status": Member.Status.ACTIVE,
                "membership_plan": plan,
            },
        )
        return member

    def _ensure_student(self, *, password: str) -> Any:
        return self._ensure_user(
            email=PERSONA_STUDENT_EMAIL,
            first_name="Demo",
            last_name="Student",
            password=password,
            with_profile=True,
        )

    def _ensure_user(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        password: str,
        with_profile: bool = False,
    ) -> Any:
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=email,
            defaults={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
            },
        )
        # Always (re)set the password so re-seeding refreshes credentials.
        user.set_password(password)
        if not user.email:
            user.email = email
        user.first_name = first_name
        user.last_name = last_name
        user.save()
        # Mark email verified so allauth code-login works without a real round-trip.
        EmailAddress.objects.update_or_create(
            user=user,
            email=email,
            defaults={"verified": True, "primary": True},
        )
        # Detach the auto-created Member: demo personas should never inflate the
        # active-member count on prod. The post_save signal may have created one
        # with status=ACTIVE; drop it.
        from membership.models import Member

        Member.objects.filter(user=user).delete()
        if with_profile:
            UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    "preferred_name": first_name,
                    "first_attendance_status": UserProfile.FirstAttendance.RETURNING,
                    "onboarding_completed_at": timezone.now(),
                },
            )
        return user

    def _ensure_classes(
        self, category: Category, instructor: Any
    ) -> tuple[ClassOffering, ClassOffering, ClassOffering]:
        now = timezone.now()

        past = self._upsert_class(
            slug=f"{DEMO_SLUG_PREFIX}past-fundamentals",
            title="[DEMO] Glassblowing Fundamentals (Past)",
            category=category,
            instructor=instructor,
            price_cents=8000,
            capacity=6,
            session_start=now - timedelta(days=14),
        )
        current_free = self._upsert_class(
            slug=f"{DEMO_SLUG_PREFIX}free-intro",
            title="[DEMO] Free Studio Intro",
            category=category,
            instructor=instructor,
            price_cents=0,
            member_discount_pct=0,
            capacity=8,
            session_start=now + timedelta(days=3),
        )
        future_paid = self._upsert_class(
            slug=f"{DEMO_SLUG_PREFIX}future-advanced",
            title="[DEMO] Advanced Color Work (Upcoming)",
            category=category,
            instructor=instructor,
            price_cents=15000,
            capacity=4,
            session_start=now + timedelta(days=21),
        )
        return past, current_free, future_paid

    def _upsert_class(
        self,
        *,
        slug: str,
        title: str,
        category: Category,
        instructor: Any,
        price_cents: int,
        capacity: int,
        session_start,
        member_discount_pct: int = 10,
    ) -> ClassOffering:
        offering, _ = ClassOffering.objects.update_or_create(
            slug=slug,
            defaults={
                "title": title,
                "category": category,
                "instructor": instructor,
                "description": "Seeded demo class. Safe to delete via `manage.py demo_data --remove`.",
                "price_cents": price_cents,
                "member_discount_pct": member_discount_pct,
                "capacity": capacity,
                "status": ClassOffering.Status.PUBLISHED,
                "published_at": timezone.now(),
            },
        )
        # One 2-hour session, replacing any prior demo session so dates stay current.
        offering.sessions.all().delete()
        ClassSession.objects.create(
            class_offering=offering,
            starts_at=session_start,
            ends_at=session_start + timedelta(hours=2),
        )
        return offering

    def _ensure_student_registrations(
        self, student: Any, past_class: ClassOffering, future_paid_class: ClassOffering
    ) -> None:
        self._upsert_registration(
            offering=past_class,
            order_number=STUDENT_PAST_ORDER_NUMBER,
            email=student.email,
            first_name="Demo",
            last_name="Student",
            status=Registration.Status.CONFIRMED,
            confirmed_at=timezone.now() - timedelta(days=15),
            amount_paid_cents=past_class.price_cents,
        )
        self._upsert_registration(
            offering=future_paid_class,
            order_number=STUDENT_FUTURE_ORDER_NUMBER,
            email=student.email,
            first_name="Demo",
            last_name="Student",
            status=Registration.Status.CONFIRMED,
            confirmed_at=timezone.now() - timedelta(days=2),
            amount_paid_cents=future_paid_class.price_cents,
        )

    def _ensure_instructor_class_rosters(
        self,
        past_class: ClassOffering,
        current_free_class: ClassOffering,
        future_paid_class: ClassOffering,
    ) -> None:
        # Past class: 2 extra registrants alongside the student
        for i, last in enumerate(["Alvarez", "Brooks"], start=1):
            self._upsert_registration(
                offering=past_class,
                order_number=f"PL-DMA{i + 1}-26",
                email=f"past{i}@{DEMO_EMAIL_DOMAIN}",
                first_name="Past",
                last_name=last,
                status=Registration.Status.CONFIRMED,
                confirmed_at=timezone.now() - timedelta(days=15),
                amount_paid_cents=past_class.price_cents,
            )
        # Current free class: 1 extra confirmed registrant (+ guest added later)
        self._upsert_registration(
            offering=current_free_class,
            order_number="PL-DMC2-26",
            email=f"current1@{DEMO_EMAIL_DOMAIN}",
            first_name="Current",
            last_name="Chen",
            status=Registration.Status.CONFIRMED,
            confirmed_at=timezone.now() - timedelta(days=1),
            amount_paid_cents=0,
        )
        # Future paid class: 1 extra pending registrant alongside the student's confirmed
        self._upsert_registration(
            offering=future_paid_class,
            order_number="PL-DMU2-26",
            email=f"future1@{DEMO_EMAIL_DOMAIN}",
            first_name="Future",
            last_name="Yamamoto",
            status=Registration.Status.PENDING,
            amount_paid_cents=0,
        )

    def _ensure_guest_registration(self, current_free_class: ClassOffering) -> None:
        self._upsert_registration(
            offering=current_free_class,
            order_number=GUEST_ORDER_NUMBER,
            email=PERSONA_GUEST_EMAIL,
            first_name="Demo",
            last_name="Guest",
            status=Registration.Status.CONFIRMED,
            confirmed_at=timezone.now() - timedelta(hours=6),
            amount_paid_cents=0,
        )

    def _upsert_registration(
        self,
        *,
        offering: ClassOffering,
        order_number: str,
        email: str,
        first_name: str,
        last_name: str,
        status: str,
        confirmed_at=None,
        amount_paid_cents: int = 0,
    ) -> Registration:
        registration, created = Registration.objects.update_or_create(
            order_number=order_number,
            defaults={
                "class_offering": offering,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "status": status,
                "confirmed_at": confirmed_at,
                "amount_paid_cents": amount_paid_cents,
                "wants_newsletter": False,
            },
        )
        # Liability waiver is required on every real registration; replicate so
        # the demo data passes the same invariants as production data.
        Waiver.objects.update_or_create(
            registration=registration,
            kind=Waiver.Kind.LIABILITY,
            defaults={
                "waiver_text": "[DEMO] Seeded waiver signed by the seed script.",
                "signature_text": f"{first_name} {last_name}",
            },
        )
        return registration

    def _ensure_discount_codes(self) -> None:
        DiscountCode.objects.update_or_create(
            code="DEMO10",
            defaults={
                "description": "[DEMO] 10% off — admin-approved.",
                "discount_pct": 10,
                "is_active": True,
                "is_approved": True,
            },
        )
        DiscountCode.objects.update_or_create(
            code="DEMO5OFF",
            defaults={
                "description": "[DEMO] $5 flat discount — admin-approved.",
                "discount_fixed_cents": 500,
                "is_active": True,
                "is_approved": True,
            },
        )
        DiscountCode.objects.update_or_create(
            code="DEMOINSTRUCTOR20",
            defaults={
                "description": "[DEMO] Instructor-created, pending admin approval.",
                "discount_pct": 20,
                "is_active": True,
                "is_approved": False,
            },
        )
        self.stdout.write("  Discount codes: DEMO10 (approved), DEMO5OFF (approved), DEMOINSTRUCTOR20 (pending)")

    def _ensure_registration_questions(self) -> None:
        RegistrationQuestion.objects.update_or_create(
            prompt="How did you hear about this class?",
            defaults={
                "question_type": RegistrationQuestion.QuestionType.SHORT_TEXT,
                "is_required": False,
                "is_active": True,
                "sort_order": 1,
            },
        )
        RegistrationQuestion.objects.update_or_create(
            prompt="Do you have any allergies or medical conditions we should know about?",
            defaults={
                "question_type": RegistrationQuestion.QuestionType.YES_NO,
                "is_required": True,
                "is_active": True,
                "sort_order": 2,
            },
        )
        RegistrationQuestion.objects.update_or_create(
            prompt="What's your experience level?",
            defaults={
                "question_type": RegistrationQuestion.QuestionType.SINGLE_CHOICE,
                "choices_json": ["Complete beginner", "Some experience", "Intermediate", "Advanced"],
                "is_required": True,
                "is_active": True,
                "sort_order": 3,
            },
        )
        self.stdout.write("  Registration questions: 3 seeded (short text, yes/no, single choice)")

    # --- Local-dev personas, pending class, images, orientations ------------

    def _ensure_member_persona(self, *, password: str) -> Any:
        """The 'test member' the QA flow logs in as to browse and book orientations."""
        return self._ensure_demo_member(
            email=PERSONA_MEMBER_EMAIL, first_name="Demo", last_name="Member", password=password
        )

    def _ensure_demo_member(self, *, email: str, first_name: str, last_name: str, password: str) -> Any:
        """Create (or refresh) an ACTIVE demo Member with a linked, verified login.

        Unlike ``_ensure_user`` this keeps the auto-created Member alive — these
        personas *are* members (they join guilds and book orientations), so the
        Member row must persist. Local-dev only (callers gate on DEBUG), so a
        real environment's active-member count is never inflated.
        """
        from allauth.account.models import EmailAddress as AllauthEmailAddress

        from membership.models import Member, MembershipPlan

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username=email,
            defaults={"email": email, "first_name": first_name, "last_name": last_name},
        )
        user.set_password(password)
        if not user.email:
            user.email = email
        user.first_name = first_name
        user.last_name = last_name
        user.save()
        AllauthEmailAddress.objects.update_or_create(
            user=user, email=email, defaults={"verified": True, "primary": True}
        )
        plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
        member, _ = Member.objects.update_or_create(
            user=user,
            defaults={
                "full_legal_name": f"{first_name} {last_name}",
                "preferred_name": first_name,
                "about_me": "Seeded demo member — safe to delete with `manage.py demo_data --remove`.",
                "status": Member.Status.ACTIVE,
                "membership_plan": plan,
            },
        )
        return member

    def _ensure_pending_class(self, category: Category, instructor: Any) -> ClassOffering:
        """A class the demo instructor submitted that's waiting on admin approval.

        Staged directly into PENDING with an open admin ``ClassApproval`` row so
        it shows in the admin review queue — without firing the review email the
        real ``submit_for_review`` path would. The QA tester approves it from the
        admin UI, which fires the instructor-outcome email into Mailpit.
        """
        now = timezone.now()
        offering, _ = ClassOffering.objects.update_or_create(
            slug=f"{DEMO_SLUG_PREFIX}pending-review",
            defaults={
                "title": "[DEMO] Intro to Lampworking (Pending Approval)",
                "category": category,
                "instructor": instructor,
                "created_by": instructor,
                "description": "Seeded demo class awaiting admin approval. Safe to delete via `demo_data --remove`.",
                "price_cents": 6500,
                "member_discount_pct": 10,
                "capacity": 6,
                "status": ClassOffering.Status.PENDING,
            },
        )
        offering.sessions.all().delete()
        start = now + timedelta(days=10)
        ClassSession.objects.create(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        # One open admin gate (token auto-fills on save). Reset on every re-seed.
        offering.approvals.all().delete()
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        return offering

    def _demo_image_dir(self) -> Path | None:
        image_dir = Path(settings.MEDIA_ROOT) / DEMO_IMAGE_DIRNAME
        return image_dir if image_dir.is_dir() else None

    def _attach_images(
        self, offering: ClassOffering, *, hero: str | None = None, gallery: tuple[str, ...] = ()
    ) -> None:
        """Attach a hero and/or up to four gallery images to a class, from the demo image dir.

        Idempotent and cheap to skip: a hero is set only when the field is empty
        and gallery rows added only when the class has none, so re-seeding never
        duplicates images. Missing source files are skipped silently, so the seed
        still runs on a checkout that hasn't fetched images.
        """
        image_dir = self._demo_image_dir()
        if image_dir is None:
            return
        if hero and not offering.image:
            hero_path = image_dir / hero
            if hero_path.is_file():
                with hero_path.open("rb") as fh:
                    offering.image.save(hero, File(fh), save=True)
        if gallery and not offering.gallery_images.exists():
            for sort_order, name in enumerate(gallery[:4]):
                gallery_path = image_dir / name
                if not gallery_path.is_file():
                    continue
                row = ClassImage(class_offering=offering, sort_order=sort_order, alt_text=f"{offering.title} photo")
                with gallery_path.open("rb") as fh:
                    row.image.save(name, File(fh), save=True)

    def _ensure_orientations(self, member: Any, *, password: str) -> list[str]:
        """Enable orientations on the demo guilds, post recurring times, and seed bookings.

        Returns human-readable summary lines for the command output.
        """
        from membership import orientations
        from membership.models import Guild

        bookers = [
            self._ensure_demo_member(
                email=f"{local_part}@{DEMO_EMAIL_DOMAIN}", first_name=first, last_name=last, password=password
            )
            for local_part, first, last in ORIENTATION_BOOKER_PERSONAS
        ]
        guilds = []
        for name in DEMO_ORIENTATION_GUILDS:
            guild = Guild.objects.filter(name=name).first()
            if guild is None:
                self.stdout.write(self.style.WARNING(f"  Orientation: guild {name!r} not found — skipped."))
                continue
            self._ensure_orientation_settings(guild)
            self._ensure_availability_rules(guild)
            orientations.generate_slots(guild=guild)
            guilds.append(guild)
        summary: list[str] = []
        if guilds:
            self._seed_orientation_bookings(guilds, member, bookers, summary)
        return summary

    def _ensure_orientation_settings(self, guild: Any) -> Any:
        """Switch orientation on for a guild, filling optional config only when blank.

        Re-seed never clobbers a guild's own info/emails (e.g. Jewelry's pulled
        settings); it only guarantees the guild is accepting and that the
        thank-you / welcome emails are populated so those paths are testable.
        """
        from membership.models import GuildOrientationSettings

        info = (
            "Orientation is a short walkthrough of the studio, tools, and safety basics before you "
            "start working on your own. Pick a time that works for you below."
        )
        obj, _ = GuildOrientationSettings.objects.get_or_create(
            guild=guild,
            defaults={
                "is_enabled": True,
                "is_closed": False,
                "allow_custom_requests": True,
                "info": info,
                "default_seats": 4,
                "default_location": "Front Desk",
                "default_duration_minutes": 60,
            },
        )
        obj.is_enabled = True
        obj.is_closed = False
        if not obj.info:
            obj.info = info
        if not obj.thankyou_email_subject:
            obj.thankyou_email_enabled = True
            obj.thankyou_email_subject = f"Welcome to the {guild.name}!"
            obj.thankyou_email_body = (
                "Thanks for completing your orientation — you're cleared to start using the space. "
                "Reach out to your guild lead any time with questions."
            )
            obj.thankyou_email_updated_at = timezone.now()
        if not obj.join_email_subject:
            obj.join_email_enabled = True
            obj.join_email_subject = f"You joined the {guild.name}"
            obj.join_email_body = "Welcome aboard! Watch the guild page for upcoming orientations, classes, and events."
            obj.join_email_updated_at = timezone.now()
        obj.save()
        return obj

    def _ensure_availability_rules(self, guild: Any) -> None:
        """Two weekly recurring orientation windows per guild (Wed evening, Sat late-morning)."""
        from membership.models import OrientationAvailability

        location = f"Front Desk {DEMO_ORIENTATION_MARKER}"
        rules = [
            (OrientationAvailability.Weekday.WEDNESDAY, time(18, 0), time(19, 0)),
            (OrientationAvailability.Weekday.SATURDAY, time(11, 0), time(12, 0)),
        ]
        for weekday, start, end in rules:
            OrientationAvailability.objects.get_or_create(
                guild=guild,
                weekday=weekday,
                start_time=start,
                defaults={"end_time": end, "seats": 4, "location": location, "is_active": True},
            )

    def _ensure_past_slot(self, guild: Any) -> Any:
        """A single past, demo-marked manual slot so a completed orientation has somewhere to live."""
        from membership.models import OrientationSlot

        OrientationSlot.objects.filter(
            guild=guild,
            source=OrientationSlot.Source.MANUAL,
            location__icontains=DEMO_ORIENTATION_MARKER,
            starts_at__lt=timezone.now(),
        ).delete()
        start = (timezone.now() - timedelta(days=7)).replace(minute=0, second=0, microsecond=0)
        return OrientationSlot.objects.create(
            guild=guild,
            availability=None,
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            seats=4,
            location=f"Front Desk {DEMO_ORIENTATION_MARKER}",
            source=OrientationSlot.Source.MANUAL,
        )

    def _make_booking(self, slot: Any, member: Any, status: str, *, completed: bool = False) -> Any:
        """Create/refresh one orientation booking directly (no emails), bypassing the booking guards."""
        from membership.models import OrientationBooking

        booking, _ = OrientationBooking.objects.get_or_create(
            slot=slot, member=member, defaults={"guild": slot.guild, "status": status}
        )
        booking.guild = slot.guild
        booking.status = status
        booking.is_completed = completed
        if status == OrientationBooking.Status.CONFIRMED and booking.confirmed_at is None:
            booking.confirmed_at = timezone.now()
        if completed:
            booking.oriented_by = slot.guild.guild_lead
        booking.save()
        return booking

    def _seed_orientation_bookings(
        self, guilds: list[Any], member: Any, bookers: list[Any], summary: list[str]
    ) -> None:
        """Spread requested / confirmed / completed bookings across the demo guilds.

        The unique 'one active booking per (guild, member)' constraint is honored
        by never giving the same member two live bookings on one guild.
        """
        from membership.models import OrientationBooking, OrientationSlot

        status = OrientationBooking.Status

        def demo_slots(guild: Any) -> list[Any]:
            return list(
                guild.orientation_slots.filter(
                    source=OrientationSlot.Source.GENERATED, location__icontains=DEMO_ORIENTATION_MARKER
                )
                .upcoming()
                .order_by("starts_at")
            )

        def when(slot: Any) -> str:
            return f"{timezone.localtime(slot.starts_at):%a %b %-d, %-I%p}"

        primary = guilds[0]
        primary_slots = demo_slots(primary)
        if primary_slots:
            self._make_booking(primary_slots[0], member, status.REQUESTED)
            self._make_booking(primary_slots[0], bookers[0], status.REQUESTED)
            summary.append(
                f"{primary.name}: {member.display_name} REQUESTED {when(primary_slots[0])} "
                "(+ 1 more pending) — approve to test"
            )
        if len(primary_slots) >= 2:
            self._make_booking(primary_slots[1], bookers[1], status.CONFIRMED)
            summary.append(f"{primary.name}: {bookers[1].display_name} CONFIRMED {when(primary_slots[1])}")
        past_slot = self._ensure_past_slot(primary)
        self._make_booking(past_slot, bookers[2], status.CONFIRMED, completed=True)
        summary.append(f"{primary.name}: {bookers[2].display_name} COMPLETED a past orientation (oriented)")

        if len(guilds) >= 2:
            second = guilds[1]
            second_slots = demo_slots(second)
            if second_slots:
                self._make_booking(second_slots[0], member, status.CONFIRMED)
                summary.append(f"{second.name}: {member.display_name} CONFIRMED {when(second_slots[0])}")
            if len(second_slots) >= 2:
                self._make_booking(second_slots[1], bookers[3], status.REQUESTED)
                summary.append(f"{second.name}: {bookers[3].display_name} REQUESTED (pending)")

        if len(guilds) >= 3:
            third = guilds[2]
            third_slots = demo_slots(third)
            if third_slots:
                self._make_booking(third_slots[0], bookers[0], status.REQUESTED)
                summary.append(f"{third.name}: {bookers[0].display_name} REQUESTED (pending)")

    # --- Remove -------------------------------------------------------------

    @transaction.atomic
    def _remove(self) -> None:
        from membership.models import Member

        User = get_user_model()

        self.stdout.write(self.style.NOTICE("Removing demo data..."))

        demo_classes = ClassOffering.objects.filter(slug__startswith=DEMO_SLUG_PREFIX)
        # Registrations belong to classes (cascade), but also catch any rogue
        # demo-email registrations that ended up on a non-demo class.
        reg_count = (
            Registration.objects.filter(class_offering__in=demo_classes).count()
            + Registration.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}")
            .exclude(class_offering__in=demo_classes)
            .count()
        )
        Registration.objects.filter(class_offering__in=demo_classes).delete()
        Registration.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").delete()

        class_count = demo_classes.count()
        demo_classes.delete()  # cascades sessions, gallery images, instructor messages

        category_count = Category.objects.filter(slug__startswith=DEMO_SLUG_PREFIX).count()
        Category.objects.filter(slug__startswith=DEMO_SLUG_PREFIX).delete()

        # Members linked to a demo email — by user or by pre-signup email.
        member_count = (
            Member.objects.filter(user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
            + Member.objects.filter(_pre_signup_email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        )
        Member.objects.filter(user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").delete()
        Member.objects.filter(_pre_signup_email__endswith=f"@{DEMO_EMAIL_DOMAIN}").delete()

        user_count = User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").delete()

        code_count = DiscountCode.objects.filter(code__startswith="DEMO").count()
        DiscountCode.objects.filter(code__startswith="DEMO").delete()

        question_count = RegistrationQuestion.objects.filter(prompt__startswith="How did you hear").count()
        question_count += RegistrationQuestion.objects.filter(prompt__startswith="Do you have any allergies").count()
        question_count += RegistrationQuestion.objects.filter(prompt__startswith="What's your experience").count()
        RegistrationQuestion.objects.filter(prompt__startswith="How did you hear").delete()
        RegistrationQuestion.objects.filter(prompt__startswith="Do you have any allergies").delete()
        RegistrationQuestion.objects.filter(prompt__startswith="What's your experience").delete()

        slot_count, rule_count, disabled_count = self._remove_orientations()

        self.stdout.write(
            self.style.SUCCESS(
                f"Removed: {user_count} user(s), {member_count} member(s), "
                f"{class_count} class(es), {category_count} categor(ies), {reg_count} registration(s), "
                f"{code_count} discount code(s), {question_count} registration question(s), "
                f"{slot_count} orientation slot(s), {rule_count} orientation rule(s), "
                f"orientation re-disabled on {disabled_count} guild(s)."
            )
        )

    def _remove_orientations(self) -> tuple[int, int, int]:
        """Delete demo-marked orientation rules + slots and re-disable the guilds we emptied.

        Demo bookings are already gone — they cascade when the demo Members are
        deleted above. A guild that still has its own (non-demo) rules or slots
        keeps its orientation toggle; only guilds left empty by us get switched
        back off.
        """
        from membership.models import GuildOrientationSettings, OrientationAvailability, OrientationSlot

        demo_slots = OrientationSlot.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER)
        demo_rules = OrientationAvailability.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER)
        slot_count = demo_slots.count()
        rule_count = demo_rules.count()
        demo_slots.delete()
        demo_rules.delete()

        disabled = 0
        for name in DEMO_ORIENTATION_GUILDS:
            obj = GuildOrientationSettings.objects.filter(guild__name=name, is_enabled=True).first()
            if obj is None:
                continue
            if obj.guild.orientation_rules.exists() or obj.guild.orientation_slots.exists():
                continue  # has its own non-demo orientation data — leave it enabled
            obj.is_enabled = False
            obj.save(update_fields=["is_enabled"])
            disabled += 1
        return slot_count, rule_count, disabled

    # --- Status -------------------------------------------------------------

    def _print_status(self) -> None:
        from membership.models import (
            GuildOrientationSettings,
            Member,
            OrientationAvailability,
            OrientationBooking,
            OrientationSlot,
        )

        User = get_user_model()

        users = User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}")
        classes = ClassOffering.objects.filter(slug__startswith=DEMO_SLUG_PREFIX)
        regs = Registration.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}")
        members = Member.objects.filter(user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}")

        self.stdout.write(self.style.NOTICE("Demo data currently in this database:"))
        self.stdout.write(f"  Users:          {users.count()}")
        for u in users:
            self.stdout.write(f"    - {u.email}")
        self.stdout.write(
            f"  Members:        {members.count()}  (0 on a real env; local-dev seeds active demo members)"
        )
        self.stdout.write(f"  Classes:        {classes.count()}")
        for c in classes:
            self.stdout.write(f"    - {c.slug} → {c.title} [{c.get_status_display()}]")
        self.stdout.write(f"  Registrations:  {regs.count()}")
        for r in regs:
            self.stdout.write(
                f"    - {r.order_number}: {r.first_name} {r.last_name} ({r.email}) → {r.class_offering.slug}"
            )

        demo_rules = OrientationAvailability.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER)
        demo_slots = OrientationSlot.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER)
        demo_bookings = OrientationBooking.objects.filter(member__user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}")
        enabled = GuildOrientationSettings.objects.filter(guild__name__in=DEMO_ORIENTATION_GUILDS, is_enabled=True)
        self.stdout.write(
            f"  Orientation:    {demo_rules.count()} rule(s), {demo_slots.count()} slot(s), "
            f"{demo_bookings.count()} booking(s)"
        )
        for s in enabled:
            self.stdout.write(f"    - enabled on {s.guild.name}")
