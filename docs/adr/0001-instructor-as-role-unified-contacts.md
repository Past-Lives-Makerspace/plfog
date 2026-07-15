# Instructor is a role; member/instructor profiles share a table with a unified contact list

**Context.** UAT feedback asked to "separate member profile tables from instructor profiles" and to add labeled, custom contact fields.

**Decision.** An Instructor is a **role a Member holds** (it grants class-creation and a public instructor page), not a separate entity — so we do **not** extract an `InstructorProfile` table; every instructor is always a Member. Member and instructor settings are **two tabs on the one user-profile page**, with the Instructor tab hidden unless the member holds the instructor role. Each surface has its **own bio** (member-directory `about_me` vs a new `instructor_bio`). Contact methods become a **single labeled `MemberContact` list** — `{label, value, show_in_directory, show_on_instructor_page, sort_order}` — that **absorbs** the former fixed `instructor_website`, `instructor_social_handle`, and `other_contact_info` fields (existing values migrate into seeded rows). `phone` and `discord` stay first-class.

**Why not the alternatives.** A separate `InstructorProfile` table was rejected because instructors are never non-members — the join buys nothing and costs a data migration plus rework of role-granting, the public instructor page, and admin forms. Keeping the fixed website/social fields *alongside* the new contact list was rejected as redundant: once a contact can be flagged "show on instructor page," a dedicated website field is just a contact row.

**Consequence.** One data migration folds three columns into `MemberContact` and backfills `instructor_bio` from `about_me` so no live instructor page blanks on deploy.
