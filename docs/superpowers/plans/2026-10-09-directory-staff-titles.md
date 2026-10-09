# Member directory: staff titles, and "Employee" becomes "Staff"

Felix, 2026-10-09: "For the member directory on the card view can we add the title of the employee? And can we change the word employee to 'staff'?"

## Today
- `templates/hub/member_directory.html` cards show `member.get_member_type_display` as the role chip (`Member.MemberType.EMPLOYEE = "employee", "Employee"`).
- A person's title lives in the Leadership Directory: `LeadershipRole.title` lines on a `LeadershipListing` (per tab, `is_listed` shows or hides the card). Prod 2026-10-09: 6 active employees; 5 have role titles, e.g. Lee Mendelsohn "Co-Executive Director / Director of Operations" and "Board Advisor", Dixie Junius "Community Engagement Manager".

## Acceptance criteria
1. The member type's display label is "Staff" everywhere it shows (directory chip, admin member edit, filters, exports that use the label). The stored value stays `employee`; Airtable's mapping (`airtable_sync/config.py`) is untouched.
2. Each directory card (both the photo and the initials layouts) shows the person's titles from their listed Leadership Directory role lines (`is_listed` listings only), in tab then role order, deduplicated, under the name. A person with none shows no title line. This applies to any member with a listed role line, not only staff.
3. No N+1: the directory query prefetches the listings and roles it needs (assert with a query count spec).
4. Grep and update specs and e2e that assert "Employee" text.

## Out of scope
- A separate job title field on Member.
- Renaming the stored value `employee`.
