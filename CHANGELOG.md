# Changelog

All notable changes to CardanoInterface are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased] — 2026-10-07

### Fixed

- A mistyped wallet password no longer aborts the door it was asked in.
  One wrong keystroke used to kill the whole Send Funds flow with a raw
  decryption error (hit by the operator 2026-09-30) — and because the
  send selector sits after the password prompt, it never rendered. The
  new shared `_prompt_wallet_password` re-asks in plain language until
  the password decrypts or the user gives up; Send Funds, the sign
  unlock, both cosigner-key doors, Show Mnemonic and the checkpoint
  admin signature all use it. Delete Wallet keeps its abort on purpose
  (a failed check on a destructive door should stop it). Validated live
  on preprod over Koios: wrong password → retry → selector → 2 ADA sent
  and chain-confirmed (tx `9ff46563…`, 4/4 `audit/results/senddoor_retry.json`)
  — also the first chain-proof of the Koios submit path.

- Koios backend: token bundles were invisible. `address_utxos` populates
  `asset_list` only when `_extended: true` is requested, so on the Koios
  backend every wallet parsed as pure ADA — token-holding wallets got no
  "Choose assets" option in the send selector, and sweeps/builds modelled
  transactions that drop the tokens (the ledger rejects those with
  ValueNotConserved). Fixed by requesting `_extended`.

- Personal Send Funds: the amount prompt now shows the live balance, and
  an unfundable amount (balance minus fee minus the change minimum) is
  refused in plain words naming the real balance and the EVERYTHING
  alternative — the raw "All UTxO selectors failed" from the operator's
  2026-08-21 mainnet session can no longer surface from this door.

- Validated live on preprod over Koios with a token-heavy wallet, 7/7
  (`audit/results/koios_assets.json`): assets table, asset picker,
  multi-asset partial+all send confirmed by deltas, over-balance refusal,
  sweep leaving the wallet at zero UTxOs. Mainnet Koios read-only 3/3
  (`audit/results/mainnet_koios_health.json`): mainnet+Koios boots in the
  TUI, the health door answers, newest-epoch parameters fetch. Nothing
  was submitted on mainnet.

## [Unreleased] — 2026-09-28

### Added

- Hermetic test suite (`uv run pytest -m "not live"`): 49 tests across
  three concern files (money, thresholds, multisig script) — real functions
  against externally-sourced golden vectors (CIP-1854 semantics, a
  cardano-cli-11 cross-validated script CBOR, documented threshold
  conventions, proven float-math regressions). Every expectation carries a
  provenance header citing its oracle.
- `testbuilder/`: a test-generation pipeline (LangGraph over a local vLLM
  endpoint) that drafts tests from registered oracles, checks them
  mechanically, requires assessment before anything enters the suite,
  verifies pytest+ruff, and records every run with provenance in
  `testbuilder/kb/`. Includes a PyQt6 dashboard and a terminal lifecycle
  console (`testbuilder/tui.py`).
- CI: `compliance` job (Intersect OSC documentation must-haves, claim
  hygiene, lockfile check, requirements.txt SBOM artifact) and `tests` job
  (hermetic suite) — see `.gitlab-ci.yml`.
- `pyproject`: pytest config (pythonpath, `live` marker); pytest, langgraph,
  PyQt6 in the dev dependency group.

### Removed

- `pynguin` runtime dependency (regression-oracle generator; wrong tool for
  this standard).
## [Unreleased]

### Added
- Governance hardening for Intersect MRP onboarding:
  - `CONTRIBUTING.md`, `SECURITY.md`, `ROADMAP.md`, `CODE_OF_CONDUCT.md`,
    `CODEOWNERS`, `MAINTAINERS`, `DESCRIPTION`, `CHANGELOG`
  - Issue and merge request templates under `.gitlab/`
  - Test stage in `.gitlab-ci.yml` (mypy + ruff)

## [0.1.0] - 2026-08-21

### Added
- Single-file Python TUI wallet interface
- HD wallet creation (24-word mnemonic, CIP-1852)
- Encrypted wallet storage (PBKDF2 + Fernet)
- Multi-network support (Mainnet, Preprod, Preview)
- Address generation (enterprise and base)
- Transaction building (single and multi-recipient)
- Backend abstraction: Blockfrost, Koios, Ogmios, Kupo
- Native multisig wallets: N-of-M, CIP-1854, key-hash scripts
- Multisig recovery from CBOR hex, version-wrapped `.cbor`, checkpoint packages,
  and transaction witness sets
- Relay (accumulating CBOR) and collect (operator-collect) signing modes
- Stake delegation from multisig wallets
- Wallet checkpoints (tamper-evident, admin-signed) with import/export
- Strict mypy + ruff configuration (0 errors under ultra-strict config)