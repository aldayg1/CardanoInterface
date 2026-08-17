---
name: no-legacy-code
description: "Mandatory full-replacement discipline whenever a fix, migration, or refactor supersedes existing code — one canonical implementation per concern, no shims, no dead code, no preserved-as-legacy. CardanoInterface is a single-file app; legacy code means duplicate functions, commented-out blocks, or old paths left in place. Triggers on: migrate, migration, replace, rewrite, deprecate, supersede, reimplement, legacy, dead code, old code path, parallel implementation, keep for compatibility, fallback, shim."
---

# No Legacy Code Left Behind

CardanoInterface is a single-file app. There is one `send_ada`, one
`wallet_create`, one `load_encrypted_wallet`. Legacy code here means:
duplicate functions, commented-out blocks, old paths left in place, or
`if False:` guards that preserve dead logic. This skill enforces clean
replacement.

## Before starting a migration/replacement

1. **Find every call site of the thing being replaced**, not just the
   obvious ones:
   ```bash
   rg -n "old_function_name" CardanoInterface.py
   ```
   A migration that updates the definition but misses a caller doesn't
   reduce the legacy surface — it adds a second, silently-diverging one.

2. **Decide the replacement is real BEFORE writing it** — don't build the
   new path speculatively "alongside" the old one to compare. If you're
   migrating, migrate; if you're still evaluating whether to migrate, that's
   a different, smaller task and should say so explicitly.

## While migrating

3. **Delete the old implementation in the SAME change**, not a follow-up.
   "I'll clean up the old path later" is exactly how this keeps regressing.
   Later doesn't come.

4. **No re-export shims, no compatibility wrappers, no `_v2`/`_new`/`_old`
   suffix functions.** If a caller's reference breaks because the old function
   moved, fix the caller's reference — don't add a wrapper that calls the
   new thing under the old name "so nothing breaks."

5. **Update every doc/comment that describes the old behavior**, not just
   the code. A docstring or comment that still describes the deleted path
   as current is itself a form of legacy left behind.

## After migrating — verify, don't assume

6. **Grep again** for the old name/pattern across the whole file to confirm
   zero references remain:
   ```bash
   rg -n "old_function_name" CardanoInterface.py
   ```
   Empty output is the bar. A stray reference in a comment or docstring
   is still a real leftover.

7. **Run the project's normal verification** on everything touched
   (`ruff check CardanoInterface.py`) — a deletion can break an import
   or a reference elsewhere just as easily as new code can.

8. **State explicitly what was deleted**, not just what was added. "Removed
   `X`, `Y`, `Z`; replaced with `W`" is the report this skill exists to
   produce — "added W" alone leaves the reader unable to tell whether the
   old path is actually gone.

## What counts as legacy in a single-file app

- Commented-out code blocks (delete them, git has history)
- `if False:` guards around dead logic
- Duplicate functions with near-identical names
- Functions that are defined but never called
- Old `print()` statements replaced by Rich console output
- Old encryption paths replaced by `encrypt_data`/`decrypt_data`
- Old backend implementations superseded by the current ABC pattern
