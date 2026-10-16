# Changelog

All notable changes to CardanoInterface are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased] — 2026-10-16

### Security (audit run-3 remediation)

- The check sandbox that executes unreviewed model drafts is now
  deny-by-default: instead of read-binding the whole host filesystem, it
  mounts only /usr, /etc, /dev, /proc, the interpreter tree, the repo and a
  scratch dir — a draft can no longer read anything else on the machine
  (home-directory credentials, other projects), only the repo's own wallet/
  user trees and local secret files, which stay masked on top. The review
  directory is mounted read-only, so a draft can no longer write the
  approval artifacts (approved.json / machine_validated.json) it is later
  judged by — a draft could previously forge its own agent approval and be
  written into the committed suite even after failing its own check.
- The unattended refresh's verify step now runs the composed suite inside
  the same sandbox as the check phase. Machine-validated drafts are never
  human-read before verify executes them, and verify previously ran them
  unsandboxed with the full operator environment; "unreviewed code never
  runs outside the sandbox" now holds at every node.
- delete_user's twice-typed password confirmation verifies by return value:
  load_user_data never raises (its error wrapper returns None), so the
  previous exception-based check was dead code and any two identical wrong
  passwords deleted the account. user_login's "Incorrect password" message
  was unreachable for the same reason and works again.
- Script recovery refuses when the backend is unreachable instead of
  reporting every address as unfunded: all backends map a connection
  failure to an empty UTxO list, so an outage during recovery used to
  present a chain-verified "holds no funds" for addresses never queried and
  pin the wallet to the default reading. (Supplying the exact address
  remains the offline path.)
- The relay sign flow now ed25519-verifies every incoming witness against
  the transaction body before it is shown as "signed", counted toward the
  threshold, or forwarded in the accumulated CBOR — the same per-witness
  check the partial-import and assemble doors already apply. A fabricated
  signature in a relay carrier is dropped with a loud warning instead of
  fabricating the collection state the review displays.
- A checkpoint can no longer restore over an existing personal wallet: the
  restore refuses when the target directory has no script_hash or holds
  personal secret files, instead of rewriting its network record and
  planting multisig identity files beside its keys.
- The multisig create flow records "you" only from the password-verified
  'me' picker. A pasted key hash whose typed label happens to equal one of
  your own wallet names no longer makes the program claim "You are:
  cosigner N, signing with your wallet" for a key this machine does not
  hold, persist a false claimed_signer_index, or offer the administrator
  designation on that false identity.
- Spend recipients are network-guarded on both money paths: the multisig
  builder and the personal Send Funds door refuse a recipient address from
  another network before anything is built (the same refusal recovery and
  relay import already apply), instead of building a ceremony the ledger
  rejects or submitting a cross-network payment.
- debug_main.log is created owner-only through an O_NOFOLLOW open before
  any handler is installed (it was created world-readable under the shell
  umask before the umask was pinned, never repaired, and followed a
  pre-planted symlink). The one-line log filter also flattens exception
  text now, so a backend's rejection body can no longer forge whole records
  in the operator's forensic log via a traceback's raw newlines.
- Backend-controlled strings are escaped at their remaining console sinks
  (backend-setup health/error lines and the submit-success transaction-id
  banners), and a submit result's id is displayed only when it is shaped
  like one — a lying endpoint can no longer style the terminal, forge lines
  inside the SUBMITTED banner, or make an accepted transaction read as
  rejected through a markup error.
- Checkpoint-carried cosigner labels and the package's script JSON render
  as literal text at every remaining screen (assemble, partial import,
  edit-labels, the rejected-name warning, and Script details), so a crafted
  checkpoint cannot style the signature-collection screens or abort those
  doors with a mismatched tag.
- The oracle freshness sweep parses Oracle-Hash stamps and requires exactly
  one matching the registry, instead of substring-matching the hash
  anywhere in the file; stamping refuses to prepend over a different
  existing stamp. The format_ada approved source carried two contradictory
  stamps (one demanding wrong displayed values) and the composed money
  suite shipped only the corrupted one as CI-green ground truth; both are
  rebuilt from the registered oracle.
- The CI source-guard battery is self-contained on a fresh clone: its
  rich_balance.py sibling is tracked (the wholesale audit/ ignore is
  narrowed to re-include exactly the two battery files), and the pipeline
  asserts the sibling exists before using the battery, so the merge-path
  gate can actually pass and discriminate instead of failing every clone
  with a missing-dependency "violation".
- The startup permission repair also tightens the data root directory
  itself (owner-only): replacing a tree needs only write permission on its
  parent, so a group-writable data root kept the wallet and user trees
  whole-tree replaceable even with owner-only contents.

## [Unreleased] — 2026-10-12

### Security (audit run-2 remediation)

- Signing an imported transaction now verifies what the review always
  claimed: every input of the body is proven to be an unspent output of
  the wallet's script address (queried live) before anything is shown or
  signed. A relay body crafted to spend someone's personal wallet with
  the genuine multisig script attached — one approval used to yield a
  complete ledger-valid key witness for that plain spend — is refused
  with a plain-language error. When the backend is unreachable, an
  externally-supplied body is refused rather than reviewed on an
  unverified provenance claim; sessions built by this program carry a
  `local_build` marker and still sign offline. A script attached to a
  received transaction that is not the wallet's own script can no longer
  author the displayed threshold or cosigner list.
- The relay sign path now applies the network guard every other
  wallet-facing door applies (a known wrong-network wallet is refused),
  and a transaction whose outputs pay mainnet-family addresses is
  refused outright under a preprod session instead of recovering a
  wallet stamped with the wrong network.
- Wallet files and directories are owner-only. The process pins
  umask 0o077 (single-threaded TUI), secret writers create files
  0600 through O_NOFOLLOW (a planted symlink is refused, not followed),
  and a startup pass repairs trees written by older versions (664/775
  observed → 600/700). The Blockfrost API key prompt masks input like
  every password prompt.
- Every door that sets a wallet-encryption password now asks for a
  non-empty password twice (Create Wallet, both import paths, the
  fresh-wallet branch of Show Mnemonic). An empty password made the
  at-rest key computable from the wallet file's own salt.
- Imported packages are validated before they touch the filesystem: a
  transaction package's tx_id must be 64-hex AND equal the hash of the
  CBOR it carries (it names the session directory), a checkpoint's
  config.name is held to the same single-component rule as a typed name,
  and `secure_path_join`/user account paths refuse absolute, `..`,
  empty and separator-bearing components instead of joining them.
  Username and wallet-name enumeration loops skip unsafe entries with a
  logged warning; Delete User is bound to the logged-in account and
  asks per-wallet confirmation before removing a multisig directory
  another account may reference.
- Strings that arrive from other machines (checkpoint names, cosigner
  labels, package stage) and from the chain (asset names) render as
  literal text, never as Rich markup — they can no longer forge styled
  urgency lines, hyperlinks or erase the real threshold line, and a
  mismatched tag can no longer abort a screen. Backend-derived error
  strings are escaped at their console sinks, and a logging filter keeps
  every log record on one line so no external string can forge a
  debug_main.log entry.
- Show Mnemonic no longer offers to "generate a new mnemonic" for a
  wallet that already holds signing keys (that wrote a brand-new
  identity over the funds' real keys); the branch survives only for a
  genuinely empty wallet directory, with the confirmed-password prompt.
  A failed mnemonic import no longer leaves a phantom wallet entry
  pointing at the directory it just deleted.
- The multisig balance scan prints each native token's policy id beside
  its name; the personal send door shows the transaction fee and asks
  before submitting; protocol parameters from any backend are bounded
  at the mapping boundary (an inflated fee coefficient or a zeroed
  min-UTxO constant from a lying endpoint falls back to the honest
  default); stake delegation refuses an address whose stake credential
  is not this wallet's own script; recovery refuses a known address
  from another network and refuses to guess when both address shapes of
  one script hold funds; "Show My Cosigner Key" and the create-flow
  "me" answer re-derive the published key hashes under the wallet
  password and heal a tampered key-hash cache instead of publishing it.
- testbuilder: unreviewed model drafts execute inside a bubblewrap
  sandbox (filesystem read-only except the review dir and a scratch
  dir, wallet/user data and local secrets masked, no network; missing
  bwrap or venv is a hard error, never an unsandboxed fallback).
  Approval is content-bound: `--approve` records the sha256 of the
  bytes read, machine validation writes a distinguishable
  `machine_validated.json` that only an explicitly unattended refresh
  may consume, and a draft changed after approval is refused at write.
  Oracle fetches allowlist https hosts only (Koios instances and
  raw.githubusercontent.com), print every URL fetched, and
  kb/sources/ is untracked. Refresh stages a detected source change as
  `pending_source_hash` and promotes it only after the dependent remake
  verifies; dry runs persist nothing.
- CI now runs the same whole-file source-guard battery as the local
  commit hooks (`audit/ci/source_guards.py`: no type ignores, no stubs,
  no float money math, Rich markup balance), so the merge path and the
  commit path cannot diverge.

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