"""Test-knowledge base — repo-local, committed, self-contained.

The provenance store for generated tests: run reports (OKF frontmatter, one
per symbol per build), accumulated model-failure lessons, and the approved
per-symbol test sources that compose the concern test files in tests/.
Merge-not-append: the index keeps one entry per symbol; superseded report
files remain on disk as history.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent / "kb"
RUNS_DIR = KB_DIR / "runs"
APPROVED_DIR = KB_DIR / "approved"
INDEX_FILE = KB_DIR / "index.json"
LESSONS_FILE = KB_DIR / "lessons.md"


def _load_index() -> list[dict[str, str]]:
    if INDEX_FILE.exists():
        return list(json.loads(INDEX_FILE.read_text(encoding="utf-8")))
    return []


def _save_index(entries: list[dict[str, str]]) -> None:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def record_lesson(text: str) -> None:
    """Append a durable model-failure lesson (one line each so prompts can
    carry them cheaply). Lessons only accumulate."""
    KB_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).date().isoformat()
    with LESSONS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"- {date} {text.strip()}\n")


def lessons(limit: int = 8) -> str:
    """Recent lessons, formatted for a drafting prompt."""
    if not LESSONS_FILE.exists():
        return "(none recorded yet)"
    lines = [ln for ln in LESSONS_FILE.read_text(encoding="utf-8").splitlines()
             if ln.strip()][-limit:]
    return "\n".join(lines) if lines else "(none recorded yet)"


def seed_lessons() -> None:
    """Seed a fresh clone's lessons file with the founding failure modes, so
    contributors' first runs already respect them."""
    if LESSONS_FILE.exists():
        return
    for lesson in (
        "Binary test material MUST use bytes.fromhex: small models write hex "
        "strings as bytes literals (b-quotes = ASCII text, double length, "
        "wrong bytes).",
        "Never assert exact error-message text unless the oracle documents it: "
        "message strings are implementation detail. Assert exception type only.",
        "Library calls must match the API grounding (signatures introspected "
        "from the installed venv), never model memory.",
        "Expected CBOR primitives must be pure python (ints/lists/bytes), not "
        "library constructor objects; prefer documented byte-form assertions.",
        "Rule-derived boundaries must respect domain constraints (a signature "
        "threshold of 0 is never a valid script state; floor bounded at 1).",
    ):
        record_lesson(lesson)


def index_entries() -> list[dict[str, str]]:
    """Public read access to the knowledge-base index."""
    return _load_index()


def query_runs(symbol: str) -> list[dict[str, str]]:
    return [e for e in _load_index() if e.get("symbol") == symbol]


def compose_dossier(symbol: str) -> str:
    """Prior knowledge for a symbol, for a drafting prompt."""
    runs = query_runs(symbol)
    if not runs:
        return "none on record"
    parts: list[str] = []
    for entry in runs:
        path = Path(entry["file"])
        if path.exists():
            parts.append(f"--- prior run ({entry.get('date', '?')}, "
                         f"verify: {entry.get('verify', '?')}) ---\n"
                         + path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts) if parts else "none on record"


def record_run(*, symbol: str, oracle: str, model: str, rounds: int,
               verify: str, assessment_notes: str = "", concern: str = "misc") -> Path:
    """Write a run report and upsert the index (one entry per symbol)."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    date = now.date().isoformat()
    base = f"{symbol}_{date}"
    path = RUNS_DIR / f"{base}.md"
    n = 2
    while path.exists():
        path = RUNS_DIR / f"{base}-{n}.md"
        n += 1

    body = (
        "---\n"
        "type: Test Run Report\n"
        f"title: testbuilder — {symbol} ({date})\n"
        f"timestamp: '{now.isoformat(timespec='seconds')}'\n"
        "resource:\n"
        f"- CardanoInterface.py#{symbol}\n"
        "tags:\n"
        "- testing\n"
        f"- {concern}\n"
        f"- {symbol.lower()}\n"
        "status: researched\n"
        "okf_version: '1.0'\n"
        "---\n\n"
        f"# testbuilder run — {symbol}\n\n"
        f"- **Concern:** {concern}\n"
        f"- **Model:** {model}\n"
        f"- **Refinement rounds to mechanical-clean:** {rounds}\n"
        f"- **Verification:** {verify}\n\n"
        "## Oracle\n\n"
        f"{oracle}\n\n"
        "## Assessment notes\n\n"
        f"{assessment_notes or '(none)'}\n"
    )
    path.write_text(body, encoding="utf-8")

    index = [e for e in _load_index() if e.get("symbol") != symbol]
    index.append({
        "file": str(path.relative_to(KB_DIR.parent.parent)),
        "symbol": symbol, "date": date,
        "verify": verify, "rounds": str(rounds), "concern": concern,
        "status": "ok" if verify == "pass" else "failed",
    })
    _save_index(index)
    return path


def save_approved(concern: str, symbol: str, code: str) -> Path:
    """Store the reviewed, approved source of one symbol's tests (the KB copy
    that concern files are composed from)."""
    out = APPROVED_DIR / concern / f"{symbol}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code, encoding="utf-8")
    return out


def approved_symbols(concern: str) -> list[tuple[str, Path]]:
    """(symbol, path) pairs of every approved source for a concern, sorted."""
    d = APPROVED_DIR / concern
    if not d.exists():
        return []
    return sorted((p.stem, p) for p in d.glob("*.py"))


# ── Oracle registry: the DB of external values ────────────────────────────
# Every symbol's oracle text lives here with its source citation and the hash
# the approved tests are stamped with. External premise changed → update the
# entry (new captured date) → the hash changes → dependent tests read as
# stale → the refresh sweep deletes and remakes them. This is how the suite
# stays fresh without anyone hand-auditing every test.

ORACLES_FILE = KB_DIR / "oracles.json"


def oracle_hash(symbol: str, oracle: str) -> str:
    import hashlib
    return hashlib.sha256(f"{symbol}\n{oracle}".encode()).hexdigest()[:12]


def get_oracles() -> dict[str, dict[str, str]]:
    if ORACLES_FILE.exists():
        return dict(json.loads(ORACLES_FILE.read_text(encoding="utf-8")))
    return {}


def get_oracle(symbol: str) -> dict[str, str] | None:
    return get_oracles().get(symbol)


def save_oracle(symbol: str, oracle: str, source: str,
                captured: str | None = None,
                source_ref: dict[str, str] | None = None) -> dict[str, str]:
    """Register (or update) a premise. Idempotent for identical text: the
    original capture date is preserved, because freshness belongs to the
    premise's content, not to how often it is re-asserted."""
    import datetime as dt
    existing = get_oracles().get(symbol)
    if existing and existing.get("oracle") == oracle:
        return existing
    entry = {
        "oracle": oracle,
        "source": source,
        "captured": captured or dt.date.today().isoformat(),
        "hash": oracle_hash(symbol, oracle),
    }
    if source_ref:
        entry["source_ref"] = source_ref
        if existing:
            entry["source_hash"] = existing.get("source_hash", "")
    oracles = get_oracles()
    oracles[symbol] = entry
    KB_DIR.mkdir(parents=True, exist_ok=True)
    ORACLES_FILE.write_text(json.dumps(oracles, indent=2) + "\n", encoding="utf-8")
    return entry


METRICS_FILE = KB_DIR / "metrics.jsonl"


def record_metrics(*, stale: int, regenerated: int, tagged: int,
                   remediated: int, defective: int, all_fresh: bool) -> None:
    """One JSON line per refresh run — the correction-rate trend the operator
    watches. Append-only; the dashboard renders the last entries."""
    KB_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "date": datetime.now(UTC).date().isoformat(),
        "stale": stale, "regenerated": regenerated, "tagged": tagged,
        "remediated": remediated, "defective": defective,
        "all_fresh": all_fresh,
    }) + "\n"
    with METRICS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)


def metrics(limit: int = 6) -> list[dict[str, object]]:
    """Most recent refresh trend entries, oldest first."""
    if not METRICS_FILE.exists():
        return []
    lines = METRICS_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def stale_symbols() -> list[tuple[str, str]]:
    """(symbol, reason) for every registry symbol whose test is missing,
    failed its last verification, or was built from a different oracle hash."""
    reasons: list[tuple[str, str]] = []
    index = {e.get("symbol"): e for e in _load_index()}
    from testbuilder import concerns  # local import: concerns has no deps
    for symbol, entry in get_oracles().items():
        h = entry["hash"]
        concern = concerns.get_concern(symbol)
        approved = APPROVED_DIR / concern / f"{symbol}.py"
        if not approved.exists():
            reasons.append((symbol, "no approved source"))
            continue
        if f"Oracle-Hash: {h}" not in approved.read_text(encoding="utf-8"):
            reasons.append((symbol, "built from an older oracle (hash mismatch)"))
            continue
        last = index.get(symbol)
        if last and last.get("verify") != "pass":
            reasons.append((symbol, f"last verification: {last.get('verify')}"))
    return reasons


def stamp_approved(concern: str, symbol: str, oracle_hash: str) -> None:
    """Stamp an approved source with the oracle hash it was built/assessed
    against. The stamp is the freshness token the sweep checks."""
    approved = APPROVED_DIR / concern / f"{symbol}.py"
    if not approved.exists():
        return
    text = approved.read_text(encoding="utf-8")
    stamp = f"# Oracle-Hash: {oracle_hash}"
    if stamp in text:
        return
    approved.write_text(f"{stamp}\n{text}", encoding="utf-8")


# ── Defective branding: failed remakes are quarantined, never hidden ──────

DEFECTIVE_DIR = KB_DIR / "defective"


def mark_defective(symbol: str, concern: str, reason: str) -> Path:
    """Brand a symbol's test defective: the approved source moves out of the
    composition set (the concern file is regenerated without it), and the
    index entry carries the defect visibly until an agent fixes it."""
    src = APPROVED_DIR / concern / f"{symbol}.py"
    dest_dir = DEFECTIVE_DIR / concern
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{symbol}.py"
    if src.exists():
        src.rename(dest)
    index = _load_index()
    for entry in index:
        if entry.get("symbol") == symbol:
            entry["status"] = "defective"
            entry["reason"] = reason
    _save_index(index)
    return dest


def defective_symbols() -> list[tuple[str, str]]:
    """(symbol, reason) pairs currently branded defective."""
    return [(e.get("symbol", "?"), e.get("reason", ""))
            for e in _load_index() if e.get("status") == "defective"]
