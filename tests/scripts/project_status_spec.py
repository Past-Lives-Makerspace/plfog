"""BDD specs for ``.github/scripts/project_status.py``.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its
file path via ``importlib``, the same way ``bot_review_post_spec.py`` loads its script.

Two properties carry most of the weight here.

**Forward only.** A card at Present must not be dragged back to Implement by the next push to
its branch, because a fix round on a reviewed pull request is several pushes and the board
would thrash. The rank comparison is the guard, and ``--force`` is the one sanctioned way
past it.

**Issue resolution must not scrape.** PR #366's body reads "Closes #364. Parent epic: #358
(stays open...)" — a loose ``#\\d+`` scrape would have marched the epic to Observe against
the body's own explicit instruction. So the specs pin the narrowest-first order and pin that
prose mentioning a ticket mid-sentence resolves to nothing.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import urllib.error
from email.message import Message
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "project_status.py"

_ORG = "Past-Lives-Makerspace"
_REPO = "Past-Lives-Makerspace/plfog"
_PROJECT_ID = "PVT_project"
_FIELD_ID = "PVTSSF_status"
_ISSUE_ID = "I_issue"
_ITEM_ID = "PVTI_item"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("project_status", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    return _load_script()


@pytest.fixture
def env(monkeypatch) -> None:
    monkeypatch.setenv("PROJECT_PAT", "tok")
    monkeypatch.setenv("PROJECT_OWNER", _ORG)
    monkeypatch.setenv("PROJECT_NUMBER", "4")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)


class _FakeResponse:
    """Stands in for the object ``urlopen`` yields as a context manager."""

    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


_PHASES = _load_script().PHASES


def _board(options: list[str] | None = None, title: str = "Delivery") -> dict:
    names = options if options is not None else list(_PHASES)
    return {
        "organization": {
            "projectV2": {
                "id": _PROJECT_ID,
                "title": title,
                "field": {"id": _FIELD_ID, "options": [{"id": f"opt_{n}", "name": n} for n in names]},
            }
        }
    }


def _issue(item: bool = True, other_board: bool = False) -> dict:
    nodes = []
    if other_board:
        nodes.append({"id": "PVTI_elsewhere", "project": {"id": "PVT_other"}})
    if item:
        nodes.append({"id": _ITEM_ID, "project": {"id": _PROJECT_ID}})
    return {"repository": {"issueOrPullRequest": {"id": _ISSUE_ID, "projectItems": {"nodes": nodes}}}}


def _status(name: str | None) -> dict:
    nodes = [{}] if name is None else [{"name": name, "field": {"id": _FIELD_ID}}]
    return {"node": {"fieldValueByName": {"nodes": nodes}}}


class _Server:
    """Routes a GraphQL document to a canned response by a distinctive substring of it.

    Routing on the query text rather than call order keeps a spec readable when the code
    under test legitimately changes how many round trips it makes.
    """

    def __init__(self, **routes: dict) -> None:
        self.routes = routes
        self.calls: list[dict] = []

    _MARKERS = (
        ("projectV2(number:", "board"),
        ("issueOrPullRequest", "issue"),
        ("pullRequest(number:", "pr"),
        ("addProjectV2ItemById(input:", "add"),
        ("updateProjectV2ItemFieldValue", "set"),
        ("fieldValues(first:", "item_status"),
    )

    def __call__(self, request, timeout=None) -> _FakeResponse:
        payload = json.loads(request.data.decode())
        self.calls.append(payload)
        query = payload["query"]
        for marker, name in self._MARKERS:
            if marker in query:
                if name not in self.routes:
                    raise AssertionError(f"spec did not provide a '{name}' response for: {query[:60]}")
                return _FakeResponse({"data": self.routes[name]})
        raise AssertionError(f"unroutable query: {query[:80]}")

    def sent(self, name: str) -> list[dict]:
        marker = next(m for m, n in self._MARKERS if n == name)
        return [c["variables"] for c in self.calls if marker in c["query"]]


@pytest.fixture
def serve(module, monkeypatch):
    def _install(**routes) -> _Server:
        server = _Server(**routes)
        monkeypatch.setattr(module.urllib.request, "urlopen", server)
        return server

    return _install


def describe_project_status():
    def describe_graphql():
        def it_returns_the_data_object(module, serve):
            serve(board=_board())
            data = module.graphql("query { projectV2(number: 1) { id } }", {}, "tok")
            assert data["organization"]["projectV2"]["id"] == _PROJECT_ID

        def it_sends_the_token_as_a_bearer_credential(module, monkeypatch):
            captured = {}

            def _urlopen(request, timeout=None):
                captured["auth"] = request.headers["Authorization"]
                return _FakeResponse({"data": {}})

            monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
            module.graphql("query { x }", {}, "s3cret")
            assert captured["auth"] == "bearer s3cret"

        def describe_when_github_returns_an_http_error():
            def it_raises_with_the_status_and_body(module, monkeypatch):
                def _urlopen(request, timeout=None):
                    raise urllib.error.HTTPError("u", 401, "Unauthorized", Message(), io.BytesIO(b"bad credentials"))

                monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
                with pytest.raises(module.ProjectStatusError, match="HTTP 401.*bad credentials"):
                    module.graphql("query { x }", {}, "tok")

        def describe_when_the_api_is_unreachable():
            def it_raises_naming_the_reason(module, monkeypatch):
                def _urlopen(request, timeout=None):
                    raise urllib.error.URLError("name resolution failed")

                monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
                with pytest.raises(module.ProjectStatusError, match="name resolution failed"):
                    module.graphql("query { x }", {}, "tok")

        def describe_when_graphql_reports_errors():
            def it_raises_rather_than_returning_partial_data(module, monkeypatch):
                body = {"data": {"organization": None}, "errors": [{"message": "INSUFFICIENT_SCOPES"}]}
                monkeypatch.setattr(module.urllib.request, "urlopen", lambda request, timeout=None: _FakeResponse(body))
                with pytest.raises(module.ProjectStatusError, match="INSUFFICIENT_SCOPES"):
                    module.graphql("query { x }", {}, "tok")

    def describe_load_board():
        def it_returns_the_ids_and_the_option_map(module, serve):
            serve(board=_board())
            project_id, field_id, options = module.load_board(_ORG, 4, "tok")
            assert project_id == _PROJECT_ID
            assert field_id == _FIELD_ID
            assert options["Observe"] == "opt_Observe"

        def describe_when_the_organization_is_invisible():
            def it_raises(module, serve):
                serve(board={"organization": None})
                with pytest.raises(module.ProjectStatusError, match="No organization"):
                    module.load_board(_ORG, 4, "tok")

        def describe_when_the_board_number_is_wrong():
            def it_raises_naming_the_number(module, serve):
                serve(board={"organization": {"projectV2": None}})
                with pytest.raises(module.ProjectStatusError, match="No project number 4"):
                    module.load_board(_ORG, 4, "tok")

        def describe_when_status_is_not_a_single_select_field():
            def it_raises(module, serve):
                serve(board={"organization": {"projectV2": {"id": _PROJECT_ID, "title": "D", "field": None}}})
                with pytest.raises(module.ProjectStatusError, match="no single-select field"):
                    module.load_board(_ORG, 4, "tok")

            def it_also_raises_when_the_field_carries_no_options(module, serve):
                field = {"id": _FIELD_ID}
                serve(board={"organization": {"projectV2": {"id": _PROJECT_ID, "title": "D", "field": field}}})
                with pytest.raises(module.ProjectStatusError, match="no single-select field"):
                    module.load_board(_ORG, 4, "tok")

        def describe_when_a_phase_column_is_missing():
            def it_names_every_missing_column(module, serve):
                serve(board=_board(["Triage", "Plan", "Done"]))
                with pytest.raises(module.ProjectStatusError, match="Research, Implement, Present, Observe"):
                    module.load_board(_ORG, 4, "tok")

            def it_lists_what_the_board_does_have(module, serve):
                serve(board=_board([]))
                with pytest.raises(module.ProjectStatusError, match=r"options are: \(none\)"):
                    module.load_board(_ORG, 4, "tok")

    def describe_find_issue():
        def it_returns_the_issue_and_its_card_on_this_board(module, serve):
            serve(issue=_issue(item=True))
            assert module.find_issue("o", "r", 357, _PROJECT_ID, "tok") == (_ISSUE_ID, _ITEM_ID)

        def describe_when_the_issue_is_on_no_board():
            def it_returns_no_card(module, serve):
                serve(issue=_issue(item=False))
                assert module.find_issue("o", "r", 357, _PROJECT_ID, "tok") == (_ISSUE_ID, None)

        def describe_when_the_issue_sits_on_a_different_board():
            def it_does_not_mistake_that_card_for_this_one(module, serve):
                serve(issue=_issue(item=False, other_board=True))
                assert module.find_issue("o", "r", 357, _PROJECT_ID, "tok") == (_ISSUE_ID, None)

        def describe_when_the_repository_is_invisible():
            def it_raises(module, serve):
                serve(issue={"repository": None})
                with pytest.raises(module.ProjectStatusError, match="No repository"):
                    module.find_issue("o", "r", 357, _PROJECT_ID, "tok")

        def describe_when_the_number_names_nothing():
            def it_raises(module, serve):
                serve(issue={"repository": {"issueOrPullRequest": None}})
                with pytest.raises(module.ProjectStatusError, match="does not exist"):
                    module.find_issue("o", "r", 9999, _PROJECT_ID, "tok")

        def describe_when_the_number_names_a_pull_request():
            def it_raises_rather_than_moving_a_card_that_does_not_exist(module, serve):
                serve(issue={"repository": {"issueOrPullRequest": {}}})
                with pytest.raises(module.ProjectStatusError, match="is a pull request, not an issue"):
                    module.find_issue("o", "r", 366, _PROJECT_ID, "tok")

    def describe_current_status():
        def it_returns_the_column_name(module, serve):
            serve(item_status=_status("Present"))
            assert module.current_status(_ITEM_ID, _FIELD_ID, "tok") == "Present"

        def describe_when_the_field_is_unset():
            def it_returns_none(module, serve):
                serve(item_status=_status(None))
                assert module.current_status(_ITEM_ID, _FIELD_ID, "tok") is None

        def describe_when_the_card_carries_another_single_select_field():
            def it_ignores_it(module, serve):
                nodes = [{"name": "P1", "field": {"id": "PVTSSF_priority"}}]
                serve(item_status={"node": {"fieldValueByName": {"nodes": nodes}}})
                assert module.current_status(_ITEM_ID, _FIELD_ID, "tok") is None

    def describe_move():
        def it_sets_the_column_and_reports_the_transition(module, serve):
            server = serve(
                board=_board(),
                issue=_issue(item=True),
                item_status=_status("Implement"),
                set={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": _ITEM_ID}}},
            )
            result = module.move("o", "r", 357, "Present", _ORG, 4, "tok")
            assert result == "#357: Implement -> Present"
            assert server.sent("set")[0]["option"] == "opt_Present"

        def describe_when_the_issue_is_not_on_the_board_yet():
            def it_adds_the_card_first_and_says_so(module, serve):
                server = serve(
                    board=_board(),
                    issue=_issue(item=False),
                    add={"addProjectV2ItemById": {"item": {"id": _ITEM_ID}}},
                    set={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": _ITEM_ID}}},
                )
                result = module.move("o", "r", 357, "Implement", _ORG, 4, "tok")
                assert result == "#357: no status -> Implement (added to the board)"
                assert server.sent("add")[0]["content"] == _ISSUE_ID

        def describe_when_the_card_is_already_in_the_target_column():
            def it_does_nothing(module, serve):
                server = serve(board=_board(), issue=_issue(), item_status=_status("Observe"))
                assert module.move("o", "r", 357, "Observe", _ORG, 4, "tok") == (
                    "#357 is already in Observe; nothing to do."
                )
                assert server.sent("set") == []

        def describe_when_the_card_is_already_further_along():
            def it_refuses_to_drag_it_backwards(module, serve):
                server = serve(board=_board(), issue=_issue(), item_status=_status("Present"))
                result = module.move("o", "r", 357, "Implement", _ORG, 4, "tok")
                assert result == "#357 is in Present, which is not behind Implement; left alone."
                assert server.sent("set") == []

            def it_moves_anyway_when_forced(module, serve):
                server = serve(
                    board=_board(),
                    issue=_issue(),
                    item_status=_status("Observe"),
                    set={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": _ITEM_ID}}},
                )
                result = module.move("o", "r", 357, "Research", _ORG, 4, "tok", force=True)
                assert result == "#357: Observe -> Research"
                assert server.sent("set")[0]["option"] == "opt_Research"

        def describe_when_the_card_sits_in_a_column_that_is_not_a_phase():
            def it_treats_it_as_behind_everything_and_moves(module, serve):
                serve(
                    board=_board(),
                    issue=_issue(),
                    item_status=_status("Icebox"),
                    set={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": _ITEM_ID}}},
                )
                assert module.move("o", "r", 357, "Plan", _ORG, 4, "tok") == "#357: Icebox -> Plan"

        def describe_with_a_status_that_is_not_a_phase():
            def it_raises_before_touching_the_api(module, serve):
                serve()
                with pytest.raises(module.ProjectStatusError, match="'Shipped' is not a phase"):
                    module.move("o", "r", 357, "Shipped", _ORG, 4, "tok")

    def describe_issues_from_text():
        def it_reads_a_ticket_line(module):
            assert module.issues_from_text("Ticket: #357\n\nBody.", "fog/whatever") == [357]

        def it_reads_several_from_one_line(module):
            assert module.issues_from_text("Tickets: #357, #358", None) == [357, 358]

        def it_deduplicates(module):
            assert module.issues_from_text("Ticket: #357\nTicket: #357", None) == [357]

        def describe_when_there_is_no_ticket_line():
            def it_falls_back_to_the_branch_name(module):
                assert module.issues_from_text("No ticket here.", "fog/357-public-topbar-mobile") == [357]

            def it_reads_a_branch_under_any_prefix(module):
                assert module.issues_from_text(None, "fix/205-index-name-cap") == [205]

            def describe_and_the_branch_carries_no_number():
                def it_finds_nothing(module):
                    assert module.issues_from_text(None, "fog/composer-preselection-fix") == []

                def it_finds_nothing_for_a_bare_branch_name(module):
                    assert module.issues_from_text(None, "wiki-b") == []

                def it_finds_nothing_when_the_number_is_not_the_first_segment(module):
                    assert module.issues_from_text(None, "fog/topbar-357-fix") == []

        def describe_when_prose_merely_mentions_a_ticket_mid_sentence():
            def it_does_not_scrape_it(module):
                body = "Closes #364. Parent epic: #358 (stays open; the sibling child #365 is closed)."
                assert module.issues_from_text(body, "fog/364-release-guard") == [364]

        def describe_with_neither_a_body_nor_a_branch():
            def it_returns_nothing(module):
                assert module.issues_from_text(None, None) == []

    def describe_issues_for_pr():
        def it_prefers_what_github_itself_links(module, serve):
            pr = {
                "repository": {
                    "pullRequest": {
                        "body": "Ticket: #999",
                        "headRefName": "fog/111-x",
                        "closingIssuesReferences": {"nodes": [{"number": 364}]},
                    }
                }
            }
            serve(pr=pr)
            assert module.issues_for_pr("o", "r", 366, "tok") == [364]

        def describe_when_github_links_nothing():
            def it_falls_back_to_the_ticket_line(module, serve):
                pr = {
                    "repository": {
                        "pullRequest": {
                            "body": "Ticket: #357. Left open on merge deliberately.",
                            "headRefName": "fog/357-public-topbar-mobile",
                            "closingIssuesReferences": {"nodes": []},
                        }
                    }
                }
                serve(pr=pr)
                assert module.issues_for_pr("o", "r", 363, "tok") == [357]

            def it_returns_nothing_for_a_pull_request_with_no_ticket(module, serve):
                pr = {
                    "repository": {
                        "pullRequest": {
                            "body": "Drive-by typo fix.",
                            "headRefName": "typo",
                            "closingIssuesReferences": {"nodes": []},
                        }
                    }
                }
                serve(pr=pr)
                assert module.issues_for_pr("o", "r", 400, "tok") == []

        def describe_when_the_number_is_not_a_pull_request():
            def it_raises(module, serve):
                serve(pr={"repository": {"pullRequest": None}})
                with pytest.raises(module.ProjectStatusError, match="is not a pull request"):
                    module.issues_for_pr("o", "r", 357, "tok")

            def it_also_raises_when_the_repository_is_invisible(module, serve):
                serve(pr={"repository": None})
                with pytest.raises(module.ProjectStatusError, match="is not a pull request"):
                    module.issues_for_pr("o", "r", 357, "tok")

    def describe_main():
        def it_moves_the_issue_it_was_given(module, serve, env, capsys):
            serve(
                board=_board(),
                issue=_issue(),
                item_status=_status("Plan"),
                set={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": _ITEM_ID}}},
            )
            assert module.main(["--issue", "357", "--status", "Implement"]) == 0
            assert "#357: Plan -> Implement" in capsys.readouterr().out

        def it_moves_every_issue_a_pull_request_names(module, serve, env, capsys):
            pr = {
                "repository": {
                    "pullRequest": {
                        "body": "Tickets: #357, #358",
                        "headRefName": "fog/x",
                        "closingIssuesReferences": {"nodes": []},
                    }
                }
            }
            serve(
                pr=pr,
                board=_board(),
                issue=_issue(),
                item_status=_status("Implement"),
                set={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": _ITEM_ID}}},
            )
            assert module.main(["--pr", "363", "--status", "Present"]) == 0
            out = capsys.readouterr().out
            assert "#357: Implement -> Present" in out
            assert "#358: Implement -> Present" in out

        def it_reads_an_issue_number_out_of_a_branch(module, serve, env, capsys):
            serve(
                board=_board(),
                issue=_issue(),
                item_status=_status(None),
                set={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": _ITEM_ID}}},
            )
            assert module.main(["--branch", "fog/357-topbar", "--status", "Implement"]) == 0
            assert "#357: no status -> Implement" in capsys.readouterr().out

        def it_passes_force_through(module, serve, env, capsys):
            serve(
                board=_board(),
                issue=_issue(),
                item_status=_status("Done"),
                set={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": _ITEM_ID}}},
            )
            assert module.main(["--issue", "357", "--status", "Plan", "--force"]) == 0
            assert "#357: Done -> Plan" in capsys.readouterr().out

        def describe_when_nothing_names_a_ticket():
            def it_succeeds_quietly(module, serve, env, capsys):
                serve()
                assert module.main(["--branch", "fog/no-number-here", "--status", "Implement"]) == 0
                assert "nothing to do" in capsys.readouterr().out

        def describe_when_a_required_variable_is_missing():
            def it_fails_naming_the_variable(module, serve, env, monkeypatch, capsys):
                monkeypatch.delenv("PROJECT_PAT")
                assert module.main(["--issue", "357", "--status", "Plan"]) == 1
                assert "PROJECT_PAT is not set" in capsys.readouterr().err

            def it_fails_on_an_empty_variable_too(module, serve, env, monkeypatch, capsys):
                monkeypatch.setenv("PROJECT_NUMBER", "")
                assert module.main(["--issue", "357", "--status", "Plan"]) == 1
                assert "PROJECT_NUMBER is not set" in capsys.readouterr().err

        def describe_when_the_repository_is_not_owner_slash_repo():
            def it_fails(module, serve, env, monkeypatch, capsys):
                monkeypatch.setenv("GITHUB_REPOSITORY", "plfog")
                assert module.main(["--issue", "357", "--status", "Plan"]) == 1
                assert "owner/repo" in capsys.readouterr().err

        def describe_when_the_board_cannot_be_reached():
            def it_emits_a_workflow_error_annotation(module, serve, env, capsys):
                serve(board={"organization": None})
                assert module.main(["--issue", "357", "--status", "Plan"]) == 1
                err = capsys.readouterr().err
                assert err.startswith("::error title=Delivery board not updated::")
                assert "No organization" in err

        def describe_with_a_status_outside_the_phases():
            def it_is_refused_by_the_argument_parser(module, env):
                with pytest.raises(SystemExit):
                    module.main(["--issue", "357", "--status", "Shipped"])

        def describe_with_no_source_at_all():
            def it_is_refused_by_the_argument_parser(module, env):
                with pytest.raises(SystemExit):
                    module.main(["--status", "Plan"])
