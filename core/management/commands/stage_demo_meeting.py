"""Stage the production demo-meeting data set (idempotent).

Builds the accounts and class content one live walkthrough needs:

* ``counciltreasurer+member@`` — a plain member with a clean teaching-application
  state, so "a member asks to become an instructor" can be performed live.
* ``counciltreasurer+instructor@`` — a teaching-enabled member ("Demo Teacher")
  owning three classes: a FULL paid class with a waitlist, a FREE class with open
  seats, and a submit-ready DRAFT.
* ``counciltreasurer+admin@`` — holds the CLASS_APPROVER capability, which is what
  routes both the executive-validation email and the "someone wants to host a
  workshop" request to it. Neither follows the admin role.
* ``counciltreasurer+guildlead@`` — a plain member who leads the Cartographers
  Guild (no admin tier), so the guild-lead review stage is honestly a guild lead.

Every persona gets an explicit EMAIL preference row set ON for every event that
declares an email channel, so nothing is swallowed by an opt-out default.

The command writes rows only. It never calls ``submit_for_review``, ``publish``,
``promote_from_waitlist`` or any other method that emits an event, so running it
against production sends no email, posts nothing to Discord, and rings no bells.
The walkthrough itself is safe to run to the end: every class here is ``[DEMO]``
titled with a ``demo-`` slug, and ``ClassOffering.is_demo`` holds the publish
broadcast back from the makerspace Discord channel.
Hero and gallery photos reuse storage keys that other rows already reference, so
no upload happens and no orphan cleanup can strand another class's picture.

``--remove`` drops the staged classes (and their registrations); it leaves the
personas alone.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from classes.models import Category, ClassImage, ClassOffering, ClassSession, Registration
from core.events.registry import Channel, ChannelDefault, all_events
from core.models import NotificationPreference
from membership.models import AdminCapability, Guild, GuildMembership, Member

PACIFIC = ZoneInfo("America/Los_Angeles")

#: Every staged class lives under this category so the guild-lead review stage routes
#: to the Cartographers Guild lead (the ``+guildlead`` persona).
CATEGORY_SLUG = "demo-cart-category"
GUILD_SLUG = "cartographers-guild"

MEMBER_EMAIL = "counciltreasurer+member@pastlives.space"
INSTRUCTOR_EMAIL = "counciltreasurer+instructor@pastlives.space"
GUILDLEAD_EMAIL = "counciltreasurer+guildlead@pastlives.space"
ADMIN_EMAIL = "counciltreasurer+admin@pastlives.space"

#: Storage keys already carried by other rows of the SAME model, so reusing them
#: cannot orphan anything: ``delete_if_unreferenced`` sees the original row and
#: refuses to delete the object. No upload, no new R2 write.
HERO_KEYS = {
    "full": "classes/images/056763cee07895f5f730132a0f6c84d014909d824b072af39dd159ceb16a38f3.jpg",
    "open": "classes/images/abdd3259f466965a7bd575565a5813051e233627eee82e08c6bff581b004f9a6.jpg",
    "draft": "classes/images/4ba23a4d1ba0b8ce021f4430dee885eaf843432a4f0652daaefa9868f5a0316b.jpg",
}
GALLERY_KEYS = {
    "full": [
        "classes/images/shaker-side-table-hand-cut-joinery-1.jpg",
        "classes/images/shaker-side-table-hand-cut-joinery-2.jpg",
    ],
    "open": [
        "classes/images/shaker-side-table-hand-cut-joinery-3.jpg",
        "classes/images/shaker-side-table-hand-cut-joinery-4.jpg",
    ],
    "draft": [
        "classes/images/IMG_7598_aToVAUV.jpg",
        "classes/images/PXL_20260125_190609808.jpg",
        "classes/images/cindy2_UdXxCGD.jpg",
    ],
}

FULL_SLUG = "demo-map-lettering"
OPEN_SLUG = "demo-map-compass"
DRAFT_SLUG = "demo-map-watercolor"
STAGED_SLUGS = [FULL_SLUG, OPEN_SLUG, DRAFT_SLUG]

FULL_DESCRIPTION = (
    "A demo class. Spend a morning learning the hand lettering that makes an old map look like an old map. "
    "We start with letter shapes on scrap, move to a compass rose, and finish by lettering the title block "
    "of a map you take home. All ink, nibs, and paper are provided, and no drawing experience is assumed."
)
OPEN_DESCRIPTION = (
    "A demo class. An easy first session with a compass, a straight edge, and a bottle of ink. We walk "
    "through scale, orientation, and the handful of marks every map needs, then draw a small map of a room "
    "you know well. Free, and everything you need is on the bench when you arrive."
)
DRAFT_DESCRIPTION = (
    "A demo class. Flat washes, graded washes, and the trick of laying color over ink without lifting the "
    "line. We mix a small palette of earth tones, test them on scraps, and then wash a printed base map so "
    "the coast reads as coast. Bring nothing; brushes and paper are here."
)

#: (email local part, first, last) for the seat holders and waitlisters. Every address is a
#: plus alias of the council inbox, so every demo email loops back to one mailbox.
FULL_SEATS = [
    ("demo1", "Avery", "Demo"),
    ("demo2", "Brooke", "Demo"),
    ("demo3", "Casey", "Demo"),
    ("demo4", "Dana", "Demo"),
]
FULL_WAITLIST = [
    ("wait1", "Emerson", "Demo"),
    ("wait2", "Frankie", "Demo"),
]
OPEN_SEATS = [
    ("demo5", "Harper", "Demo"),
    ("demo6", "Indigo", "Demo"),
]


class Command(BaseCommand):
    help = "Stage (or remove) the production demo-meeting accounts, classes, and registrations."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete the staged classes and their registrations. Personas are left in place.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="With --remove, list what would be deleted. Staging is all-or-nothing and reports nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django.db import connection

        self.stdout.write(f"Database host: {connection.settings_dict['HOST'] or '(local sqlite)'}")
        if options["remove"]:
            self._remove(dry_run=options["dry_run"])
            return
        self._stage(dry_run=options["dry_run"])

    # --- teardown -----------------------------------------------------------

    def _remove(self, *, dry_run: bool) -> None:
        offerings = list(ClassOffering.objects.filter(slug__in=STAGED_SLUGS))
        registrations = Registration.objects.filter(class_offering__in=offerings)
        self.stdout.write(f"Classes: {[o.slug for o in offerings]}")
        self.stdout.write(f"Registrations: {registrations.count()}")
        if dry_run:
            self.stdout.write("[dry-run] nothing written")
            return
        with transaction.atomic():
            registrations.delete()
            for offering in offerings:
                offering.approvals.all().delete()
                offering.gallery_images.all().delete()
                offering.sessions.all().delete()
                offering.activity.all().delete()
                offering.delete()
        self.stdout.write(self.style.SUCCESS("Removed the staged classes."))

    # --- staging ------------------------------------------------------------

    def _stage(self, *, dry_run: bool) -> None:
        if dry_run:
            self.stdout.write("[dry-run] staging is all-or-nothing; re-run without --dry-run to apply")
            return
        with transaction.atomic():
            category = Category.objects.get(slug=CATEGORY_SLUG)
            guild = Guild.objects.get(slug=GUILD_SLUG)

            member = self._reset_member_persona()
            instructor = self._ensure_instructor_persona(guild)
            lead = self._demote_guild_lead_persona()
            admin = self._ensure_admin_capability()

            full = self._stage_full_class(category, instructor)
            open_class = self._stage_open_class(category, instructor)
            draft = self._stage_draft_class(category, instructor)

            for persona in (member, instructor, lead, admin):
                if persona is not None and persona.user is not None:
                    count = self._enable_all_email(persona.user)
                    self.stdout.write(f"  email prefs ON for {persona.primary_email}: {count} events")

        self.stdout.write(self.style.SUCCESS("Staged:"))
        for offering in (full, open_class, draft):
            self.stdout.write(
                f"  {offering.slug}  status={offering.status}  seats_left={offering.spots_remaining}"
                f"  price=${offering.price_cents / 100:.2f}"
            )

    def _reset_member_persona(self) -> Member:
        """Put the plain-member persona back to 'never asked to teach' with a clean roster."""
        member = Member.objects.get(user__email__iexact=MEMBER_EMAIL)
        member.fog_role = Member.FogRole.MEMBER
        member.status = Member.Status.ACTIVE
        member.instructor_oriented_at = None
        member.teaching_applied_at = None
        member.teaching_application_note = ""
        member.teaching_decided_at = None
        member.teaching_decline_reason = ""
        member.save(
            update_fields=[
                "fog_role",
                "status",
                "instructor_oriented_at",
                "teaching_applied_at",
                "teaching_application_note",
                "teaching_decided_at",
                "teaching_decline_reason",
            ]
        )
        stale = Registration.objects.filter(email__iexact=MEMBER_EMAIL)
        if stale.exists():
            self.stdout.write(f"  clearing {stale.count()} stale registration(s) for the member persona")
            stale.delete()
        self.stdout.write(f"  member persona reset: {member.display_name} (pk={member.pk})")
        return member

    def _ensure_instructor_persona(self, guild: Guild) -> Member:
        """Create or refresh the teaching-enabled persona that owns the staged classes."""
        member = Member.objects.filter(user__email__iexact=INSTRUCTOR_EMAIL).first()
        if member is None:
            member = Member.objects.filter(_pre_signup_email__iexact=INSTRUCTOR_EMAIL).first()
        created = member is None
        if member is None:
            # Mirror the plan the other demo personas carry: membership_plan is NOT NULL.
            plan = Member.objects.get(user__email__iexact=MEMBER_EMAIL).membership_plan
            member = Member(
                preferred_name="Demo Teacher",
                full_legal_name="Demo Teacher",
                member_type=Member.MemberType.STANDARD,
                membership_plan=plan,
                status=Member.Status.ACTIVE,
                fog_role=Member.FogRole.MEMBER,
                join_date=timezone.localdate(),
            )
            member._pre_signup_email = INSTRUCTOR_EMAIL
            member.save()  # post_save auto-provisions the passwordless User + verified email
            member.refresh_from_db()

        member.status = Member.Status.ACTIVE
        member.fog_role = Member.FogRole.MEMBER
        member.about_me = "Demo account for product walkthroughs (counciltreasurer alias). Safe to remove."
        member.instructor_bio = (
            "Demo instructor account. Draws maps that never quite match the ground, and teaches other "
            "people to do the same."
        )
        member.welcome_email_sent_at = member.welcome_email_sent_at or timezone.now()
        if member.instructor_oriented_at is None:
            member.instructor_oriented_at = timezone.now()
            member.teaching_decided_at = timezone.now()
            member.teaching_decline_reason = ""
        member.save()
        member.ensure_instructor_slug()
        GuildMembership.objects.get_or_create(member=member, guild=guild)
        verb = "created" if created else "refreshed"
        self.stdout.write(f"  instructor persona {verb}: {member.display_name} (pk={member.pk}, user={member.user_id})")
        return member

    def _demote_guild_lead_persona(self) -> Member | None:
        """Make the guild-lead persona a plain member again — a lead, not an admin.

        It drifted to ``fog_role=admin`` with every AdminCapability, which would show a
        full admin sidebar during a walkthrough about role separation. The guild-lead
        FK and the co-lead staff row are untouched, so the review stage still routes here.
        """
        member = Member.objects.filter(user__email__iexact=GUILDLEAD_EMAIL).first()
        if member is None:
            return None
        member.fog_role = Member.FogRole.MEMBER
        member.save(update_fields=["fog_role"])
        dropped = AdminCapability.objects.filter(member=member).delete()[0]
        user = member.user
        if user is not None and (user.is_staff or user.is_superuser):
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=["is_staff", "is_superuser"])
        self.stdout.write(f"  guild-lead persona demoted to member; dropped {dropped} capability row(s)")
        return member

    def _ensure_admin_capability(self) -> Member | None:
        """The admin persona must HOLD class_approver or the validation email skips it."""
        member = Member.objects.filter(user__email__iexact=ADMIN_EMAIL).first()
        if member is None:
            return None
        _, created = AdminCapability.objects.get_or_create(
            member=member, capability=AdminCapability.Capability.CLASS_APPROVER
        )
        state = "granted" if created else "already held"
        self.stdout.write(f"  admin persona class_approver {state}")
        return member

    # --- classes ------------------------------------------------------------

    def _stage_full_class(self, category: Category, instructor: Member) -> ClassOffering:
        offering = self._upsert_offering(
            slug=FULL_SLUG,
            title="[DEMO] Hand Lettered Map Making",
            description=FULL_DESCRIPTION,
            category=category,
            instructor=instructor,
            price_cents=4500,
            capacity=len(FULL_SEATS),
            status=ClassOffering.Status.PUBLISHED,
            session_at=datetime(2026, 10, 24, 10, 0, tzinfo=PACIFIC),
            duration_hours=3,
            hero_key=HERO_KEYS["full"],
            gallery_keys=GALLERY_KEYS["full"],
            materials_included="Nibs, ink, practice paper, and one sheet of good cotton rag to take home.",
            materials_to_bring="Nothing. Wear something you do not mind getting ink on.",
        )
        offering.published_at = offering.published_at or timezone.now()
        offering.save(update_fields=["published_at"])
        for local, first, last in FULL_SEATS:
            self._upsert_registration(
                offering,
                local=local,
                first=first,
                last=last,
                status=Registration.Status.CONFIRMED,
                amount_paid_cents=offering.price_cents,
            )
        for local, first, last in FULL_WAITLIST:
            self._upsert_registration(
                offering,
                local=local,
                first=first,
                last=last,
                status=Registration.Status.WAITLISTED,
                amount_paid_cents=0,
            )
        return offering

    def _stage_open_class(self, category: Category, instructor: Member) -> ClassOffering:
        offering = self._upsert_offering(
            slug=OPEN_SLUG,
            title="[DEMO] Compass and Ink: Cartography Basics",
            description=OPEN_DESCRIPTION,
            category=category,
            instructor=instructor,
            price_cents=0,
            capacity=8,
            status=ClassOffering.Status.PUBLISHED,
            session_at=datetime(2026, 10, 31, 13, 0, tzinfo=PACIFIC),
            duration_hours=2,
            hero_key=HERO_KEYS["open"],
            gallery_keys=GALLERY_KEYS["open"],
            materials_included="Compass, straight edge, ink, and paper.",
            materials_to_bring="Just yourself.",
        )
        offering.published_at = offering.published_at or timezone.now()
        offering.save(update_fields=["published_at"])
        for local, first, last in OPEN_SEATS:
            self._upsert_registration(
                offering,
                local=local,
                first=first,
                last=last,
                status=Registration.Status.CONFIRMED,
                amount_paid_cents=0,
            )
        return offering

    def _stage_draft_class(self, category: Category, instructor: Member) -> ClassOffering:
        """The submit-ready draft: every readiness item passes, the optional fields are blank.

        Blank on purpose, so there is something real to fill in live before hitting
        Submit: prerequisites, materials to bring, safety requirements, and the
        welcome email.
        """
        offering = self._upsert_offering(
            slug=DRAFT_SLUG,
            title="[DEMO] Watercolor Washes for Maps",
            description=DRAFT_DESCRIPTION,
            category=category,
            instructor=instructor,
            price_cents=6000,
            capacity=6,
            status=ClassOffering.Status.DRAFT,
            session_at=datetime(2026, 11, 14, 10, 0, tzinfo=PACIFIC),
            duration_hours=3,
            hero_key=HERO_KEYS["draft"],
            gallery_keys=GALLERY_KEYS["draft"],
            materials_included="Paints, brushes, palettes, and printed base maps to wash.",
            materials_to_bring="",
        )
        offering.approvals.all().delete()
        return offering

    def _upsert_offering(
        self,
        *,
        slug: str,
        title: str,
        description: str,
        category: Category,
        instructor: Member,
        price_cents: int,
        capacity: int,
        status: str,
        session_at: datetime,
        duration_hours: int,
        hero_key: str,
        gallery_keys: list[str],
        materials_included: str,
        materials_to_bring: str,
    ) -> ClassOffering:
        offering, created = ClassOffering.objects.get_or_create(
            slug=slug,
            defaults={"title": title, "category": category, "price_cents": price_cents},
        )
        offering.title = title
        offering.description = description
        offering.category = category
        offering.instructor = instructor
        offering.created_by = instructor
        offering.price_cents = price_cents
        offering.capacity = capacity
        offering.status = status
        offering.is_private = False
        offering.scheduling_model = ClassOffering.SchedulingModel.FIXED
        offering.scheduling_type = ClassOffering.SchedulingType.SINGLE_SESSION
        offering.materials_included = materials_included
        offering.materials_to_bring = materials_to_bring
        offering.image = hero_key
        offering.save()

        offering.sessions.all().delete()
        ClassSession.objects.create(
            class_offering=offering,
            starts_at=session_at,
            ends_at=session_at + timedelta(hours=duration_hours),
        )
        offering.gallery_images.all().delete()
        for order, key in enumerate(gallery_keys):
            ClassImage.objects.create(class_offering=offering, image=key, sort_order=order)
        verb = "created" if created else "updated"
        self.stdout.write(f"  class {verb}: {slug}")
        return offering

    def _upsert_registration(
        self,
        offering: ClassOffering,
        *,
        local: str,
        first: str,
        last: str,
        status: str,
        amount_paid_cents: int,
    ) -> Registration:
        email = f"counciltreasurer+{local}@pastlives.space"
        registration = Registration.objects.filter(class_offering=offering, email__iexact=email).first()
        if registration is None:
            registration = Registration(
                class_offering=offering,
                email=email,
                self_serve_token=secrets.token_urlsafe(48),
            )
        registration.first_name = first
        registration.last_name = last
        registration.status = status
        registration.amount_paid_cents = amount_paid_cents
        registration.payment_due_cents = 0
        registration.payment_link_sent_at = None
        registration.waitlist_notified_at = None
        registration.cancelled_at = None
        registration.cancellation_reason = ""
        registration.confirmed_at = timezone.now() if status == Registration.Status.CONFIRMED else None
        registration.save()
        return registration

    # --- preferences --------------------------------------------------------

    def _enable_all_email(self, user: Any) -> int:
        """Turn the EMAIL channel ON explicitly for every event that offers one.

        An absent row means the event's own default decides, and several of the events
        this walkthrough depends on (a new registration reaching the instructor, for
        one) default OFF. Writing an explicit row removes the guesswork.
        """
        count = 0
        for event in all_events():
            spec = event.channel(Channel.EMAIL)
            if spec is None or spec.default is ChannelDefault.FORCED:
                continue
            NotificationPreference.objects.update_or_create(
                user=user,
                event_key=event.key,
                channel=Channel.EMAIL.value,
                defaults={"enabled": True},
            )
            count += 1
        return count
