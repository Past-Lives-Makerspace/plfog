from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

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

DEFAULT_MEMBERS_FILE = Path(settings.BASE_DIR) / "core" / "data" / "seed_members.json"


class Command(BaseCommand):
    help = "Load demo seed data for development"

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--members-file",
            type=str,
            default=None,
            help="Path to seed members JSON file (default: core/data/seed_members.json)",
        )

    def _load_members_data(self, members_file: str | None) -> dict[str, Any]:
        path = Path(members_file) if members_file else DEFAULT_MEMBERS_FILE
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if not path.exists():
            raise CommandError(
                f"Members file not found: {path}\n"
                f"Copy core/data/seed_members.example.json to {path} and fill in real data, "
                f"or pass --members-file to point to a different file."
            )
        with open(path) as f:
            return json.load(f)  # type: ignore[no-any-return]

    def handle(self, *args: str, **options: Any) -> None:  # type: ignore[override]
        verbosity: int = options.get("verbosity", 1)
        members_data = self._load_members_data(options.get("members_file"))

        plan = self._seed_membership_plan(verbosity)
        users = self._seed_users(members_data, verbosity)
        members = self._seed_members(members_data, users, plan, verbosity)
        guilds = self._seed_guilds(verbosity)
        self._seed_guild_leads(members_data, guilds, members, users, verbosity)
        self._seed_guild_links(guilds, verbosity)
        self._seed_wishlist_items(guilds, verbosity)
        buyables = self._seed_buyables(guilds, verbosity)
        spaces = self._seed_spaces(verbosity)
        self._seed_leases(guilds, spaces, verbosity)
        self._seed_orders(members_data, buyables, users, verbosity)

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

    def _seed_users(self, members_data: dict[str, Any], verbosity: int) -> dict[str, UserType]:
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
            *[{"email": p["email"], "is_staff": True} for p in members_data["staff"]],
            *[{"email": p["email"], "is_staff": False} for p in members_data["guild_leads"]],
            *[{"email": p["email"], "is_staff": False} for p in members_data["regular_members"]],
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
        members_data: dict[str, Any],
        users: dict[str, UserType],
        plan: MembershipPlan,
        verbosity: int,
    ) -> dict[str, Member]:
        members: dict[str, Member] = {}

        for person in members_data["staff"]:
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

        for person in members_data["guild_leads"]:
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

        for person in members_data["regular_members"]:
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
                "description": ("The Jewelry Guild supports bench work, stone setting, casting, and beadwork."),
            },
            {
                "name": "Art Framing Guild",
                "icon": "crop_square",
                "intro": "Custom framing and preservation",
                "description": ("The Art Framing Guild offers mat cutting, glass fitting, and custom frame assembly."),
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
                "description": ("The Tech Guild operates 3D printers, laser cutters, and an electronics bench."),
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
                "description": ("The Writers Guild hosts workshops, open-mic readings, and a risograph zine press."),
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
        members_data: dict[str, Any],
        guilds: dict[str, Guild],
        members: dict[str, Member],
        users: dict[str, UserType],
        verbosity: int,
    ) -> None:
        # Set Guild.guild_lead FK for each guild's primary lead
        for guild_name, email in members_data["primary_guild_leads"].items():
            guild = guilds[guild_name]
            member = members[email]
            if guild.guild_lead != member:
                guild.guild_lead = member
                guild.save()

        # Create GuildMembership (is_lead=True) for ALL guild leads
        for person in members_data["guild_leads"]:
            guild = guilds[person["guild"]]
            user = users[person["email"]]
            GuildMembership.objects.get_or_create(
                guild=guild,
                user=user,
                defaults={"is_lead": True},
            )

        # Create GuildMembership (is_lead=False) for regular members
        for email, guild_names in members_data["member_guilds"].items():
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
        members_data: dict[str, Any],
        buyables: dict[str, Buyable],
        users: dict[str, UserType],
        verbosity: int,
    ) -> None:
        from django.utils import timezone

        for item in members_data.get("orders", []):
            buyable = buyables[str(item["buyable"])]
            user = users[str(item["user"])]
            status = item["status"]
            Order.objects.get_or_create(
                buyable=buyable,
                user=user,
                amount=item["amount"],
                defaults={
                    "quantity": item["quantity"],
                    "status": status,
                    "paid_at": timezone.now() if status == Order.Status.PAID else None,
                },
            )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Sample orders seeded."))
