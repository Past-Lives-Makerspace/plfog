import os
from django.db import migrations
from django.core.files import File


def seed_guild_content(apps, schema_editor):
    Guild = apps.get_model("membership", "Guild")
    GuildLink = apps.get_model("membership", "GuildLink")

    # Path for images relative to project root
    BANNER_BASE = "static/img/guild_banners/"

    guilds_data = [
        {
            "name": "Metalworkers Guild",
            "about": (
                "Welcome to Past Lives Metal Guild. We are fortunate to have access to tools for "
                "pretty much any type of metal work you might want to do. From forging to fabrication, "
                "milling and welding our fully equipped metal shop get your project done.\n\n"
                "• 2 gas forges and one induction forge with 5 forging stations available\n"
                "• MIG, TiG, stick, and Oxy-Acetylene welding available\n"
                "• Milling and lathes are available\n"
                "• Separate grinding room\n\n"
                "Expand your Metal Working skills\n\n"
                "Large fabrication area that can handle multiple members working at the same time\n"
                "Expert guidance from experienced instructors and craftspeople"
            ),
            "hero": "metalworking_hero.jpg",
            "links": [{"label": "Blacksmithing Classes", "url": "https://portal.pastlives.space/classes"}],
        },
        {
            "name": "Woodworking Guild",
            "about": (
                "At Past Lives Makerspace, our Wood Shop is a beacon for carpenters, artisan woodworkers and hobbyists.\n\n"
                "Spanning over 2,000 square feet, this space is equipped with:\n"
                "• Power tools, saws, jointers, and planers\n"
                "• Specialized areas for wood turning, sanding, and fine hand woodworking\n"
                "• A diverse tool selection, from traditional hand tools to modern power tools – electric saws, drills, sanders, and more.\n\n"
                "Expand Your Skills in Woodworking\n\n"
                "Join our community to enhance your skills, learn new techniques, and connect with fellow enthusiasts. We offer:\n"
                "• Regular woodworking classes with a 10% discount for members\n"
                "• Hands-on learning and collaboration opportunities\n"
                "• Personalized assistance and expert guidance\n\n"
                "Opportunities for Growth and Collaboration\n"
                "• Spacious areas for personal projects\n"
                "• Storage and private work areas for rent\n"
                "• Teaching opportunities and workforce participation for additional income\n\n"
                "Build community. Join the Woodworkers Guild."
            ),
            "hero": "woodworking_hero.jpg",
            "links": [{"label": "Woodworking Classes", "url": "https://portal.pastlives.space/classes"}],
        },
        {
            "name": "Ceramics Guild",
            "about": (
                "Welcome to the Past Lives Ceramics Guild! Our studio is a vibrant space for potters of all skill levels, "
                "from those touching clay for the first time to seasoned ceramic artists. We provide a fully equipped "
                "environment to explore wheel throwing, hand-building, and glazing.\n\n"
                "• 10 professional pottery wheels\n"
                "• Large kiln for high-fire and low-fire projects\n"
                "• Dedicated hand-building area with slab rollers and extruders\n"
                "• Wide selection of house glazes and underglazes\n\n"
                "Master Your Craft\n\n"
                "Regular workshops in wheel throwing and hand-building are available. Our guild members enjoy a collaborative "
                "atmosphere where techniques and inspirations are shared freely. Join us to turn your creative visions into fired reality."
            ),
            "hero": "ceramics_hero.jpg",
            "links": [{"label": "Ceramics Classes", "url": "https://portal.pastlives.space/classes"}],
        },
        {
            "name": "Textiles Guild",
            "about": (
                "Step into the Past Lives Textile Guild, a haven for fiber artists, quilters, and fashion designers. "
                "Whether you're interested in traditional weaving, modern garment construction, or experimental textile art, "
                "our studio offers the tools and community to support your journey.\n\n"
                "• Industrial and domestic sewing machines\n"
                "• Large cutting tables and pattern-making supplies\n"
                "• Looms for weaving and spinning wheels\n"
                "• Specialized area for screen printing and dyeing\n\n"
                "Weave Your Story\n\n"
                "We host regular classes in sewing, embroidery, and fabric dyeing. Join a community of makers dedicated to the "
                "art of fiber and thread. Members receive expert guidance and access to a wealth of shared knowledge."
            ),
            "hero": "textiles_hero.jpg",
            "links": [{"label": "Textile Classes", "url": "https://portal.pastlives.space/classes"}],
        },
        {
            "name": "Jewelry Guild",
            "about": (
                "The Past Lives Jewelry Guild is a specialized workspace for jewelry makers, lapidary enthusiasts, "
                "and metal smiths focused on fine detail. Our bench-equipped studio provides everything you need for "
                "soldering, stone setting, and lost-wax casting.\n\n"
                "• Professional jewelry benches with flex shafts\n"
                "• Soldering stations with oxy-propane torches\n"
                "• Centrifugal and vacuum casting equipment\n"
                "• Polishing lathes and ultrasonic cleaners\n\n"
                "Refine Your Artistry\n\n"
                "Explore the world of fine metalwork through our regular workshops. From beginner basics to advanced "
                "stone setting, our instructors are here to help you shine. Build your skills and your collection in "
                "a supportive, creative environment."
            ),
            "hero": "jewelry_hero.jpg",
            "links": [{"label": "Jewelry Classes", "url": "https://portal.pastlives.space/classes"}],
        },
        {
            "name": "Tech Guild",
            "about": (
                "The Past Lives Tech Guild is the digital heart of our makerspace. It's a playground for hackers, "
                "engineers, and digital artists who want to bridge the gap between code and physical reality. "
                "From 3D printing to custom electronics, we provide the high-tech tools for your next big project.\n\n"
                "• High-resolution 3D printers (FDM and SLA)\n"
                "• Laser cutters for precision engraving and cutting\n"
                "• Electronics lab with soldering stations and oscilloscopes\n"
                "• CNC routers for digital fabrication\n\n"
                "Innovate and Create\n\n"
                "We offer workshops in 3D modeling, Arduino/Raspberry Pi programming, and laser cutting. Join a community "
                "of tech enthusiasts dedicated to learning and building the future together."
            ),
            "hero": "tech_hero.jpg",
            "links": [{"label": "Tech Classes", "url": "https://portal.pastlives.space/classes"}],
        },
        {
            "name": "Art Framing Guild",
            "about": (
                "The Art Framing Guild provides members with the tools and space to professionally frame their own "
                "artwork, photographs, and memorabilia. From precision mat cutting to custom frame assembly, our "
                "studio helps you give your art the presentation it deserves.\n\n"
                "• Professional mat cutters and glass cutters\n"
                "• V-nailers and point drivers for frame assembly\n"
                "• Large work surfaces for layout and mounting\n"
                "• Selection of framing hardware and supplies\n\n"
                "Frame Your World\n\n"
                "Join us for regular workshops on framing basics, matting techniques, and mounting methods. Whether "
                "you're a professional artist or just want to display your family photos beautifully, our guild is "
                "here to help you finish your projects with style."
            ),
            "hero": "art_framing_hero.jpg",
            "links": [],
        },
        {
            "name": "Food Independence Guild",
            "about": (
                "The Food Independence Guild is focused on building sustainable, local food systems. We believe in the "
                "power of growing, preserving, and sharing our own food. Join us to learn about urban gardening, "
                "fermentation, and community food projects.\n\n"
                "• Resources for urban agriculture and composting\n"
                "• Workshops on food preservation and fermentation\n"
                "• Community seed swaps and plant starts\n"
                "• Collaborative food projects and shared meals\n\n"
                "Grow Your Future\n\n"
                "Participate in our regular classes on everything from sourdough baking to vegetable fermentation. "
                "Connect with fellow food enthusiasts and help us grow a more resilient, self-sufficient community."
            ),
            "hero": "food_independence_hero.jpg",
            "links": [],
        },
        {
            "name": "Gardeners Guild",
            "about": (
                "The Gardeners Guild is for those who love to get their hands in the dirt. We manage our outdoor spaces "
                "and provide a sanctuary for both plants and people. Whether you're a seasoned gardener or just starting "
                "to grow your first tomato, you'll find a home here.\n\n"
                "• Shared garden beds and tools\n"
                "• Composting systems and soil health resources\n"
                "• Quiet outdoor spaces for reflection and creativity\n"
                "• Knowledge sharing on native plants and pollinators\n\n"
                "Cultivate Connection\n\n"
                "Join us for garden workdays and workshops on seasonal planting, organic pest control, and garden design. "
                "Help us keep Past Lives green and growing while connecting with nature and your neighbors."
            ),
            "hero": "gardeners_hero.jpg",
            "links": [],
        },
        {
            "name": "Glass Guild",
            "about": (
                "The Past Lives Glass Guild is dedicated to the ancient and beautiful art of glass working. Our studio "
                "supports a variety of techniques including stained glass, fused glass, and glass casting. It's a "
                "place where light and color come together in stunning ways.\n\n"
                "• Grinders and cutters for stained glass\n"
                "• Kilns for glass fusing and slumping\n"
                "• Soldering stations for copper foil and lead came work\n"
                "• Sandblasting equipment for etching and frosting\n\n"
                "Illuminate Your Creativity\n\n"
                "Explore the possibilities of glass through our regular classes. From creating your first suncatcher "
                "to mastering complex fusing techniques, our guild offers the tools and community to support your artistic vision."
            ),
            "hero": "glass_hero.jpg",
            "links": [],
        },
        {
            "name": "Leatherwork Guild",
            "about": (
                "The Leatherwork Guild is a specialized workspace for those who love working with hide. From traditional "
                "saddlery to modern fashion accessories, our studio provides the heavy-duty tools and techniques required "
                "for professional leather craft.\n\n"
                "• Industrial leather sewing machines\n"
                "• Selection of punches, stamps, and carving tools\n"
                "• Large cutting surfaces and specialized adhesives\n"
                "• Skiving and edge-finishing equipment\n\n"
                "Craft Your Legacy\n\n"
                "Join our regular workshops to learn the basics of leather crafting, pattern making, and hand-stitching. "
                "Whether you're making a simple belt or a complex bag, our guild members are here to share their expertise "
                "and passion for this timeless craft."
            ),
            "hero": "leatherwork_hero.jpg",
            "links": [],
        },
        {
            "name": "Writers Guild",
            "about": (
                "The Writers Guild is a sanctuary for wordsmiths of all kinds. Whether you're working on a novel, poetry, "
                "screenplays, or non-fiction, our guild provides the quiet space and supportive community to help you find "
                "your voice and finish your draft.\n\n"
                "• Quiet writing spaces and collaborative lounges\n"
                "• Regular workshops on craft, editing, and publishing\n"
                "• Peer review groups and feedback sessions\n"
                "• Opportunities for public readings and showcases\n\n"
                "Tell Your Story\n\n"
                "Join us for regular write-ins and workshops led by experienced authors. Connect with fellow writers, "
                "share your progress, and celebrate the art of storytelling in all its forms. Your story starts here."
            ),
            "hero": "writers_hero.jpg",
            "links": [],
        },
        {
            "name": "Events Guild",
            "about": (
                "The Events Guild is the social heart of Past Lives. We're dedicated to bringing our community together "
                "through workshops, showcases, and celebrations. If you love organizing, hosting, or just participating "
                "in great community events, this is the guild for you.\n\n"
                "• Coordination for monthly Art Walks\n"
                "• Support for member-led workshops and talks\n"
                "• Planning for seasonal celebrations and showcases\n"
                "• Opportunities to volunteer and lead community initiatives\n\n"
                "Celebrate Together\n\n"
                "Stay tuned for our calendar of upcoming events! Join the guild to help us create a vibrant, inclusive "
                "environment where every member can shine. From planning to execution, we're building community one event at a time."
            ),
            "hero": "events_hero.jpg",
            "links": [],
        },
        {
            "name": "Prison Outreach Guild",
            "about": (
                "The Prison Outreach Guild is dedicated to supporting incarcerated individuals through art and community "
                "connection. We believe in the transformative power of creativity and the importance of maintaining "
                "human bonds across prison walls.\n\n"
                "• Coordination for art supply drives\n"
                "• Support for correspondence and mentorship programs\n"
                "• Organizing exhibitions of work by incarcerated artists\n"
                "• Advocacy for arts programs in the carceral system\n\n"
                "Create Beyond Borders\n\n"
                "Join us to learn how you can contribute to this vital mission. Whether through art, letters, or advocacy, "
                "your involvement helps build bridges and foster hope. Together, we can make a difference through the power "
                "of creative expression."
            ),
            "hero": "prison_outreach_hero.jpg",
            "links": [],
        },
        {
            "name": "Visual Arts/Gallery Guild",
            "about": (
                "The visual arts guild was started as a way for artists to have workspace even if they aren’t renting a "
                "studio. It’s meant for anyone who is practicing traditional arts and crafts. A place to share skills, "
                "create art together, or have some chill space to make something on your own. Shelves are available for "
                "rent at $5 a month. We have a large variety of art and craft supplies that are free for member use. "
                "Every Thursday is Parallel play night from 5-8. Bring a project or raid the supply cabinet and join in!\n\n"
                "The visual arts guild also runs the gallery space downstairs which offers space for classes and hosts "
                "the monthly First Friday Art Walks and Model Drawing every 4th Sunday."
            ),
            "hero": "visual_arts_hero.jpg",
            "links": [],
        },
    ]

    for data in guilds_data:
        # Check for name variations to avoid duplicates
        name = data["name"]
        guild = Guild.objects.filter(name=name).first()
        if not guild:
            # Check for common variations
            if name == "Metalworkers Guild":
                guild = Guild.objects.filter(name__icontains="Metal").first()
            elif name == "Woodworking Guild":
                guild = Guild.objects.filter(name__icontains="Wood").first()

        if not guild:
            guild = Guild(name=name)

        guild.about = data["about"]

        hero_path = os.path.join(BANNER_BASE, data["hero"])
        if os.path.exists(hero_path):
            with open(hero_path, "rb") as f:
                guild.banner_image.save(data["hero"], File(f), save=False)

        guild.save()

        for link_data in data.get("links", []):
            GuildLink.objects.get_or_create(guild=guild, label=link_data["label"], url=link_data["url"])


def reverse_seed(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0038_guild_contact_email_guild_meeting_schedule_and_more"),
    ]
    operations = [
        migrations.RunPython(seed_guild_content, reverse_seed),
    ]
