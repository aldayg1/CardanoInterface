"""Test-builder: LangGraph-enforced test generation pipeline.

Canonical location: ~/toolkit/shared/testbuilder (the operator's dev+admin
stack). A validated mirror is later copied into contributor repos; until then
this is the only copy.

Enforcement model (the operator's test-suite skill, mechanically applied):
  inventory → plan → draft → check → assess ⟶ INTERRUPT ⟶ write → verify
            → record → report
  - plan FAILS unless every target symbol carries an EXTERNAL oracle
    (spec / reference tool / captured response / documented past failure).
    No oracle, no draft — expectations may not come from reading the code.
  - draft is MULTI-CALL (the model has context — use it): analyze (thinking)
    → draft → self-critique → revise. Prior knowledge is pulled from the
    test-knowledge base (knowledge.compose_dossier) like a research dossier.
  - check is a DETERMINISTIC gate (no model feedback): the draft is hit
    with ruff --fix + ruff check + pytest in the target repo; failures feed
    back into a revision call. Drafts must arrive mechanically clean — the
    human/agent assess gate verifies ORACLE FIDELITY, not syntax.
  - the graph INTERRUPTS before write: drafts land in tests/_review/ and
    nothing enters the suite until the driving agent has READ them and
    recorded approval (approved.json). Responsibility stays with the agent.
  - verify runs pytest + ruff on the written files and fails closed.
  - record writes a run report to the test-knowledge base (OKF frontmatter,
    index upsert per repo+symbol), so the next build of this symbol starts
    from what was already learned.

Every written test file carries a provenance header (oracle, generator, date).
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from . import concerns, knowledge

VLLM_HOST = os.getenv("VLLM_HOST", "http://localhost:8010")
VLLM_MODEL = os.getenv("TESTBUILDER_MODEL", "Qwen/Qwen3-8B-AWQ")
MAX_REFINE_ROUNDS = 3

THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
THINK_UNTERMINATED = re.compile(r"<think>.*\Z", re.DOTALL)
CODE_BLOCK = re.compile(r"```python\s*(.*?)```", re.DOTALL)

# vLLM downtime policy (operator decree 2026-09-26): when the serve is not
# answering, ANNOUNCE it and retry after 3min, then 5min, then 10min —
# cancellable during every wait — instead of hanging silently and making the
# operator believe tests are being generated when nothing is. Each wait is
# cancellable (Ctrl-C / SIGTERM); if the serve returns inside a wait window
# the run continues cleanly with the same request. Total budget: 18 min.
RETRY_SCHEDULE_SECONDS: tuple[int, ...] = tuple(
    int(s) for s in
    os.getenv("TESTBUILDER_RETRY_SCHEDULE", "180,300,600").split(",")
)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class VLLMUnavailable(RuntimeError):
    """The vLLM endpoint stayed down through the whole retry schedule, or the
    operator cancelled a wait. Always the operator's visible answer — never a
    silent hang."""


def _wait_or_cancel(seconds: int, message: str) -> None:
    """Sleep out one retry delay, cancellable. Ctrl-C or SIGTERM during the
    wait raises VLLMUnavailable with the cancel stated plainly."""
    print(f"[vllm] {message} retrying in {seconds}s "
          f"(cancel: Ctrl-C)", flush=True)
    try:
        time.sleep(seconds)
    except KeyboardInterrupt as cancel:
        raise VLLMUnavailable(
            f"cancelled by operator while waiting for vLLM: {message}") from cancel


def _vllm_post(payload: dict[str, object]) -> str:
    """One request with the announced downtime retry schedule around it."""
    body = json.dumps(payload).encode()
    delays = (0,) + RETRY_SCHEDULE_SECONDS
    last_error: str = "unknown"
    for delay in delays:
        if delay:
            _wait_or_cancel(delay, f"vLLM not serving ({last_error}). ")
        try:
            req = urllib.request.Request(
                f"{VLLM_HOST}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                result = json.load(resp)
            text: str = result["choices"][0]["message"]["content"]
            return THINK_UNTERMINATED.sub("", THINK.sub("", text))
        except urllib.error.HTTPError as http_err:
            if http_err.code in RETRYABLE_STATUS:
                last_error = f"HTTP {http_err.code}"
                continue
            raise VLLMUnavailable(
                f"vLLM rejected the request (HTTP {http_err.code}: "
                f"{http_err.reason}) — model name or payload problem, "
                "not downtime") from http_err
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as down:
            last_error = type(down).__name__
            continue
    raise VLLMUnavailable(
        f"vLLM at {VLLM_HOST} stayed down through the full retry schedule "
        f"(last error: {last_error}) — run aborted cleanly; nothing was "
        "written. Rerun the same command once the serve is back.")


def ensure_vllm_up() -> None:
    """Startup health check: know IMMEDIATELY that the serve is down instead
    of discovering it twenty minutes into a run. Same schedule, same
    cancellability."""
    try:
        with urllib.request.urlopen(f"{VLLM_HOST}/v1/models", timeout=5) as resp:
            if resp.status == 200:
                return
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ConnectionError, OSError):
        pass
    delays = RETRY_SCHEDULE_SECONDS
    last_error = "no response"
    for attempt, delay in enumerate(delays, 1):
        _wait_or_cancel(delay, f"vLLM at {VLLM_HOST} is not serving "
                               f"({last_error}). Attempt {attempt}/{len(delays)}.")
        try:
            with urllib.request.urlopen(f"{VLLM_HOST}/v1/models", timeout=5) as resp:
                if resp.status == 200:
                    print("[vllm] serve is back — continuing.", flush=True)
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                ConnectionError, OSError) as still_down:
            last_error = type(still_down).__name__
    raise VLLMUnavailable(
        f"vLLM at {VLLM_HOST} is not serving and did not come back within the "
        "retry schedule — run aborted cleanly. Rerun the same command once the "
        "serve is up.")

RULES = """Hard rules — violating any makes the test WRONG:
1. Probe the REAL function; never mock or stub the unit under test.
2. Every expected value must be justifiable from the ORACLE (a spec, reference-
   tool output, captured real response, or documented past failure) — never
   from the code's current behavior.
3. Never assert exact error-message text unless the ORACLE documents it —
   message strings are implementation detail. Assert the exception TYPE only
   (pytest.raises(ValueError)), or a short stable fragment the oracle states.
4. Binary material is built with bytes.fromhex("…") — a bytes literal like
   b"96f9…" is ASCII TEXT (twice the length, wrong bytes), a proven failure.
5. Hermetic: no network, no daemons, no real user data; tmp_path for files.
6. One behavior per test; test names state the behavior accurately.
7. Do NOT write provenance headers — the pipeline prepends the authentic one.
8. Import the unit from the target module; plain pytest; import pytest only if
   actually used (e.g. pytest.raises). The file must run with `uv run pytest`
   alone, from the repo root.
9. Use only the library APIs shown in the API GROUNDING notes (real
   signatures from the installed package) — never guessed method names."""


class SymbolRecord(TypedDict):
    name: str
    lineno: int
    source: str


class PlanRecord(TypedDict):
    symbol: str
    layer: str
    oracle: str


class DraftRecord(TypedDict):
    symbol: str
    code: str


class BuilderState(TypedDict, total=False):
    repo_path: str
    target_file: str
    module_name: str
    symbols: list[str]
    inventory: list[SymbolRecord]
    plans: list[PlanRecord]
    drafts: list[DraftRecord]
    groundings: dict[str, str]
    tags: list[dict[str, str]]
    prior_failure: dict[str, str]
    intel: dict[str, dict[str, object]]
    review_dir: str
    approved: list[str]
    written: list[str]
    verify: dict[str, object]
    assessment_notes: str
    validation_flags: dict[str, list[str]]
    errors: list[str]


def _vllm_chat(prompt: str, max_tokens: int = 8192, thinking: bool = True) -> str:
    payload: dict[str, object] = {
        "model": VLLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if not thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return _vllm_post(payload)


def _provenance_header(oracle: str,
                       ohash: str | None = None,
                       review_note: str = "reviewed by the driving agent before write") -> str:
    """Wrapped provenance header — every line ≤96 chars (ruff E501 at 100).
    The Oracle-Hash stamp is the freshness token the refresh sweep checks."""
    import textwrap
    lines = ["#"]
    paras = []
    if ohash:
        paras.append(f"Oracle-Hash: {ohash}")
    paras.append(f"Oracle: {oracle}")
    paras.append(f"Generator: {VLLM_MODEL}, {review_note}")
    paras.append(f"Date: {datetime.now(UTC).date().isoformat()}")
    for para in paras:
        wrapped = textwrap.wrap(para, width=95, subsequent_indent="    ",
                                break_long_words=True, break_on_hyphens=False)
        lines.extend(f"# {w}" for w in wrapped)
        lines.append("#")
    return "\n".join(lines) + "\n"


def _repo_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)  # repo venv must win, not the caller's
    return env


def _api_grounding(state: BuilderState, rec: SymbolRecord) -> str:
    """Real API facts for the types the function touches, introspected from
    the REPO's installed environment — the model never guesses method names.
    Best-effort: on any failure returns '' and the prompt just omits it."""
    probe = """
import ast, inspect
mod = __import__(__MODULE__)
tree = ast.parse(open(__TARGET__, encoding='utf-8').read())
names = set()
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == __SYMBOL__:
        for arg in [node.returns] + [a.annotation for a in node.args.args]:
            for sub in ast.walk(arg or ast.parse('None')):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
lines = []
for name in sorted(names):
    obj = getattr(mod, name, None)
    if obj is None:
        try:
            import pycardano
            obj = getattr(pycardano, name, None)
        except Exception:
            obj = None
    if obj is None:
        continue
    lines.append(name + ': ' + str(obj)[:160])
    for meth in ('to_cbor', 'to_primitive', 'from_primitive', 'encode'):
        m = getattr(obj, meth, None)
        if m is not None:
            try:
                lines.append('  .' + meth + str(inspect.signature(m))
                             + '  # ' + (m.__doc__ or '').strip()[:100])
            except (TypeError, ValueError):
                lines.append('  .' + meth + '(...)')
print(chr(10).join(lines[:40]))
""".replace("__MODULE__", repr(state["module_name"])).replace(
        "__TARGET__", repr(str(state["target_file"]))).replace(
        "__SYMBOL__", repr(rec["name"]))
    try:
        proc = _run_cmd(["uv", "run", "python", "-c", probe], state["repo_path"])
        return proc.stdout.strip()[:2500] if proc.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError) as probe_err:
        # Grounding is best-effort by design: a probe that cannot run must
        # never block drafting — the prompt just omits it.
        print(f"api-grounding probe failed: {probe_err}", flush=True)
        return ""


def string_parts(node: ast.AST) -> list[str]:
    """String literals reachable in an expression, in source order (implicit
    concatenation, JoinedStr literal chunks)."""
    parts: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            parts.extend(string_parts(v))
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts.extend(string_parts(node.left))
        parts.extend(string_parts(node.right))
    return parts


def inventory_node(state: BuilderState) -> dict[str, object]:
    tree = ast.parse(Path(state["target_file"]).read_text(encoding="utf-8"))
    lines = Path(state["target_file"]).read_text(encoding="utf-8").splitlines()
    wanted = state.get("symbols") or []
    records: list[SymbolRecord] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if wanted and node.name not in wanted:
            continue
        records.append({
            "name": node.name,
            "lineno": node.lineno,
            "source": "\n".join(lines[node.lineno - 1:node.end_lineno]),
        })
    missing = sorted(set(wanted) - {r["name"] for r in records})
    errors = ([f"symbol(s) not found at top level of "
               f"{state['target_file']}: {missing}"] if missing else [])
    return {"inventory": records, "errors": errors}


def plan_node(state: BuilderState) -> dict[str, object]:
    plans: list[PlanRecord] = []
    errors: list[str] = list(state.get("errors") or [])
    for rec in state["inventory"]:
        plan = next((p for p in state.get("plans", []) if p["symbol"] == rec["name"]), None)
        if not plan or not plan.get("oracle", "").strip():
            # Registry fallback: a registered premise is a supplied oracle —
            # the DB is where external values live between runs.
            registered = knowledge.get_oracle(rec["name"])
            if registered:
                plan = {"symbol": rec["name"], "layer": "L1",
                        "oracle": registered["oracle"],
                        "oracle_hash": registered["hash"]}
        if not plan or not plan.get("oracle", "").strip():
            errors.append(
                f"{rec['name']}: NO EXTERNAL ORACLE — refusing to draft. "
                "Provide --oracle 'name=spec / reference-tool output / captured "
                "response / documented past failure', or register the premise "
                "in testbuilder/kb/oracles.json."
            )
            continue
        # Enforcement: the DB is the single home of premises. Every oracle
        # this builder uses is registered here at plan time (idempotent), so
        # the freshness sweep always covers everything ever built. Off-the-
        # books premises are impossible by construction.
        entry = knowledge.save_oracle(
            rec["name"], plan["oracle"],
            source=plan.get("oracle_source", "driver-supplied at plan time"))
        plan["oracle_hash"] = entry["hash"]
        plans.append(plan)
    return {"plans": plans, "errors": errors}


def draft_node(state: BuilderState) -> dict[str, object]:
    """Multi-call drafting: the model has context, so use it — analyze with
    full reasoning, then draft, then self-critique, then revise. Prior runs
    for this symbol are pulled from the test-knowledge base first."""
    errors: list[str] = list(state.get("errors") or [])
    drafts: list[DraftRecord] = []
    lessons = knowledge.lessons()
    groundings: dict[str, str] = {}
    prior_failures = state.get("prior_failure") or {}
    intel: dict[str, dict[str, object]] = {}
    for plan in state["plans"]:
        src = next(r["source"] for r in state["inventory"] if r["name"] == plan["symbol"])
        dossier = knowledge.compose_dossier(plan["symbol"])
        grounding = _api_grounding(
            state, next(r for r in state["inventory"] if r["name"] == plan["symbol"]))
        groundings[plan["symbol"]] = grounding
        intel.setdefault(plan["symbol"], {
            "lessons_lines": len(lessons.splitlines()),
            "dossier": ("present" if dossier != "none on record" else "none"),
            "grounding_chars": len(grounding),
        })
        prior = prior_failures.get(plan["symbol"])
        prior_note = (
            f"A PRIOR ATTEMPT at this symbol was TAGGED FAILED with this "
            f"description (avoid repeating the mistake; never adopt actual "
            f"output as an expected value):\n{prior}\n"
        ) if prior else ""

        analysis = _vllm_chat(
            "You are designing tests for a function. Do NOT write code yet.\n\n"
            f"{RULES}\n\n"
            f"ORACLE (every expected value must be justified by this): "
            f"{plan['oracle']}\n\n"
            f"Lessons from prior runs (mandatory to respect):\n{lessons}\n\n"
            + prior_note +
            f"Prior knowledge from the test-knowledge base for this symbol:\n"
            f"{dossier}\n\n"
            f"API GROUNDING (real signatures from the installed package — "
            f"use nothing else):\n{grounding or '(none needed — plain types)'}\n\n"
            f"Function source:\n```python\n{src}\n```\n\n"
            "Enumerate the behaviors, edge cases, and error paths worth testing. "
            "For each: test name, inputs, expected value, and HOW THE ORACLE "
            "justifies that expected value. Two kinds of case, both required:\n"
            "  (a) oracle-stated: the value appears in the oracle text;\n"
            "  (b) rule-derived: the value follows from a rule clause the oracle "
            "states (mark it 'derived: <clause>') — boundaries (zero, one, max, "
            "scaling), rounding, and documented error paths.\n"
            "Reject any case you cannot tie to the oracle. "
            "Output a numbered test plan only."
        )

        # Emission with self-healing: the analysis already happened, so if the
        # model re-derives until the budget dies (finish=length → unterminated
        # <think>, no code block), retry with escalating remediation instead
        # of failing the symbol.
        code: str | None = None
        remediations = (
            "Write the complete pytest file implementing exactly that analysis "
            "(including the rule-derived cases). Output ONLY a ```python code block.",
            "Do not re-derive anything. Output ONLY the ```python code block "
            "containing the final test file, starting with the imports.",
            "REMEDIATION: no reasoning, no prose — output the final complete "
            "test file as one ```python code block, nothing else.",
        )
        for attempt, note in enumerate(remediations):
            raw = _vllm_chat(
                f"You write pytest test files under a strict standard.\n\n{RULES}\n\n"
                f"Target module: {state['module_name']}\n"
                f"ORACLE: {plan['oracle']}\n\n"
                f"API GROUNDING (use nothing else):\n{grounding or '(plain types)'}\n\n"
                f"Approved analysis:\n{analysis}\n\n"
                f"Function source:\n```python\n{src}\n```\n\n{note}",
                thinking=(attempt < len(remediations) - 1),
            )
            m = CODE_BLOCK.search(raw)
            if m:
                code = m.group(1).split("```")[0]
                break
        if code is None:
            errors.append(f"{plan['symbol']}: draft call produced no python code "
                          "block after remediated retries")
            continue

        critique = _vllm_chat(
            "Review this pytest draft against the checklist. Be adversarial.\n\n"
            f"{RULES}\n\n"
            f"ORACLE: {plan['oracle']}\n\n"
            f"API GROUNDING (calls must match these signatures):\n"
            f"{grounding or '(plain types)'}\n\n"
            f"Approved analysis:\n{analysis}\n\n"
            f"Draft:\n```python\n{code}\n```\n\n"
            "Check: (a) every expected value traceable to the oracle (oracle-"
            "stated or 'derived: <clause>'), (b) every analysis case covered, "
            "(c) no provenance headers, (d) imports all used, names accurate, "
            "(e) hermetic, (f) NO invented error-message strings, (g) binary "
            "material via bytes.fromhex not bytes literals, (h) every library "
            "call matches the API grounding. Output a numbered list of "
            "REQUIRED changes, or exactly the word CLEAN."
        )

        if "CLEAN" not in critique:
            revised = _vllm_chat(
                f"{RULES}\n\nORACLE: {plan['oracle']}\n\n"
                f"Draft:\n```python\n{code}\n```\n\n"
                f"Required changes from review:\n{critique}\n\n"
                "Output the corrected complete file as a ```python block."
            )
            m2 = CODE_BLOCK.search(revised)
            if m2:
                code = m2.group(1).split("```")[0]
            else:
                errors.append(f"{plan['symbol']}: revise call produced no code block; "
                              "keeping pre-revision draft")

        drafts.append({"symbol": plan["symbol"],
                       "code": _provenance_header(plan["oracle"],
                                                  plan.get("oracle_hash"))
                                  + "\n" + code})
    return {"drafts": drafts, "groundings": groundings, "intel": intel,
            "errors": errors}


def _run_cmd(cmd: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=600, env=_repo_env())


def check_node(state: BuilderState) -> dict[str, object]:
    """Deterministic gate after drafting — the model NEVER sees failure
    output. Each draft is written and hit with ruff --fix + ruff + pytest;
    a failing draft is TAGGED with a failure description and excluded from
    everything downstream. Re-making a tagged symbol is a FRESH generation
    (with the tag as context), never an edit of the failed draft — so there
    is no mechanism by which actual output can be absorbed as an expected
    value. That was the regression-oracle door; it is gone."""
    errors: list[str] = list(state.get("errors") or [])
    review_dir = Path(state["repo_path"]) / "tests" / "_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    clean: list[DraftRecord] = []
    tags: list[dict[str, str]] = []
    for draft in state.get("drafts", []):
        review_file = review_dir / f"draft_{draft['symbol']}.py"
        review_file.write_text(draft["code"], encoding="utf-8")
        _run_cmd(["uv", "run", "ruff", "check", "--fix", str(review_file)],
                 state["repo_path"])
        ruff = _run_cmd(["uv", "run", "ruff", "check", str(review_file)],
                        state["repo_path"])
        pytest_run = _run_cmd(["uv", "run", "pytest", str(review_file), "-q",
                               "--no-header"], state["repo_path"])
        if ruff.returncode == 0 and pytest_run.returncode == 0:
            clean.append(draft)
            continue
        description = (
            f"ruff rc={ruff.returncode}: "
            f"{(ruff.stdout + ruff.stderr)[-600:]} | "
            f"pytest rc={pytest_run.returncode}: "
            f"{(pytest_run.stdout + pytest_run.stderr)[-1200:]}")
        tags.append({"symbol": draft["symbol"], "description": description})
        errors.append(f"{draft['symbol']}: FAILED draft check — tagged for "
                      "re-making (see refresh report / assessment queue)")
    return {"drafts": clean, "tags": tags, "review_dir": str(review_dir),
            "errors": errors}


def assess_node(state: BuilderState) -> dict[str, object]:
    """Land the checked drafts in tests/_review/ and STOP. The driving agent
    must read them; nothing is written into the suite until approved.json
    exists (write_node enforces it — there is no bypass)."""
    review_dir = Path(state["repo_path"]) / "tests" / "_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    for d in state.get("drafts", []):
        (review_dir / f"draft_{d['symbol']}.py").write_text(d["code"], encoding="utf-8")
    return {"review_dir": str(review_dir)}


# Any import statement, single-line or parenthesized multi-line: sections
# must carry no imports — the composer hoists them all to the file top.
IMPORT_LINE = re.compile(
    r"^(?:from [\w.]+ import (?:\([^)]*\)|[^\n(]*)|import [^\n]+)\n?",
    re.MULTILINE,
)


def _compose_concern_file(concern: str, repo_path: str) -> str:
    """Regenerate tests/test_<concern>.py from every approved symbol source in
    the KB. The composed file mirrors the monolith: one file per concern,
    forever — never a file per symbol."""
    pairs = knowledge.approved_symbols(concern)
    sections: list[str] = []
    imports: set[str] = set()
    for symbol, path in pairs:
        code = path.read_text(encoding="utf-8")
        for match in IMPORT_LINE.finditer(code):
            normalized = " ".join(match.group(0).split())
            imports.add(normalized)
        code = IMPORT_LINE.sub("", code).lstrip("\n")
        sections.append(f"# {'═' * 66}\n# {symbol}\n# {'═' * 66}\n\n{code.rstrip()}\n")
    import_block = "\n".join(sorted(imports)) + "\n" if imports else ""
    header = (
        f"# tests/test_{concern}.py — composed by testbuilder (concern: {concern}).\n"
        "# Generated content: edit the approved sources under\n"
        f"# testbuilder/kb/approved/{concern}/ and re-run the builder's write phase.\n\n"
    )
    return header + "\n" + import_block + "\n\n" + "\n".join(sections)


def validate_node(state: BuilderState) -> dict[str, object]:
    """Unattended mode's machine approver (the final phase's first half):
    deterministic checks on every checked draft — freshness stamp present,
    at least one test, no error-message-string assertions unless the oracle
    itself documents them. Symbols passing land in approved.json (recorded as
    machine-approved, pending the agent's post-hoc validation); the rest are
    refused to write and reported."""
    errors: list[str] = list(state.get("errors") or [])
    review_dir = Path(state["repo_path"]) / "tests" / "_review"
    flags: dict[str, list[str]] = {}
    approved: list[str] = []
    for plan in state.get("plans", []):
        symbol = plan["symbol"]
        review_file = review_dir / f"draft_{symbol}.py"
        if not review_file.exists():
            errors.append(f"validate: {symbol} has no checked draft file")
            continue
        code = review_file.read_text(encoding="utf-8")
        checks: list[str] = []
        ohash = plan.get("oracle_hash")
        registered = knowledge.get_oracle(symbol)
        if not registered:
            checks.append("premise is not registered in the oracle DB")
        elif ohash != registered["hash"]:
            checks.append(
                "built from a premise that is not the DB's current version "
                f"(built {ohash}, live {registered['hash']}) — knowledge is "
                "not fresh")
        if ohash and f"Oracle-Hash: {ohash}" not in code:
            checks.append("missing freshness stamp (Oracle-Hash)")
        if not re.search(r"^def test_", code, re.MULTILINE):
            checks.append("no test functions")
        if 'match="' in code:
            quoted = set(re.findall(r'match="([^"]+)"', code))
            undocumented = [q for q in quoted if q not in plan["oracle"]]
            if undocumented:
                checks.append(
                    f"asserts error-message text the oracle does not document: "
                    f"{undocumented}")
        conflicts = [ln.strip() for ln in code.splitlines()
                     if "ORACLE-CONFLICT:" in ln]
        if conflicts:
            checks.append(
                "oracle-actual conflicts present (expected values kept, "
                "reality differed — likely a code bug or a bad oracle): "
                + "; ".join(conflicts[:3]))
        flags[symbol] = checks
        if not checks:
            approved.append(symbol)
        else:
            errors.append(f"validate: {symbol} flagged — " + "; ".join(checks))
    if approved:
        (review_dir / "approved.json").write_text(
            json.dumps(approved, indent=2) + "\n", encoding="utf-8")
    return {"validation_flags": flags, "errors": errors}


def write_node(state: BuilderState) -> dict[str, object]:
    errors: list[str] = list(state.get("errors") or [])
    if not state.get("plans"):
        return {"errors": errors + ["write skipped: no plans survived validation"]}
    approved_file = Path(state["review_dir"]) / "approved.json"
    if not approved_file.exists():
        return {"errors": errors
                + ["write refused: approved.json missing — ASSESS the drafts first "
                   "(read tests/_review/draft_*.py, then record approved symbols)"]}
    recorded = json.loads(approved_file.read_text(encoding="utf-8"))
    if not isinstance(recorded, list) or not recorded:
        return {"errors": errors + ["write refused: approved.json is empty or invalid"]}
    touched_concerns: set[str] = set()
    for symbol in recorded:
        # The REVIEWED file is the source of truth: the driving agent may have
        # hand-edited drafts during assessment (state drafts are stale).
        review_file = Path(state["review_dir"]) / f"draft_{symbol}.py"
        if not review_file.exists():
            errors.append(f"write: approved symbol {symbol} has no reviewed draft file")
            continue
        concern = concerns.get_concern(symbol)
        knowledge.save_approved(concern, symbol,
                                review_file.read_text(encoding="utf-8"))
        touched_concerns.add(concern)
    written: list[str] = []
    for concern in sorted(touched_concerns):
        dest = Path(state["repo_path"]) / "tests" / f"test_{concern}.py"
        dest.write_text(_compose_concern_file(concern, state["repo_path"]),
                        encoding="utf-8")
        # Merge/sort the composed imports before verify sees the file.
        _run_cmd(["uv", "run", "ruff", "check", "--fix", str(dest)],
                 state["repo_path"])
        written.append(str(dest))
    if not written:
        errors.append("write: no approved drafts were written")
    return {"written": written, "errors": errors}


def verify_node(state: BuilderState) -> dict[str, object]:
    errors: list[str] = list(state.get("errors") or [])
    result: dict[str, object] = {"pytest_ok": False, "ruff_ok": False}
    for path in state.get("written", []):
        proc = _run_cmd(["uv", "run", "pytest", path, "-q", "--no-header"],
                        state["repo_path"])
        result["pytest_output"] = (proc.stdout + proc.stderr)[-3000:]
        if proc.returncode != 0:
            errors.append(f"verify FAILED (pytest) for {path}:\n{result['pytest_output']}")
        else:
            result["pytest_ok"] = True
        ruff = _run_cmd(["uv", "run", "ruff", "check", path], state["repo_path"])
        if ruff.returncode != 0:
            errors.append(f"verify FAILED (ruff) for {path}:\n{ruff.stdout[-1500:]}")
        else:
            result["ruff_ok"] = True
    return {"verify": result, "errors": errors}


def record_node(state: BuilderState) -> dict[str, object]:
    """Write the run report to the test-knowledge base (also on failure —
    a failed build is knowledge)."""
    if not state.get("written"):
        return {}
    verify = state.get("verify") or {}
    ok = bool(verify.get("pytest_ok")) and bool(verify.get("ruff_ok"))
    # One record per symbol: the index keys symbol → latest report, so a
    # batch never blurs whose oracle produced what.
    for plan in state.get("plans", []):
        if plan["symbol"] not in [p["symbol"] for p in state.get("plans", [])]:
            continue
        path = knowledge.record_run(
            symbol=plan["symbol"],
            oracle=plan["oracle"],
            model=VLLM_MODEL,
            rounds=0,
            verify="pass" if ok else "fail",
            assessment_notes=state.get("assessment_notes", ""),
            concern=concerns.get_concern(plan["symbol"]),
        )
        print(f"  recorded: {path}")


def report_node(state: BuilderState) -> dict[str, object]:
    print("== testbuilder report ==")
    print(f"  inventory: {[r['name'] for r in state['inventory']]}")
    print(f"  plans:     {[p['symbol'] for p in state['plans']]}")
    print(f"  drafts:    {[d['symbol'] for d in state.get('drafts', [])]}")
    print(f"  written:   {state.get('written', [])}")
    for sym, info in (state.get("intel") or {}).items():
        print(f"  intel[{sym}]: {info}")
    print(f"  verify:    {state.get('verify', {})}")
    for err in state.get("errors", []):
        print(f"  ERROR: {err}")
    return {}
