#!/usr/bin/env bash
# Seed fictional instructors + class offerings for testing the public book
# surface (book.localhost / book.pastlives.space). Idempotent — re-running
# updates timestamps but won't duplicate.
#
# After running, browse http://book.localhost:8000/classes/ to see them.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
exec .venv/bin/python manage.py shell <<'PY'
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone

from classes.models import Category, ClassOffering, ClassSession, Instructor

User = get_user_model()


def get_or_create_instructor(slug: str, display_name: str, bio: str, social: str = "") -> Instructor:
    user, _ = User.objects.get_or_create(
        username=f"{slug}@demo.example",
        defaults={"email": f"{slug}@demo.example"},
    )
    instructor, _ = Instructor.objects.get_or_create(
        slug=slug,
        defaults={
            "user": user,
            "display_name": display_name,
            "bio": bio,
            "social_handle": social,
        },
    )
    return instructor


def get_or_create_offering(slug: str, **defaults) -> ClassOffering:
    defaults.setdefault("status", ClassOffering.Status.PUBLISHED)
    defaults.setdefault("published_at", timezone.now())
    defaults.setdefault("member_discount_pct", 10)
    offering, _ = ClassOffering.objects.get_or_create(slug=slug, defaults=defaults)
    return offering


def ensure_sessions(offering: ClassOffering, day_offsets: list[int], duration_hours: float = 2.5) -> None:
    if offering.sessions.exists():
        return
    for offset in day_offsets:
        start = timezone.now() + timedelta(days=offset, hours=2)
        ClassSession.objects.create(
            class_offering=offering,
            starts_at=start,
            ends_at=start + timedelta(hours=duration_hours),
        )


# Instructors — fictional, so we never collide with real seeded instructors.
wren = get_or_create_instructor(
    "wren-hollow",
    "Wren Hollow",
    "Textiles instructor with a sashiko obsession and a side gig in indigo dyeing.",
    "@wrenhollow",
)
tobias = get_or_create_instructor(
    "tobias-crane",
    "Tobias Crane",
    "Bladesmith and woodworker. Teaches forge fundamentals and the occasional cutting board class.",
    "@tcrane.forge",
)
sage = get_or_create_instructor(
    "sage-lin",
    "Sage Lin",
    "Production potter and stained-glass hobbyist. Most at home next to a kiln.",
    "@sagelin.studio",
)

# Categories — use what's already in the DB (don't create duplicates).
ceramics = Category.objects.get(slug="ceramics")
glass = Category.objects.get(slug="glass")
metal = Category.objects.get(slug="metalworking")
wood = Category.objects.get(slug="woodworking")
textiles = Category.objects.get(slug="textiles")
visual = Category.objects.get(slug="visual-arts")

# 1. Single-session intro — cheap, large capacity, all-ages.
sashiko = get_or_create_offering(
    "beginner-sashiko-stitching",
    title="Beginner Sashiko Stitching",
    category=textiles,
    instructor=wren,
    description="A relaxed, two-hour intro to traditional Japanese running-stitch embroidery. Walk out with a finished sampler.",
    prerequisites="None — total beginners welcome.",
    materials_included="Indigo cotton, thread, needle, thimble.",
    price_cents=3500,
    capacity=10,
    age_minimum=12,
    age_guardian_note="Ages 12-16 must register alongside a guardian.",
)
ensure_sessions(sashiko, [5], duration_hours=2)

# 2. Multi-session intensive — pricey, small cohort, PPE + prereq.
forge = get_or_create_offering(
    "intro-to-forge-welding",
    title="Intro to Forge Welding",
    category=metal,
    instructor=tobias,
    description="Two evenings learning to fire-weld scrolls, leaves, and a simple bottle opener. We forge, you keep what you make.",
    prerequisites="Past Lives Basic Shop Safety required before first session.",
    materials_included="Stock steel, coal, flux, finishing oil.",
    materials_to_bring="Cotton long-sleeves, closed leather boots, hair tie.",
    safety_requirements="Safety glasses and earplugs provided. No synthetic fabrics.",
    price_cents=14500,
    capacity=4,
    age_minimum=18,
)
ensure_sessions(forge, [9, 16], duration_hours=3)

# 3. Recurring fixed sessions — open studio style, three weeks.
wheel = get_or_create_offering(
    "open-studio-wheel-throwing",
    title="Open Studio: Wheel Throwing",
    category=ceramics,
    instructor=sage,
    description="Three weekly evenings on the wheel with light instruction and lots of wedging. Ideal for folks who've thrown once or twice and want time on the wheel.",
    prerequisites="One prior throwing experience recommended.",
    materials_included="Clay, tools, aprons, bisque + glaze firing.",
    price_cents=8000,
    capacity=6,
    recurring_pattern="Weekly on Tuesdays for three weeks.",
)
ensure_sessions(wheel, [3, 10, 17], duration_hours=2.5)

# 4. Single-session craft — solid mid-price, model release on (to exercise that flow).
glass_class = get_or_create_offering(
    "stained-glass-suncatchers",
    title="Stained Glass Suncatchers",
    category=glass,
    instructor=sage,
    description="Cut, foil, and solder a small suncatcher you'll take home the same evening. Photos of finished work may be used in our gallery.",
    prerequisites="None.",
    materials_included="Glass, foil, solder, hanging chain.",
    safety_requirements="Apron and safety glasses provided.",
    price_cents=6500,
    capacity=6,
    requires_model_release=True,
)
ensure_sessions(glass_class, [12], duration_hours=3)

# 5. Flexible scheduling — 1:1 / by-arrangement.
boards = get_or_create_offering(
    "custom-cutting-board-workshop",
    title="Custom Cutting Board Workshop",
    category=wood,
    instructor=tobias,
    description="A private workshop where we mill, glue-up, and finish a hardwood cutting board to your spec. Schedule a date with the instructor after registering.",
    prerequisites="Comfortable around power tools (we'll handle the table saw together).",
    materials_included="Hardwood stock, glue, food-safe finish.",
    price_cents=11000,
    capacity=2,
    scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
    flexible_note="After you register, Tobias will reach out within 48 hours to schedule a 4-hour block.",
    age_minimum=16,
)

# 6. Single-session, no upcoming sessions yet — exercises the "interested?" path.
watercolor = get_or_create_offering(
    "botanical-watercolor-basics",
    title="Botanical Watercolor Basics",
    category=visual,
    instructor=wren,
    description="An afternoon of loose, expressive botanical painting. We'll work from clippings in the makerspace garden.",
    prerequisites="None.",
    materials_included="Paper, brushes, watercolor pans. Take home what you make.",
    price_cents=4500,
    capacity=10,
)
# No sessions yet — intentionally, to test the empty-session state on the catalog.

# 7. Free workshop — exercises the no-Stripe registration path end-to-end.
open_house = get_or_create_offering(
    "free-tool-orientation",
    title="Free Tool Orientation",
    category=Category.objects.get(slug="general"),
    instructor=tobias,
    description="A free 45-minute walk-through of the shop's hand and power tools. No project, no take-home — just a chance to meet the space and ask anything.",
    prerequisites="None.",
    materials_included="None — bring closed-toe shoes.",
    price_cents=0,
    member_discount_pct=0,
    capacity=12,
)
ensure_sessions(open_house, [2], duration_hours=0.75)

published = ClassOffering.objects.filter(status=ClassOffering.Status.PUBLISHED, is_private=False)
print(f"Published, public class offerings: {published.count()}")
for o in published.order_by("title"):
    upcoming = o.sessions.filter(starts_at__gte=timezone.now()).count()
    print(f"  - {o.title}  ({o.category.name}, ${o.price_cents/100:.2f}, {upcoming} upcoming session(s))")

print("\nDone. Browse http://book.localhost:8000/classes/")
PY
