# Recurring-class slugs are date-stamped, for new offerings only

**Context.** The same class recurs often (same title, different dates), so title-only slugs collide, and the existing bare `-2/-3` collision suffix is meaningless to a human reading the URL.

**Decision.** A new class offering's slug is `slugify(title)-YYYY-MM-DD`, where the date is the offering's first session date; a same-day collision falls back to a `-2` tiebreak. Full date — not month+year — because a class can recur several times within one month. Auto-generated on **both** the teach and admin create forms (the admin form previously let the slug be hand-typed, which is where collisions slipped in). Applied to **new offerings only** — existing offerings keep their current slugs, so no already-indexed URL changes or 404s.

**Why not the alternatives.** Raw DB id (`/classes/intro-104`) is unique but meaningless to humans and weaker for SEO. Applying *any* new scheme retroactively would 404 every indexed URL, so the scheme is scoped to new rows. F4b (unique per-class title + description) already shipped the core SEO fix; date-stamped slugs only disambiguate recurring runs and make the URL self-describing.
