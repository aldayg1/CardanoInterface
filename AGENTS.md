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

9b. **Money is integer lovelace, end to end.** ADA is a display representation only. Binary floats must never touch a money value: `int(float('1.000001') * 1_000_000)` yields `1000000`, one lovelace short (proven on this build; also `0.1 + 0.2 != 0.3`). Parse user ADA input with `ada_to_lovelace` (exact `Decimal` parse, validates ≤6 decimals), compute in integer lovelace, display with `format_ada` (pure integer arithmetic). Never `float(amount)`, never `amount * 1_000_000`, never `lovelace / 1_000_000` for display.

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

## Test fixtures (preprod E2E)

**Preprod test-only.** Documented at the operator's explicit request for end-to-end multisig validation. These hold test ADA on preprod, never mainnet funds. Mnemonics are kept only in each wallet's `mnemonic.enc` (not written here).

| Fixture | Wallet dir | Network | Address | Purpose |
|---|---|---|---|---|
| Funder | `CardanoInterface/wallets/ci_funder` | preprod | `addr_test1qzz2chxl5d4535twdz79ysrvzehufmgqrk6sx2492jvd3s30hcksxl03dh0r42eruwd6mzuzz6q0tzzuevpylzhyclcqhgx0rl` |  Operator sends preprod ADA here; the agent distributes from it. |
| Admin / terminator | `CardanoInterface/wallets/ci_admin` | preprod | `addr_test1qzt0j4n64nuz0plh3jsc08avvuu3paltdeuprz3apmn09cud0f6e6nxnwfh9vdfxzxdg8rf94m0juczmfq9ysv87cv9smxy8ry` |  Cosigner_0 of the test multisig; also the terminator (signs last, submits). Key hash `96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3`. |
| Cosigner 1 | `CardanoInterface/wallets/ci_cosigner1` | preprod | `addr_test1qp7r5k58nc3yuzxm32c4jj0alps7thaq2wjewcmwjpmy68ry9k3w7t0r0x7gmycr7qlhycm942zf96jdhynzqhedju6qzm2y82` |  Cosigner_1 of the test multisig. Key hash `7c3a5a879e224e08db8ab15949fdf861e5dfa053a597636e90764d1c`. |
| Cosigner 2 | `CardanoInterface/wallets/ci_cosigner2` | preprod | `addr_test1qryjt6xpc4qgkhlwqk9h4em7yuva4xwa7w90wyjm7vydpapx3trvlnzzvly5wlhc7ccme2sgms4j6h78wxgyndgdgg2s8l33k7` |  Cosigner_2 of the test multisig; also a spend recipient in E2E tests. Key hash `c925e8c1c5408b5fee058b7ae77e2719da99ddf38af7125bf308d0f4`. |

**Test multisig (2-of-3, key-hash, recovered from `.cbor`):** `CardanoInterface/wallets/ci_dao_recovered` — script address `addr_test1wp7z95wxeavetjgwspw9quwkzknmadw8y2nvvvvzupqfc7c59k3k0`, script CBOR `830302838200581c96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e38200581c7c3a5a879e224e08db8ab15949fdf861e5dfa053a597636e90764d1c8200581cc925e8c1c5408b5fee058b7ae77e2719da99ddf38af7125bf308d0f4`.

**Test multisig 2 (2-of-2, ci_admin + ci_cosigner1):** `CardanoInterface/wallets/ci_new_multisig` — script address `addr_test1wpl45pfyt7p2nwdqnx6svnejdc7uzskg33hjv67s490kkegex8smj`. Created via the raw-`.cbor` door, then a Wallet Checkpoint Package was exported (`export_wallet_checkpoint`) and re-imported (`import_package`) with script-hash verification + config restore — the Package door round-trip is validated.

**E2E validation record (preprod, 2026-08-13):**
- Funding: funder → multisig script (5 ADA), tx `44a91641fd3f5c86f908880dfb78a8407ae3f442eb175e0a179ea8ca5878a065`, fee 0.167 ADA. ✓ confirmed.
- **First on-chain multisig spend:** recover `.cbor` → build (1 ADA → ci_cosigner2) → sign cosigner_1 + cosigner_0 (personal wallets, relay) → assemble (both signatures verified against body hash) → submit → **confirmed**, tx `f54ba779c94df35c5e40c4a56b05d544b95f8c3f00fb4560a71b6bd0e2e926df`, fee 0.181 ADA. Outputs `#0`=1 ADA (ci_cosigner2), `#1`=3.819055 ADA (change to script). Proves the recovery spine end-to-end on real funds.
- **Second on-chain spend — file-carrier (operator-collect):** build → **export unsigned `.cbor`** → cosigner **imports file + signs + exports partial `.cbor`** → admin/terminator same → assembler **imports both partial files** → assemble (signatures verified) → submit → **confirmed**, tx `147f89affca0fb5590cf4c3872487b10cb591ff948552c97f91b8a5026b1907d`, fee 0.181 ADA. Proves the partially-signed CBOR moves between cosigners as files end-to-end. (The TUI now has a file **selector** — `_prompt_file_path` — for `.cbor`/`.json`, with tab-completable path navigation + numbered recent files; verified rendering in the real app via the Import Script CBOR menu.)
- **Third on-chain spend — RELAY (accumulating CBOR, spec Topology 3, 2026-08-14):** build → export unsigned → cosigner1 **imports the CBOR → signs (`accumulate=True`) → exports the same CBOR with their signature added** → admin/terminator **imports that CBOR → signs → threshold reached → submitted** → **confirmed**, tx `f845bb125cf24e8d112f04ee6422be667b00f79a3fae19307fca0c0057ccf9a3`, fee 0.181 ADA. Witnesses grew 0→1→2 per hop; the fully-accumulated tx id equals the hop-0 body id (body immutable, only signatures accumulated). Funds landed in the **individual wallet** ci_cosigner2. Relay is wired into the TUI sign flow: a file/paste import takes the relay path (accumulate + export + submit-on-threshold); a session import stays operator-collect.
- **min-UTxO protocol-param bug — found on-chain, root-caused, fixed (2026-08-14):** the first attempt of the 4th spend was **rejected by the ledger** (Ogmios error 3125: change output 277,452 < minimumRequiredValue **849,070**). Root cause: both backends mapped `minUtxoDepositCoefficient`/`coins_per_utxo_size` into `coins_per_utxo_word` and derived `coins_per_utxo_byte = //8` (538), so PyCardano's `min_lovelace_post_alonzo` (`(160 + output size) × coins_per_utxo_byte`) under-computed the minimum and the builder emitted underfunded change outputs. Fix (both backends): map `coins_per_utxo_byte` directly from `minUtxoDepositCoefficient`/`coins_per_utxo_size` (4310) and `coins_per_utxo_word` to the Alonzo constant 34482, matching pycardano's own `ogmios_v6` backend; the `//8` conversion is deleted. Verified: builder now computes 857,690 for that output — ≥ the ledger's 849,070 (conservative-safe).
- **Float money-math bug — proven on this build, fixed (2026-08-14):** `int(float(s) * 1_000_000)` silently produced wrong lovelace for real inputs (e.g. `1.000001` → `1000000`, `0.000249` → `248`). All money paths converted to integer lovelace end to end: new canonical `ada_to_lovelace` (exact `Decimal` parse, rejects >6 decimals/zero/negative) + `format_ada` (integer-only display); `send_ada` re-typed to `amount_lovelace: int`; `funds_send` and the multisig build flow parse exactly; all 9 `lovelace / 1_000_000` display sites replaced. Validated through the real interactive build flow: typing `1.000001` produces a tx output of exactly 1,000,001 lovelace (the old float path built 1,000,000). Two dead float-balance helpers (`get_wallet_utxo_and_balance`, `calculate_tf_and_collateral` — zero callers, placeholder math) deleted.
- **Checkpoint fidelity bugs — found via the Package door, fixed (2026-08-14):** `export_wallet_checkpoint` dropped recovery provenance (`recovery`, `key_hashes`, `script_type`, `network`), so a restored recovery wallet came back as "full" and build crashed with `KeyError: 'payment_template'`; the summary writer also hard-subscripted `config["network"]`. Fixed: export carries the recovery fields + network; import writes the correct `type` (`multisig_recovery`) and user-list entry; summary reads network defensively. Validated: raw-`.cbor` repair → checkpoint export → import → build all work on the restored wallet (`ci_new_multisig`, topped up 2 ADA via tx `da307cba24733faa8b2b9a2422aca821703807f56da6339040aebae90d49ee71`).
- **Fourth on-chain spend — raw-`.cbor` wallet spent INTO a new CI multisig (2026-08-14):** top-up funder → test multisig (5 ADA, tx `5fb69d35345c843b2e7c62a3c8fc9a082aacb000195c02b5dfe906a7e3f6e008`) → relay (cosigner1 import+sign+export → admin import+sign → threshold) → **confirmed** tx `d5a4287c4c6ff1494e44421c2f92a7e510e1591484674be8ff44d9304394c1b6`: 1 ADA landed at the **new 2-of-2 CI multisig** `addr_test1wpl45...`, change 5.275868 ADA back. Completes the recovery narrative: dead-provider `.cbor` → recovered → cooperatively spent into a fresh CI multisig, all on real funds.
- Balances after: funder ~487.7 ADA; test multisig (2-of-3) ~5.276 ADA; new CI multisig (2-of-2) ~3 ADA; ci_cosigner2 +3 ADA.

**Key-hash model rework — validated on preprod (2026-08-17):**
- The creation model was reworked from cosigner **xpubs** to cosigner **key hashes**, per `MULTISIG_SPEC.md`. `build_native_script` (xpub-based) and the six xpub helpers are deleted; `build_native_script_from_key_hashes` is the only builder. Created and recovered wallets are now one shape with one signing path — previously a created wallet's script named CIP-1854 keys while the signing path for recovered wallets used CIP-1852 personal-wallet keys, so the two models could not interoperate.
- `cosigner_key_hash_candidates` derives a cosigner's key hash at **both** CIP-1852 (personal wallet) and CIP-1854 (shared-wallet providers) and matches whichever the script names. This is what makes a script written by a sunset provider's software signable here; matching only one standard would import such a wallet and then find it unsignable.
- No signing key is stored in a multisig directory any more (`signing_keys/mnemonic.enc` removed) — a cosigner signs with the personal wallet they already have. Their key hashes are cached in the clear in **their own** wallet dir (`cosigner_key_hashes.json`, public data), so identity matching normally costs no password.
- Thresholds and key hashes are read from `script.cbor`, never from `config.json`, so an edited config cannot lower a threshold. Checkpoint import re-derives both from the script.
- **Full cycle on real funds**, tx `a99b9ddf43cb5c8b103a76e05590d9def382ffbd80a2526a629276d0f689cde6`: three fresh cosigner wallets → each publishes only a key hash → 2-of-3 script built from hashes alone → funded 5 ADA (tx `16ad3daca7305754f844e0b952aa4ffe915ae65a9242d6f0724da184c933e44f`) → **wallet directory deleted**, leaving only the CBOR hex → recovered to the byte-identical address → checkpoint exported → 2 ADA spend built, signed by two cosigners with their personal wallets, both signatures verified against the body hash, submitted → confirmed.
- **Relay driven through the real TUI as three separate people**, tx `92dbf17798c409070479cafb35bc671d3bfc7b090b3ace53deb69bb9b3222f98` (2-of-2, `ci_new_multisig`) and `d16149e6251c6442bfa80e9ec2520f6e716d470eef3b115dd45eee5138700fca` (2-of-3, `ci_dao_recovered`): admin builds + exports unsigned CBOR → a cosigner **who does not have the wallet** imports the file, is offered the wallet rebuilt from the script inside it, is auto-identified as cosigner 2, reviews, signs, exports → admin imports, sees "1 of 2 collected, ✓ signed cosigner 2", signs last, submits → both confirmed on chain.
- **TUI bugs found by these runs and fixed:** the sign flow demanded you pick a multisig wallet first, dead-ending any cosigner sent a bare transaction ("no multisig wallets found"); a relay signer's review reported "0 of 2 collected" while holding a transaction their cosigner had already signed; a mistyped signing password aborted to the menu with a raw `RuntimeError: Failed to decrypt mnemonic`; cosigner numbering mixed 0-based (`cosigner_0`) and 1-based ("you are cosigner 2").

**E2E validation flow:** operator funds `ci_funder` → agent distributes ADA to freshly-created admin + cosigner wallets → builds a key-hash multisig script → funds the script address from `ci_funder` → recovers the wallet from the `.cbor` → builds / signs (relay) / assembles / submits a real spend → confirms on-chain.

**Foreign wallet recovered and spent (2026-08-17), tx `aaa7997ad62bb823e46db34b49eb8f300461047cc44fda1902b93d024a31849b`:** script + address built by **cardano-cli 11.0** (not this program) at `addr_test1wqlhk58cr0qrzfhyezchl7fujulhtx795felaclyygdqxagzzyu2s`, hash `3f7b50f8…`, which this program re-derived byte-identically from the CBOR alone. Cosigner keys were placed at provider-style paths — `m/1854'/1815'/0'/0/0`, `m/1854'/1815'/1'/0/0`, `m/1852'/1815'/0'/0/3` — two of them invisible to an account-0/address-0 check. Funded 5 ADA → recovered from the hex string with no prior wallet → signed by two cosigners → assembled with both signatures verified → submitted → 2 ADA to the recipient, 2.819055 change back, confirmed in Kupo at slot 131321390. Staging/driver: `foreign_setup.py` + `foreign_spend.py` in the session scratchpad. This is the test that actually proves recovery, as opposed to proving parsing.

**Foreign-script validation (2026-08-17):** the official [CIP-29](https://cips.cardano.org/cip/CIP-29) native-script test vectors (bare `sig`, nested `all[sig, any[after 42, sig]]`, bare timelock, `atLeast 2 of 3`) all parse and derive addresses; CIP-1854 itself publishes no CBOR vectors. Real third-party scripts were also pulled from the chain via Kupo (`GET /scripts/{hash}` after harvesting script addresses from `/matches`): an `all` 2-of-2 and an `atLeast` **1-of-2**, both parsing with the correct requirement and re-deriving their on-chain address exactly. Note a public CBOR only exercises import/parse/address — never signing, since none of its keys are yours.

**Node/infrastructure rule:** the operator starts and stops the Cardano nodes, Ogmios and Kupo from their own Cardano Dashboard. Do not start, stop, or restart them; if a backend is down, say so and wait. Kupo runs as a **host process** (`/mnt/cardano-node/bin/kupo … --host 127.0.0.1 --port 1442`), not a container, so `docker ps` will not show it — check `ps` before claiming it is down.

## Key Files
| Path | Purpose |
|---|---|
| `CardanoInterface.py` | Entire app |
| `pyproject.toml` + `uv.lock` | Dependencies, strict mypy/ruff config; uv-managed environment |
| `CardanoInterface/users/` | Encrypted user data files |
| `CardanoInterface/wallets/` | Encrypted wallet keys + `network.txt` |
| `MULTISIG_SPEC.md` | Multisig wallet specification |
| `debug_main.log` | Debug logging |
