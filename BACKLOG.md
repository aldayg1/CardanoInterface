# BACKLOG.md — Active Work Tracking

## Multisig key-hash rework — DONE (2026-08-17)

The creation model moved from cosigner xpubs to cosigner key hashes, closing the
largest model gap in `MULTISIG_SPEC.md`. Created and recovered wallets are now one
shape with one signing path; previously they named keys at different derivation
paths and could not interoperate. Validated on preprod with real transactions —
see `MULTISIG_SPEC.md` §On-chain validation record and `AGENTS.md`.

Delivered: `build_native_script_from_key_hashes`, `cosigner_key_hash_candidates`
(matches CIP-1852 **and** CIP-1854, so sunset-provider scripts are signable),
`classify_cbor` + a unified "Recover or import" door, sign-from-the-transaction
with automatic cosigner identification, `claimed_signer_index` wired, labels,
a regrouped multisig menu. Deleted: the xpub script builder, six xpub helpers,
`signing_keys/mnemonic.enc` (no signing key in a multisig directory), and the
per-index address scan (one script means one address).

mypy and ruff remain at **0 errors**.

**Threshold bug found via the CIP-29 vectors and fixed (2026-08-17):** the threshold was
read from a single field, which is only correct for `atLeast`. An `any` script — a joint
account where either party may spend alone — was reported as N-of-N, so this program
would have refused a one-signature spend the chain accepts, making a recovered joint
account unspendable exactly when a cosigner is gone. `script_min_signatures` now walks
the script tree (`all`→sum, `any`→min, `atLeast(n)`→n cheapest, timelock→0), and
`script_is_flat_threshold` keeps the UI honest about nested scripts.

### Remaining multisig work

- [x] **Cosigner matching beyond account 0 / address 0 — DONE (2026-08-17).**
      `find_cosigner_derivation` searches both standards over 3 accounts x 20
      addresses, primary position first, and returns the HD node so the key it
      finds can actually sign. Verified against keys placed at
      `m/1854'/1815'/1'/0/0`, `m/1852'/1815'/0'/0/5`, `m/1854'/1815'/2'/0/7`.
      Widen `COSIGNER_SEARCH_ACCOUNTS` / `COSIGNER_SEARCH_INDICES` if a provider
      is ever found using something further out.
- [ ] No interactive way to add or edit labels on an existing wallet, so a recovered
      wallet stays `cosigner 1..N` forever.
- [ ] Recovering a wallet cannot tell you whether you can sign it *before* you
      commit to importing it — you import, then run "Which cosigner am I?".
      Fine, but a pre-import check would be kinder.
- [ ] Multi-asset spends: token parameters exist in `build_multisig_transaction`
      but are not wired to the interactive wrapper (ADA only).
### Administration features — REQUIRED, not optional (operator, 2026-08-17)

`MULTISIG_SPEC.md` describes these as "parked". That was the spec's framing and
it is wrong: they are required management functionality for CI package
administration. Reclassified as outstanding work.

- [ ] **Admin gating** — enforce `admin_cosigner_index` so only the admin may
      build, assemble and submit, while every cosigner can still sign. The field
      is written at creation and read by `_initiator_check`, but no wrapper
      enforces it, so the wallet runs gating-off regardless of what was chosen.
- [ ] **Tamper-evident admin-signed checkpoints** — sign the canonical checkpoint
      JSON with the admin's key and verify on import against a vkey hash in the
      script. Today import verifies the script hash and re-derives the threshold
      from the script, but the admin designation itself is unauthenticated, so
      anyone who can edit a checkpoint can name themselves admin.
- [ ] **Stake / delegation scripts** — CIP-1854 delegation templates; a multisig
      wallet cannot currently delegate its stake at all.
- [ ] **Timelock creation** — `after`/`before` parse, display and are enforced by
      the ledger on recovery, but the creator only emits `atLeast`, so a timelocked
      wallet cannot be made here.

## Static-analysis debt — RESOLVED (2026-08-14)

The dedicated annotation pass called for below happened: `CardanoInterface.py`
now passes the full gate with **0 mypy errors and 0 ruff errors**, and the
config is stricter than before, not looser:

- The `[[tool.mypy.overrides]] ignore_missing_imports` block for untyped
  libraries is **deleted**. In its place `follow_untyped_imports = true`
  makes mypy use pycardano's real inline annotations, so the whole dependency
  surface has real types instead of module-Any (this caught real arg-type
  mismatches during the pass). Zero `# type: ignore`, zero `typing.cast`,
  zero explicit `Any` anywhere in the source.
- Architecture change that made this possible: `CardanoBackend` is now a
  real `pycardano ChainContext` subclass (backends implement `_utxos` /
  `submit_tx_cbor` / typed `submit_tx`), and `protocol_param` returns a real
  `pycardano ProtocolParameters` dataclass built from live chain data
  (Blockfrost mapping mirrors pycardano's own backend; Ogmios mapping reads
  the actual v6 keys, including the true `memory`/`cpu` execution prices as
  exact rationals instead of silently falling back to defaults because the
  code looked for stale v5 key names).
- All JSON handling goes through a closed `JSON` value type with typed
  accessors (`_json_object`/`_json_int`/`_json_str`/...), so `json.loads`
  and `Response.json()` Any never propagates.
- Latent bugs found and fixed during the pass:
  - Rich markup crashes (rule 11): the main-menu and multisig-wallet
    subtitles split their closing tags across string literals without a
    space (`[/bold underline` + `bright_cyan]`), which raises
    `rich MarkupError` at render time.
  - `tracker_lp` read `utxo.amount` (UTxO has no `amount`; it lives on
    `utxo.output`) — the asset view could only ever fail.
  - Token displays called `.hex()` on `ScriptHash`/`AssetName` wrapper
    objects that have no such method; they now unwrap `.payload` via the
    typed `_multi_asset_items` helper.
  - Two missing-space display strings (`onpreprod`, `issubmitted`).
- Dead code deleted: `with_progress_bar`, `with_progress`, `log_and_print`,
  `convert_decorator`, `verify_wallet_file_integrity`,
  `EncryptionError.check_encryption_state`,
  `DataValidationError.validate_data_format`,
  `identify_and_connect_network`, and the unused hex-conversion helper
  chain (`bytes_to_hex`, `string_to_bytes`, `hex_to_bytes`,
  `bytes_to_string`, `hex_to_string`, `string_to_hex`, `safe_fromhex`).
  The triplicated wallet-entry migration block became one canonical
  `_migrate_wallet_entries`. `pynacl` is a declared dependency (it was
  always imported directly); the unused direct `blockfrost-python` pin is
  removed (it remains a transitive pycardano dependency).
- Proof it still works (all on this build):
  - `uv run mypy CardanoInterface.py` → 0 errors; `uv run ruff check` → clean.
  - Live preprod Ogmios (read-only): network detect, health, tip slot,
    UTxO queries (funder 487.499273 ADA; test multisig 5.275868 ADA —
    byte-exact match to the on-chain E2E record), protocol parameters
    (min_fee 44/155381, coins_per_utxo_byte 4310, real chain prices), a
    full unsigned multisig body built through the new ChainContext
    subclass (fee 0.167393 ADA, min-UTxO check ≥ ledger minimum).
  - Crypto identities: all three fixture wallets' CIP-1852 payment key
    hashes match the recovered script's recorded cosigner hashes;
    mnemonic→keys→address round-trips; exact lovelace math regression
    (`1.000001` → 1000001). (The bech32 xpub round-trip checked here was
    dropped with the xpub helpers in the 2026-08-17 key-hash rework.)
  - Sign cycle on a wallet copy (no submission): operator-collect partial
    verified against the body hash with the right cosigner identity, then
    relay-accumulate reached 2-of-2 with the body hash unchanged.
  - TUI E2E via echo-pipe: login (ci_admin), register (fresh user),
    network/backend selection over live Ogmios+Kupo, main menu, wallet
    list, multisig submenu, clean exit.

Policy going forward: the counts stay at zero. Any change that adds an
error, an ignore, a cast, or an explicit Any does not ship.

## uv migration record (2026-08-13, complete)

- All dependencies managed by uv from `pyproject.toml`, locked in
  `uv.lock`, venv on uv-managed CPython 3.13.12
  (`tool.uv.python-preference = "only-managed"` — no PATH/system python).
- mypy + ruff are dev dependencies (`uv add --dev`).
- Runtime pip auto-installer (`check_and_install_dependencies`) and
  try/except pip-import blocks deleted from `CardanoInterface.py`;
  `PythonDependencies.txt` deleted; `run_CardanoInterface.bat` now uses
  `uv run`; pip-era Cardano packages (pycardano, blockfrost-python,
  mnemonic, qrcode, ogmios, cardano-tools, base58, ECPy, pprintpp)
  uninstalled from the anaconda base env.
- Dead dependencies removed: `typeguard` (unused; re-enters transitively
  via pycardano), `qrcode[pil]` (unused import deleted).
- `cbor2` constrained `<6`: pycardano 0.18's default cbor2pure path needs
  5.x exception names; the previous lock (cbor2 6.1.4) could not import
  pycardano at all — surfaced by the fresh-venv rebuild, fixed and
  validated by a live preprod Ogmios smoke run.

## Test Users (Preprod)

| Username | Purpose |
|----------|---------|
| `multisig_test1` .. `multisig_test6` | Cosigners 1-6 for preprod E2E. Passwords in `CREDENTIALS.local.md` (gitignored). |

**Network:** Preprod (discovered dynamically via Ogmios)
**Backend:** Local Ogmios + Kupo

