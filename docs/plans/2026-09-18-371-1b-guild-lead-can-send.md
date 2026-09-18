# 371 item 1, second half: the Emails tab a guild lead lands on actually sends

Parent issue: https://github.com/Past-Lives-Makerspace/plfog/issues/371 (item 1)
Follows: #414, which fixed the silent-refusal half.

## The anti-pattern

`_guild_access()` (`classes/access.py:195`) grants `can_view_emails=True` and
`can_send_email=False`.

Ruling 12 gives a guild lead no Overview on a class they do not teach, so **Emails is
the tab they land on**, and it is the one thing they cannot use. The capability matrix
as it stands:

| Role | roster | emails tab | send |
|---|---|---|---|
| admin | yes | yes | yes |
| instructor | yes | yes | yes |
| **guild lead** | **no** | **yes** | **no** |
| reviewer | no | no | no |

They can already reach the composer: `_can_compose` (`hub/views.py:3435`) admits them
on their staffed guild. What refuses them is the audience gate,
`_compose_audience_forbidden` to `_can_announce_to_class` (`hub/views.py:3427`), which
admits only an effective admin or the class's own instructor. Verified at runtime during
the #419 review: a guild lead on a class they do not teach gets 200 from
`hub_compose_preview` and **403 from `hub_compose_send`**.

So the surface is shown and the action is refused. That is exactly the shape this issue
was opened about.

## The decision, and what it changes

**Jo's call: let a guild lead send, with the per-person picker.**

This is a deliberate relaxation of Ruling 12 in practice, and it should be recorded as
one rather than arrived at quietly. Ruling 12 withholds the roster on someone else's
class; the composer's recipient picker lists registrants by name, so a guild lead who can
compose to a class can see who is in it. Jo was shown that consequence and chose it.

**It also reverses a decision recorded in #414.** That PR argued the link was correctly
withheld from a guild lead, and its spec carries a comment saying so. Both the comment
and `it_offers_nothing_to_a_guild_lead_who_does_not_teach_the_class` must be updated
rather than worked around.

## The build

1. `_guild_access()` gains `can_send_email=True`.

2. **`_can_announce_to_class` delegates to `classes.access`.** Today the affordance
   (`{% if access.can_send_email %}`) and the endpoint gate are two separate
   expressions that happen to agree. #414's review confirmed they agree only because
   `classes/access.py` is stricter on the instructor leg, and called it "not by
   construction". Make it by construction:

       access = class_access(request, offering)
       return access is not None and access.can_send_email

   The template flag and the send gate then become literally the same predicate, and the
   "button that lies" class of bug stops being possible rather than merely being absent.
   Watch for an import cycle between `hub` and `classes`; use the local-import idiom the
   file already uses if there is one.

3. Nothing is needed for the picker. `_RecipientChoiceField` (`hub/forms.py:3263`), the
   per-person checkboxes, the select-all and the "add someone" box already exist and
   already work once the viewer is admitted.

## Acceptance criteria

1. A guild lead on a class in their guild that they do not teach sees Send Email on the
   Emails tab, reaches the composer locked to that class, sees the per-person picker,
   and **Send succeeds** rather than returning 403.
2. A guild lead on a class in a guild they neither lead nor staff is still refused, and
   still sees no link.
3. Instructor and admin behaviour is unchanged.
4. An admin previewing a lower role still gets 404 on the whole screen, unchanged.
5. A reviewer-grant holder still cannot send.
6. The affordance and the send gate are proved to be the same predicate: a spec that
   would fail if one were changed without the other.
7. #414's guild-lead spec and its comment are updated to the new truth, not deleted.

## Out of scope

- Roster visibility itself. `can_view_registrations` stays False for a guild lead; only
  the composer's own recipient list exposes names, which is the accepted consequence.
- Anything in #370.
- The audience-gate silent refusal on Send and Save Draft. That is #415.
- Changing who counts as a guild lead or staffer.

## Files expected to change

- `classes/access.py` (`_guild_access`)
- `hub/views.py` (`_can_announce_to_class`)
- `classes/spec/views/send_email_affordance_spec.py` (the #414 spec and its comment)
- new or extended specs for the criteria above

Touching anything outside this list is a question for the orchestrator.
