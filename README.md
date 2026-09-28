# CardanoInterface

[![pipeline status](https://gitlab.com/RefracticLabs/cardanointerface/badges/main/pipeline.svg)](https://gitlab.com/RefracticLabs/cardanointerface/-/pipelines)

Local, sovereign Cardano wallet interface with multisig support. Create wallets, manage funds, and recover from sunset wallet providers — all from your own machine, no hosted services required.

**Who is this for?** You hold funds in a Cardano multisig wallet and its provider disappeared (Nami deprecated, Typhon multisig rejected, Lace shared wallets limited) — or your DAO/treasury wants threshold spending without trusting a hosted service. If that is not you, the [multisig recovery story](#why-cardanointerface) below is still the fastest way to see what this does.

## Why CardanoInterface?

Cardano wallet providers are sunsetting. Nami deprecated. Typhon's multisig proposal rejected. Lace shared wallets are limited. If you have funds in a multisig wallet created by a provider that no longer exists, you need a way to recover them.

CardanoInterface solves this: give it the `.cbor` hex from your old wallet, round up the surviving cosigners, and move your funds. No central service, no trusted third party.

**Key capabilities:**
- Recover multisig wallets from CBOR hex, version-wrapped `.cbor` files, wallet checkpoint packages, or transactions
- Create new multisig wallets (N-of-M) with key-hash-based scripts
- Build, sign (relay or collect), assemble, and submit transactions
- Delegate stake from multisig wallets
- Send ADA and native tokens from personal wallets
- Supports three backends: Blockfrost, Koios (free), and Ogmios+Kupo (local node)

## Requirements

- **Python 3.11+** (3.13 recommended)
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — manages dependencies and runs the app
- A Cardano backend:
  - **Blockfrost** — remote REST API (needs a [project ID](https://blockfrost.io/))
  - **Koios** — free public REST API, no key needed
  - **Ogmios + Kupo** — local node bridge (auto-discovers network)

## Setup

### 1. Install uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Clone the repository

```bash
git clone https://gitlab.com/RefracticLabs/cardanointerface.git
cd cardanointerface
```

### 3. Install dependencies

```bash
uv sync
```

This creates a `.venv/` with all dependencies locked in `uv.lock`. No manual pip or venv activation needed.

### 4. Run

```bash
uv run CardanoInterface.py
```

**Windows:** double-click `run_CardanoInterface.bat` (checks for uv, syncs, runs).

## First Launch

### Step 1: Register or Login

```
Do you want to (login/register/exit)?
```

- **First time:** type `register`
  - Choose a username (letters and numbers only)
  - Choose a password (min 8 chars, must include upper, lower, digit, special char)
  - Your encrypted user data is saved to `CardanoInterface/users/`

- **Returning user:** type `login`
  - Enter your username and password
  - 3 failed attempts = lockout

### Step 2: Select a network

```
Select a Cardano network:
  1. Mainnet
  2. Preprod
  3. Preview
```

Type `1`, `2`, or `3`. **Preprod** is recommended for testing.

### Step 3: Connect a backend

```
Select a backend:
  1. Blockfrost (remote API)
  2. Local Node (Ogmios + Kupo)
  3. Koios (public API)
```

| Backend | API Key | Setup |
|---------|---------|-------|
| **Blockfrost** | Required | Enter your API key when prompted |
| **Koios** | None | Connects immediately |
| **Ogmios + Kupo** | None | Enter URLs (defaults: `localhost:1337` / `localhost:1442`) |

### Step 4: You're at the Main Menu

```
Main Menu
1. View Wallets
2. Create Wallet
3. Import Wallet
4. Multisig Wallets
5. View Wallet Assets
6. Show Mnemonic Passphrase
7. Send Funds
8. Delete Wallet
9. Delete User
10. Pool Registration
11. DRep Registration
12. Switch Backend
13. Debug Backend Health
14. Exit
```

## Features

### Personal Wallets

| Feature | Menu | Description |
|---------|------|-------------|
| **View Wallets** | `1` | Lists all wallets with addresses and network tags. Warns on network mismatch. |
| **Create Wallet** | `2` | Generates a new 24-word mnemonic wallet with encrypted key storage. |
| **Import Wallet** | `3` | Import via mnemonic phrase or key files (`.skey`). |
| **View Assets** | `5` | Shows ADA balance and native tokens for a selected wallet. |
| **Show Mnemonic** | `6` | Decrypts and displays the mnemonic (requires password). |
| **Send Funds** | `7` | Send ADA and/or native tokens to an address. |
| **Delete Wallet** | `8` | Removes a wallet and its encrypted key files. |
| **Switch Backend** | `12` | Change network or backend without restarting. |
| **Debug Health** | `13` | Shows connection status, detected network, and protocol parameters. |

### Multisig Wallets

Select **4** from the Main Menu to open the multisig submenu:

```
  ── Your wallets ──
1. View wallets and balances
2. Show my cosigner key

  ── Get a wallet ──
3. Create a multisig wallet
4. Recover or import (script .cbor, package, transaction)
5. Which cosigner am I?

  ── Spend ──
6. Build a transaction
7. Sign a transaction
8. Assemble and submit
9. Delegate stake to a pool

  ── Share and back up ──
10. Export wallet checkpoint (back up / send to a cosigner)
11. Export transaction package (send to a cosigner)
```

#### Creating a Multisig Wallet

1. Enter a wallet name
2. Choose the number of cosigners (e.g. `3`)
3. Choose the threshold (e.g. `2` for a 2-of-3)
4. For each cosigner, enter their key hash (or derive it from a wallet on this machine)
5. The app builds a native script, derives the script address, and saves it

#### Recovering a Multisig Wallet

This is the core feature for disaster recovery. Accepts any of:

- **Script CBOR hex** — paste the raw hex string from your old wallet
- **Version-wrapped CBOR file** — binary `.cbor` exported by wallet backends (`[1, [script]]` envelope)
- **Wallet checkpoint package** — exported via option `10`
- **Transaction CBOR** — extracts the script from a transaction's witness set

The app reads the script, re-derives the address, and probes the chain for funds. If the script was inside a version envelope, it unwraps it and shows both readings — the chain decides which one holds funds.

#### Signing Transactions

Two signing modes:

- **Relay mode (recommended):** Export unsigned CBOR → send to next cosigner → they import, sign, export → repeat until threshold met → auto-submits on last signature
- **Collect mode:** Gather all signatures yourself, then assemble and submit

#### Delegating Stake

1. Select a multisig wallet
2. Enter the pool ID to delegate to
3. The app builds a registration + delegation certificate
4. Export → relay sign → submit (same flow as spending)

#### Wallet Checkpoints

Export a portable package containing the script, key hashes, and metadata. Send this to a cosigner who doesn't have the wallet — they can import it via option `4`.

## Network Rules

Wallets are network-specific. A wallet created on Preprod cannot hold or spend Mainnet funds (and vice versa). The app warns you if a wallet's network doesn't match the selected backend.

**Never send Mainnet funds to a Preprod wallet address or vice versa.** This will result in permanent loss.

## Data Storage

| Path | Contents |
|------|----------|
| `CardanoInterface/users/` | Encrypted user data (`*.userdb`) |
| `CardanoInterface/wallets/` | Encrypted keys, mnemonics, network tags per wallet |
| `debug_main.log` | Debug log (auto-created) |

All wallet keys and mnemonics are encrypted at rest with your password.

## Security

- All keys encrypted at rest with your password
- No mnemonics, keys, or passwords logged or written to disk unencrypted
- All money values use integer lovelace (no floating-point rounding)
- Network isolation prevents cross-network operations
- See [SECURITY.md](SECURITY.md) for vulnerability reporting

## License

MIT License. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report bugs, suggest features, and submit changes.
