"""The job that can mint a publishing credential runs nothing of ours.

`release.yml` used to do everything in one job that held `id-token: write`: `pip install`
resolving lower-bound-only ranges, `pip-audit`, `bandit`, the full test suite, and
`python -m build`. Every one of those executes third-party code, and any of it could have read
`ACTIONS_ID_TOKEN_REQUEST_URL` / `_TOKEN` from the job environment, minted a PyPI-audience OIDC
token, and published arbitrary artifacts as `csa-zendesk`.

That is **amplification** rather than a vulnerability of ours — it needs a compromised dependency
first, which is outside our control. What it does is turn *"a dependency was compromised"* into
*"our published artifact was compromised"*, and that lands on every operator holding a Zendesk
credential.

csa-zendesk was built with the split from its first release, rather than arriving at it the way
the sibling did - but the property is inherited, not independently earned, so the test comes with
it. A pipeline that has never been wrong is exactly the one where nobody notices the shape
changing.

**Why this is a test and not just a fix.** The property is one careless step from gone. Adding
`actions/checkout` to `publish` "to read the version", or a `pip install` "to check something
first", restores the exact exposure — and it would look entirely reasonable in review. So the
shape is asserted rather than remembered.

The assertions are deliberately about **structure**, not about a list of forbidden strings: the
credential-holding job may not check out the repository, may not run shell, and may use only
actions on a named allowlist. A new step has to be added to that list on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"

# Everything the credential-holding job is permitted to invoke. Hand-written: a step added here
# is a step somebody decided belongs next to a live publishing credential.
PUBLISH_MAY_USE = {
    "actions/download-artifact",
    "pypa/gh-action-pypi-publish",
}


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def jobs(workflow):
    return workflow["jobs"]


def uses(step):
    return (step.get("uses") or "").split("@")[0]


class TestExactlyOneJobCanMintTheCredential:
    def test_only_one_job_has_id_token_write(self, jobs):
        holders = [name for name, job in jobs.items() if (job.get("permissions") or {}).get("id-token") == "write"]
        assert holders == ["publish"], (
            f"jobs holding id-token: write are {holders}; exactly one should, and it should be "
            f"the one that does nothing else"
        )

    def test_the_build_job_has_no_id_token(self, jobs):
        """The load-bearing half: this is where every third-party thing runs."""
        assert "id-token" not in (jobs["build"].get("permissions") or {})

    def test_the_WORKFLOW_LEVEL_block_does_not_grant_it_either(self, workflow):
        """#323. Both assertions above read PER-JOB permissions, and a workflow-level grant is
        INHERITED by every job that does not override it — so adding `id-token: write` at the
        top would hand the credential to `build`, the job that runs `pip install` and the test
        suite, **with both of those assertions still green**.

        `release.yml` is correct today (`permissions: contents: read` at the top, `id-token`
        scoped to `publish` alone). This is a guard gap rather than a live leak, and the point
        of writing it now is that the leak would be invisible to the tests that exist.
        """
        top = workflow.get("permissions") or {}
        assert "id-token" not in top, (
            "the workflow-level permissions block grants id-token, which every job inherits - "
            "including `build`. Scope it to the `publish` job instead."
        )

    def test_the_workflow_level_default_is_read_only(self, workflow):
        """The counterweight: absence of `id-token` is not the same as a narrow default. An
        omitted block inherits the repository default, which may be write-all."""
        top = workflow.get("permissions") or {}
        assert top, (
            "release.yml has no workflow-level permissions block, so jobs inherit the "
            "repository default - state it explicitly"
        )
        assert all(v == "read" for v in top.values()), (
            f"workflow-level permissions are {top}; the default every job inherits should be "
            f"read-only, with any write scoped to the one job that needs it"
        )

    def test_the_publish_job_waits_for_the_build_job(self, jobs):
        assert jobs["publish"].get("needs") == "build"

    def test_the_environment_gate_is_on_the_job_with_the_credential(self, jobs):
        """Approval should gate the credential, not the build. It also means the reviewer
        approves a build they can see passed, rather than one about to start."""
        assert jobs["publish"].get("environment") == "pypi"


class TestThePublishJobRunsNothingOfOurs:
    def test_it_does_not_check_out_the_repository(self, jobs):
        """Not present is stronger than not executed: project code is not even on disk in the
        job that can publish."""
        assert not any(uses(s) == "actions/checkout" for s in jobs["publish"]["steps"]), (
            "the publish job checks out the repository; the split exists so our code is absent "
            "from the job holding the credential"
        )

    def test_it_runs_no_shell_at_all(self, jobs):
        offenders = [s.get("name") or uses(s) for s in jobs["publish"]["steps"] if "run" in s]
        assert offenders == [], (
            f"the publish job runs shell steps {offenders}; anything invoked there executes "
            f"beside a live publishing credential"
        )

    def test_every_action_it_uses_is_on_the_allowlist(self, jobs):
        used = {uses(s) for s in jobs["publish"]["steps"]}
        assert used <= PUBLISH_MAY_USE, (
            f"the publish job uses {sorted(used - PUBLISH_MAY_USE)}, which is not on the "
            f"allowlist in this test. If it genuinely belongs beside a publishing credential, "
            f"add it here deliberately."
        )

    def test_the_allowlist_is_not_stale(self, jobs):
        """An allowlist naming actions nobody uses stops describing anything."""
        used = {uses(s) for s in jobs["publish"]["steps"]}
        assert PUBLISH_MAY_USE <= used, (
            f"the allowlist names {sorted(PUBLISH_MAY_USE - used)}, which the job no longer uses"
        )


class TestTheBuildJobStillDoesTheChecking:
    """Splitting must not have moved a gate off the path. Each of these is something the release
    refuses to publish without."""

    @pytest.mark.parametrize(
        "needle", ["pip-audit", "bandit", "pytest", "python -m build", "twine check", "refusing to publish"]
    )
    def test_the_gate_is_still_there(self, jobs, needle):
        shell = "\n".join(s.get("run", "") for s in jobs["build"]["steps"])
        assert needle in shell, f"the build job no longer runs {needle!r}"

    def test_it_hands_the_artifacts_on(self, jobs):
        upload = [s for s in jobs["build"]["steps"] if uses(s) == "actions/upload-artifact"]
        assert upload, "the build job builds nothing the publish job can use"
        assert upload[0]["with"].get("if-no-files-found") == "error", (
            "without this, an empty dist/ uploads cleanly and the publish job succeeds having "
            "shipped nothing - a green release that released nothing"
        )


class TestEveryActionIsPinnedToACommit:
    """Repo convention, and it matters more in these two jobs than anywhere else."""

    @pytest.mark.parametrize("job", ["build", "publish"])
    def test_no_floating_tags(self, jobs, job):
        floating = [s["uses"] for s in jobs[job]["steps"] if "uses" in s and not _is_sha(s["uses"])]
        assert floating == [], f"{job} uses unpinned actions: {floating}"


def _is_sha(ref: str) -> bool:
    pin = ref.split("@")[-1]
    return len(pin) == 40 and all(c in "0123456789abcdef" for c in pin)
