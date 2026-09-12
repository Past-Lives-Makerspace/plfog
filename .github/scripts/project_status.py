"""Move a ticket's card across the delivery board when GitHub can see the work move.

The board is an organization ProjectV2 whose ``Status`` field carries the RPIPO phases:
Triage, Research, Plan, Implement, Present, Observe, Done. This script sets that field.

**It cannot carry the whole pipeline, and pretending otherwise would be the bug.** GitHub
only emits an event for work that reaches GitHub: a branch appearing, a pull request opening,
a pull request merging, an issue closing. Research and Plan happen entirely on a laptop, in a
corpus that lives outside the repo by design (``~/.rpipo/<repo>/<issue>/``), and GitHub never
hears about either. Those two columns are set by the ``rpipo-research`` and ``rpipo-plan``
skills calling this same script with ``--status``, which is why the CLI exists at all rather
than the logic living inline in the workflow.

So the division is:

    Triage     an issue exists            -- the project's own built-in auto-add workflow
    Research   scouts are out             -- rpipo-research, locally
    Plan       an approach is being cut   -- rpipo-plan, locally
    Implement  a branch exists            -- this script, on push
    Present    a pull request is open     -- this script, on pull_request
    Observe    it merged, prod unverified -- this script, on pull_request closed+merged
    Done       the issue closed           -- this script, on issues closed

**Movement is forward only.** The ranks above are ordered, and a card already at or past the
requested column is left alone. Without that rule the board thrashes: every push to a branch
with an open pull request would drag the card from Present back to Implement, and a fix round
on a reviewed PR is several pushes. ``--force`` overrides it, for the one honest case of
going backwards -- a merged ticket that Observe sends back for another round.

**Which issue a pull request belongs to** is deliberately not "scrape ``#\\d+`` out of the
body". PR #366's body opens "Closes #364. Parent epic: #358 (stays open...)", and a scraper
would have marched the epic to Observe against the body's own explicit instruction. The
resolution order is narrowest-first:

    1. ``closingIssuesReferences`` -- what GitHub itself considers linked. Authoritative.
    2. A ``Ticket: #N`` line -- this repo's convention for a PR that advances an issue
       without closing it. PR #363 used exactly that, and left #357 open on purpose.
    3. The branch name, ``<anything>/<number>-<slug>`` -- e.g. ``fog/357-public-topbar-mobile``.

Anything else resolves to no issue and the run says so and exits zero. A pull request that
belongs to no ticket is the ordinary case, not an error.

The credential is ``PROJECT_PAT``, a classic token carrying **only** the ``project`` scope,
and deliberately not ``BOT_PAT``. ``BOT_PAT`` holds ``repo`` and is handed to a
``pull_request_target`` job that reads attacker-controlled diff text; widening it to reach
the org's boards would widen that blast radius for no reason. A token that can only move
cards can only ever move cards. ``GITHUB_TOKEN`` is not an option at all: it is scoped to the
repository that issued it, and an organization board is not in any repository.

Stdlib only -- this workflow installs nothing beyond the interpreter.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

_API = "https://api.github.com/graphql"
_STATUS_FIELD = "Status"

# Ordered. The index is the rank that makes movement forward-only.
PHASES = ("Triage", "Research", "Plan", "Implement", "Present", "Observe", "Done")

# `fog/357-public-topbar-mobile` -> 357. The prefix is not pinned to `fog/` because
# the repo has used others (`wiki-b`, `fix/...`); what matters is a number leading the slug.
_BRANCH_ISSUE_RE = re.compile(r"^[^/]+/(\d+)-")
# `Ticket: #357`, `Tickets: #357, #358`. Case-insensitive, line-anchored: a mid-sentence
# "the ticket: #358" in prose must not count, which is why this is not a loose search.
_TICKET_LINE_RE = re.compile(r"^\s*tickets?:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_ISSUE_REF_RE = re.compile(r"#(\d+)")


class ProjectStatusError(RuntimeError):
    """Raised when the board cannot be reached or does not look the way it must."""


def graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    """POST a GraphQL document and return ``data``, raising on any error GitHub reports.

    Args:
        query: The GraphQL document.
        variables: Its variables.
        token: A token carrying the ``project`` scope.

    Returns:
        The ``data`` object of the response.

    Raises:
        ProjectStatusError: On a transport error, an HTTP error, or a GraphQL ``errors``
            array. A partial success is treated as a failure: a board left half-moved with a
            green tick is the failure mode this whole change exists to remove.
    """
    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        _API,
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "plfog-project-status",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise ProjectStatusError(f"GitHub returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProjectStatusError(f"Could not reach the GitHub API: {exc.reason}") from exc

    if "errors" in body:
        messages = "; ".join(error["message"] for error in body["errors"])
        raise ProjectStatusError(f"GraphQL refused the request: {messages}")
    return body["data"]


_PROJECT_QUERY = """
query($org: String!, $number: Int!) {
  organization(login: $org) {
    projectV2(number: $number) {
      id
      title
      field(name: "Status") {
        ... on ProjectV2SingleSelectField { id options { id name } }
      }
    }
  }
}
"""


def load_board(org: str, number: int, token: str) -> tuple[str, str, dict[str, str]]:
    """Resolve the board and its ``Status`` field by name, never by a recorded id.

    Field and option ids are opaque and change if the field is rebuilt, so pinning them in a
    workflow file guarantees a silent breakage the first time somebody edits the board in the
    UI. Looking them up by name each run costs one query and fails loudly instead.

    Args:
        org: The organization login that owns the board.
        number: The board's number, as it appears in its URL.
        token: A token carrying the ``project`` scope.

    Returns:
        The project id, the ``Status`` field id, and a mapping of option name to option id.

    Raises:
        ProjectStatusError: If the board does not exist, has no ``Status`` single-select
            field, or is missing any phase column.
    """
    data = graphql(_PROJECT_QUERY, {"org": org, "number": number}, token)
    organization = data["organization"]
    if organization is None:
        raise ProjectStatusError(f"No organization '{org}', or the token cannot see it.")
    project = organization["projectV2"]
    if project is None:
        raise ProjectStatusError(
            f"No project number {number} on '{org}'. Check the number in the board's URL, "
            f"and that PROJECT_PAT's owner has access to the board."
        )
    field = project["field"]
    if field is None or "options" not in field:
        raise ProjectStatusError(f"Board '{project['title']}' has no single-select field named '{_STATUS_FIELD}'.")

    options = {option["name"]: option["id"] for option in field["options"]}
    missing = [phase for phase in PHASES if phase not in options]
    if missing:
        raise ProjectStatusError(
            f"Board '{project['title']}' is missing the column(s) {', '.join(missing)}. "
            f"Its {_STATUS_FIELD} options are: {', '.join(options) or '(none)'}."
        )
    return project["id"], field["id"], options


_ISSUE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issueOrPullRequest(number: $number) {
      ... on Issue {
        id
        projectItems(first: 20) { nodes { id project { id } } }
      }
    }
  }
}
"""


def find_issue(owner: str, repo: str, number: int, project_id: str, token: str) -> tuple[str, str | None]:
    """The issue's node id, plus its existing card on this board if it has one.

    Args:
        owner: Repository owner.
        repo: Repository name.
        number: Issue number.
        project_id: The board, so a card on some *other* board is not mistaken for this one.
        token: A token carrying the ``project`` scope.

    Returns:
        The issue node id, and the id of its card on this board or ``None`` if it has none.

    Raises:
        ProjectStatusError: If the number names nothing, or names a pull request.
    """
    data = graphql(_ISSUE_QUERY, {"owner": owner, "repo": repo, "number": number}, token)
    repository = data["repository"]
    if repository is None:
        raise ProjectStatusError(f"No repository {owner}/{repo}, or the token cannot see it.")
    issue = repository["issueOrPullRequest"]
    if issue is None:
        raise ProjectStatusError(f"{owner}/{repo}#{number} does not exist.")
    if "id" not in issue:
        # issueOrPullRequest resolved, but not to an Issue -- the inline fragment above
        # returns an empty object for a PullRequest. A PR has no card of its own here.
        raise ProjectStatusError(f"{owner}/{repo}#{number} is a pull request, not an issue.")

    for node in issue["projectItems"]["nodes"]:
        if node["project"]["id"] == project_id:
            return issue["id"], node["id"]
    return issue["id"], None


_ADD_MUTATION = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}
"""

_SET_MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field,
            value: {singleSelectOptionId: $option}}
  ) { projectV2Item { id } }
}
"""

_ITEM_STATUS_QUERY = """
query($item: ID!, $field: ID!) {
  node(id: $item) {
    ... on ProjectV2Item {
      fieldValueByName: fieldValues(first: 20) {
        nodes { ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2SingleSelectField { id } } } }
      }
    }
  }
}
"""


def current_status(item_id: str, field_id: str, token: str) -> str | None:
    """The card's current column name, or ``None`` if the field is unset.

    Args:
        item_id: The card.
        field_id: The ``Status`` field.
        token: A token carrying the ``project`` scope.

    Returns:
        The column name, or ``None`` when the card has no status yet.
    """
    data = graphql(_ITEM_STATUS_QUERY, {"item": item_id, "field": field_id}, token)
    for node in data["node"]["fieldValueByName"]["nodes"]:
        # Non-single-select values come back as empty objects from the inline fragment.
        if node and node["field"]["id"] == field_id:
            return node["name"]
    return None


def move(
    owner: str,
    repo: str,
    issue: int,
    status: str,
    org: str,
    project_number: int,
    token: str,
    force: bool = False,
) -> str:
    """Put an issue's card in ``status``, adding it to the board first if it is not on it.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue: Issue number.
        status: Target column, one of :data:`PHASES`.
        org: Organization owning the board.
        project_number: The board's number.
        token: A token carrying the ``project`` scope.
        force: Move backwards as well as forwards.

    Returns:
        A one-line description of what happened, for the run log.

    Raises:
        ProjectStatusError: If ``status`` is not a phase, or the board cannot be reached.
    """
    if status not in PHASES:
        raise ProjectStatusError(f"'{status}' is not a phase. Expected one of: {', '.join(PHASES)}.")

    project_id, field_id, options = load_board(org, project_number, token)
    issue_id, item_id = find_issue(owner, repo, issue, project_id, token)

    if item_id is None:
        item_id = graphql(_ADD_MUTATION, {"project": project_id, "content": issue_id}, token)["addProjectV2ItemById"][
            "item"
        ]["id"]
        present = None
        added = " (added to the board)"
    else:
        present = current_status(item_id, field_id, token)
        added = ""

    if present == status:
        return f"#{issue} is already in {status}; nothing to do."

    # Forward-only unless forced. An unset status, or one somebody renamed out from under us,
    # ranks below everything so the move always proceeds.
    if not force and present is not None and present in PHASES:
        if PHASES.index(present) >= PHASES.index(status):
            return f"#{issue} is in {present}, which is not behind {status}; left alone."

    graphql(
        _SET_MUTATION,
        {"project": project_id, "item": item_id, "field": field_id, "option": options[status]},
        token,
    )
    return f"#{issue}: {present or 'no status'} -> {status}{added}"


_PR_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      body
      headRefName
      closingIssuesReferences(first: 10) { nodes { number } }
    }
  }
}
"""


def issues_from_text(body: str | None, branch: str | None) -> list[int]:
    """Issue numbers a pull request advances, from its ``Ticket:`` line or its branch name.

    Used only when GitHub itself reports no closing reference. See the module docstring for
    why a bare ``#\\d+`` scrape of the body is not acceptable here.

    Args:
        body: The pull request body, which may be empty.
        branch: The head branch name.

    Returns:
        Issue numbers, in order, without duplicates. Empty when neither source names one.
    """
    found: list[int] = []
    for line in _TICKET_LINE_RE.findall(body or ""):
        found.extend(int(number) for number in _ISSUE_REF_RE.findall(line))
    if not found and branch:
        match = _BRANCH_ISSUE_RE.match(branch)
        if match:
            found.append(int(match.group(1)))
    return list(dict.fromkeys(found))


def issues_for_pr(owner: str, repo: str, number: int, token: str) -> list[int]:
    """Every issue a pull request should move, narrowest source first.

    Args:
        owner: Repository owner.
        repo: Repository name.
        number: Pull request number.
        token: A token carrying the ``project`` scope.

    Returns:
        Issue numbers. Empty is an ordinary result, not an error.

    Raises:
        ProjectStatusError: If the pull request does not exist.
    """
    data = graphql(_PR_QUERY, {"owner": owner, "repo": repo, "number": number}, token)
    repository = data["repository"]
    if repository is None or repository["pullRequest"] is None:
        raise ProjectStatusError(f"{owner}/{repo}#{number} is not a pull request.")
    pull_request = repository["pullRequest"]

    closing = [node["number"] for node in pull_request["closingIssuesReferences"]["nodes"]]
    if closing:
        return closing
    return issues_from_text(pull_request["body"], pull_request["headRefName"])


def _env(name: str) -> str:
    """A required environment variable, or a clear failure naming it.

    Args:
        name: The variable to read.

    Returns:
        Its value.

    Raises:
        ProjectStatusError: If it is unset or empty. A missing PROJECT_PAT must stop the run,
            not fall back to an anonymous request that 401s ten lines later.
    """
    value = os.environ.get(name, "")
    if not value:
        raise ProjectStatusError(f"{name} is not set.")
    return value


def main(argv: list[str] | None = None) -> int:
    """Move one ticket, or every ticket a pull request names.

    Args:
        argv: Argument list, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` on success or on there being nothing to do, ``1`` on any failure.
    """
    parser = argparse.ArgumentParser(description="Move a ticket's card on the delivery board.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--issue", type=int, help="Issue number to move.")
    source.add_argument("--pr", type=int, help="Pull request whose issues should move.")
    source.add_argument("--branch", help="Branch name to read an issue number out of.")
    parser.add_argument("--status", required=True, choices=PHASES, help="Target column.")
    parser.add_argument("--force", action="store_true", help="Allow a backwards move.")
    args = parser.parse_args(argv)

    try:
        token = _env("PROJECT_PAT")
        org = _env("PROJECT_OWNER")
        project_number = int(_env("PROJECT_NUMBER"))
        owner, _, repo = _env("GITHUB_REPOSITORY").partition("/")
        if not repo:
            raise ProjectStatusError("GITHUB_REPOSITORY is not in 'owner/repo' form.")

        if args.issue is not None:
            issues = [args.issue]
        elif args.pr is not None:
            issues = issues_for_pr(owner, repo, args.pr, token)
        else:
            issues = issues_from_text(None, args.branch)

        if not issues:
            print(f"No ticket to move for {args.pr or args.branch}; nothing to do.")
            return 0

        for issue in issues:
            print(move(owner, repo, issue, args.status, org, project_number, token, args.force))
    except ProjectStatusError as exc:
        print(f"::error title=Delivery board not updated::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
