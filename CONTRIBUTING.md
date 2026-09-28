# Contributing to CardanoInterface

Thank you for your interest in contributing to CardanoInterface. This document explains how to get involved.

## Reporting Bugs

Open an issue on [GitLab](https://gitlab.com/RefracticLabs/cardanointerface/-/issues) with:

- What you were doing when the bug occurred
- The exact error message or unexpected behavior
- Your environment: OS, Python version (`python --version`), backend type (Blockfrost/Koios/Ogmios)
- Steps to reproduce

Do **not** include mnemonics, private keys, passwords, or API keys in any issue.

## Suggesting Features

Open an issue with:

- What problem the feature solves
- How you envision it working
- Whether it aligns with the project's goal of being a local, sovereign wallet tool

## Submitting Changes

1. Fork the repository on GitLab
2. Create a branch from `main` (`git checkout -b feature/my-change`)
3. Make your changes
4. Run the checks before submitting:
   ```bash
   uv run mypy CardanoInterface.py
   uv run ruff check CardanoInterface.py
   ```
5. Open a merge request with a clear description of what changed and why

## Code Standards

- **No mocks, stubs, or placeholders** in source code. Every function must do what it says it does.
- **One implementation per concern.** No duplicate functions or parallel code paths.
- **Integer lovelace for all money values.** Never use floats for ADA amounts.
- **Research before guessing.** Check PyCardano, CIP-1854, CIP-30, Ogmios, and Kupo documentation before writing code that depends on their behavior.
- **No hardcoded networks.** Always read from `current_network` or `SELECTED_NETWORK`.
- **Credentials stay local.** Never log, print, or commit mnemonics, private keys, passwords, or API keys.

## Development Setup

```bash
git clone https://gitlab.com/RefracticLabs/cardanointerface.git
cd cardanointerface
uv sync
uv run CardanoInterface.py
```

## Testing

The suite is hermetic by default — it runs with zero infrastructure (no
network, no node, no wallets) and must stay that way:

```bash
uv sync                      # dev tools + pytest come from the dev group
uv run pytest -m "not live"  # the hermetic suite (seconds)
uv run mypy CardanoInterface.py
uv run ruff check CardanoInterface.py
```

Test layers:

- **L1 golden-vector probes** (`tests/test_money.py`, `tests/test_thresholds.py`,
  `tests/test_multisig_script.py`) — real functions against externally-sourced
  expected values (spec documents, cardano-cli cross-validations, on-chain
  records). Every expectation carries a provenance header citing its oracle.
- **L2 recorded integration** (planned) — the builder replayed against
  captured chain responses (stored verbatim, with provenance and a refresh
  command).
- **L3 live end-to-end** — marked `live`, skipped by default; requires a
  Preprod node and test wallets. Run explicitly with `uv run pytest -m live`.

Tests are generated and maintained with `testbuilder/` (see its README):
each test's expected values trace to a registered oracle in
`testbuilder/kb/oracles.json`, and every build is recorded with provenance in
`testbuilder/kb/`. Good first contributions: add a golden vector or extend a
probe — read `testbuilder/kb/lessons.md` first.

CI runs the hermetic suite on every pipeline; the `live` marker is excluded
there by design.

For end-to-end validation beyond the suite, use a Preprod backend with test
wallets.

## Security

If you discover a security vulnerability, do **not** open a public issue. See [SECURITY.md](SECURITY.md) for responsible disclosure instructions.
