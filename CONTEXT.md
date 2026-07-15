# Past Lives Makerspace (plfog)

Membership, guild, and class/workshop management for Past Lives Makerspace (Portland, OR). Two surfaces share one codebase: the **FOG hub** (member hub) and the **book CMS** (public class catalog + booking).

## Language

**Member**:
A person with a Past Lives Makerspace membership and a hub account. The canonical person-record (`membership.Member`).
_Avoid_: user (that's the Django auth record behind a Member), customer, account.

**Instructor**:
A Member who has been granted the **instructor role** by an admin. The role permits creating classes and gives the Member a public instructor page. Not a separate record — it is a facet of a Member (`is_instructor` ⇔ the member holds the instructor role / has an `instructor_slug`).
_Avoid_: instructor account, instructor profile (as a separate table/entity), teacher.

**Member bio**:
The short blurb a Member writes about themselves, shown in the member directory (`about_me`). Edited on member settings.
_Avoid_: about me (ambiguous — say which bio), profile bio.

**Instructor bio**:
A *separate* teaching-focused bio shown on the public instructor page, distinct from the Member bio. Edited on the instructor settings page, labeled "About me as an instructor". A Member who instructs maintains both bios independently.
_Avoid_: about me, instructor about.

**Onboarding**:
The home "Get started" checklist a new Member works through (`Member.onboarding` / `OnboardingChecklist`) — finish your profile, join a guild, etc. It is a checklist of **links**, not a questionnaire. (An older book-CMS 3-step question wizard that collected signup answers was retired; "onboarding" no longer refers to that.)
_Avoid_: onboarding wizard, onboarding questions, onboarding form.

**Class offering**:
One scheduled instance of a class (`classes.ClassOffering`) — a specific run with its own date(s), capacity, and slug. A class taught repeatedly produces many offerings ("runs") over time; each is a distinct offering with a distinct URL.
_Avoid_: using bare "class" for both the abstract class and a single dated run — a run is an offering.

**Contact**:
A labeled contact method on a Member — `{label, value}` (e.g. "Booking email" → an address) with per-surface placement toggles (show in the member directory and/or on the instructor page). One list per Member; absorbs the old fixed website/social/other-contact fields. `phone` and `discord` remain first-class fields, not Contacts.
_Avoid_: contact field, social link, other contact info.

**Guild** (hub):
A member-run interest group within the makerspace (woodshop, blacksmithing, etc.), with leads, staff, and a public page. The real `membership.Guild`.
_Avoid_: using bare "guild" for a class catalog category — that is a **Guild Type** (see below).

**Guild Type**:
The catalog category a class belongs to (the `classes.Category` model). User-facing copy calls it a "Guild Type" — not "category" or bare "Guild". A Guild Type may link to a hub Guild to route a submitted class's approval to that Guild's Lead, but a Guild Type (catalog category) and a Guild (member group) are distinct.
_Avoid_: category (in user-facing copy), class type, bare "guild".
