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


class Command(BaseCommand):
    help = "Load demo seed data for development"

    def handle(self, *args: str, **options: int) -> None:  # type: ignore[override]
        verbosity: int = options.get("verbosity", 1)

        users = self._seed_users(verbosity)
        plan = self._seed_membership_plan(verbosity)
        self._seed_members(users, plan, verbosity)
        guilds = self._seed_guilds(verbosity)
        self._seed_guild_memberships(guilds, users, verbosity)
        self._seed_guild_links(guilds, verbosity)
        self._seed_wishlist_items(guilds, verbosity)
        buyables = self._seed_buyables(guilds, verbosity)
        spaces = self._seed_spaces(guilds, verbosity)
        self._seed_leases(guilds, spaces, verbosity)
        self._seed_orders(buyables, users, verbosity)

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Seed data loaded successfully."))

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def _seed_users(self, verbosity: int) -> dict[str, UserType]:
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

        lead1, created = User.objects.get_or_create(
            username="lead1@pastlives.space",
            defaults={"email": "lead1@pastlives.space"},
        )
        if created:
            lead1.set_password("testpass123")
            lead1.save()

        lead2, created = User.objects.get_or_create(
            username="lead2@pastlives.space",
            defaults={"email": "lead2@pastlives.space"},
        )
        if created:
            lead2.set_password("testpass123")
            lead2.save()

        member_user, created = User.objects.get_or_create(
            username="member@pastlives.space",
            defaults={"email": "member@pastlives.space"},
        )
        if created:
            member_user.set_password("testpass123")
            member_user.save()

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Users seeded."))

        return {"admin": admin, "lead1": lead1, "lead2": lead2, "member": member_user}

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
    # Members
    # ------------------------------------------------------------------

    def _seed_members(
        self,
        users: dict[str, UserType],
        plan: MembershipPlan,
        verbosity: int,
    ) -> None:
        Member.objects.get_or_create(
            user=users["lead1"],
            defaults={
                "full_legal_name": "Alex Rivera",
                "preferred_name": "Alex",
                "email": users["lead1"].email,
                "membership_plan": plan,
                "status": Member.Status.ACTIVE,
                "join_date": date(2023, 6, 1),
            },
        )

        Member.objects.get_or_create(
            user=users["lead2"],
            defaults={
                "full_legal_name": "Jordan Kim",
                "preferred_name": "Jordan",
                "email": users["lead2"].email,
                "membership_plan": plan,
                "status": Member.Status.ACTIVE,
                "join_date": date(2023, 9, 15),
            },
        )

        Member.objects.get_or_create(
            user=users["member"],
            defaults={
                "full_legal_name": "Sam Taylor",
                "preferred_name": "Sam",
                "email": users["member"].email,
                "membership_plan": plan,
                "status": Member.Status.ACTIVE,
                "role": Member.Role.STANDARD,
                "join_date": date(2024, 3, 1),
            },
        )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Members seeded."))

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
    # Guild memberships
    # ------------------------------------------------------------------

    def _seed_guild_memberships(
        self,
        guilds: dict[str, Guild],
        users: dict[str, UserType],
        verbosity: int,
    ) -> None:
        lead1_guilds = ["Ceramics Guild", "Woodworking Guild"]
        lead2_guilds = ["Glass Guild", "Metal Guild"]

        for name in lead1_guilds:
            GuildMembership.objects.get_or_create(
                guild=guilds[name],
                user=users["lead1"],
                defaults={"is_lead": True},
            )

        for name in lead2_guilds:
            GuildMembership.objects.get_or_create(
                guild=guilds[name],
                user=users["lead2"],
                defaults={"is_lead": True},
            )

        member_guilds = ["Ceramics Guild", "Textiles Guild"]
        for name in member_guilds:
            GuildMembership.objects.get_or_create(
                guild=guilds[name],
                user=users["member"],
                defaults={"is_lead": False},
            )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Guild memberships seeded."))

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
            {"guild": "Ceramics Guild", "name": "Ceramics Class Pass", "unit_price": Decimal("45.00")},
            {"guild": "Ceramics Guild", "name": "Open Studio Time", "unit_price": Decimal("15.00")},
            {"guild": "Ceramics Guild", "name": "Kiln Firing", "unit_price": Decimal("25.00")},
            {"guild": "Woodworking Guild", "name": "Wood Shop Day Pass", "unit_price": Decimal("30.00")},
            {"guild": "Woodworking Guild", "name": "Joinery Workshop", "unit_price": Decimal("65.00")},
            {"guild": "Glass Guild", "name": "Flamework Intro Class", "unit_price": Decimal("55.00")},
            {"guild": "Glass Guild", "name": "Glass Fusing Session", "unit_price": Decimal("40.00")},
            {"guild": "Textiles Guild", "name": "Natural Dye Workshop", "unit_price": Decimal("35.00")},
            {"guild": "Metal Guild", "name": "Intro to Welding", "unit_price": Decimal("75.00")},
            {"guild": "Metal Guild", "name": "Forge Day Pass", "unit_price": Decimal("20.00")},
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
        guilds: dict[str, Guild],
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
                "sublet_guild": "Ceramics Guild",
            },
            {
                "space_id": "S-102",
                "name": "Wood Shop",
                "space_type": Space.SpaceType.STUDIO,
                "size_sqft": Decimal("600.00"),
                "status": Space.Status.OCCUPIED,
                "is_rentable": True,
                "sublet_guild": "Woodworking Guild",
            },
            {
                "space_id": "S-103",
                "name": "Glass Studio",
                "space_type": Space.SpaceType.STUDIO,
                "size_sqft": Decimal("300.00"),
                "status": Space.Status.OCCUPIED,
                "is_rentable": True,
                "sublet_guild": "Glass Guild",
            },
            {
                "space_id": "ST-01",
                "name": "Guild Storage A",
                "space_type": Space.SpaceType.STORAGE,
                "size_sqft": Decimal("80.00"),
                "status": Space.Status.AVAILABLE,
                "is_rentable": True,
                "sublet_guild": None,
            },
        ]

        spaces: dict[str, Space] = {}
        for item in space_data:
            sublet_name = item.pop("sublet_guild")
            sublet_guild = guilds[str(sublet_name)] if sublet_name else None
            space, _ = Space.objects.get_or_create(
                space_id=item["space_id"],
                defaults={**item, "sublet_guild": sublet_guild},
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
                "buyable": "Ceramics Class Pass",
                "user": users["lead1"],
                "quantity": 1,
                "amount": 4500,
                "status": Order.Status.PAID,
                "paid_at": timezone.now(),
            },
            {
                "buyable": "Open Studio Time",
                "user": users["lead2"],
                "quantity": 2,
                "amount": 3000,
                "status": Order.Status.PAID,
                "paid_at": timezone.now(),
            },
            {
                "buyable": "Intro to Welding",
                "user": users["lead1"],
                "quantity": 1,
                "amount": 7500,
                "status": Order.Status.PENDING,
                "paid_at": None,
            },
        ]

        for item in order_data:
            buyable = buyables[str(item["buyable"])]
            Order.objects.get_or_create(
                buyable=buyable,
                user=item["user"],
                amount=item["amount"],
                defaults={
                    "quantity": item["quantity"],
                    "status": item["status"],
                    "paid_at": item["paid_at"],
                },
            )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Sample orders seeded."))
