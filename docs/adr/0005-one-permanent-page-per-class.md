# One permanent page per class; a run's slug is no longer its address

Supersedes [0002](0002-recurring-class-slugs-date-stamped.md).

**Context.** A class was many `ClassOffering` rows, one per run, each with its own slug and public page. Run it again and date splitting copied the slug with `-run` added, so a busy class grew addresses like `picture-framing-2026-10-11-run-run-run-6`, and 0002's date stamp only made each one readable. On 18 Nov 2026 production held 704 runs in 217 classes; 630 published runs had only past dates and still answered. Search engines saw hundreds of near-identical pages, and every new run started its search history from nothing. The only thing tying runs together was `grouping_key`, recomputed from title and Class Type on every save, so renaming one run quietly split it off its class.

**Decision.** A class is a `ClassRecord`, and every run belongs to one through `ClassOffering.class_record` (#1260). The class owns its address (`slug`, set once from its title and never changed by a rename), its title and its Class Type; editing a run's title or Class Type edits the class and every run of it. Each run keeps its dates, seats, price, registrations and payouts. Description, photos, FAQs and instructors stay on the runs for now, and the class page shows the newest run's. Run it again and date splitting add a run to the same class; Duplicate starts a new class. A new class's slug is its slugified title, then the Class Type's slug added, then a number, skipping any slug another class or another class's run already holds. A run still gets a unique slug as before, but it stops being an address: old run links redirect to their class (#1260 parts 2 and 3). The catalog groups runs by class.

**Why not the alternatives.** Keeping `grouping_key` as the identity leaves a class defined by its spelling, so a rename splits it and a typo merges two. Moving every class level field off the run at once would rewrite the composer, the emails and the Eventbrite push in one step; the address, title and Class Type are what the permanent page needs, and the rest can follow. Date stamped run slugs (0002) solve readability, not the duplicate pages.

**Consequences.**
- A rename now renames every past run of the class too, and every run already listed on Eventbrite is queued to send its new name.
- `grouping_key` stays, derived as before. It picks the class a run with no class joins (one saved by the old release during the deploy that added the class), and still groups Manage Classes and the class page's other dates until those move to the class.
- Run slugs must stay unique forever and are never reused for a class, because old links, in-app notifications and calendar rows store `/classes/<run slug>/`.
- A class with no runs left is deleted with its last run, so its slug is free again and its Class Type can be deleted.
