from __future__ import annotations

from django.db import migrations
from django.utils.text import slugify

# category name -> list of skill names. Ordered; category index drives sort_order.
SEED: dict[str, list[str]] = {
    "Woodworking": [
        "Furniture making",
        "Small woodworking projects",
        "Cabinetry",
        "Wood turning",
        "Carving",
        "Joinery",
        "CNC routing",
        "Finishing & refinishing",
    ],
    "Metal & Jewelry": [
        "Welding (MIG/TIG)",
        "Blacksmithing",
        "Silversmithing",
        "Lost-wax casting",
        "Machining",
        "Sheet metal",
        "Engraving",
        "Stone setting",
    ],
    "Textiles & Fiber": [
        "Sewing",
        "Garment making",
        "Quilting",
        "Weaving",
        "Knitting",
        "Crochet",
        "Embroidery",
        "Screen printing",
        "Dyeing",
    ],
    "Leather": ["Leatherworking", "Bag & wallet making", "Tooling & carving", "Bookbinding"],
    "Ceramics & Glass": [
        "Wheel throwing",
        "Hand-building",
        "Glazing",
        "Kiln firing",
        "Stained glass",
        "Lampworking",
        "Glassblowing",
    ],
    "Paper & Print": ["Letterpress", "Printmaking", "Linocut", "Risograph", "Zine making", "Calligraphy"],
    "Electronics & Fab": [
        "Electronics & soldering",
        "Microcontrollers (Arduino/Pi)",
        "Robotics",
        "3D modeling (CAD)",
        "3D printing",
        "Laser cutting",
        "PCB design",
    ],
    "Software & Tech": [
        "Coding",
        "Web development",
        "Website design & consulting",
        "AI development & consulting",
        "Mobile apps",
        "Game development",
        "Data & automation",
        "IT & networking",
    ],
    "Music & Audio": [
        "Music production",
        "Audio engineering",
        "Mixing & mastering",
        "Songwriting",
        "Instrument repair",
        "DJing",
        "Live sound",
    ],
    "Photo & Video": [
        "Photography",
        "Videography",
        "Photo editing",
        "Video editing",
        "Motion graphics",
        "Lighting",
    ],
    "Art & Design": [
        "Illustration",
        "Painting",
        "Graphic design",
        "UX/UI design",
        "Branding & logos",
        "Murals",
        "Sculpture",
        "Animation",
    ],
    "Writing & Media": ["Copywriting", "Editing", "Technical writing", "Grant writing", "Social media"],
    "Trades & Misc": [
        "Carpentry",
        "Electrical",
        "Plumbing",
        "Upholstery",
        "Sign making",
        "Prop & set building",
        "Teaching & workshops",
        "Consulting",
    ],
}


def _all_slugs() -> tuple[set[str], set[str]]:
    cat_slugs = {slugify(name) for name in SEED}
    skill_slugs = {slugify(s) for skills in SEED.values() for s in skills}
    return cat_slugs, skill_slugs


def seed(apps, schema_editor) -> None:
    SkillCategory = apps.get_model("membership", "SkillCategory")
    Skill = apps.get_model("membership", "Skill")
    for order, (cat_name, skills) in enumerate(SEED.items()):
        category, _ = SkillCategory.objects.get_or_create(
            slug=slugify(cat_name), defaults={"name": cat_name, "sort_order": order}
        )
        for skill_name in skills:
            Skill.objects.get_or_create(
                slug=slugify(skill_name),
                defaults={"name": skill_name, "category": category, "status": "approved"},
            )


def unseed(apps, schema_editor) -> None:
    SkillCategory = apps.get_model("membership", "SkillCategory")
    Skill = apps.get_model("membership", "Skill")
    cat_slugs, skill_slugs = _all_slugs()
    Skill.objects.filter(slug__in=skill_slugs).delete()
    SkillCategory.objects.filter(slug__in=cat_slugs).delete()


class Migration(migrations.Migration):
    dependencies = [("membership", "0056_skills_directory")]
    operations = [migrations.RunPython(seed, unseed)]
