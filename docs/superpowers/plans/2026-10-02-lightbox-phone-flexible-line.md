# Lightbox clear of the title, phone numbers dial, flexible booking line editable

Three instructor and member asks from 2026-10-02, shipped together as one small PR.

## 1. The gallery lightbox clashes with the class title

**Root cause.** The gallery lives in the booking rail, and on a laptop the rail is `position: sticky`. A sticky box is a stacking context, so the lightbox inside it (`position: fixed; z-index: 1000`) can only stack within the rail, and the rail stacks under the hero's content block (`.cp-detail__hero-content`, `position: relative; z-index: 1`). The title painted over the open photo. On a phone the rail is not sticky, so the bug never showed there.

**Fix.** The lightbox is wrapped in `<template x-teleport="body">` in `templates/classes/_components/gallery.html`, so it stacks against the page. Alpine keeps the component's scope on a teleported node, and the window key handlers still fire.

**Proof.** `tests/e2e/class_page_lightbox_spec.py`: at 1366 wide, open the lightbox and `document.elementFromPoint` at the title's centre lands inside `.cls-lightbox`; the spec fails on the old template. Escape and the arrow keys still work.

## 2. Member Directory phone numbers dial

`hub_tags.tel_href` keeps the digits (and a leading plus) of whatever the member typed; `member_directory.html` wraps a visible phone in `<a href="tel:...">`. A value with no digits stays plain text. Hidden phones are unchanged.

## 3. The Flexible Scheduling line is editable

New `ClassOffering.flexible_booking_text` (`default=""`, `db_default=""` for the deploy window). `default_flexible_booking_line` builds the standard sentence from the instructor and the window; `flexible_booking_line` is what the page shows (own text, else standard). The composer's Flexible block carries a "How booking works" box pre-filled with the standard line; a line that comes back unchanged (spacing folded) is stored blank, so it keeps following a renamed instructor or a later window. The published class form carries the box only for a flexible class.

## Out of scope

The guild gallery lightbox (`_guild_gallery.html`, its own component, not reported), phone links anywhere but the Member Directory, the instructor note (unchanged, still below the line).
