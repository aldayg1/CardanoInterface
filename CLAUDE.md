# CardanoInterface — Agent Guide

## Run
```bash
uv run CardanoInterface.py
```
No build step, single-file Python app. Dependencies are managed by uv from `pyproject.toml` (locked in `uv.lock`): `uv sync` installs, `uv add <pkg>` adds/updates, `uv run <cmd>` executes in the project venv. No pip, no manual venv activation.

## Architecture
- **Single file**: `CardanoInterface.py` (~2400 lines). Everything is in one module.
- **Entrypoint**: `main()` at bottom. `if __name__ == "__main__": main()`.
- **Backend abstraction** (2 options, selectable at startup):
  - `BlockfrostBackend` — remote REST (needs valid API key)
  - `OgmiosBackend` — local JSON-RPC bridge (auto-discovers network from `/health`)
  - `KupoBackend` — local UTxO indexer (optional, used alongside Ogmios)
- **Global `context`** — single `CardanoBackend` instance; all blockchain ops route through it.
- **Network flow**: User selects network first (Mainnet/Preprod/Preview), then backend. Ogmios validates the detected network matches the selection. Never hardcode the network.

## Non-negotiable operating rules

1. **Validate real behavior.** Observe the actual result — what the user sees, what the backend returns, what the chain confirms. Do not claim code works because it "should" work or because the logic looks right.

2. **No mocks, stubs, fakes, or placeholder implementations in source.** This is a runtime application, not a test suite. Every function must do what it says it does. No `MagicMock`, no `unittest.mock.patch`, no `if False:` guards, no `raise NotImplementedError("TODO")` in shipped code.

3. **Own every error.** Every traceback, lint failure, warning, or bad result is yours. Fix it and prove the fix, or document it in `BACKLOG.md` with the operator's acknowledgement. Never deliver code that silently fails.

4. **One canonical implementation per concern.** This is a single-file app. There is one `send_ada`, one `wallet_create`, one `load_encrypted_wallet`. Do not create duplicate functions, variant functions with near-identical names, or parallel code paths that do the same thing. If a function needs to do something different, it takes a parameter — it does not get a twin.

5. **No type cheats.** Do not use `# type: ignore` without inline justification. Do not use `-> Any` as a return type to dodge a real type error. Do not use bare `Any` where a concrete type exists. Fix the real type issue. If you encounter a pre-existing cheat in a file you are editing, fix it while you are there.

6. **No legacy code left behind.** When replacing or refactoring code, delete the old implementation in the same change. Do not leave old functions "just in case." Do not create `_v2`, `_new`, `_old`, or `_clean` variants. The old code is gone when the new code ships.

7. **Research before guessing.** Before writing code that depends on PyCardano, CIP-1854, CIP-8, CIP-30, CIP-5, Ogmios, Kupo, or any external library/API: read the actual documentation. Use `context7_query-docs`, `websearch`, or `webfetch` to find the real answer. Do not assume behavior based on similar libraries. Cardano has specific rules — get them right from the source.

8. **Never expose credentials.** Never log, print, commit, or write to docs: mnemonics, private keys, passwords, API keys, or secret values. The encryption system (`encrypt_data`/`decrypt_data`) exists for a reason — use it.

9. **Never hardcode the network.** Always read from `current_network` or `SELECTED_NETWORK`. Wallets created on one network do not work on another. This is a real source of fund loss.

10. **Recursive prompts are forbidden.** Use `while` loops for re-prompting on failure. Recursive calls cause stack overflow on bad input.

11. **Rich markup must be balanced.** Opening and closing tags must match exactly: `[bold bright_cyan]...[/bold bright_cyan]` — never `[/bright_cyan]` alone.

12. **Run type checker and linter before handoff.** Run `uv run mypy CardanoInterface.py` and `uv run ruff check CardanoInterface.py` on changed code before claiming it is done. Introduce **no new errors** — the pre-existing error count under the ultra-strict mypy config may only go down (see `BACKLOG.md`). mypy and ruff are uv dev dependencies (`[dependency-groups]` in `pyproject.toml`); `uv sync` installs them. Never pip-install.

13. **Fix adjacent cheats opportunistically.** If you are editing a function and see a type cheat, mock, legacy pattern, or obvious bug in adjacent code — fix it while you are there. Do not leave known problems sitting next to your clean work. The count must go down, not up.

14. **Security operations are read-only unless explicitly authorized.** Query UTxOs, query chain state, query balances — these are fine. Submitting transactions, delegating stake, or moving funds requires explicit operator approval for anything beyond manual testing on preprod.

## Documentation discipline

- Keep context factual, dated, and current. A completed task becomes evidence in the relevant spec or is removed from the active backlog.
- Detailed requirements belong in a focused specification (like `MULTISIG_SPEC.md`). Active work belongs in `BACKLOG.md`. Context explains stable decisions and boundaries.
- Do not preserve obsolete instructions as legacy context. Incorporate true facts into the relevant document, then delete the obsolete one.

## Agent Skills

This project defines reusable skills as `SKILL.md` files under `.agents/skills/`. When a task matches a skill's purpose, load it before proceeding — do not reimplement what the skill already documents. Skills exist to enforce consistent workflows, mandatory automation, and canonical patterns; using them is faster and safer than guessing.

## Common Gotchas
- **`prompt_toolkit` + pipe input**: `session.prompt()` uses terminal raw mode. Piping stdin works but ANSI sequences leak into output. Use `pexpect` or echo piping for automation.
- **Rich markup**: Closing tags must match opening tags exactly. `[bold bright_cyan]...[/bold bright_cyan]` — never `[/bright_cyan]`.
- **Blockfrost health endpoint** (`/api/v0/health`) is public (no auth). API key validation uses `/api/v0/epochs/latest/parameters` which requires auth.
- **User data** persists in `CardanoInterface/users/*.userdb` (encrypted). API key is **not** persisted between sessions.
- **Wallets store network** in `network.txt` inside wallet dir. Wallets created on one network don't work on another.

## Testing
No test framework. Manual / echo-pipe testing + static analysis:
```bash
# Static analysis (dev tools come from the uv project venv)
uv run mypy CardanoInterface.py
uv run ruff check CardanoInterface.py

# Manual test
echo -e "login\nuser\nPass1234!\n2\n2\n\n\nexit" | uv run CardanoInterface.py
```
Input order: login/register → username → password → network (1/2/3) → backend (1/2) → Ogmios URL → Kupo URL → ...

## Key Files
| Path | Purpose |
|---|---|
| `CardanoInterface.py` | Entire app |
| `pyproject.toml` + `uv.lock` | Dependencies, strict mypy/ruff config; uv-managed environment |
| `CardanoInterface/users/` | Encrypted user data files |
| `CardanoInterface/wallets/` | Encrypted wallet keys + `network.txt` |
| `MULTISIG_SPEC.md` | Multisig wallet specification |
| `debug_main.log` | Debug logging |
