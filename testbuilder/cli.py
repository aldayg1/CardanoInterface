"""CLI for the test-builder graph.

Phase 1 (inventory → plan → draft → check → assess): generates drafts into
<repo>/tests/_review/ and REFUSES to continue — the driving agent must read
every draft (test-suite skill: the agent owns correctness, the model only
types).

Phase 2 (--complete): after the agent has read the drafts and recorded
approved.json (a JSON list of approved symbol names, possibly after hand
edits), this runs write → verify → record → report.

vLLM downtime: at startup the endpoint is health-checked; if it is not
serving, the run ANNOUNCES it and retries on the 3min/5min/10min schedule,
cancellable during every wait (Ctrl-C / SIGTERM). If the serve comes back
inside a window the run continues cleanly; if not, it aborts with a plain
statement and exit code 75 (EX_TEMPFAIL) — never a silent hang that looks
like work.

Usage:
  uv run python -m shared.testbuilder.cli REPO TARGET.py \
      [--symbol NAME]... --oracle "NAME=external oracle text"...
  uv run python -m shared.testbuilder.cli REPO TARGET.py --complete
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    # Invoked as a plain file (uv run python cli.py .. ../CardanoInterface.py)
    # from any directory: re-dispatch through the package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import testbuilder.cli as _pkg

    raise SystemExit(_pkg.main())

import argparse
import json
import signal
from datetime import UTC, datetime

from langgraph.graph import END, StateGraph

from . import knowledge
from .graph import (
    BuilderState,
    PlanRecord,
    VLLMUnavailable,
    _run_cmd,
    assess_node,
    check_node,
    draft_node,
    ensure_vllm_up,
    inventory_node,
    plan_node,
    record_node,
    report_node,
    validate_node,
    verify_node,
    write_node,
)


def _sigterm_to_cancel(signum: int, frame: object) -> None:
    """Convert SIGTERM into the same cancellable path as Ctrl-C so background
    runs announce their cancellation instead of dying silently mid-wait."""
    raise KeyboardInterrupt(f"SIGTERM ({signum}) received")


def _module_name(target: Path) -> str:
    return target.stem


def _refresh(args: argparse.Namespace, dry: bool = False) -> int:
    """Unattended final phase: staleness scan → delete+remake stale symbols
    (machine-validated) → verify → record → final report. The agent's job is
    AFTERWARDS: read last_report.md and validate against the standard."""
    from testbuilder import acquire
    registry = knowledge.get_oracles()
    changed = acquire.refresh_sources(registry)
    stale = knowledge.stale_symbols()
    stale_map = dict(stale)
    for symbol, change_reason in changed:
        if symbol not in stale_map:
            stale_map[symbol] = change_reason
    stale = list(stale_map.items())
    print(f"refresh: {len(registry)} registered symbol(s), "
          f"{len(changed)} source change(s) detected")
    if not stale:
        knowledge.record_metrics(stale=0, regenerated=0, tagged=0,
                                 remediated=0, defective=0, all_fresh=True)
        print("refresh: all fresh — nothing to do")
        return 0
    for symbol, reason in stale:
        print(f"  STALE: {symbol} — {reason}")
    if dry:
        print("dry-run: stopping after the scan")
        return 0

    def _regen_once(symbol: str, prior_failure: dict[str, str] | None = None
                    ) -> tuple[dict[str, object], list[str]]:
        state: BuilderState = {
            "repo_path": str(Path(args.repo).resolve()),
            "target_file": str(Path(args.target).resolve()),
            "module_name": Path(args.target).stem,
            "symbols": [symbol],
            "plans": [],
            "prior_failure": prior_failure or {},
            "errors": [],
        }
        # registry supplies the oracle in plan_node (no --oracle needed)
        g = _mk_graph(["inventory", "plan", "draft", "check", "validate",
                       "write", "verify", "record", "report"])
        result = g.invoke(state)
        return result, (result.get("errors") or [])

    report_lines: list[str] = [
        "# testbuilder refresh report", "",
        f"Date: {datetime.now(UTC).date().isoformat()}",
        "Mode: unattended (machine-validated, pending post-hoc agent review)",
        f"Stale symbols: {len(stale)}", "",
    ]
    generated = 0
    failed: list[tuple[str, str, str]] = []
    for symbol, reason in stale:
        print(f"── regenerating {symbol} ({reason})")
        try:
            result, errors = _regen_once(symbol)
        except VLLMUnavailable as down:
            report_lines += [f"## {symbol}", "", f"- ABORTED: {down}", ""]
            print(f"vLLM UNAVAILABLE: {down}", file=sys.stderr)
            return 75
        ok = bool((result.get("verify") or {}).get("pytest_ok"))
        verdict = "REGENERATED + VERIFIED" if (ok and not errors) else "FAILED"
        if ok and not errors:
            generated += 1
        else:
            tag_desc = "; ".join(
                t["description"][:300] for t in result.get("tags", []))
            failed.append((symbol, reason, tag_desc or "unknown failure"))
        report_lines += [
            f"## {symbol}", "",
            f"- Staleness reason: {reason}",
            f"- Verdict: {verdict}",
            f"- Check tags: {result.get('tags', [])}",
            f"- Validation flags: {result.get('validation_flags', {})}",
            f"- Verify: {result.get('verify', {})}",
            "", ""]
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)

    # Remediation policy (operator decree): a failed remake is not skipped —
    # it gets EXACTLY ONE more attempt at the end; if that fails too, the
    # symbol is branded DEFECTIVE, quarantined out of the composed suite, and
    # left visibly for a fast skill-driven fix.
    remediated = 0
    defective: list[tuple[str, str, Path | None]] = []
    for symbol, reason, tag_desc in failed:
        print(f"── re-making {symbol} (one fresh generation, failure context attached)")
        try:
            result, errors = _regen_once(symbol, prior_failure={symbol: tag_desc})
        except VLLMUnavailable as down:
            print(f"vLLM UNAVAILABLE: {down}", file=sys.stderr)
            return 75
        ok = bool((result.get("verify") or {}).get("pytest_ok"))
        if ok and not errors:
            remediated += 1
            generated += 1
            report_lines += [
                f"## {symbol} — remediation", "",
                "- Verdict: RE-MADE CLEANLY on the single fresh attempt",
                "", ""]
            continue
        # Still failing after the one retry: brand it DEFECTIVE and
        # quarantine it out of the composed suite.
        from testbuilder import concerns
        from testbuilder.graph import _compose_concern_file
        concern = concerns.get_concern(symbol)
        evidence = knowledge.mark_defective(
            symbol, concern, f"failed remake + failed remediation "
            f"(last reason: {reason})")
        dest = Path(args.repo) / "tests" / f"test_{concern}.py"
        dest.write_text(
            _compose_concern_file(concern, args.repo), encoding="utf-8")
        _run_cmd(["uv", "run", "ruff", "check", "--fix", str(dest)],
                 args.repo)
        defective.append((symbol, concern, evidence))
        report_lines += [
            f"## {symbol} — DEFECTIVE", "",
            f"- Failure description: {tag_desc[:400]}",
            "- Remake and its single remediation attempt BOTH failed.",
            f"- Quarantined to: {evidence}",
            "- Concern file recomposed WITHOUT it (suite stays green).",
            "- Fix with the test-suite skill, then re-register/refresh.", "",
            ""]

    report_path = Path(args.repo) / "testbuilder" / "kb" / "last_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    knowledge.record_metrics(
        stale=len(stale), regenerated=generated, tagged=len(failed),
        remediated=remediated, defective=len(defective),
        all_fresh=(len(stale) == 0))
    print(f"refresh complete: {generated}/{len(stale)} clean "
          f"({remediated} re-made on the single retry, "
          f"{len(defective)} branded DEFECTIVE)")
    print(f"report: {report_path}")
    return 0 if generated == len(stale) else 1


def _mk_graph(nodes: list[str]) -> StateGraph:
    available = {
        "inventory": inventory_node, "plan": plan_node, "draft": draft_node,
        "check": check_node, "assess": assess_node, "validate": validate_node,
        "write": write_node,
        "verify": verify_node, "record": record_node, "report": report_node,
    }
    g: StateGraph = StateGraph(BuilderState)
    for name in nodes:
        g.add_node(name, available[name])
    for a, b in zip(nodes, nodes[1:], strict=False):
        g.add_edge(a, b)
    g.add_edge(nodes[-1], END)
    g.set_entry_point(nodes[0])
    return g.compile()


def main() -> int:
    ap = argparse.ArgumentParser(prog="testbuilder")
    ap.add_argument("repo", help="target repository root")
    ap.add_argument("target", help="source file to build tests for")
    ap.add_argument("--symbol", action="append", default=[],
                    help="top-level function to target (repeatable; default: all)")
    ap.add_argument("--oracle", action="append", default=[],
                    help="'name=external oracle text' (repeatable, REQUIRED)")
    ap.add_argument("--complete", action="store_true",
                    help="phase 2: write approved drafts and verify")
    ap.add_argument("--notes", default="",
                    help="assessment notes recorded to the knowledge base (phase 2)")
    ap.add_argument("--refresh", action="store_true",
                    help="unattended final phase: remake every stale symbol, "
                         "machine-validate, write, verify, record, report")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --refresh: only print the staleness scan")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _sigterm_to_cancel)

    # A --dry-run staleness scan is pure DB/files: the model server is not
    # part of that contract and must not gate it.
    if not (args.refresh and args.dry_run):
        try:
            ensure_vllm_up()
        except VLLMUnavailable as down:
            print(f"vLLM UNAVAILABLE: {down}", file=sys.stderr)
            return 75  # EX_TEMPFAIL: temporary infrastructure failure

    if args.refresh:
        return _refresh(args, dry=args.dry_run)

    repo = Path(args.repo).resolve()
    target = Path(args.target).resolve()
    if not target.exists():
        print(f"target not found: {target}", file=sys.stderr)
        return 2

    state: BuilderState = {
        "repo_path": str(repo),
        "target_file": str(target),
        "module_name": _module_name(target),
        "symbols": args.symbol,
        "errors": [],
    }

    try:
        if args.complete:
            review = repo / "tests" / "_review"
            plans_file = review / "plans.json"
            if not plans_file.exists():
                print("phase 2 requires tests/_review/plans.json from phase 1",
                      file=sys.stderr)
                return 2
            saved = json.loads(plans_file.read_text(encoding="utf-8"))
            state.update(saved)  # plans + inventory + drafts + review_dir + rounds
            if args.notes:
                state["assessment_notes"] = args.notes
            # approved.json is enforced inside write_node — no bypass here.
            g = _mk_graph(["write", "verify", "record", "report"])
            result = g.invoke(state)
        else:
            plans: list[PlanRecord] = []
            for spec in args.oracle:
                if "=" not in spec:
                    print(f"--oracle must be 'name=text', got: {spec}",
                          file=sys.stderr)
                    return 2
                name, text = spec.split("=", 1)
                plans.append({"symbol": name.strip(), "layer": "L1",
                              "oracle": text.strip()})
            state["plans"] = plans
            g = _mk_graph(["inventory", "plan", "draft", "check", "assess"])
            result = g.invoke(state)
    except VLLMUnavailable as down:
        # Server died mid-run: the announced retries already ran and were
        # either exhausted or cancelled. Clean stop, nothing half-written.
        print(f"vLLM UNAVAILABLE: {down}", file=sys.stderr)
        return 75

    errors = result.get("errors") or []
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    if not args.complete:
        drafts = result.get("drafts") or []
        if drafts:
            # plans.json lands even when phase 1 had errors: the assess gate
            # exists precisely to review/refine/resolve what the nodes
            # produced — including drafts the refine loop could not clean.
            review = Path(result.get("review_dir")
                          or Path(repo) / "tests" / "_review")
            review.mkdir(parents=True, exist_ok=True)
            (review / "plans.json").write_text(
                json.dumps({k: result.get(k) for k in
                            ("repo_path", "target_file", "module_name", "inventory",
                             "plans", "drafts", "review_dir", "tags",
                             "errors")}, indent=2),
                encoding="utf-8")
            print(f"\nASSESSMENT REQUIRED — read every draft in {review},")
            if errors:
                print(f"({len(errors)} phase-1 error(s) recorded above — resolve "
                      "them in the drafts before approving)")
            print("then record approvals:  "
                  f"echo '[\"{drafts[0]['symbol']}\"]' > {review}/approved.json")
            print("then run again with --complete (add --notes \"...\" to record "
                  "assessment notes in the knowledge base)")
            return 1 if errors else 0
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
