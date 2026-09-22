# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in CardanoInterface, please report it responsibly.

**Do not open a public issue for security vulnerabilities.**

Instead, email [security@refracticlabs.io](mailto:security@refracticlabs.io) with:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You should receive an acknowledgment within 72 hours.

## Scope

This security policy applies to:

- The CardanoInterface application (`CardanoInterface.py`)
- Wallet key storage and encryption
- Transaction building and signing logic
- Multisig script handling and CBOR parsing

## Out of Scope

- Third-party backends (Blockfrost, Koios, Ogmios) — report issues to those services directly
- Vulnerabilities requiring physical access to the user's machine
- Social engineering attacks

## Security Principles

CardanoInterface is designed with these security constraints:

- **Local-only operation.** No data leaves your machine except what you explicitly submit to a Cardano backend.
- **Encrypted at rest.** All wallet keys and mnemonics are encrypted with the user's password.
- **No credential logging.** The application never logs, prints, or writes mnemonics, private keys, passwords, or API keys to disk.
- **Integer lovelace.** All money values use integer arithmetic to prevent floating-point rounding errors.
- **Network isolation.** Wallets are bound to a specific network (Mainnet/Preprod/Preview) and the application refuses cross-network operations.

## Dependency Security

This project uses `uv` for dependency management with a locked `uv.lock` file. GitLab CI runs SAST and Secret Detection on every push. If you discover a vulnerability in a dependency, report it through the channels above.
