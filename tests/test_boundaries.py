import csv
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def rows():
    with (ROOT / "analysis/tool-boundaries.csv").open() as fh:
        return list(csv.DictReader(fh))


def test_every_tool_has_exactly_one_capability():
    for r in rows():
        assert r["capability"] and " " not in r["capability"], r["tool"]


def test_tools_sharing_an_operation_differ_on_an_axis_or_a_constraint():
    # The point of ADR-016: one operation backs several tools, and what separates
    # them is the constraint. Two tools on one operation with identical axes AND
    # no distinguishing constraint would be a duplicate, not a split.
    by_op: dict[tuple[str, str], list[dict]] = {}
    for r in rows():
        by_op.setdefault((r["method"], r["path"]), []).append(r)
    for op, tools in by_op.items():
        if len(tools) == 1:
            continue
        seen = set()
        for t in tools:
            key = (t["effect"], t["reversibility"], t["reach"], t["constraint"])
            assert key not in seen, f"{op}: {t['tool']} is indistinguishable from a sibling"
            seen.add(key)


def test_a_tool_on_a_shared_operation_must_carry_a_constraint():
    # If an operation backs more than one tool, "none" is not a legal constraint -
    # it would mean the tool can do everything its siblings were split apart for.
    by_op: dict[tuple[str, str], list[dict]] = {}
    for r in rows():
        by_op.setdefault((r["method"], r["path"]), []).append(r)
    for op, tools in by_op.items():
        if len(tools) > 1:
            for t in tools:
                assert t["constraint"] != "none", f"{t['tool']} shares {op} but constrains nothing"


def test_the_checker_script_passes():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check_boundaries.py")], capture_output=True)
    assert r.returncode == 0, r.stdout.decode() + r.stderr.decode()
