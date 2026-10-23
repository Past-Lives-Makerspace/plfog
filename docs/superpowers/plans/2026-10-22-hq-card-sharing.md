# HQ card sharing: Can open and Team, and who sees the HQ in the sidebar

Source: Lee's email "Holiday Bizzare HQ & CMS", 2026-10-22: "What is the best way to grant the team permission to a certain card in the HQ ledger without granting full admin access?" Felix's brief, 2026-10-22: the HQ shows in the sidebar to admins only, or to anyone Lee has opened at least one card to. A person sees only the cards opened to them. Lee must find it super easy to add people to a card, and regular members must never see sensitive cards such as Sponsorship. Uploads and edits stay admin only: shared people sign up, fill in forms and log rows, but never change the forms.

## Decisions (Felix, locked 2026-10-22)

- **Two levels per card.** Each card keeps its audience (Admins only, Members, Everyone) and gains two people lists:
  - **Can open**: these people see the card and use it as a member would. They sign up for shifts, log a bottle, file a check-in and read the page.
  - **Team**: everything Can open gives, plus the card's organiser work. They add, edit and delete sponsor, vendor, marketing, ledger entry, bar sale, staff time, launch task and guild demo rows; mark donors thanked; see rosters, donors and reports; and download the CSVs.
  - Today's `named_members` become Team: a data migration copies them, or the field is renamed. Nobody loses access they have today.
- **Admin only, whatever the lists say** (settings and uploads, "changing the forms"):
  - card edit, card move, hub edit, hero and Update the doors (already admin only)
  - volunteer shifts (already on card edit)
  - flyer upload, remove and links (`flyer_upload`, `flyer_remove`, `flyer_links`)
  - winery logos (`winery_logo`)
  - the ledger's approval figure (`ledger_approval`)
  - drink and ticket prices (`Set drink prices`, `Set ticket prices`)
  - budget lines: add, edit and delete (`ledger_line_add`, `ledger_line_edit`, `ledger_line_delete`)
  - the vendor floor capacity (`vendor_floor`)

  The Team still logs ledger entries and receipts against the budget lines. Every admin-only button disappears for a Team member (rendered by the effective role, so an admin's preview hides it), and its POST answers 403 by the actual role. Grep `hq/views.py` for every `organizer_required` and classify each one. Anything not on the admin list above stays Team.

## The sidebar and the HQ page

1. The Holiday Bizarre sidebar entry (both blocks of `templates/hub/base.html`) shows to an admin by the effective role whenever any hub exists. A non-admin sees it only when the open hub has at least one card they may open (see 3). Compute this in one context processor with a bounded number of queries; members load every hub page, so add a query-count spec. It shows on the members surface and to guest accounts on the same rule.
2. The `holiday_bizarre` feature switch no longer decides the entry. Remove it from Site Settings so no admin flips a switch that does nothing, using a core migration if switches are rows. Make sure nothing else reads it. Keep the Admin Tools card.
3. "May open" means the viewer can actually use the door. Some pages are Team only today (`organizer_page`): Vendor Tables, the Ledger, and the Check-in reports and hours pages. On those cards, the Members and Everyone audiences and the Can open list would put a door on someone's HQ that answers 403. So for the `vendor_tables` and `ledger` kinds, the share panel offers only Admins only plus Team. The model refuses any other audience, and a Can open list, for those kinds (form validation plus a check in `is_open_to`). Check every other kind's page in the same way, and list your findings in the PR.
4. The HQ page (`hq:page`) shows a non-admin only the cards they may open, as it does today. With none, a signed-in non-admin gets the themed 404, the same as a closed hub. A signed-out visitor still sees Everyone cards; with no Everyone card, the page is a 404 for them too.
5. Sponsorship today renders a non-organiser view for Members. Keep that, but seed and default nothing new: every card's audience stays whatever is in the database (prod is all Admins only). Lee opens what she wants.

## The share panel (make it easy)

6. Each card on the HQ page shows admins (by the effective role) a small chip with who can open it: "Admins only", "Members", "Everyone", plus "· N people" when the lists have anyone. A Share button on the card opens a modal (`components/modal.html`) where Lee:
   - picks the audience as three plain choices, each with one line saying who that is. For the Team-only kinds only the allowed choice shows, with a line saying why.
   - adds people to Can open and to Team with a searchable member picker. Reuse the pattern behind `inquiryMemberPicker` (`static/js/inquiry_member_picker.js`, `templates/classes/admin/partials/inquiry_add_form.html`) rather than the 300-row checkbox list on card edit. Chosen people appear as removable chips showing name and email. Only ACTIVE members are offered, plus anyone already on the list (shown even if no longer active, so they can be removed).
   - Saving is one POST, admin only by the actual role, answered over htmx. It swaps the card's chip, shows a toast, and works without htmx too.
7. Card edit (`templates/hq/card_edit.html`) uses the same two pickers in place of the checkbox list, so there is one way to share. FRONTEND.md applies: no bare tab rows, the `.pl-help` pattern, no native `title=`, and single-line template comments.
8. Copy is plain words with no dashes. Avoid the word "organiser" in the UI; use "Team".

## Acceptance criteria

1. Every point above has a spec. Cover the permission edges with crafted POSTs: a Can open member POSTs to each Team action and gets 403; a Team member POSTs to each admin-only action and gets 403; a stranger gets 404 on a non-public card; an admin previewing as a member sees that member's buttons.
2. Sidebar visibility, tested in both sidebar blocks: an admin, a member with a shared card, a member with no card, a member when a Members card exists, a guest account, and signed out.
3. The migration preserves the named members as Team. Test it, or test the copy function it calls.
4. A query-count spec on a hub page for the new context processor.
5. A Playwright e2e: an admin shares the Ledger with a member as Team; that member sees the HQ in the sidebar with only the Ledger card on it, can log an entry, and sees no budget line, approval or price editor.
6. `hq/AGENTS.md` "Who" section rewritten to match.

## Out of scope

- Sharing with a guild or a role as a group (people only).
- Notifying someone when they are added to a card.
- Per-row permissions inside a tracker.
- Changing any card's audience in prod (Lee does it from the panel).
