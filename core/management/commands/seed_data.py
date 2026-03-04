from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

if TYPE_CHECKING:
    from django.contrib.auth.models import User as UserType

from membership.models import (
    Buyable,
    Guild,
    GuildMembership,
    GuildWishlistItem,
    Lease,
    Member,
    MembershipPlan,
    Order,
    Space,
)

User = get_user_model()

# ------------------------------------------------------------------
# People data
# ------------------------------------------------------------------

STAFF = [
    {
        "email": "morlock@pastlives.space",
        "name": "Morlock",
        "preferred": "Morlock",
        "role": "employee",
        "join": "2020-01-01",
    },
    {
        "email": "athan@pastlives.space",
        "name": "Athan Spathas",
        "preferred": "Athan",
        "role": "employee",
        "join": "2022-03-01",
    },
    {
        "email": "dixie@pastlives.space",
        "name": "Dixie Junius",
        "preferred": "Dixie",
        "role": "employee",
        "join": "2023-01-15",
    },
    {
        "email": "lee@pastlives.space",
        "name": "Lee Mendelsohn",
        "preferred": "Lee",
        "role": "employee",
        "join": "2022-06-01",
    },
    {
        "email": "shane@pastlives.space",
        "name": "Shane Stewart",
        "preferred": "Shane",
        "role": "employee",
        "join": "2023-04-01",
    },
    {
        "email": "sushuma@pastlives.space",
        "name": "Sushuma",
        "preferred": "Sushuma",
        "role": "employee",
        "join": "2024-06-01",
    },
    {
        "email": "phoebe@pastlives.space",
        "name": "Phoebe Valenti",
        "preferred": "Phoebe",
        "role": "contractor",
        "join": "2023-02-01",
    },
]

GUILD_LEADS = [
    {
        "email": "jenelle@pastlives.space",
        "name": "Jenelle Giordano",
        "preferred": "Jenelle",
        "guild": "Art Framing Guild",
        "join": "2023-03-01",
    },
    {
        "email": "jholtman@pastlives.space",
        "name": "J. Holtman",
        "preferred": "JHoltman",
        "guild": "Ceramics Guild",
        "join": "2022-09-01",
    },
    {
        "email": "jamie@pastlives.space",
        "name": "Jamie Lindner",
        "preferred": "Jamie",
        "guild": "Events Guild",
        "join": "2023-05-01",
    },
    {
        "email": "amber@pastlives.space",
        "name": "Amber Terlouw",
        "preferred": "Amber",
        "guild": "Gardeners Guild",
        "join": "2023-06-01",
    },
    {
        "email": "kristin@pastlives.space",
        "name": "Kristin Mitsu Shiga",
        "preferred": "Kristin",
        "guild": "Jewelry Guild",
        "join": "2023-01-01",
    },
    {
        "email": "shawn@pastlives.space",
        "name": "Shawn Fox",
        "preferred": "Shawn",
        "guild": "Leatherworking Guild",
        "join": "2023-04-15",
    },
    {
        "email": "billy@pastlives.space",
        "name": "William Ottaviani",
        "preferred": "BillyO",
        "guild": "Metal Guild",
        "join": "2022-11-01",
    },
    {
        "email": "cait@pastlives.space",
        "name": "Cait Johnstone",
        "preferred": "Cait",
        "guild": "Glass Guild",
        "join": "2023-02-15",
    },
    {
        "email": "deb@pastlives.space",
        "name": "Deb Clough",
        "preferred": "Deb",
        "guild": "Prison Outreach Guild",
        "join": "2022-08-01",
    },
    {
        "email": "mira@pastlives.space",
        "name": "Mira Glasser",
        "preferred": "Mira",
        "guild": "Prison Outreach Guild",
        "join": "2023-01-15",
    },
    {
        "email": "patricia@pastlives.space",
        "name": "Patricia Fischer",
        "preferred": "Patricia",
        "guild": "Tech Guild",
        "join": "2023-07-01",
    },
    {
        "email": "brooke@pastlives.space",
        "name": "Brooke Sauvage",
        "preferred": "Brooke",
        "guild": "Textiles Guild",
        "join": "2023-03-15",
    },
    {
        "email": "kate@pastlives.space",
        "name": "Kate",
        "preferred": "Kate",
        "guild": "Visual Arts Guild",
        "join": "2023-05-15",
    },
    {
        "email": "chris@pastlives.space",
        "name": "Chris Ellwanger",
        "preferred": "Chris",
        "guild": "Woodworking Guild",
        "join": "2022-10-01",
    },
    {
        "email": "penina@pastlives.space",
        "name": "Penina Finger",
        "preferred": "Penina",
        "guild": "Writers Guild",
        "join": "2023-06-15",
    },
]

# Primary guild lead per guild (for Guild.guild_lead FK — only one allowed)
PRIMARY_GUILD_LEADS = {
    "Art Framing Guild": "jenelle@pastlives.space",
    "Ceramics Guild": "jholtman@pastlives.space",
    "Events Guild": "jamie@pastlives.space",
    "Gardeners Guild": "amber@pastlives.space",
    "Glass Guild": "cait@pastlives.space",
    "Jewelry Guild": "kristin@pastlives.space",
    "Leatherworking Guild": "shawn@pastlives.space",
    "Metal Guild": "billy@pastlives.space",
    "Prison Outreach Guild": "deb@pastlives.space",
    "Tech Guild": "patricia@pastlives.space",
    "Textiles Guild": "brooke@pastlives.space",
    "Visual Arts Guild": "kate@pastlives.space",
    "Woodworking Guild": "chris@pastlives.space",
    "Writers Guild": "penina@pastlives.space",
}

REGULAR_MEMBERS = [
    {"email": "lane@pastlives.space", "name": "Lane Martinez", "preferred": "Lane", "join": "2024-01-15"},
    {"email": "pj@pastlives.space", "name": "PJ Nguyen", "preferred": "PJ", "join": "2024-02-01"},
    {"email": "dreenius@pastlives.space", "name": "Dreenius Walker", "preferred": "Dreenius", "join": "2024-03-01"},
    {"email": "robin@pastlives.space", "name": "Robin Blackwood", "preferred": "Robin", "join": "2024-04-15"},
    {"email": "alex@pastlives.space", "name": "Alex Rivera", "preferred": "Alex", "join": "2023-06-01"},
    {"email": "sam@pastlives.space", "name": "Sam Taylor", "preferred": "Sam", "join": "2024-03-01"},
    {"email": "casey@pastlives.space", "name": "Casey Finch", "preferred": "Casey", "join": "2024-06-01"},
    {"email": "morgan@pastlives.space", "name": "Morgan Reeves", "preferred": "Morgan", "join": "2024-07-15"},
]

# Regular members' guild memberships (non-lead)
MEMBER_GUILDS = {
    "lane@pastlives.space": ["Ceramics Guild", "Woodworking Guild"],
    "pj@pastlives.space": ["Glass Guild"],
    "dreenius@pastlives.space": ["Visual Arts Guild", "Events Guild"],
    "robin@pastlives.space": ["Textiles Guild"],
    "alex@pastlives.space": ["Ceramics Guild", "Metal Guild"],
    "sam@pastlives.space": ["Woodworking Guild", "Tech Guild"],
    "casey@pastlives.space": ["Jewelry Guild"],
    "morgan@pastlives.space": ["Writers Guild", "Visual Arts Guild"],
}


class Command(BaseCommand):
    help = "Load demo seed data for development"

    def handle(self, *args: str, **options: int) -> None:  # type: ignore[override]
        verbosity: int = options.get("verbosity", 1)

        plan = self._seed_membership_plan(verbosity)
        users = self._seed_users(verbosity)
        members = self._seed_members(users, plan, verbosity)
        guilds = self._seed_guilds(verbosity)
        self._seed_guild_leads(guilds, members, users, verbosity)
        self._seed_guild_links(guilds, verbosity)
        self._seed_wishlist_items(guilds, verbosity)
        buyables = self._seed_buyables(guilds, verbosity)
        spaces = self._seed_spaces(verbosity)
        self._seed_leases(guilds, spaces, verbosity)
        self._seed_orders(buyables, users, verbosity)

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Seed data loaded successfully."))

    # ------------------------------------------------------------------
    # Membership plan
    # ------------------------------------------------------------------

    def _seed_membership_plan(self, verbosity: int) -> MembershipPlan:
        plan, _ = MembershipPlan.objects.get_or_create(
            name="Standard",
            defaults={"monthly_price": Decimal("75.00")},
        )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Membership plan seeded."))

        return plan

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def _seed_users(self, verbosity: int) -> dict[str, UserType]:
        users: dict[str, UserType] = {}

        # Admin superuser
        admin, created = User.objects.get_or_create(
            username="admin@pastlives.space",
            defaults={
                "email": "admin@pastlives.space",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            admin.set_password("testpass123")
            admin.save()
        users["admin@pastlives.space"] = admin

        # All people: staff + guild leads + regular members
        all_people = [
            *[{"email": p["email"], "is_staff": True} for p in STAFF],
            *[{"email": p["email"], "is_staff": False} for p in GUILD_LEADS],
            *[{"email": p["email"], "is_staff": False} for p in REGULAR_MEMBERS],
        ]

        for person in all_people:
            email = person["email"]
            user, created = User.objects.get_or_create(
                username=email,
                defaults={"email": email, "is_staff": person["is_staff"]},
            )
            if created:
                user.set_password("testpass123")
                user.save()
            users[email] = user

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS(f"{len(users)} users seeded."))

        return users

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def _seed_members(
        self,
        users: dict[str, UserType],
        plan: MembershipPlan,
        verbosity: int,
    ) -> dict[str, Member]:
        members: dict[str, Member] = {}

        for person in STAFF:
            member, _ = Member.objects.get_or_create(
                user=users[person["email"]],
                defaults={
                    "full_legal_name": person["name"],
                    "preferred_name": person["preferred"],
                    "email": person["email"],
                    "membership_plan": plan,
                    "status": Member.Status.ACTIVE,
                    "role": person["role"],
                    "join_date": date.fromisoformat(person["join"]),
                },
            )
            members[person["email"]] = member

        for person in GUILD_LEADS:
            member, _ = Member.objects.get_or_create(
                user=users[person["email"]],
                defaults={
                    "full_legal_name": person["name"],
                    "preferred_name": person["preferred"],
                    "email": person["email"],
                    "membership_plan": plan,
                    "status": Member.Status.ACTIVE,
                    "role": Member.Role.GUILD_LEAD,
                    "join_date": date.fromisoformat(person["join"]),
                },
            )
            members[person["email"]] = member

        for person in REGULAR_MEMBERS:
            member, _ = Member.objects.get_or_create(
                user=users[person["email"]],
                defaults={
                    "full_legal_name": person["name"],
                    "preferred_name": person["preferred"],
                    "email": person["email"],
                    "membership_plan": plan,
                    "status": Member.Status.ACTIVE,
                    "role": Member.Role.STANDARD,
                    "join_date": date.fromisoformat(person["join"]),
                },
            )
            members[person["email"]] = member

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS(f"{len(members)} members seeded."))

        return members

    # ------------------------------------------------------------------
    # Guilds
    # ------------------------------------------------------------------

    def _seed_guilds(self, verbosity: int) -> dict[str, Guild]:
        guild_data = [
            {
                "name": "Ceramics Guild",
                "icon": "palette",
                "intro": "Clay, glaze, and fire",
                "description": (
                    "The Ceramics Guild offers open studio time, kiln access, "
                    "and structured classes for all skill levels."
                ),
            },
            {
                "name": "Woodworking Guild",
                "icon": "carpenter",
                "intro": "From rough lumber to fine furniture",
                "description": (
                    "The Woodworking Guild maintains a full shop with hand tools, power tools, and CNC equipment."
                ),
            },
            {
                "name": "Glass Guild",
                "icon": "blur_on",
                "intro": "Flamework, fusing, and stained glass",
                "description": (
                    "The Glass Guild explores the full spectrum of glass arts — "
                    "from flamework beads to large-format fused panels."
                ),
            },
            {
                "name": "Textiles Guild",
                "icon": "checkroom",
                "intro": "Fiber arts and fabric crafts",
                "description": (
                    "The Textiles Guild covers weaving, sewing, embroidery, natural dye, and surface design."
                ),
            },
            {
                "name": "Metal Guild",
                "icon": "hardware",
                "intro": "Welding, forging, and fabrication",
                "description": (
                    "The Metal Guild provides access to welding stations, forge, and metal fabrication equipment."
                ),
            },
            {
                "name": "Prison Outreach Guild",
                "icon": "volunteer_activism",
                "intro": "Art education in correctional facilities",
                "description": (
                    "The Prison Outreach Guild delivers art education programs inside Oregon correctional facilities."
                ),
            },
            {
                "name": "Leatherworking Guild",
                "icon": "content_cut",
                "intro": "Hide, stitch, and craft",
                "description": (
                    "The Leatherworking Guild provides tools and workspace for leather tooling, "
                    "stitching, and finishing."
                ),
            },
            {
                "name": "Jewelry Guild",
                "icon": "diamond",
                "intro": "Metalsmithing, beading, and lapidary",
                "description": (
                    "The Jewelry Guild supports bench work, stone setting, casting, and beadwork."
                ),
            },
            {
                "name": "Art Framing Guild",
                "icon": "crop_square",
                "intro": "Custom framing and preservation",
                "description": (
                    "The Art Framing Guild offers mat cutting, glass fitting, and custom frame assembly."
                ),
            },
            {
                "name": "Events Guild",
                "icon": "event",
                "intro": "Shows, markets, and community gatherings",
                "description": (
                    "The Events Guild organizes art shows, maker markets, and community events at the makerspace."
                ),
            },
            {
                "name": "Gardeners Guild",
                "icon": "yard",
                "intro": "Growing, composting, and green spaces",
                "description": (
                    "The Gardeners Guild maintains raised beds, a compost system, and native plantings on site."
                ),
            },
            {
                "name": "Tech Guild",
                "icon": "memory",
                "intro": "Electronics, 3D printing, and code",
                "description": (
                    "The Tech Guild operates 3D printers, laser cutters, and an electronics bench."
                ),
            },
            {
                "name": "Visual Arts Guild",
                "icon": "brush",
                "intro": "Painting, drawing, and printmaking",
                "description": (
                    "The Visual Arts Guild provides easel space, a printmaking press, and shared drawing sessions."
                ),
            },
            {
                "name": "Writers Guild",
                "icon": "edit_note",
                "intro": "Workshops, readings, and zine-making",
                "description": (
                    "The Writers Guild hosts workshops, open-mic readings, and a risograph zine press."
                ),
            },
        ]

        guilds: dict[str, Guild] = {}
        for data in guild_data:
            guild, _ = Guild.objects.get_or_create(
                name=data["name"],
                defaults={
                    "icon": data["icon"],
                    "intro": data["intro"],
                    "description": data["description"],
                    "is_active": True,
                },
            )
            guilds[data["name"]] = guild

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS(f"{len(guilds)} guilds seeded."))

        return guilds

    # ------------------------------------------------------------------
    # Guild leads + memberships
    # ------------------------------------------------------------------

    def _seed_guild_leads(
        self,
        guilds: dict[str, Guild],
        members: dict[str, Member],
        users: dict[str, UserType],
        verbosity: int,
    ) -> None:
        # Set Guild.guild_lead FK for each guild's primary lead
        for guild_name, email in PRIMARY_GUILD_LEADS.items():
            guild = guilds[guild_name]
            member = members[email]
            if guild.guild_lead != member:
                guild.guild_lead = member
                guild.save()

        # Create GuildMembership (is_lead=True) for ALL guild leads
        for person in GUILD_LEADS:
            guild = guilds[person["guild"]]
            user = users[person["email"]]
            GuildMembership.objects.get_or_create(
                guild=guild,
                user=user,
                defaults={"is_lead": True},
            )

        # Create GuildMembership (is_lead=False) for regular members
        for email, guild_names in MEMBER_GUILDS.items():
            user = users[email]
            for guild_name in guild_names:
                guild = guilds[guild_name]
                GuildMembership.objects.get_or_create(
                    guild=guild,
                    user=user,
                    defaults={"is_lead": False},
                )

        lead_count = GuildMembership.objects.filter(is_lead=True).count()
        member_count = GuildMembership.objects.filter(is_lead=False).count()
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(f"Guild memberships seeded ({lead_count} leads, {member_count} members).")
            )

    # ------------------------------------------------------------------
    # Guild links
    # ------------------------------------------------------------------

    def _seed_guild_links(self, guilds: dict[str, Guild], verbosity: int) -> None:
        links_map = {
            "Ceramics Guild": [
                {"name": "Instagram", "url": "https://instagram.com/pastlives_ceramics"},
                {"name": "Website", "url": "https://pastlives.space/guilds/ceramics"},
            ],
            "Woodworking Guild": [
                {"name": "Instagram", "url": "https://instagram.com/pastlives_wood"},
            ],
            "Glass Guild": [
                {"name": "Instagram", "url": "https://instagram.com/pastlives_glass"},
                {"name": "Etsy", "url": "https://etsy.com/shop/pastlivesglass"},
            ],
        }

        for guild_name, links in links_map.items():
            guild = guilds[guild_name]
            if not guild.links:
                guild.links = links
                guild.save()

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Guild links seeded."))

    # ------------------------------------------------------------------
    # Wishlist items
    # ------------------------------------------------------------------

    def _seed_wishlist_items(self, guilds: dict[str, Guild], verbosity: int) -> None:
        wishlist_data = [
            {
                "guild": "Ceramics Guild",
                "name": "Skutt KM-1227 Kiln",
                "description": "Larger electric kiln to increase firing capacity.",
                "estimated_cost": Decimal("3200.00"),
                "link": "https://skutt.com/products/kilns/km-series/km-1227/",
            },
            {
                "guild": "Ceramics Guild",
                "name": "Brent CXC Pottery Wheel",
                "description": "Extra wheel for busy open studio sessions.",
                "estimated_cost": Decimal("1150.00"),
                "link": "https://www.brentpotterywheels.com/product/cxc/",
            },
            {
                "guild": "Woodworking Guild",
                "name": "SawStop 3HP Cabinet Saw",
                "description": "Safety table saw with flesh-detection braking.",
                "estimated_cost": Decimal("4200.00"),
                "link": "https://www.sawstop.com/table-saws/professional-cabinet-saw/",
            },
        ]

        for item in wishlist_data:
            guild = guilds[str(item["guild"])]
            GuildWishlistItem.objects.get_or_create(
                guild=guild,
                name=str(item["name"]),
                defaults={
                    "description": item["description"],
                    "estimated_cost": item["estimated_cost"],
                    "link": item["link"],
                },
            )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Guild wishlist items seeded."))

    # ------------------------------------------------------------------
    # Buyables
    # ------------------------------------------------------------------

    def _seed_buyables(
        self,
        guilds: dict[str, Guild],
        verbosity: int,
    ) -> dict[str, Buyable]:
        buyable_data = [
            {"guild": "Ceramics Guild", "name": "Stoneware Clay (25 lb)", "unit_price": Decimal("18.00")},
            {"guild": "Ceramics Guild", "name": "Glaze (pint)", "unit_price": Decimal("12.00")},
            {"guild": "Ceramics Guild", "name": "Kiln Firing", "unit_price": Decimal("25.00")},
            {"guild": "Woodworking Guild", "name": "Hardwood Board (bd ft)", "unit_price": Decimal("8.00")},
            {"guild": "Woodworking Guild", "name": "Plywood Sheet (4x8)", "unit_price": Decimal("45.00")},
            {"guild": "Glass Guild", "name": "Bullseye Sheet Glass", "unit_price": Decimal("22.00")},
            {"guild": "Glass Guild", "name": "Glass Frit (1 lb)", "unit_price": Decimal("14.00")},
            {"guild": "Textiles Guild", "name": "Natural Dye Kit", "unit_price": Decimal("28.00")},
            {"guild": "Metal Guild", "name": "Steel Rod (6 ft)", "unit_price": Decimal("12.00")},
            {"guild": "Metal Guild", "name": "Welding Wire Spool", "unit_price": Decimal("35.00")},
            {"guild": "Leatherworking Guild", "name": "Veg-Tan Leather Hide (sq ft)", "unit_price": Decimal("15.00")},
            {"guild": "Jewelry Guild", "name": "Sterling Silver Wire (ft)", "unit_price": Decimal("10.00")},
            {"guild": "Tech Guild", "name": "PLA Filament (1 kg)", "unit_price": Decimal("22.00")},
        ]

        buyables: dict[str, Buyable] = {}
        for item in buyable_data:
            guild_name = str(item["guild"])
            name = str(item["name"])
            guild = guilds[guild_name]
            buyable, _ = Buyable.objects.get_or_create(
                guild=guild,
                name=name,
                defaults={
                    "unit_price": item["unit_price"],
                    "is_active": True,
                },
            )
            buyables[name] = buyable

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS(f"{len(buyables)} buyables seeded."))

        return buyables

    # ------------------------------------------------------------------
    # Spaces
    # ------------------------------------------------------------------

    def _seed_spaces(
        self,
        verbosity: int,
    ) -> dict[str, Space]:
        space_data = [
            {
                "space_id": "S-101",
                "name": "Ceramics Studio",
                "space_type": Space.SpaceType.STUDIO,
                "size_sqft": Decimal("400.00"),
                "status": Space.Status.OCCUPIED,
                "is_rentable": True,
            },
            {
                "space_id": "S-102",
                "name": "Wood Shop",
                "space_type": Space.SpaceType.STUDIO,
                "size_sqft": Decimal("600.00"),
                "status": Space.Status.OCCUPIED,
                "is_rentable": True,
            },
            {
                "space_id": "S-103",
                "name": "Glass Studio",
                "space_type": Space.SpaceType.STUDIO,
                "size_sqft": Decimal("300.00"),
                "status": Space.Status.OCCUPIED,
                "is_rentable": True,
            },
            {
                "space_id": "ST-01",
                "name": "Guild Storage A",
                "space_type": Space.SpaceType.STORAGE,
                "size_sqft": Decimal("80.00"),
                "status": Space.Status.AVAILABLE,
                "is_rentable": True,
            },
        ]

        spaces: dict[str, Space] = {}
        for item in space_data:
            space, _ = Space.objects.get_or_create(
                space_id=item["space_id"],
                defaults=item,
            )
            spaces[space.space_id] = space

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS(f"{len(spaces)} spaces seeded."))

        return spaces

    # ------------------------------------------------------------------
    # Leases
    # ------------------------------------------------------------------

    def _seed_leases(
        self,
        guilds: dict[str, Guild],
        spaces: dict[str, Space],
        verbosity: int,
    ) -> None:
        guild_ct = ContentType.objects.get_for_model(Guild)

        lease_data = [
            {
                "guild": "Ceramics Guild",
                "space_id": "S-101",
                "base_price": Decimal("1500.00"),
                "monthly_rent": Decimal("1500.00"),
            },
            {
                "guild": "Woodworking Guild",
                "space_id": "S-102",
                "base_price": Decimal("2250.00"),
                "monthly_rent": Decimal("2250.00"),
            },
            {
                "guild": "Glass Guild",
                "space_id": "S-103",
                "base_price": Decimal("1125.00"),
                "monthly_rent": Decimal("1125.00"),
            },
        ]

        for item in lease_data:
            guild = guilds[str(item["guild"])]
            space = spaces[str(item["space_id"])]
            Lease.objects.get_or_create(
                content_type=guild_ct,
                object_id=guild.pk,
                space=space,
                defaults={
                    "lease_type": Lease.LeaseType.MONTH_TO_MONTH,
                    "base_price": item["base_price"],
                    "monthly_rent": item["monthly_rent"],
                    "start_date": date(2024, 1, 1),
                },
            )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Guild leases seeded."))

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def _seed_orders(
        self,
        buyables: dict[str, Buyable],
        users: dict[str, UserType],
        verbosity: int,
    ) -> None:
        from django.utils import timezone

        order_data = [
            {
                "buyable": "Stoneware Clay (25 lb)",
                "user": "lane@pastlives.space",
                "quantity": 2,
                "amount": 3600,
                "status": Order.Status.PAID,
                "paid_at": timezone.now(),
            },
            {
                "buyable": "Bullseye Sheet Glass",
                "user": "cait@pastlives.space",
                "quantity": 3,
                "amount": 6600,
                "status": Order.Status.PAID,
                "paid_at": timezone.now(),
            },
            {
                "buyable": "Hardwood Board (bd ft)",
                "user": "chris@pastlives.space",
                "quantity": 10,
                "amount": 8000,
                "status": Order.Status.PAID,
                "paid_at": timezone.now(),
            },
            {
                "buyable": "Welding Wire Spool",
                "user": "billy@pastlives.space",
                "quantity": 1,
                "amount": 3500,
                "status": Order.Status.PENDING,
                "paid_at": None,
            },
        ]

        for item in order_data:
            buyable = buyables[str(item["buyable"])]
            user = users[str(item["user"])]
            Order.objects.get_or_create(
                buyable=buyable,
                user=user,
                amount=item["amount"],
                defaults={
                    "quantity": item["quantity"],
                    "status": item["status"],
                    "paid_at": item["paid_at"],
                },
            )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Sample orders seeded."))
