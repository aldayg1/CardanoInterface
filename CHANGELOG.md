# Changelog

All notable changes to CardanoInterface are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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