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

from datetime import timedelta
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from classes.models import (
    Category,
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
        # Registration questions are global (no demo- scoping), so seeding them on
        # a real environment leaks them onto every registrant's form. Keep this in
        # local dev only; real environments manage questions via the CMS admin.
        if settings.DEBUG:
            self._ensure_registration_questions()
        else:
            self.stdout.write(
                self.style.WARNING("  Registration questions: skipped (DEBUG off — manage these in the CMS admin)")
            )

        self.stdout.write(self.style.SUCCESS("\nDemo data ready. Log in details:"))
        self.stdout.write(f"  Student (non-member): {PERSONA_STUDENT_EMAIL}  /  password: {password}")
        self.stdout.write(f"  Instructor:           {PERSONA_INSTRUCTOR_EMAIL}  /  password: {password}")
        self.stdout.write("\nGuest lookup (no login):")
        self.stdout.write(f"  Last name: Guest    Order #: {GUEST_ORDER_NUMBER}")
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

        self.stdout.write(
            self.style.SUCCESS(
                f"Removed: {user_count} user(s), {member_count} member(s), "
                f"{class_count} class(es), {category_count} categor(ies), {reg_count} registration(s), "
                f"{code_count} discount code(s), {question_count} registration question(s)."
            )
        )

    # --- Status -------------------------------------------------------------

    def _print_status(self) -> None:
        from membership.models import Member

        User = get_user_model()

        users = User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}")
        classes = ClassOffering.objects.filter(slug__startswith=DEMO_SLUG_PREFIX)
        regs = Registration.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}")
        members = Member.objects.filter(user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}")

        self.stdout.write(self.style.NOTICE("Demo data currently in this database:"))
        self.stdout.write(f"  Users:          {users.count()}")
        for u in users:
            self.stdout.write(f"    - {u.email}")
        self.stdout.write(f"  Members:        {members.count()}  (should normally be 0 for demo personas)")
        self.stdout.write(f"  Classes:        {classes.count()}")
        for c in classes:
            self.stdout.write(f"    - {c.slug} → {c.title}")
        self.stdout.write(f"  Registrations:  {regs.count()}")
        for r in regs:
            self.stdout.write(
                f"    - {r.order_number}: {r.first_name} {r.last_name} ({r.email}) → {r.class_offering.slug}"
            )
