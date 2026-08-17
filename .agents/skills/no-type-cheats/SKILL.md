---
name: no-type-cheats
description: "Mandatory strict-typing gate for every Python file touched in CardanoInterface — run BEFORE considering any edit done. pyproject.toml runs mypy in strict mode plus extra disallow_any_* flags; this skill is about never regressing that with a cheat (unjustified # type: ignore, -> Any, real mocks in source, etc.) and fixing pre-existing cheats you encounter in a file you're already touching. Triggers on: editing any .py file, type: ignore, -> Any, mypy error, mypy strict, RETURN_ANY, TYPE_IGNORE, MOCK_IN_SRC, type cheat, typing escape, disallow_any."
---

# No Type Cheats

CardanoInterface's `pyproject.toml` runs mypy in real strict mode
(`strict = true` plus `disallow_any_expr`, `disallow_any_explicit`,
`disallow_any_decorated`, `disallow_subclassing_any`, `warn_unused_ignores`,
`disallow_any_unimported` — stricter than vanilla `--strict`). The gate
exists. What keeps failing is discipline: cheats get added under time
pressure, "fix it later" never happens, and the count goes up instead of
down.

**The fix is procedural, not another linter.** Every time you edit
`CardanoInterface.py`, before you consider that edit done:

## 1. Run mypy on the file you touched

```bash
mypy CardanoInterface.py
```

No `--strict` flag needed — `pyproject.toml` already sets it repo-wide.
Zero errors on the file you touched is the bar. If the touched file's
errors are pre-existing and clearly unrelated to your change (a documented,
recognized pattern this session — e.g. `disallow_any_expr` firing on a
third-party `dict[str, Any]` payload from an untyped SDK), that's the ONE
acceptable reason to leave it, and only if you say so explicitly, not
silently. Never leave a NEW error you just introduced.

## 2. The five documented cheat patterns — never introduce, always fix on sight

These are the categories this skill tracks — not new categories, the ones
already documented:

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

- **UNTAGGED_OPTIONAL** — `Optional[X]` without a clear `None` handling path.
  If a function accepts `None`, the code must actually handle the `None` case.
  If it does not, the parameter should not be `Optional`.

- **DUPLICATE_FUNCTION** — a near-identical twin function (e.g. `send_ada_v2`,
  `send_ada_new`, `create_wallet_msig`). Merge into the canonical function,
  delete the duplicate, fix every caller.

## 3. Opportunistic fix, not just your own diff

If the function you're editing ALREADY has cheats near what you touched —
even ones you didn't introduce and aren't directly related to your
change — fix them while you're there. This is the operator's explicit
instruction, not a suggestion: a dedicated "someday" cleanup pass for
thousands of accumulated cheats never actually happens, so the only way
the count goes down instead of up is fixing them as you naturally pass
through each function. Don't go out of your way hunting for cheats —
but don't skip ones sitting right next to your edit either.

## 4. Never regress a clean function

If `mypy CardanoInterface.py` was clean before your edit, it must be
clean after. A new error your own change introduced is never "pre-existing
debt" — it's yours, fix it before moving on, same turn.

## 5. Verify before claiming done

Never say a function is "type-clean" or "no cheats" without having actually
run `mypy` on it in this turn. A memory of "I checked this earlier" from
several edits ago is not current — code drifts as you edit adjacent logic.

## 6. Run ruff as well

After mypy passes, also run:

```bash
ruff check CardanoInterface.py
```

mypy catches type errors. Ruff catches lint errors (unused imports,
undefined names, style violations). Both must pass clean before handoff.
