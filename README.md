# CardanoInterface

Local Cardano wallet interface with Ogmios/Kupo or Blockfrost backends. Single-file Python TUI app for creating, managing, and spending from wallets — including native multisig (N-of-M) wallets with CBOR export/import.

## Requirements

- **Python 3.11+** (3.13 recommended)
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — manages dependencies and runs the app
- A Cardano backend:
  - **Blockfrost** — remote REST API (needs a [project ID](https://blockfrost.io/))
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

## Running the app

```bash
uv run CardanoInterface.py
```

**Windows:** double-click `run_CardanoInterface.bat` (checks for uv, syncs, runs).

## First launch — step by step

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
```

**Option 1 — Blockfrost:**
- Enter your Blockfrost API key when prompted
- Validated automatically against the selected network

**Option 2 — Local Node:**
- Enter Ogmios URL (default: `http://localhost:1337`)
- Enter Kupo URL (default: `http://localhost:1442`)
- Kupo is optional — if unreachable, Ogmios handles UTxO queries (slower)
- The app validates the node's network matches your selection

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

## Features — Main Menu

### View Wallets (`1`)
Lists all your wallets with addresses and network tags. Warns if a wallet is on the wrong network.

### Create Wallet (`2`)
1. Enter a unique wallet name (letters, numbers, underscores)
2. Enter a password to encrypt the wallet's signing keys
3. A 24-word mnemonic is generated — **write it down**
4. You must type back a random word from the mnemonic to confirm you saved it
5. Wallet is created with a derived address and saved locally

### Import Wallet (`3`)
Two methods:
- **Mnemonic import** — paste your 24-word phrase, set a password
- **Key file import** — provide paths to encrypted `payment.skey` and `stake.skey` files

### Multisig Wallets (`4`)
Opens the multisig submenu — see [Multisig section](#multisig-wallets) below.

### View Wallet Assets (`5`)
Shows ADA balance and native tokens for a selected wallet.

### Show Mnemonic Passphrase (`6`)
Decrypts and displays the mnemonic for a wallet (requires your password).

### Send Funds (`7`)
1. Enter the source wallet name
2. Enter the recipient's address
3. Enter the ADA amount (in ADA, e.g. `1.5`)
4. Optionally send a native token (policy ID, asset name, amount)
5. Enter your wallet password to sign and submit

### Delete Wallet (`8`)
Removes a wallet and its encrypted key files.

### Delete User (`9`)
Removes your user account and all associated wallet data.

### Pool Registration (`10`) / DRep Registration (`11`)
Stake pool and DRep registration entry points.

### Switch Backend (`12`)
Re-select network and backend without restarting.

### Debug Backend Health (`13`)
Shows connection status, detected network, and protocol parameters.

## Multisig Wallets

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

### Creating a multisig wallet (`3`)

1. Enter a wallet name
2. Choose the number of cosigners (e.g. `3`)
3. Choose the threshold (e.g. `2` for a 2-of-3)
4. For each cosigner:
   - Enter their key hash (or have the app derive it from a wallet on this machine)
5. The app builds a native script, derives the script address, and saves it

### Recovering a multisig wallet (`4`)

Accepts any of:
- **Script CBOR hex** — paste the raw hex string
- **Version-wrapped CBOR file** — binary `.cbor` exported by wallet backends (`[1, [script]]` envelope)
- **Wallet checkpoint package** — exported via option `10`
- **Transaction CBOR** — extracts the script from a transaction's witness set

The app reads the script, re-derives the address, and probes the chain for funds. If the script was inside a version envelope, it unwraps it and shows both readings — the chain decides which one holds funds.

### Which cosigner am I? (`5`)

Enter a script CBOR hex. The app searches your local wallets to find which cosigner key hashes match the script, and tells you your index.

### Build a transaction (`6`)

1. Select a multisig wallet
2. Enter the recipient address
3. Enter the ADA amount
4. Optionally add a token transfer
5. The app builds an unsigned transaction and saves it as a `.cbor` file

### Sign a transaction (`7`)

1. Import an unsigned or partially-signed `.cbor` file (file path, paste hex, or drag-and-drop)
2. Select which of your wallets holds a cosigner key for this script
3. Enter your wallet password
4. Your signature is added and the updated `.cbor` is exported

**Relay mode:** if you import a file that already has signatures, the app accumulates your signature, exports the updated file, and auto-submits once the threshold is met.

### Assemble and submit (`8`)

1. Import a fully-signed `.cbor` file (or paste hex)
2. The app verifies all signatures against the script hash
3. Confirms and submits the transaction to the network
4. Displays the transaction ID on success

### Delegate stake to a pool (`9`)

1. Select a multisig wallet
2. Enter the pool ID to delegate to
3. The app builds a registration + delegation certificate into a signing session
4. Export → relay sign → submit (same flow as spending)

### Export wallet checkpoint (`10`)

Creates a portable package (`.cbor` or `.json`) containing the script, key hashes, and metadata. Send this to a cosigner who doesn't have the wallet — they can import it via option `4`.

### Export transaction package (`11`)

Exports an unsigned or partially-signed transaction as a `.cbor` file. Send it to the next cosigner to sign.

## Data storage

| Path | Contents |
|---|---|
| `CardanoInterface/users/` | Encrypted user data (`*.userdb`) |
| `CardanoInterface/wallets/` | Encrypted keys, mnemonics, network tags per wallet |
| `debug_main.log` | Debug log (auto-created) |

All wallet keys and mnemonics are encrypted at rest with your password.

## Network warning

Wallets are network-specific. A wallet created on Preprod cannot hold or spend Mainnet funds (and vice versa). The app warns you if a wallet's network doesn't match the selected backend.
