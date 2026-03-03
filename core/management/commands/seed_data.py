from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.utils import timezone

from education.models import MakerClass, Orientation, ScheduledOrientation
from membership.models import (
    FavoriteEvent,
    Guild,
    GuildVote,
    Lease,
    Member,
    MembershipPlan,
    Space,
)
from outreach.models import Event

User = get_user_model()

TODAY = timezone.now().date()


class Command(BaseCommand):
    help = "Seed the database with realistic demo data for Past Lives Makerspace"

    def add_arguments(self, parser):  # type: ignore[override]
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all existing data before seeding",
        )

    def handle(self, *args: object, **options: object) -> None:
        if options["flush"]:
            self._flush_data()

        users = self._seed_users()
        plans = self._seed_membership_plans()
        members = self._seed_members(users, plans)
        guilds = self._seed_guilds(members)
        self._seed_guild_votes(members, guilds)
        spaces = self._seed_spaces(guilds)
        self._seed_leases(members, guilds, spaces)
        self._seed_maker_classes(guilds)
        self._seed_orientations(guilds)
        events = self._seed_events(guilds, users)
        self._seed_favorites(users, events)

        self.stdout.write(self.style.SUCCESS("\nSeed complete."))

    # -------------------------------------------------------------------------
    # Flush
    # -------------------------------------------------------------------------

    def _flush_data(self) -> None:
        self.stdout.write("Flushing existing data...")
        FavoriteEvent.objects.all().delete()
        Event.objects.all().delete()
        ScheduledOrientation.objects.all().delete()
        Orientation.objects.all().delete()
        MakerClass.objects.all().delete()
        Lease.objects.all().delete()
        Space.objects.all().delete()
        GuildVote.objects.all().delete()
        Guild.objects.all().delete()
        Member.objects.all().delete()
        MembershipPlan.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        self.stdout.write(self.style.SUCCESS("Flush complete."))

    # -------------------------------------------------------------------------
    # Users
    # -------------------------------------------------------------------------

    def _seed_users(self) -> list:
        admin, _ = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@pastlives.org",
                "is_staff": True,
                "is_superuser": True,
                "first_name": "Admin",
                "last_name": "User",
            },
        )
        admin.set_password("admin")
        admin.save()

        demo_user_data = [
            ("mia.chen", "Mia", "Chen", "mia.chen@example.com"),
            ("jordan.walsh", "Jordan", "Walsh", "jordan.walsh@example.com"),
            ("sam.okafor", "Sam", "Okafor", "sam.okafor@example.com"),
            ("priya.nair", "Priya", "Nair", "priya.nair@example.com"),
            ("eli.reyes", "Eli", "Reyes", "eli.reyes@example.com"),
            ("casey.burke", "Casey", "Burke", "casey.burke@example.com"),
            ("devon.huang", "Devon", "Huang", "devon.huang@example.com"),
            ("anya.petrov", "Anya", "Petrov", "anya.petrov@example.com"),
            ("marcos.silva", "Marcos", "Silva", "marcos.silva@example.com"),
            ("riley.nguyen", "Riley", "Nguyen", "riley.nguyen@example.com"),
        ]

        guild_lead_user_data = [
            ("jenelle.giordano", "Jenelle", "Giordano", "jenelle.giordano@example.com"),
            ("jholtman", "J", "Holtman", "jholtman@example.com"),
            ("jamie.lindner", "Jamie", "Lindner", "jamie.lindner@example.com"),
            ("amber.terlouw", "Amber", "Terlouw", "amber.terlouw@example.com"),
            ("kristin.shiga", "Kristin", "Shiga", "kristin.shiga@example.com"),
            ("shawn.fox", "Shawn", "Fox", "shawn.fox@example.com"),
            ("william.ottaviani", "William", "Ottaviani", "william.ottaviani@example.com"),
            ("cait.johnstone", "Cait", "Johnstone", "cait.johnstone@example.com"),
            ("deb.clough", "Deb", "Clough", "deb.clough@example.com"),
            ("mira.glasser", "Mira", "Glasser", "mira.glasser@example.com"),
            ("patricia.fischer", "Patricia", "Fischer", "patricia.fischer@example.com"),
            ("brooke.sauvage", "Brooke", "Sauvage", "brooke.sauvage@example.com"),
            ("kate.visualarts", "Kate", "VisualArts", "kate.visualarts@example.com"),
            ("chris.ellwanger", "Chris", "Ellwanger", "chris.ellwanger@example.com"),
            ("penina.finger", "Penina", "Finger", "penina.finger@example.com"),
        ]

        all_user_data = demo_user_data + guild_lead_user_data
        users = []
        for username, first, last, email in all_user_data:
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"email": email, "first_name": first, "last_name": last},
            )
            user.set_password("demo")
            user.save()
            users.append(user)

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(users)} demo users + admin"))
        return users

    # -------------------------------------------------------------------------
    # Membership Plans
    # -------------------------------------------------------------------------

    def _seed_membership_plans(self) -> list:
        from decimal import Decimal

        plans_data = [
            {"name": "Drop-In", "monthly_price": Decimal("50.00"), "deposit_required": None},
            {"name": "Part-Time", "monthly_price": Decimal("150.00"), "deposit_required": Decimal("200.00")},
            {"name": "Full-Time", "monthly_price": Decimal("300.00"), "deposit_required": Decimal("400.00")},
            {"name": "Studio", "monthly_price": Decimal("500.00"), "deposit_required": Decimal("600.00")},
        ]
        plans = []
        for item in plans_data:
            plan, _ = MembershipPlan.objects.get_or_create(
                name=item["name"],
                defaults={
                    "monthly_price": item["monthly_price"],
                    "deposit_required": item["deposit_required"],
                },
            )
            plans.append(plan)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(plans)} membership plans"))
        return plans

    # -------------------------------------------------------------------------
    # Members
    # -------------------------------------------------------------------------

    def _seed_members(self, users: list, plans: list) -> list:
        drop_in, part_time, full_time, studio = plans

        member_data = [
            # (user_idx, full_legal_name, preferred_name, phone, status, role, plan, join_offset_days)
            # Regular demo members (user indices 0-9)
            (0, "Mia Chen", "Mia", "503-555-0101", Member.Status.ACTIVE, Member.Role.STANDARD, full_time, 400),
            (1, "Jordan Walsh", "Jordan", "503-555-0102", Member.Status.ACTIVE, Member.Role.STANDARD, full_time, 600),
            (2, "Samuel Okafor", "Sam", "503-555-0103", Member.Status.ACTIVE, Member.Role.STANDARD, part_time, 300),
            (3, "Priya Nair", "Priya", "503-555-0104", Member.Status.ACTIVE, Member.Role.WORK_TRADE, part_time, 500),
            (4, "Eli Reyes", "Eli", "503-555-0105", Member.Status.ACTIVE, Member.Role.STANDARD, full_time, 250),
            (5, "Casey Burke", "Casey", "503-555-0106", Member.Status.ACTIVE, Member.Role.VOLUNTEER, drop_in, 180),
            (6, "Devon Huang", "Devon", "503-555-0107", Member.Status.ACTIVE, Member.Role.STANDARD, full_time, 450),
            (7, "Anya Petrov", "Anya", "503-555-0108", Member.Status.ACTIVE, Member.Role.STANDARD, full_time, 700),
            (8, "Marcos Silva", "Marcos", "503-555-0109", Member.Status.ACTIVE, Member.Role.STANDARD, part_time, 320),
            (9, "Riley Nguyen", "Riley", "503-555-0110", Member.Status.ACTIVE, Member.Role.EMPLOYEE, full_time, 550),
            # Guild lead members (user indices 10-24)
            (
                10,
                "Jenelle Giordano",
                "Jenelle",
                "503-555-0201",
                Member.Status.ACTIVE,
                Member.Role.GUILD_LEAD,
                studio,
                800,
            ),
            (11, "J Holtman", "J", "503-555-0202", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 750),
            (12, "Jamie Lindner", "Jamie", "503-555-0203", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 700),
            (13, "Amber Terlouw", "Amber", "503-555-0204", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 650),
            (14, "Kristin Shiga", "Kristin", "503-555-0205", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 600),
            (15, "Shawn Fox", "Shawn", "503-555-0206", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 550),
            (
                16,
                "William Ottaviani",
                "William",
                "503-555-0207",
                Member.Status.ACTIVE,
                Member.Role.GUILD_LEAD,
                studio,
                500,
            ),
            (17, "Cait Johnstone", "Cait", "503-555-0208", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 450),
            (18, "Deb Clough", "Deb", "503-555-0209", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 400),
            (19, "Mira Glasser", "Mira", "503-555-0215", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 400),
            (
                20,
                "Patricia Fischer",
                "Patricia",
                "503-555-0210",
                Member.Status.ACTIVE,
                Member.Role.GUILD_LEAD,
                studio,
                350,
            ),
            (21, "Brooke Sauvage", "Brooke", "503-555-0211", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 300),
            (22, "Kate VisualArts", "Kate", "503-555-0212", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 250),
            (23, "Chris Ellwanger", "Chris", "503-555-0213", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 200),
            (24, "Penina Finger", "Penina", "503-555-0214", Member.Status.ACTIVE, Member.Role.GUILD_LEAD, studio, 150),
        ]

        members = []
        for row in member_data:
            idx, legal, preferred, phone, status, role, plan, offset = row
            user = users[idx]
            join_date = TODAY - timedelta(days=offset)
            defaults = {
                "full_legal_name": legal,
                "preferred_name": preferred,
                "email": user.email,
                "phone": phone,
                "billing_name": legal,
                "emergency_contact_name": f"{preferred} Emergency Contact",
                "emergency_contact_phone": "503-555-9999",
                "emergency_contact_relationship": "Partner",
                "membership_plan": plan,
                "status": status,
                "role": role,
                "join_date": join_date,
            }
            member, _ = Member.objects.get_or_create(user=user, defaults=defaults)
            members.append(member)

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(members)} members"))
        return members

    # -------------------------------------------------------------------------
    # Guilds
    # -------------------------------------------------------------------------

    def _seed_guilds(self, members: list) -> list:
        guilds_data = [
            {
                "name": "Art Framing",
                "intro": "Preserving and presenting artwork with custom frames.",
                "description": (
                    "The Art Framing Guild provides mat cutters, glass cutters, frame saws, and a "
                    "mounting press. We build custom frames for paintings, prints, and photographs."
                ),
                "icon": "frame_inspect",
                "guild_lead": members[10],
            },
            {
                "name": "Ceramics",
                "intro": "Shaping earth into lasting forms.",
                "description": (
                    "The Ceramics Guild features electric and kick wheels, slab rollers, extruders, "
                    "and two kilns. We fire earthenware, stoneware, and porcelain."
                ),
                "icon": "emoji_objects",
                "guild_lead": members[11],
            },
            {
                "name": "Events",
                "intro": "Bringing the community together through shared experiences.",
                "description": (
                    "The Events Guild organizes open houses, gallery nights, maker faires, and "
                    "community gatherings. We coordinate volunteers, venues, and programming."
                ),
                "icon": "event",
                "guild_lead": members[12],
            },
            {
                "name": "Gardeners",
                "intro": "Growing food and community in shared garden spaces.",
                "description": (
                    "The Gardeners Guild maintains raised beds, a greenhouse, and composting "
                    "systems. We grow herbs, vegetables, and flowers for the makerspace community."
                ),
                "icon": "yard",
                "guild_lead": members[13],
            },
            {
                "name": "Jewelry",
                "intro": "Crafting wearable art from precious metals and stones.",
                "description": (
                    "The Jewelry Guild provides jewelers benches, flex shafts, rolling mills, a kiln, "
                    "and lapidary equipment. We work in silver, gold, bronze, and gemstones."
                ),
                "icon": "diamond",
                "guild_lead": members[14],
            },
            {
                "name": "Leather",
                "intro": "Crafting durable goods from hides and skins.",
                "description": (
                    "The Leather Guild maintains stitching horses, skivers, splitters, and stamping "
                    "tools. We make bags, belts, wallets, and custom leather goods."
                ),
                "icon": "checkroom",
                "guild_lead": members[15],
            },
            {
                "name": "Metalworkers",
                "intro": "Forging ideas in steel and aluminum.",
                "description": (
                    "The Metalworkers Guild covers welding (MIG, TIG, stick), plasma cutting, metal lathe, "
                    "and metal casting. We work with steel, aluminum, brass, and copper."
                ),
                "icon": "precision_manufacturing",
                "guild_lead": members[16],
            },
            {
                "name": "Glass",
                "intro": "Shaping light and color through molten glass.",
                "description": (
                    "The Glass Guild operates a glassblowing studio with a glory hole, annealer, "
                    "and lampworking stations. We also do stained glass and fusing."
                ),
                "icon": "window",
                "guild_lead": members[17],
            },
            {
                "name": "Prison Outreach",
                "intro": "Connecting makers inside and outside prison walls.",
                "description": (
                    "The Prison Outreach Guild coordinates art and craft programs with local "
                    "correctional facilities, providing instruction and materials for incarcerated makers."
                ),
                "icon": "volunteer_activism",
                "guild_lead": members[18],
            },
            {
                "name": "Tech",
                "intro": "Bridging the physical and digital worlds.",
                "description": (
                    "The Tech Guild specializes in microcontrollers, PCB design, soldering, 3D printing, "
                    "and embedded systems. We host Arduino and Raspberry Pi workshops regularly."
                ),
                "icon": "memory",
                "guild_lead": members[20],
            },
            {
                "name": "Textiles",
                "intro": "Weaving community through fiber arts.",
                "description": (
                    "From spinning and weaving to sewing and embroidery, the Textiles Guild supports "
                    "all fiber arts. We have industrial sewing machines, sergers, and a floor loom."
                ),
                "icon": "checkroom",
                "guild_lead": members[21],
            },
            {
                "name": "Visual Arts",
                "intro": "Exploring painting, drawing, and mixed media.",
                "description": (
                    "The Visual Arts Guild provides easels, a printmaking press, and studio space "
                    "for painters, illustrators, and mixed-media artists."
                ),
                "icon": "palette",
                "guild_lead": members[22],
            },
            {
                "name": "Woodworkers",
                "intro": "Crafting functional art from raw timber.",
                "description": (
                    "The Woodworkers Guild is home to cabinet makers, furniture builders, and sculptors. "
                    "We maintain a fully equipped shop with table saws, planers, jointers, and a CNC router."
                ),
                "icon": "handyman",
                "guild_lead": members[23],
            },
            {
                "name": "Writers",
                "intro": "Putting words on the page, together.",
                "description": (
                    "The Writers Guild offers a quiet writing room, weekly critique groups, and "
                    "open-mic nights. We support fiction, nonfiction, poetry, and zine-making."
                ),
                "icon": "edit_note",
                "guild_lead": members[24],
            },
        ]

        guilds = []
        for item in guilds_data:
            guild, _ = Guild.objects.get_or_create(
                name=item["name"],
                defaults={
                    "intro": item["intro"],
                    "description": item["description"],
                    "icon": item["icon"],
                    "guild_lead": item["guild_lead"],
                    "is_active": True,
                },
            )
            guilds.append(guild)

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(guilds)} guilds"))
        return guilds

    # -------------------------------------------------------------------------
    # Guild Votes
    # -------------------------------------------------------------------------

    def _seed_guild_votes(self, members: list, guilds: list) -> None:
        votes_data = [
            (0, [(1, guilds[0]), (2, guilds[4]), (3, guilds[10])]),
            (2, [(1, guilds[1]), (2, guilds[7]), (3, guilds[9])]),
            (4, [(1, guilds[12]), (2, guilds[6]), (3, guilds[11])]),
            (5, [(1, guilds[1]), (2, guilds[10]), (3, guilds[3])]),
            (8, [(1, guilds[6]), (2, guilds[12]), (3, guilds[5])]),
            (9, [(1, guilds[3]), (2, guilds[9]), (3, guilds[13])]),
        ]
        count = 0
        for member_idx, priority_guilds in votes_data:
            member = members[member_idx]
            for priority, guild in priority_guilds:
                GuildVote.objects.get_or_create(
                    member=member,
                    priority=priority,
                    defaults={"guild": guild},
                )
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded {count} guild votes"))

    # -------------------------------------------------------------------------
    # Spaces
    # -------------------------------------------------------------------------

    def _seed_spaces(self, guilds: list) -> list:
        from decimal import Decimal

        spaces: list = []
        studio_configs = [
            ("S-101", "Woodworkers Workshop", 600, Decimal("3.75"), Space.Status.OCCUPIED, guilds[12]),
            ("S-102", "Metalworkers Shop", 500, Decimal("3.50"), Space.Status.OCCUPIED, guilds[6]),
            ("S-103", "Ceramics Studio", 400, Decimal("3.75"), Space.Status.OCCUPIED, guilds[1]),
            ("S-104", "Glass Studio", 350, Decimal("3.75"), Space.Status.OCCUPIED, guilds[7]),
            ("S-105", "Jewelry Studio", 250, Decimal("4.25"), Space.Status.OCCUPIED, guilds[4]),
            ("S-106", "Textiles Studio", 300, Decimal("3.75"), Space.Status.OCCUPIED, guilds[10]),
            ("S-107", "Leather Workshop", 280, Decimal("3.75"), Space.Status.OCCUPIED, guilds[5]),
            ("S-108", "Art Framing Studio", 240, Decimal("4.00"), Space.Status.OCCUPIED, guilds[0]),
            ("S-109", "Tech Lab", 260, Decimal("4.00"), Space.Status.OCCUPIED, guilds[9]),
            ("S-110", "Visual Arts Gallery", 400, Decimal("3.50"), Space.Status.OCCUPIED, guilds[11]),
            ("S-111", "Writers Room", 180, Decimal("4.50"), Space.Status.OCCUPIED, guilds[13]),
            ("S-112", "Shared Open Workshop", 600, Decimal("3.25"), Space.Status.AVAILABLE, None),
        ]
        for sid, name, sqft, rate, status, guild in studio_configs:
            space, _ = Space.objects.get_or_create(
                space_id=sid,
                defaults={
                    "name": name,
                    "space_type": Space.SpaceType.STUDIO,
                    "size_sqft": Decimal(str(sqft)),
                    "rate_per_sqft": rate,
                    "status": status,
                    "is_rentable": True,
                    "sublet_guild": guild,
                },
            )
            spaces.append(space)

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(spaces)} spaces"))
        return spaces

    # -------------------------------------------------------------------------
    # Leases
    # -------------------------------------------------------------------------

    def _seed_leases(self, members: list, guilds: list, spaces: list) -> None:
        from decimal import Decimal

        ct_member = ContentType.objects.get_for_model(Member)
        ct_guild = ContentType.objects.get_for_model(Guild)

        leases_data = [
            (ct_guild, guilds[12], 0, Decimal("2250.00"), Lease.LeaseType.ANNUAL),
            (ct_guild, guilds[6], 1, Decimal("1750.00"), Lease.LeaseType.ANNUAL),
            (ct_guild, guilds[1], 2, Decimal("1500.00"), Lease.LeaseType.ANNUAL),
            (ct_guild, guilds[7], 3, Decimal("1312.50"), Lease.LeaseType.MONTH_TO_MONTH),
            (ct_guild, guilds[4], 4, Decimal("1062.50"), Lease.LeaseType.ANNUAL),
            (ct_guild, guilds[10], 5, Decimal("1125.00"), Lease.LeaseType.ANNUAL),
            (ct_member, members[0], 6, Decimal("1050.00"), Lease.LeaseType.MONTH_TO_MONTH),
            (ct_member, members[2], 7, Decimal("960.00"), Lease.LeaseType.ANNUAL),
        ]
        count = 0
        for ct, tenant, space_idx, rent, lease_type in leases_data:
            Lease.objects.get_or_create(
                content_type=ct,
                object_id=tenant.pk,
                space=spaces[space_idx],
                defaults={
                    "lease_type": lease_type,
                    "base_price": rent,
                    "monthly_rent": rent,
                    "start_date": TODAY - timedelta(days=365),
                },
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded {count} leases"))

    # -------------------------------------------------------------------------
    # Maker Classes
    # -------------------------------------------------------------------------

    def _seed_maker_classes(self, guilds: list) -> None:
        classes_data = [
            ("Intro to Woodworking", guilds[12], MakerClass.Status.PUBLISHED),
            ("Welding 101", guilds[6], MakerClass.Status.PUBLISHED),
            ("Pottery Basics", guilds[1], MakerClass.Status.PUBLISHED),
            ("Sewing Machine Basics", guilds[10], MakerClass.Status.PUBLISHED),
            ("Arduino Workshop", guilds[9], MakerClass.Status.PUBLISHED),
            ("Glassblowing Intro", guilds[7], MakerClass.Status.PUBLISHED),
            ("Ring Making", guilds[4], MakerClass.Status.DRAFT),
            ("Leather Belt Making", guilds[5], MakerClass.Status.DRAFT),
        ]
        count = 0
        for name, guild, status in classes_data:
            MakerClass.objects.get_or_create(
                name=name,
                defaults={"guild": guild, "status": status},
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded {count} maker classes"))

    # -------------------------------------------------------------------------
    # Orientations
    # -------------------------------------------------------------------------

    def _seed_orientations(self, guilds: list) -> None:
        now = timezone.now()
        orientations_data = [
            ("Woodworking Orientation", guilds[12], 60),
            ("Metalworking Orientation", guilds[6], 90),
            ("Ceramics Orientation", guilds[1], 60),
            ("Glass Orientation", guilds[7], 90),
        ]
        count = 0
        for name, guild, duration in orientations_data:
            orientation, _ = Orientation.objects.get_or_create(
                name=name,
                defaults={"guild": guild, "duration_minutes": duration},
            )
            ScheduledOrientation.objects.get_or_create(
                orientation=orientation,
                scheduled_at=now + timedelta(days=7),
                defaults={"status": ScheduledOrientation.Status.SCHEDULED},
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded {count} orientations with scheduled sessions"))

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def _seed_events(self, guilds: list, users: list) -> list:
        now = timezone.now()
        events_data = [
            {
                "name": "Open House",
                "description": "Monthly open house event for prospective members to tour the space and meet our community.",
                "starts_at": now + timedelta(days=15),
                "ends_at": now + timedelta(days=15, hours=3),
                "location": "Past Lives Makerspace - Main Hall",
                "is_published": True,
                "guild": None,
                "created_by": users[0],
            },
            {
                "name": "Gallery Night",
                "description": "Show off your projects and creations at our quarterly gallery night. Food and drinks provided.",
                "starts_at": now + timedelta(days=30),
                "ends_at": now + timedelta(days=30, hours=4),
                "location": "Past Lives Makerspace - All Studios",
                "is_published": True,
                "guild": None,
                "created_by": users[1],
            },
            {
                "name": "Maker Faire Prep Workshop",
                "description": "Workshop to help members prepare projects for the Portland Mini Maker Faire.",
                "starts_at": now + timedelta(days=45),
                "ends_at": now + timedelta(days=45, hours=5),
                "location": "Woodworkers Workshop",
                "is_published": True,
                "guild": guilds[12],
                "created_by": users[23],
            },
            {
                "name": "Workshop Weekend",
                "description": "Two-day intensive workshop weekend featuring multiple classes and hands-on sessions.",
                "starts_at": now + timedelta(days=60),
                "ends_at": now + timedelta(days=62),
                "location": "Past Lives Makerspace",
                "is_published": False,
                "guild": None,
                "created_by": users[9],
            },
        ]
        events = []
        for item in events_data:
            event, _ = Event.objects.get_or_create(
                name=item["name"],
                defaults={
                    "starts_at": item["starts_at"],
                    "description": item["description"],
                    "ends_at": item["ends_at"],
                    "location": item["location"],
                    "is_published": item["is_published"],
                    "guild": item["guild"],
                    "created_by": item["created_by"],
                },
            )
            events.append(event)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(events)} events"))
        return events

    # -------------------------------------------------------------------------
    # Favorites
    # -------------------------------------------------------------------------

    def _seed_favorites(self, users: list, events: list) -> None:
        """Create FavoriteEvent records linking demo users to seeded Events."""
        event_ct = ContentType.objects.get_for_model(Event)
        favorites_data = [
            (users[0], events[0]),  # mia.chen → Open House
            (users[0], events[1]),  # mia.chen → Gallery Night
            (users[1], events[0]),  # jordan.walsh → Open House
            (users[1], events[2]),  # jordan.walsh → Maker Faire Prep Workshop
            (users[2], events[1]),  # sam.okafor → Gallery Night
            (users[2], events[3]),  # sam.okafor → Workshop Weekend
            (users[3], events[0]),  # priya.nair → Open House
            (users[4], events[2]),  # eli.reyes → Maker Faire Prep Workshop
            (users[5], events[1]),  # casey.burke → Gallery Night
            (users[6], events[3]),  # devon.huang → Workshop Weekend
        ]
        count = 0
        for user, event in favorites_data:
            FavoriteEvent.objects.get_or_create(
                user=user,
                content_type=event_ct,
                object_id=event.pk,
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded {count} favorite events"))
