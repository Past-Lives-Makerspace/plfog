# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root: the glossary. Each term carries an `_Avoid_` line naming the synonyms this project does not use.
- **`docs/adr/`**: read the ADRs that touch the area you're about to work in.
- Each Django app's own `AGENTS.md`, where it has one, carries that app's detail; `CODEBASE_INDEX.md` is the map.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates entries lazily when terms or decisions actually get resolved.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-instructor-as-role-unified-contacts.md
│   └── 0002-recurring-class-slugs-date-stamped.md
└── <app>/AGENTS.md        per-app detail, not a separate context
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0002 (date-stamped recurring-class slugs), but worth reopening because…_
