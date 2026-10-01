# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

All five labels exist on the repo. `wontfix` predates this file, as do the category labels `bug` and `enhancement`; the other four were created on 2026-09-19 for `/triage`.

These are a triage vocabulary, not a progress view. Where an issue stands in the work is the board's Status column (Backlog, Todo, Research / Finding Facts, Plan / Choosing Approach, Implement / Building, Present / In Review, Observe / Checking Production, Done), and `/triage` never moves that. `ready-for-agent` on an issue means it is specified well enough for `/drive`, not that anyone has started.

Edit the right-hand column to match whatever vocabulary you actually use.
