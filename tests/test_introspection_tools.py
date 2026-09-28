"""`describe_configuration`, `demonstration_plan`, `report_a_problem` — the introspection trio.

The rest of the fleet carried these and this server did not, which meant a refusal could not be
explained from inside the conversation that hit it.

All three answer **without a credential and without a capability**, deliberately: a tool that
explains why something was refused must not itself be refusable. These tests pin that as much as
the content.

No test here reaches PyPI - `latest_on_pypi` is stubbed wherever it would be called.
"""

import json

import pytest

from csa_zendesk import _environment, policy
from csa_zendesk import server as srv


@pytest.fixture(autouse=True)
def _no_pypi(monkeypatch):
    """Every test in this module gets a stubbed index. `report_a_problem` is the only caller,
    but stubbing module-wide means a future test cannot reach the network by forgetting."""
    monkeypatch.setattr(_environment, "latest_on_pypi", lambda: _environment.__version__)


class TestTheyAnswerWithoutACredential:
    """The property that makes them worth having. If these needed the thing that is broken,
    they would be silent exactly when somebody needs them."""

    @pytest.mark.parametrize("name", ["describe_configuration", "demonstration_plan", "report_a_problem"])
    def test_no_credential_is_required(self, name, monkeypatch, tmp_path):
        monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "absent.json"))
        out = json.loads(srv.call_tool_sync(name, {}))
        assert out

    @pytest.mark.parametrize("name", ["describe_configuration", "demonstration_plan", "report_a_problem"])
    def test_none_of_them_is_gated(self, name):
        """Gating them would make the tools that explain a refusal themselves refusable."""
        assert name not in policy._GATES


class TestDescribeConfiguration:
    def test_it_reports_the_tenant_and_the_client_id(self, monkeypatch):
        monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
        monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
        out = json.loads(srv.call_tool_sync("describe_configuration", {}))
        assert out["tenant"] == "example"
        assert out["oauth_client_id"] == "cid"

    def test_an_unset_tenant_is_null_not_an_empty_string(self, monkeypatch):
        """`""` and "not configured" read differently to a model deciding what to say next."""
        monkeypatch.delenv("CSA_ZENDESK_SUBDOMAIN", raising=False)
        assert json.loads(srv.call_tool_sync("describe_configuration", {}))["tenant"] is None

    def test_reach_off_hides_reply_publicly_and_says_so(self, monkeypatch):
        monkeypatch.delenv("CSA_ZD_ALLOW_REACH", raising=False)
        out = json.loads(srv.call_tool_sync("describe_configuration", {}))
        assert out["reach_permitted"] is False
        assert "reply_publicly" in out["hidden_tools"]
        assert "reply_publicly" not in out["registered_tools"]
        assert "cannot leave the organisation" in out["reach_note"]

    def test_reach_on_registers_it_and_changes_the_note(self, monkeypatch):
        # The literal string "true". `policy.reach_permitted()` accepts nothing else, which is
        # right for a control that cannot be undone once it fires - but it means a plausible
        # `=1` silently does nothing, and `describe_configuration` is now the thing that shows
        # an operator why their reply tool is still missing. See the test below.
        monkeypatch.setenv("CSA_ZD_ALLOW_REACH", "true")
        out = json.loads(srv.call_tool_sync("describe_configuration", {}))
        assert out["reach_permitted"] is True
        assert "reply_publicly" in out["registered_tools"]
        assert out["hidden_tools"] == []
        assert "cannot be unsent" in out["reach_note"]

    def test_a_truthy_looking_reach_value_that_is_not_true_reads_as_off(self, monkeypatch):
        """`CSA_ZD_ALLOW_REACH=1` does nothing: only the literal "true" enables reach. Failing
        closed on an unrecognised value is right for a control over irreversible sends, but it
        fails closed SILENTLY - and this tool is now where an operator can see that their
        setting did not take."""
        monkeypatch.setenv("CSA_ZD_ALLOW_REACH", "1")
        out = json.loads(srv.call_tool_sync("describe_configuration", {}))
        assert out["reach_permitted"] is False
        assert "reply_publicly" in out["hidden_tools"]

    def test_a_star_allowlist_and_an_unset_one_read_differently(self, monkeypatch):
        monkeypatch.setenv("CSA_ZD_ALLOWLIST_READ", "*")
        monkeypatch.delenv("CSA_ZD_ALLOWLIST_WRITE", raising=False)
        out = json.loads(srv.call_tool_sync("describe_configuration", {}))
        assert out["allowlists"]["read"]["all_subjects"] is True
        assert out["allowlists"]["write"]["all_subjects"] is False
        assert "nothing is permitted" in out["allowlists"]["write"]["detail"]

    def test_listed_ids_are_reported(self, monkeypatch):
        monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "159445, 2")
        out = json.loads(srv.call_tool_sync("describe_configuration", {}))
        assert out["allowlists"]["write"]["ids"] == ["159445", "2"]

    def test_an_unusable_allowlist_says_so_rather_than_permitting_nothing_silently(self, monkeypatch):
        """An unset allowlist and a typo'd one both permit nothing, and only one is a mistake.
        Reporting the typo as "nothing is permitted" would hide the fix from the only person
        who can make it."""
        monkeypatch.setenv("CSA_ZD_ALLOWLIST_READ", "not-a-number")
        out = json.loads(srv.call_tool_sync("describe_configuration", {}))
        assert out["allowlists"]["read"]["unusable"] is True
        assert "not a numeric id" in out["allowlists"]["read"]["detail"]


class TestDemonstrationPlan:
    def test_it_lists_every_registered_tool(self):
        out = json.loads(srv.call_tool_sync("demonstration_plan", {}))
        assert {s["tool"] for s in out["steps"]} == {t.name for t in srv._visible_tools()}

    def test_it_names_the_capability_each_tool_costs(self):
        out = json.loads(srv.call_tool_sync("demonstration_plan", {}))
        by_name = {s["tool"]: s["capability"] for s in out["steps"]}
        assert by_name["get_ticket"] == policy.TICKET_READ
        assert "none" in by_name["describe_configuration"]

    def test_a_hidden_tool_is_absent_rather_than_listed_as_refused(self, monkeypatch):
        """Absence, not a refusing entry - a plan naming a tool this deployment does not have
        sends somebody looking for a bug that is a configuration."""
        monkeypatch.delenv("CSA_ZD_ALLOW_REACH", raising=False)
        out = json.loads(srv.call_tool_sync("demonstration_plan", {}))
        assert "reply_publicly" not in {s["tool"] for s in out["steps"]}


class TestReportAProblem:
    def test_it_carries_the_version_and_install_route(self):
        out = json.loads(srv.call_tool_sync("report_a_problem", {}))
        assert out["server_version"]
        assert out["installed_via"] in out["report"]

    def test_being_out_of_date_is_flagged_with_the_command_to_fix_it(self, monkeypatch):
        monkeypatch.setattr(_environment, "latest_on_pypi", lambda: "999.0.0")
        out = json.loads(srv.call_tool_sync("report_a_problem", {}))
        assert out["is_outdated"] is True
        assert "OUT OF DATE" in out["report"]
        assert any("Upgrade and retry before filing" in n for n in out["notes"])

    def test_being_current_says_latest(self):
        out = json.loads(srv.call_tool_sync("report_a_problem", {}))
        assert out["is_outdated"] is False
        assert "(latest)" in out["report"]

    def test_an_unreachable_index_does_not_imply_current(self, monkeypatch):
        monkeypatch.setattr(_environment, "latest_on_pypi", lambda: None)
        out = json.loads(srv.call_tool_sync("report_a_problem", {}))
        assert out["is_outdated"] is None
        assert "could not check PyPI" in out["report"]

    def test_it_leaks_no_ticket_ids_addresses_or_credentials(self, monkeypatch):
        """The tool's whole premise. A ticket id would make a report reproducible only by
        putting a real customer's ticket into a public tracker."""
        monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "159445")
        out = json.loads(srv.call_tool_sync("report_a_problem", {}))
        blob = out["report"]
        assert "159445" not in blob
        assert "@" not in blob
        assert "token" not in blob.lower()

    def test_the_new_issue_url_carries_the_report(self):
        out = json.loads(srv.call_tool_sync("report_a_problem", {}))
        assert out["new_issue_url"].startswith(out["issues_url"])
        assert "title=" in out["new_issue_url"]
