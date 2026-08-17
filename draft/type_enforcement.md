---
name: no-type-cheats
description: "Mandatory strict-typing gate for every Python file touched in the toolkit — run BEFORE considering any edit done, not as a separate later pass. The toolkit's pyproject.toml already runs mypy in strict mode plus extra disallow_any_* flags; this skill is about never regressing that with a cheat (unjustified # type: ignore, -> Any, real mocks in source, etc.) and fixing pre-existing cheats you encounter in a file you're already touching, since a separate batch cleanup pass never actually happens. Triggers on: editing any .py file in the toolkit, type: ignore, -> Any, mypy error, mypy strict, RETURN_ANY, TYPE_IGNORE, MOCK_IN_SRC, peel scan, type cheat, typing escape, disallow_any."
---

# No Type Cheats

The toolkit's `pyproject.toml` already runs mypy in real strict mode
(`strict = true` plus `disallow_any_expr`, `disallow_any_explicit`,
`disallow_any_decorated`, `disallow_subclassing_any`, `warn_unused_ignores`,
`disallow_any_unimported` — stricter than vanilla `--strict`). The gate
already exists. What keeps failing is discipline: cheats get added under
time pressure, "fix it later" never happens, and the operator has now
flagged this as a standing, recurring problem — "the toolkit has a crazy
alleged amount of type cheats, and they keep accumulating."

**The fix is procedural, not another linter.** Every time you edit a `.py`
file in this repo, before you consider that edit done:

## 1. Run mypy on the file you touched

```bash
uv run mypy path/to/file.py
```

No `--strict` flag needed — `pyproject.toml` already sets it repo-wide.
Zero errors on the file you touched is the bar. If the touched file's
errors are pre-existing and clearly unrelated to your change (a documented,
recognized pattern this session — e.g. `disallow_any_expr` firing on a
third-party `dict[str, Any]` payload from an untyped SDK), that's the ONE
acceptable reason to leave it, and only if you say so explicitly, not
silently. Never leave a NEW error you just introduced.

## 2. The five documented cheat patterns — never introduce, always fix on sight

These are `scripts/peel_scan.py`'s own tracked categories (`peel_tasks.json`)
— not new categories, the ones already documented in this repo:

- **TYPE_IGNORE** — `# type: ignore` with no inline reason. Fix the real
  type error. A justified ignore (`# type: ignore[code] — reason`) is only
  for a genuine external-library stub gap, never to dodge a real bug in
  your own code. `warn_unused_ignores = true` is on — mypy itself will tell
  you when an ignore is stale; remove it, don't leave it.
- **RETURN_ANY** — a function returns `-> Any`. Read the function body,
  write the real return type. If genuinely unknowable, use `object` or a
  proper `Protocol`/`TypeVar`, never bare `Any`.
- **MOCK_IN_SRC** — `MagicMock`/`Mock`/`AsyncMock`/`patch`/`unittest.mock`
  in non-test runtime code. Forbidden outright in this repo (no-pytest,
  no-mocks are both absolute project rules, not just typing hygiene).
- **WASM_BACKEND** — browser-only import (`pyodide`/`micropip`/`js`) in
  server-side code. Remove it or use the real stdlib/server equivalent.
- **DUPLICATE_MODULE** — a near-identical sibling file (`foo_v2.py`,
  `foo_clean.py`). Merge into the canonical file, delete the duplicate,
  fix every import.

Full categories + fix mechanics: the `peel-errors` skill and
`scripts/peel_scan.py --check`. This skill is the per-file, in-the-moment
discipline; `peel-errors` is the batch/whole-repo sweep — use both, they're
complementary, not duplicates.

## 3. Opportunistic fix, not just your own diff

If the file you're editing ALREADY has cheats near what you touched —
even ones you didn't introduce and aren't directly related to your
change — fix them while you're there. This is the operator's explicit
instruction, not a suggestion: a dedicated "someday" cleanup pass for
thousands of accumulated cheats never actually happens, so the only way
the count goes down instead of up is fixing them as you naturally pass
through each file. Don't go out of your way to open unrelated files
hunting for cheats — but don't skip ones sitting right next to your edit
either.

## 4. Never regress a clean file

If `uv run mypy path/to/file.py` was clean before your edit, it must be
clean after. A new error your own change introduced is never "pre-existing
debt" — it's yours, fix it before moving on, same turn.

## 5. Verify before claiming done

Never say a file is "type-clean" or "no cheats" without having actually run
`uv run mypy` on it in this turn. A memory of "I checked this earlier" from
several edits ago is not current — files drift as you edit adjacent code.
