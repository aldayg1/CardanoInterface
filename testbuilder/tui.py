"""Testbuilder TUI — the terminal lifecycle manager for the test suite.

This is NOT the wallet application's TUI and shares nothing with it: it is a
standalone operator console for the testbuilder life cycle, rendered with
Rich and prompt_toolkit (both dev-available), run from the repo root:

    uv run python -m testbuilder.tui

Life cycle managed here:
  registered (oracle in DB) → stale? → refresh remakes → checked →
  assessment queue → approved → written+verified → fresh
  ...with tagged failures and DEFECTIVE brands visible at every step.

Everything the dashboard shows, plus the actions: approve drafts, run the
write phase, register new premises, launch the unattended refresh.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from testbuilder import knowledge
from testbuilder.concerns import get_concern

REPO = Path(__file__).resolve().parent.parent
REVIEW_DIR = REPO / "tests" / "_review"

console = Console()


def _run(command: list[str]) -> None:
    """Run a builder command in the foreground, streaming its output."""
    console.rule("builder running")
    subprocess.run(command, cwd=REPO)
    console.rule("builder finished")


def _status_board() -> None:
    oracles = knowledge.get_oracles()
    index = {e.get("symbol"): e for e in knowledge.index_entries()}
    stale = dict(knowledge.stale_symbols())
    defective = dict(knowledge.defective_symbols())
    table = Table(title="Registry & suite state")
    for col in ("Symbol", "Concern", "Captured", "Verify", "Status"):
        table.add_column(col)
    for symbol in sorted(oracles):
        entry = index.get(symbol, {})
        verify = str(entry.get("verify", "-"))
        if symbol in defective:
            status = f"DEFECTIVE ({defective[symbol][:40]})"
        elif symbol in stale:
            status = f"STALE ({stale[symbol][:40]})"
        elif entry.get("status") == "ok":
            status = "fresh"
        else:
            status = "in suite"
        table.add_row(symbol, get_concern(symbol),
                      str(entry.get("date", entry.get("captured", "-"))),
                      verify, status)
    console.print(table)
    console.print(
        f"[dim]registered: {len(oracles)} · defects: {len(defective)} · "
        f"stale: {len(stale)}[/dim]")


def _review_queue() -> None:
    console.print(Panel.fit(json.dumps(_review_state(), indent=2),
                            title="Assessment queue (tests/_review)"))


def _review_state() -> dict[str, object]:
    drafts = sorted(p.stem.removeprefix("draft_")
                    for p in REVIEW_DIR.glob("draft_*.py"))
    approved_file = REVIEW_DIR / "approved.json"
    approved: list[str] = []
    if approved_file.exists():
        try:
            approved = json.loads(approved_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            approved = ["<INVALID approved.json>"]
    return {"drafts": drafts, "approved": approved,
            "awaiting_assessment": bool(drafts) and not approved}


def _metrics_trend() -> None:
    entries = knowledge.metrics(limit=10)
    if not entries:
        console.print("[dim]no refresh runs recorded yet[/dim]")
        return
    table = Table(title="Refresh trend (better every time = falling defects)")
    for col in ("Date", "Stale", "Regenerated", "Tagged", "Remediated",
                "Defective", "All fresh"):
        table.add_column(col)
    for m in entries:
        table.add_row(str(m.get("date")), str(m.get("stale")),
                      str(m.get("regenerated")), str(m.get("tagged")),
                      str(m.get("remediated")), str(m.get("defective")),
                      str(m.get("all_fresh")))
    console.print(table)


def _approve_flow(session: PromptSession) -> None:
    state = _review_state()
    drafts = state["drafts"]
    if not drafts:
        console.print("[yellow]no drafts in the assessment queue[/yellow]")
        return
    console.print("drafts:", ", ".join(drafts))
    raw = session.prompt(
        "approve which? (comma-separated symbols, 'all', or empty to cancel): ")
    raw = raw.strip()
    if not raw:
        console.print("cancelled")
        return
    chosen = drafts if raw == "all" else [
        d.strip() for d in raw.split(",") if d.strip() in drafts]
    unknown = [d for d in (s.strip() for s in raw.split(",")) if d not in drafts]
    if unknown and raw != "all":
        console.print(f"[red]not drafts: {unknown}[/red]")
        return
    (REVIEW_DIR / "approved.json").write_text(
        json.dumps(chosen, indent=2) + "\n", encoding="utf-8")
    console.print(f"approved: {chosen}")
    notes = session.prompt("assessment notes for the KB (optional): ").strip()
    _run(["uv", "run", "python", "-m", "testbuilder.cli", ".",
          "CardanoInterface.py", "--complete", *(
              ["--notes", notes] if notes else [])])


def _register_oracle_flow(session: PromptSession) -> None:
    symbol = session.prompt("symbol (function name): ").strip()
    if not symbol:
        return
    source = session.prompt("source citation (spec / tool / record): ").strip()
    console.print("oracle text — finish with an empty line:")
    lines: list[str] = []
    while True:
        try:
            line = session.prompt("")
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            break
        lines.append(line)
    oracle = "\n".join(lines).strip()
    if not oracle:
        console.print("[yellow]empty oracle — cancelled[/yellow]")
        return
    entry = knowledge.save_oracle(symbol, oracle,
                                  source=source or "(uncited)", )
    console.print(f"[green]registered {symbol} (hash {entry['hash']})[/green]")


def main() -> int:
    if "--selfcheck" in sys.argv:
        _status_board()
        _review_queue()
        _metrics_trend()
        return 0

    session = PromptSession()
    actions = {
        "status": "status board (registry, staleness, defects)",
        "queue": "assessment queue state",
        "trend": "refresh trend metrics",
        "lessons": "model-failure lessons",
        "approve": "review drafts → approve → run the write phase",
        "oracle": "register a new premise in the oracle DB",
        "refresh": "unattended refresh (remake stale, verify, report)",
        "help": "this list",
        "quit": "exit",
    }
    console.print(Panel.fit(
        "testbuilder lifecycle console — CardanoInterface\n"
        + "\n".join(f"  {k:8} {v}" for k, v in actions.items()),
        title="TUI"))
    while True:
        try:
            choice = session.prompt("testbuilder> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("bye")
            return 0
        if choice in ("q", "quit", "exit"):
            return 0
        if choice in ("", "help", "?"):
            for key, desc in actions.items():
                console.print(f"  [bold]{key:8}[/bold] {desc}")
        elif choice == "status":
            _status_board()
        elif choice == "queue":
            _review_queue()
        elif choice == "trend":
            _metrics_trend()
        elif choice == "lessons":
            console.print(knowledge.lessons(limit=12))
        elif choice == "approve":
            _approve_flow(session)
        elif choice == "oracle":
            _register_oracle_flow(session)
        elif choice == "refresh":
            _run(["uv", "run", "python", "-m", "testbuilder.cli", ".",
                  "CardanoInterface.py", "--refresh"])
        else:
            console.print(f"[red]unknown: {choice}[/red] — try 'help'")


if __name__ == "__main__":
    raise SystemExit(main())
