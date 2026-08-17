# Multisig Wallet — Specification

## Problem

Cardano wallets are sunsetting. Nami deprecated. Typhon's multisig Catalyst proposal rejected. Lace shared wallets are beta with a 3-co-signer limit. Users with multisig funds need a local, sovereign tool that doesn't depend on any hosted service or wallet vendor.

CardanoInterface already has Ogmios+Kupo backends (local node bridge + local UTxO indexer) and a Blockfrost backend (remote). It manages wallets with encrypted keys. It builds and signs transactions. Multisig is the natural extension.

The hardest real-world case this must solve is **disaster recovery**: a multisig provider dies and leaves its users with nothing but an opaque `.cbor` file (a native script) they cannot even read in plaintext. They must be able to recover the wallet from that `.cbor`, round up the surviving cosigners, and move the funds — without trusting any central service or handing any private material to anyone.

## What This Adds

1. **Recover multisig wallets from a `.cbor`** (native script) — the spine of this system, and the disaster-recovery path.
2. **Recover multisig wallets from a CardanoInterface Package** — seamless restore of a wallet that was made with this software.
3. **Create multisig wallets** — from cosigner key hashes (never xpubs).
4. **Sign transactions cooperatively** — each cosigner signs with their own personal wallet; signatures travel inside a relayed CBOR.
5. **Build, assemble, and submit** transactions spending from a script address.
6. **Delegate stake** — CIP-1854 delegation templates (parked).

## Operating Model

This section is the foundation. Everything else in this spec follows from it.

### Peer-to-peer, sovereign nodes

Each cosigner runs their **own independent CardanoInterface instance** with:
- their **own HD wallet**, derived from their **own mnemonic**, kept only on their machine;
- their **own backend** — a local Ogmios+Kupo stack or a remote Blockfrost endpoint.

There is no shared server, no shared backend, no coordinator service, and no shared key material. Coordination between cosigners is minimal and travels over whatever channel they choose (Signal, email, USB).

### The coordination invariant (non-negotiable)

The **only** things that ever cross machines are:

1. **Key hashes** — `blake2b-224(verification_key)`. These are public. They are literally what sits inside a native script and on-chain. Sharing a key hash reveals nothing that lets anyone derive a key, derive addresses, or sign.
2. **Signatures** — `{verification_key, ed25519_signature}` pairs, traveling **inside** the relayed transaction CBOR. Each signature is bound to the transaction body hash.

**Never** shared, never stored on another machine: a cosigner's mnemonic, signing key (`.skey`), or account xpub. In the current implementation no xpub is derived at all, anywhere — the concept has no role left in this system.

> Both exchange topologies in this spec (operator-collect and relay) honor this invariant, because in both only signatures travel — never wallet/private data.

### Why key hashes, not xpubs

The script is a native script over **key hashes**, not over xpubs. Key hashes are safe to share (public, on-chain). xpubs are not shared, because an xpub lets anyone derive child public keys and observe the whole address family of that cosigner — that is private information belonging to the cosigner alone. Therefore:

- A wallet's identity is built from **key hashes**, never from a set of shared xpubs.
- The script address is derived from the **script hash**, not from combining cosigner xpubs.
- There is no centralized "derivation from everyone's xpub" step anywhere in this system.

### Anti-tampering

Every signature is over the transaction body hash (`tx_body.hash()`). Touch the body — change an output, a fee, an input — and every collected signature becomes invalid against the new body hash. Assembly verifies each witness cryptographically against the body hash before counting it toward the threshold. This is what makes the review screen binding: the destination a cosigner sees and signs is the destination that gets submitted, or the signatures don't verify.

### The signing chain, and the "admin" as terminator

To spend, the transaction must accumulate enough signatures to meet the threshold. Because someone has to be the last signer and submit, this spec defines an **administrator** role whose job is to be the **terminator**: the admin is the **last cosigner** in the signing chain, adds the final signature, and submits. This is a practical necessity ("someone has to be last"), not a grant of power. The admin's signature is subject to the same threshold as everyone else's.

**The optional gating feature is a separate thing** (see §Wallet Roles). Gating = "only the admin may build / assemble / submit." Gating is **optional** and off by default. Do not conflate the terminator role (inherent to the signing chain) with gating (an optional software policy). With gating off, any cosigner can build and assemble; the admin is still the natural terminator because they submit.

## Cardano Native Script Grammar

Reference: https://github.com/cardano-foundation/CIPs/blob/master/CIP-1854/README.md

A multisig wallet's identity is a **native script**. Phase-1 script types:

- `sig(keyHash)` — requires a signature from the key whose hash is `keyHash`
- `all([scripts])` — ALL sub-scripts must be satisfied
- `any([scripts])` — AT LEAST ONE sub-script must be satisfied
- `atLeast(m, [scripts])` — M of K sub-scripts must be satisfied
- `after(slot)` — timelock: valid after slot
- `before(slot)` — timelock: valid before slot

The default wallet shape is `atLeast(m, [sig(h_0), sig(h_1), ... sig(h_{n-1})])` where each `h_i` is a cosigner's key hash. The script grammar supports nesting and timelocks, but the interactive creator only emits `atLeast`.

**Key rule:** cosigners share **key hashes**. They never share xpubs, mnemonics, or signing keys. (Earlier drafts of this system assumed cosigners shared account xpubs for shared address derivation. That assumption is rejected: it violates the coordination invariant and is not how this system works.)

**Concurrent signing:** "Several cosigners may submit the transaction concurrently without issue, the ledger will ensure that only one transaction eventually gets through." (CIP-1854 §Sending Transactions)

## Wallet Identity and Node Roles

A multisig **wallet** is defined purely by its native script. Its identity is:

| Artifact | Derivation |
|---|---|
| `script` (native script) | built from cosigner key hashes + threshold |
| `script.cbor` | the script, hex-encoded CBOR |
| `script_hash` | `blake2b-256(script CBOR)` |
| `script_address` | bech32 address derived from `script_hash` (payment credential) |

This identity is **immutable and re-derivable**. Anyone with `script.cbor` can re-derive the hash and address and verify them. `script.cbor` alone rebuilds the wallet's identity.

A CardanoInterface **node** (one machine running one instance) has a *relationship* to a wallet, not a wallet "type." Two axes matter:

### Axis 1 — Does this node hold a signing key for this wallet?

- **Cosigner node** — this machine holds a personal HD wallet whose payment key hash is one of the script's key hashes. It **can sign**.
- **Observer node** — this machine has the script but no key whose hash is in the script. It **cannot sign** but can build, assemble, and submit.

### Axis 2 — How did this node obtain the script?

- **Created** — a cosigner gathered the other cosigners' key hashes, chose a threshold, and built the script (see §Creation).
- **Recovered from `.cbor`** — obtained a raw native-script CBOR from a dead provider, a block explorer, or a cosigner's archive, and imported it (see §Recovery).
- **Restored from a Package** — imported a CardanoInterface Package exported by a node that had the wallet (see §Recovery).

These two axes are independent. A recovered-from-`.cbor` wallet on a cosigner node behaves identically to a created wallet on a cosigner node: the node can sign because its key hash is in the script. **There is no second-class "recovery wallet" that signs differently.** Recovery only changes how the script arrived, not how signing works.

> Implementation note: earlier drafts stored a separate `multisig_recovery` type whose signing path differed. Under the unified model, every wallet signs the same way — with the node's personal wallet, validated by key-hash membership in the script. The `recovery` flag remains only as a provenance marker (how the script arrived), not as a distinct signing mode.

## Wallet Roles — Optional Administrator Designation

### Terminator role (inherent, default)

Every signing chain needs a terminator: the last cosigner to sign, who then submits. By convention the **administrator** is the terminator. This is practical, not a privilege — the admin's signature counts toward the threshold exactly like any other cosigner's, and someone must submit the final transaction.

### Optional gating (off by default)

Separately, a wallet **may optionally** designate an admin and enable **gating** — a software policy that restricts who may **initiate** spend flows (build, assemble, submit) to the admin. Gating is:

- **optional** — off by default; the wallet works fully without it;
- **off-chain / software-enforced** — the ledger knows nothing about it (native scripts express signing constraints only, not "this key is the operator");
- **not the same as the terminator role** — even with gating off, the admin remains the natural terminator because they submit.

When gating is **off** (default): any cosigner can build, assemble, and submit; any observer can build and assemble. When gating is **on**: only the admin may build, assemble, and submit; other cosigners can still sign (otherwise the threshold could never be met).

### Why gating is software-enforced, not on-chain

Cardano native scripts express signing constraints only (`sig`, `all`, `any`, `atLeast`, `after`, `before`). There is no primitive that says "this key is the operator." Therefore:

- The admin's on-chain authority is identical to any cosigner's: whether their signature is needed is decided by the threshold, not by the admin label.
- The admin's off-chain authority ("only they initiate") is a **software policy** enforced by CardanoInterface. An attacker who bypassed the software policy could build, but could not spend without the real cosigner signatures the threshold requires.

### Storage of the admin designation

When gating is enabled, `config.json` carries:

```json
{ "admin_cosigner_index": 0 }
```

When gating is off, the field is **absent** (do not write `null`). Absence means "any cosigner can initiate" (the default). `null` is reserved for an observer node that has no signing key and therefore cannot be admin.

### Tamper barrier

Because `config.json` is editable, an attacker with file access can add, remove, or change `admin_cosigner_index` without breaking any signature. What they gain is **initiator authority** (build/assemble/submit, including picking the destination) — **not** signing authority. The threshold still requires real cosigner signatures. The tamper-proof anchors are:

- `script.cbor` → `script_hash` → `script_address`. Swapping the script changes the address and breaks signature verification against the original UTxOs.
- The signed `tx_body.hash()` for each transaction. Signatures verify against the unsigned body hash at assembly time; swapping the body after collecting signatures invalidates every signature.

So: the script hash binds the **wallet** immutably; the tx body hash binds each **transaction** immutably. Gating binds neither — it is policy. The defense against config tampering is the CBOR Package checkpoint (see §CBOR Package), exported by the admin and re-importable to restore the exact original state. (Tamper-evident admin-signed checkpoints are a parked future feature.)

### Gating is not in scope for the current work

Gating is documented here for completeness. The current work (recovery + relay + validation) does **not** implement gating; the wallet runs in default (gating-off) mode, where any cosigner can build/assemble/submit and the admin acts as terminator. See §Implementation State.

## Architecture

### Storage Model

Each multisig wallet known to a node gets a directory under `CardanoInterface/wallets/`:

```
CardanoInterface/wallets/
└── multisig_myproject/
    ├── type                  # "multisig" (provenance marker only)
    ├── network.txt           # "mainnet" | "preprod" | "preview"
    ├── script.json           # native script (JSON, human-readable)
    ├── script.cbor           # native script (CBOR hex) — the wallet's immutable identity
    ├── script_hash           # hex string
    ├── script_address        # bech32 address (payment credential)
    ├── config.json           # threshold, key hashes, labels, optional admin
    ├── known_indices/        # cached balance read from the script address
    │   └── 0.json
    └── sessions/             # signing session data
        └── <session_id>/
            ├── unsigned.cbor
            ├── unsigned_text_envelope.json
            ├── partial.cbor
            ├── partial_text_envelope.json
            ├── summary.json
            └── status.json
```

**What is deliberately NOT stored:**

- No `cosigner_N.xpub` files. Other cosigners' xpubs are never collected and never stored. (Earlier drafts stored them; that model is rejected — it violates the coordination invariant.)
- No derived-from-shared-xpubs address family. There is one script address (per script), derived from the script hash.
- **No signing key of any kind, not even this node's own.** Earlier drafts kept a copy of the operator's mnemonic in `signing_keys/mnemonic.enc` so the wallet could sign "by itself". That is removed: a cosigner signs with the personal wallet they already have, so a multisig directory contains nothing an attacker could spend with. A stolen multisig directory yields the script — which is public and on chain anyway — and nothing else.

A cosigner's own key hashes are cached, in the clear, in **their personal wallet's**
directory (`cosigner_key_hashes.json`), never in the multisig directory. Key hashes are
public by definition; caching them means matching a wallet against a script costs no
password.

A node that obtained the script by recovery from `.cbor` stores the same layout; the only difference is a provenance flag in `config.json` recording that the script came from a `.cbor` rather than being locally created.

### Key Files

| File | Format | Purpose |
|---|---|---|
| `type` | plain text | `"multisig"` (provenance only; signing behavior is identical regardless of how the script arrived) |
| `script.json` | native script JSON | Human-readable display and reconstruction |
| `script.cbor` | Hex-encoded CBOR | The wallet's immutable identity; attached to transactions as the script witness |
| `config.json` | JSON | Threshold, cosigner key hashes, optional human labels, optional `admin_cosigner_index` |
| `known_indices/0.json` | JSON | Cached balance at the script address; re-readable at any time |

No signing key lives in a multisig directory. The credential is the cosigner's own
personal wallet, named in the script by key hash and unlocked at sign time.

### config.json

```json
{
  "name": "My Project Wallet",
  "network": "mainnet",
  "threshold": 2,
  "num_cosigners": 3,
  "key_hashes": [
    "5cf83814d34e73cb1dff5f884376b741c798ee4698382d5a99ca3d8c",
    "31ef571725ec4a7dc0c36ab4dcee57863e4f140cf3b6be1f8096c422",
    "a1b2c3d4e5f6...third cosigner key hash..."
  ],
  "labels": {
    "5cf83814d34e73cb1dff5f884376b741c798ee4698382d5a99ca3d8c": "Alice",
    "31ef571725ec4a7dc0c36ab4dcee57863e4f140cf3b6be1f8096c422": "Bob"
  },
  "script_type": "atLeast",
  "provenance": "created",
  "claimed_signer_index": 0
}
```

Field notes:

- `key_hashes` — the cosigner key hashes extracted from the script. These are public; they are the script's contents.
- `labels` — **optional** human-readable names keyed by key hash, so the UI shows "Alice" instead of `cosigner_0` / a truncated hash. A node labels only what it knows; labels are local convenience, never shared.
- `script_type` — informational; the script grammar is read from `script.cbor` authoritatively.
- `provenance` — `"created"` or `"recovered_cbor"` / `"restored_package"`: how this node obtained the script. Informational only; signing behavior does not depend on it.
- `claimed_signer_index` — if this node's personal wallet key hash matches one of `key_hashes`, the index of that match. Lets the sign flow preselect the right identity instead of asking the user which wallet to sign with. (Earlier drafts wrote `null` and never used it; it is wired in the current model.)
- `admin_cosigner_index` — **optional**, present only when gating is enabled (see §Wallet Roles). Absent in default (gating-off) mode.

## Recovery

Recovery is the spine of this system. There are **two first-class recovery doors**, depending on what the user was left with. Neither is a fallback for the other — they serve different situations, and the unified importer auto-detects which one the user provides.

### Door 1 — Raw `.cbor` (external / legacy)

The disaster case. A multisig provider died and left nothing but an opaque `.cbor` file (a native script) the user cannot read in plaintext. CardanoInterface:

1. Reads and decodes the CBOR as a native script.
2. Shows the user what it is: script type, threshold, the cosigner key hashes, and the derived script address.
3. Writes the wallet (script + key hashes + threshold + address) and registers it.

This door is first-class because it is the input the user **cannot choose** — when a dead service leaves only a `.cbor`, that is what recovery must consume.

### Door 2 — CardanoInterface Package (CI-native)

The good case. The wallet was made with CardanoInterface, and someone exported a Package (checkpoint). Restore picks up the script **plus** the off-chain context the package carries (human labels, threshold, optional admin designation, derivation hints). Seamless, because all context is bundled.

### The `.cbor` recovery progression (canonical walkthrough)

Regardless of door, spending a recovered wallet follows the same three steps:

1. **Recover** — import the `.cbor` (or Package) → the node has the script, key hashes, threshold, address.
2. **Sign cooperatively** — each cosigner opens the transaction under construction in their own CardanoInterface, reviews it, and signs with their **own personal wallet**. Signatures travel inside the relayed CBOR. A cosigner's signature is accepted iff their personal wallet's key hash is one of the script's key hashes (membership) AND the signature verifies against the body hash (anti-tampering).
3. **Terminate** — the administrator, as the last cosigner, adds the final signature and submits. (With gating off, any cosigner could equally submit; the admin is the terminator by convention.)

Only signatures travel. No cosigner's mnemonic, `.skey`, or xpub ever leaves their machine.

### Recovery Scenarios (starting material)

These describe what a node can do from each starting set. They map onto the two doors and the three exchange topologies (§Exchange Topologies).

| Scenario | Starting material | Can recover identity? | Can sign? | Recommended topology |
|---|---|---|---|---|
| **A** | All cosigners' mnemonics, on one machine | yes (rebuild script from key hashes) | yes, all keys present | **Local** (no exchange) |
| **B** | Script + own mnemonic; others have theirs | yes | yes (own key); others sign on their nodes | Relay or operator-collect |
| **C** | Script CBOR only (dead provider), no keys | yes (Door 1) | yes — each cosigner signs with their personal wallet | Relay (peer) or operator-collect |
| **D** | Script address / hash only | no (cannot reconstruct script) | no | — find the script first |
| **E** | Own mnemonic only (no script) | no (cannot reconstruct script without cosigner key hashes) | cannot meet threshold alone | — contact other cosigners |

**The recurring rule:** the script (`.cbor`) rebuilds the wallet's identity; each cosigner's personal wallet rebuilds their own signing key. Both are required to spend. Neither requires sharing xpubs, mnemonics, or signing keys.

### Recovery Trust Analysis

Recovery assumes the originating software may be gone (machine lost, provider dead). What survives:

| Asset | Survives? | Format | Rebuilds |
|---|---|---|---|
| Cosigner mnemonics (each cosigner's own 24 words) | ✓ off-chain (metal, paper, memory) | BIP-39 | That cosigner's keys at the derivation path |
| `script.cbor` (native script) | ✓ if recorded anywhere (the chain itself carries it for spends) | Hex CBOR | Script address, threshold, key hashes, script type — the wallet's immutable identity |
| `config.json` (labels, admin) | ✗ lost with the machine | JSON | Labels/admin are policy, re-enterable; key hashes/threshold re-derive from the script |
| Cosigner xpubs | n/a — never collected in this model | — | Not used; addresses derive from the script hash |
| A cosigner's signing key | n/a — never stored in the multisig wallet | — | It is their personal wallet, restored from their own 24-word backup like any wallet |

### Trusted destination

In any spend — including a recovery spend — whoever builds the transaction picks the recipient address. This is a trust point **independent of signing trust**:

- **Signing trust** = does this cosigner's key hash match the script? (cryptographically verifiable)
- **Destination trust** = is the recipient address correct and safe? (human judgment by the builder)

Other cosigners MUST review the destination in the transaction review UI before signing. Their signature authorizes the destination they see. The assembly signature-verification step guarantees the body the submitter assembles IS the body every cosigner signed — no destination swap between signing and submission is possible.

## Exchange Topologies

There are three first-class ways signatures get combined into a submittable transaction. All three honor the coordination invariant (only signatures travel).

### Topology 1 — Local (no exchange)

All required keys are on one machine (Scenario A). One node builds, signs with every key it holds, and submits. No CBOR crosses machines. This is the simplest case and the baseline.

### Topology 2 — Operator-collect (hub)

One node (the operator/admin) acts as a hub:

```
OPERATOR                         COSIGNER i
   |                                |
   | 1. Build unsigned tx           |
   | 2. Send unsigned CBOR ------> |
   |                                | 3. Review + sign with own wallet
   | 4. Receive partial CBOR <----- |
   | 5. Verify signature            |
   | 6. (repeat per signer)         |
   | 7. Threshold met               |
   | 8. Submit                      |
```

Each cosigner returns a **partial** (their signature over the body hash). The operator collects the partials, verifies each against the body hash, merges the witness sets, and submits. The operator holds a collection of partials (signatures) but never any cosigner's private data. Suitable when one party naturally orchestrates (e.g., a designated admin, or a Type-B-style observer node that builds but does not sign).

### Topology 3 — Relay (peer chain)

No central hub. The transaction is the envelope and accumulates signatures as cosigners pass it along:

```
INITIATOR    COSIGNER A    COSIGNER B    ...    ADMIN (terminator)
   |             |             |                    |
   | build       |             |                    |
   | unsigned -> |             |                    |
   |             | review+sign |                    |
   |             | 1-sig tx -> |                    |
   |             |             | review+sign        |
   |             |             | 2-sig tx -> ...    |
   |             |             |             ... -> | review+sign (last)
   |             |             |                    | submit
```

Each hop: a cosigner imports the (partially-)signed transaction, reviews it (including who has already signed), adds their own witness, and forwards the now-more-signed transaction. The **administrator is the last cosigner** — they add the final signature and submit. No node maintains a per-cosigner partial store; the traveling transaction is the only signature carrier. Suitable for peer recovery (Scenario C) and ad-hoc cooperative signing.

> Note: in a relay, each intermediate cosigner necessarily sees the signatures of those who signed before them (they are inside the traveling tx). This is expected and fine — those are public signatures, not private data. The invariant that holds is: no cosigner's mnemonic/`.skey`/xpub ever crosses machines.

### Topology selection

| Situation | Topology |
|---|---|
| All keys on one machine | Local |
| One party orchestrates, others just sign-and-return | Operator-collect |
| Cosigners are peers, no hub, recovery scenario | Relay |

All three produce the same on-chain result: a transaction whose witness set satisfies the script. They differ only in workflow.

## Carrier

Two carriers are used to move scripts and transactions between nodes. **Both are first-class; neither is a fallback.** They serve different situations:

- **CardanoInterface Package (JSON)** — the canonical carrier for CardanoInterface ↔ CardanoInterface exchange. Self-describing: it bundles the script (cbor + hash + address), the transaction (stage, cbor, inputs, outputs, required signers, signatures collected, threshold), a cardano-cli-compatible TextEnvelope, and human-readable instructions. A node importing a Package immediately knows the full context (which wallet, what stage, who has signed) and can recover the wallet from the embedded script if it doesn't have it. This is the carrier for the Package recovery door and for CI-native signing exchange.
- **Raw CBOR / TextEnvelope** — the carrier for the `.cbor` recovery door (the disaster input is a bare script CBOR, not a Package) and for cardano-cli / third-party-tool interop. Minimal: just the bytes. No context, so the importing node must identify the wallet itself (or recover one from the script).

The relay and operator-collect topologies both work with either carrier. With the Package, each relay hop re-exports the package with the accumulated transaction and a growing `signatures_collected` list; with raw CBOR, each hop re-emits the accumulated transaction CBOR. (Earlier drafts framed raw CBOR as a "fallback" for Package. That framing is rejected: raw CBOR is the required input for `.cbor` disaster recovery and is first-class in its own right.)

## CBOR Package — Checkpoint & Exchange Format

The CBOR Package is a single, self-contained JSON file that bundles the wallet's immutable identity (script CBOR + hash + address) with off-chain context (labels, optional admin, threshold) and human-readable instructions. Two package kinds, distinguished by the `type` field:

1. **Wallet Checkpoint** — export the full wallet state to restore later, on any machine, with context intact. Solves the config-loss tamper vector.
2. **Transaction Package** — export an unsigned, partial, or fully-signed transaction together with the script it spends from, with instructions for importing in cardano-cli, CardanoInterface, or any compliant tool.

The Package is the canonical CI↔CI carrier (§Carrier). **Both package kinds are implemented** (see §Implementation State): `export_wallet_checkpoint`, `export_transaction_package`, and the unified `import_package` (which auto-detects kind and also accepts raw script CBOR and TextEnvelope).

### Wallet Checkpoint Package (`type = "CardanoInterface Wallet Checkpoint"`)

```json
{
  "type": "CardanoInterface Wallet Checkpoint",
  "version": 1,
  "created": "2026-08-13T00:00:00Z",
  "network": "preprod",

  "script": {
    "cbor_hex": "8303028200581c5cf8...8200581c31ef...",
    "hash": "ea333520d24e1e567bb1dfd28250104159d12a74ced495ad2e702e54",
    "address": "addr_test1wr4rxdfq6f8pu4nmk80a9qjszpq4n5f2wn8df9dd9eczu4qajweta",
    "json": {
      "atLeast": {
        "required": 2,
        "scripts": [
          { "sig": { "keyHash": "5cf83814d34e73cb1dff5f884376b741c798ee4698382d5a99ca3d8c" } },
          { "sig": { "keyHash": "31ef571725ec4a7dc0c36ab4dcee57863e4f140cf3b6be1f8096c422" } }
        ]
      }
    }
  },

  "config": {
    "name": "my_project_wallet",
    "threshold": 2,
    "num_cosigners": 2,
    "key_hashes": [
      "5cf83814d34e73cb1dff5f884376b741c798ee4698382d5a99ca3d8c",
      "31ef571725ec4a7dc0c36ab4dcee57863e4f140cf3b6be1f8096c422"
    ],
    "labels": { "5cf83814...": "Alice", "31ef5717...": "Bob" },
    "admin_cosigner_index": 0
  },

  "admin_signature": null,

  "instructions": {
    "cardano_interface": "To restore: Multisig Wallets → Import Package → select this file. Script hash is verified against the script CBOR; config is restored from the package.",
    "cardano_cli": "Script address: addr_test1wr4rxdfq6f8pu4nmk80a9qjszpq4n5f2wn8df9dd9eczu4qajweta\nNetwork: preprod (testnet-magic 1)\nThreshold: 2 of 2\n\n# Extract script.json from this package's script.json field, then:\ncardano-cli transaction build \\\n  --tx-in <txhash>#<ix> --tx-in-script-file script.json \\\n  --tx-out <recipient>+<lovelace> --out-file tx.body --testnet-magic 1\ncardano-cli transaction sign --tx-body-file tx.body \\\n  --signing-key-file cosigner0.skey --signing-key-file cosigner1.skey \\\n  --out-file tx.signed --testnet-magic 1\ncardano-cli transaction submit --tx-file tx.signed --testnet-magic 1",
    "general": "Cardano multisig wallet checkpoint. The script CBOR is the wallet's immutable identity; its hash is the wallet's address. The config carries off-chain policy (labels, optional admin, threshold). On import, CardanoInterface re-derives the script hash and verifies it."
  }
}
```

**Field reference:**

| Field | Purpose | Tamper-proof? |
|---|---|---|
| `script.cbor_hex` | NativeScript CBOR — the wallet's immutable identity | ✓ (hash re-derivable) |
| `script.hash` | Blake2b-256 of the script CBOR | ✓ (re-derive to verify) |
| `script.address` | Bech32 address from the script hash | ✓ (re-derive to verify) |
| `script.json` | Human-readable NativeScript dict | ✓ (re-derive from CBOR) |
| `config.threshold` | Required signatures | ✓ (derived from script) |
| `config.key_hashes` | Cosigner key hashes | ✓ (the script's contents) |
| `config.labels` | Human-readable names per key hash | ✗ (policy, local convenience) |
| `config.admin_cosigner_index` | Admin designation (gating) | ✗ (policy — see `admin_signature`) |
| `admin_signature` | Admin's ed25519 signature over the checkpoint (parked feature) | ✓ if present |

Note: this model carries **key hashes** in the config, not cosigner xpubs. xpubs are never exported in a package.

### Transaction Package (`type = "CardanoInterface Transaction Package"`)

```json
{
  "type": "CardanoInterface Transaction Package",
  "version": 1,
  "created": "2026-08-13T00:00:00Z",
  "network": "preprod",

  "script": {
    "cbor_hex": "8303028200581c...",
    "hash": "ea333520d24e1e56...",
    "address": "addr_test1wr4rxdfq6f8pu4..."
  },

  "transaction": {
    "stage": "unsigned",
    "cbor_hex": "84a30081825820...",
    "tx_id": "abc123def456...",
    "fee_lovelace": 165897,
    "inputs": [{ "tx_hash": "732bfd67...", "index": 0 }],
    "outputs": [{ "address": "addr_test1...", "lovelace": 1000000 }],
    "required_signers": ["5cf83814...", "31ef5717..."],
    "signatures_collected": [],
    "threshold": 2,
    "signatures_needed": 2
  },

  "text_envelope": {
    "type": "Unwitnessed Tx BabbageEra",
    "description": "Ledger Cddl Format",
    "cborHex": "84a30081825820..."
  },

  "instructions": {
    "cardano_interface": "To sign: Multisig Wallets → Import Package → select this file → review → sign with your wallet password. To forward (relay): re-export the package after signing and send to the next cosigner. To submit (admin/terminator): once threshold met, submit.",
    "cardano_cli": "# text_envelope.cborHex is cardano-cli compatible.\ncardano-cli transaction sign --tx-body-file <(echo '{...cborHex...}') \\\n  --signing-key-file payment.skey --out-file partial.signed --testnet-magic 1\n# Forward partial.signed for assembly, or submit when fully signed:\ncardano-cli transaction submit --tx-file final.signed --testnet-magic 1",
    "general": "Transaction stage: unsigned (no signatures yet). Threshold: 2 of 2. Every cosigner's signature is over the transaction body hash; assembly verifies each signature against the body hash before counting it toward the threshold."
  }
}
```

`stage` ∈ `{"unsigned", "partial", "final"}`. A `"partial"` package lists who has signed in `signatures_collected`. A `"final"` package has `signatures_needed == 0` and `text_envelope.type == "Witnessed Tx BabbageEra"`.

### Tamper-evident checkpoints (admin signature) — parked

If the admin was designated, export could optionally sign the checkpoint with the admin's payment signing key over the canonical JSON bytes (excluding `admin_signature`). On import, CardanoInterface verifies the signature against the admin's vkey hash (which is in the script). This makes the checkpoint tamper-evident. **Status: not implemented; parked.** The current checkpoint import re-derives the script hash and verifies it, but does not cryptographically authenticate the admin designation.

### Import flow

The unified importer (`import_package`) auto-detects the input:

1. Read the input (paste, file path, JSON, or raw hex).
2. **Classify:**
   - JSON with `type: "CardanoInterface Wallet Checkpoint"` → checkpoint restore.
   - JSON with `type: "CardanoInterface Transaction Package"` → transaction import.
   - JSON TextEnvelope (`type: "... Tx BabbageEra"`) → transaction import (raw).
   - Bare hex that decodes as a native script → `.cbor` recovery (Door 1).
   - Bare hex that decodes as a transaction → transaction import (raw).
3. **Checkpoint restore:** re-derive script hash from `script.cbor_hex`, compare to `script.hash` (reject on mismatch); re-derive address, compare (reject on mismatch); check `network` against `SELECTED_NETWORK` (reject on mismatch); write the wallet; restore config (labels, optional admin).
4. **Transaction import:** match the package's script hash to a known wallet (by `script_hash` file); if none matches, offer to recover the wallet from the script first. Then, by stage: `unsigned` → review + sign; `partial` → review + add signature + forward; `final` → submit.

## Operations

### Creation (from key hashes)

Because no xpubs are shared, creation is:

1. Each cosigner, on their own node, derives their personal wallet's payment key hash and shares **only the hash** with the initiating cosigner (over any channel).
2. The initiator collects the key hashes, chooses a threshold (M-of-N), and builds the native script from the hashes.
3. The initiator derives the script address from the script hash, writes the wallet (script + key hashes + threshold + address), and shares the script (as `.cbor` or a Package) with the other cosigners.
4. Each cosigner imports the script (Door 1 or Door 2) on their node. Their node detects that their personal wallet's key hash is in the script and records `claimed_signer_index`.

In the software: each cosigner runs **Show My Cosigner Key**, picks a wallet they already
have, and reads off a 56-character hex string. The initiator runs **Create a multisig
wallet**, pastes each hash (or picks their own wallet with `me`), and chooses a threshold.
`build_native_script_from_key_hashes` builds the script; no xpub is involved at any point.

### Build unsigned transaction

`build_multisig_transaction` queries UTxOs at the script address via the backend, constructs a transaction body spending those UTxOs to the chosen recipient (change back to the script address), attaches the native script as the script witness, and sets a validity window wide enough for cooperative signing (default 7 days). Outputs the unsigned CBOR + a cardano-cli TextEnvelope + (optionally) a Transaction Package.

Key builder decisions:
- `witness_override = max(1, number_of_script_key_hashes)` — PyCardano does not descend into `atLeast` scripts during fee estimation, so the witness count is set explicitly.
- `required_signers` is left unset on the body (setting it would force N-of-N); the script itself constrains signing.
- Change returns to the script address.

### Sign transaction

Each cosigner signs with their **own personal wallet**. The flow starts from the
**transaction**, not the wallet, because a cosigner is normally sent a transaction and
nothing else:

1. Take the transaction (a file, a paste, or a session already on this machine).
2. Work out which wallet it belongs to from the **script inside it**. If this node does not have that wallet, offer to rebuild it from that script — a cosigner should never be blocked by not already running the wallet they are being asked to sign for.
3. Identify the signing identity automatically: the node's personal wallet whose key hash the script names, matched at **both** CIP-1852 and CIP-1854 (cached, so this normally costs no password). Ask only when there is a real ambiguity — several matches — or no match at all.
4. Show the full review: inputs, outputs with **destination** and change labelled, fee, threshold, and who has already signed — counting the signatures already inside the incoming CBOR, not just session partials.
5. Confirm; unlock the personal wallet (re-prompting on a wrong password); sign `tx_body.hash()`.
6. Emit the signature. In relay, merge it into the incoming transaction's witness set and re-export the (more-signed) transaction; if the threshold is now met, offer to submit. In operator-collect, emit a single-witness partial.

The signature is accepted only if the signing key hash is one of the script's key hashes (membership) — checked immediately, so a non-cosigner is told at sign time, and shown both of the key hashes their wallet actually produces alongside the ones the script wants, rather than producing a partial that assembly silently discards.

### Assemble and submit

Merge the collected witnesses into one witness set alongside the native script, verify each witness cryptographically against the body hash, verify each witness's key hash is in the script, dedupe by key hash, and confirm the threshold is met. Produce the final witnessed transaction and submit via the backend. In relay, this "merge" happens incrementally at each hop; the terminator runs the final submit.

## Implementation State

Corrected to reflect the actual code as of this revision. Legend: `✅ CODED` = function exists, compiles, passes static analysis; `✅ (unit)` = tested with generated data, no blockchain interaction; `❌ NO BACKEND` = requires a running backend and has never been executed against one; `❌ NOT CODED` = not implemented.

| Concern | Code State | Backend Tested | Details |
|---|---|---|---|
| Cosigner key derivation | ✅ CODED | ✅ on chain | `cosigner_key_hash_candidates` derives the cosigner key hash at **both** standards — CIP-1852 (`m/1852'/1815'/a'/0/i`, this program's personal wallets) and CIP-1854 (`m/1854'/1815'/a'/0/i`, shared-wallet providers) — and matching accepts whichever the script names. This is what makes a script written by somebody else's tool signable here. Verified: the CIP-1852 candidate is byte-identical to the key `derive_keys_from_mnemonic` signs with. |
| Native script building (from key hashes) | ✅ CODED | ✅ on chain | `build_native_script_from_key_hashes(threshold, key_hashes)` — the only builder. Rejects duplicate hashes, wrong-length hashes, and a threshold above the cosigner count. `extract_key_hashes_from_script`, `script_hash_from_script`, `script_to_address` read it back. |
| Threshold calculation | ✅ CODED | ✅ on chain | `calculate_threshold` — floor-based, for creation. |
| Threshold **read back from a foreign script** | ✅ CODED | ✅ vs CIP-29 vectors | `script_min_signatures` walks the tree: `sig`→1, `all`→sum, `any`→min, `atLeast(n)`→n cheapest, timelock→0. Reading a single field is wrong for anything but `atLeast`: an `any` script (joint account, either party spends alone) was previously reported as N-of-N, which would have made a recovered joint account unspendable here the moment one cosigner was gone. `script_is_flat_threshold` decides whether "m of n" is an honest summary or the UI must say "at least m" and warn about nesting/timelocks. |
| Wallet creation (key-hash model) | ✅ CODED | ✅ on chain | `create_multisig_wallet(wallet_name, key_hashes, percentage, labels, our_key_hash, ...)`. No xpubs are collected, stored, or exchanged; no cosigner mnemonic is copied into the multisig directory. |
| `.cbor` recovery import | ✅ CODED | ✅ on chain | `import_script_cbor` — parses a native-script CBOR, extracts key hashes/threshold, writes an ordinary wallet. Produces the identical address to the wallet it recovers. |
| Package export (checkpoint) | ✅ CODED | ✅ on chain | `export_wallet_checkpoint`. Carries script + key hashes + labels + provenance; carries no private material and no xpubs. `admin_signature` is `null` (tamper-evident signing is parked). |
| Package export (transaction) | ✅ CODED | ✅ on chain | `export_transaction_package` — builds the full self-describing package. |
| Package import (unified) | ✅ CODED | ✅ on chain | `import_package` → `_import_wallet_checkpoint` (verifies script hash **and** re-derives threshold/key hashes from the script, so a doctored config cannot lower a threshold) / `_import_transaction_package`. |
| Build unsigned tx | ✅ CODED | ✅ on chain | `build_multisig_transaction` — one script, one address; change returns to it. |
| Sign (personal wallet) | ✅ CODED | ✅ on chain | `sign_multisig_transaction(..., signer_wallet_dir=...)`. One path for every wallet however it arrived. Membership-checked against the script before anything is written; a mistyped password re-prompts instead of erroring out. |
| Signature verification at assembly | ✅ CODED | ✅ on chain | `_verify_witness(vk_w, body_hash)` per witness, plus script membership and dedupe, before counting toward the threshold. |
| Operator-collect assembly | ✅ CODED | ✅ on chain | `assemble_multisig_transaction` merges session partials, verifies each against the body hash, submits. |
| Relay topology | ✅ CODED | ✅ on chain | `sign_multisig_transaction(accumulate=True)` merges into the incoming witness set and re-exports. Wired into the TUI: a file/paste import takes the relay path, a session import stays operator-collect. The review counts signatures already inside the incoming CBOR, so a relay signer sees who signed before them. |
| Unified "recover or import" door | ✅ CODED | ✅ on chain | `classify_cbor` (native_script / unsigned_tx / witnessed_tx / unknown) behind one menu entry that also accepts packages and checkpoints. A cosigner sent a bare transaction no longer has to know what they hold. |
| Sign starts from the transaction | ✅ CODED | ✅ on chain | A cosigner who receives a transaction for a wallet they do not have is offered the wallet, rebuilt from the script inside the transaction (`_wallet_for_incoming_transaction`). Previously this dead-ended at "no multisig wallets found". |
| `claimed_signer_index` wiring | ✅ CODED | ✅ on chain | `restore_multisig_participation` matches the node's wallets against the script and records the index + which wallet signs; the sign flow preselects it, so the common case asks no question. Key hashes are cached (public) per wallet, so matching normally costs no password. |
| Signer labels | ✅ CODED | ✅ on chain | `config.labels` (key hash → name) drives display, falling back to `cosigner N` (counted from 1). |
| End-to-end on-chain validation | ✅ VALIDATED | ✅ on chain | Key-hash creation → funding → `.cbor` recovery → cooperative signing → verified assembly → submission, confirmed on preprod. See §On-chain validation record. |
| Optional admin gating (build/assemble/submit) | ❌ NOT CODED | — | Parked feature. `admin_cosigner_index` is written when designated but not enforced by wrappers. The wallet runs in default (gating-off) mode. |
| Tamper-evident admin-signed checkpoints | ❌ NOT CODED | — | Parked. |
| Delegation / stake script | ❌ NOT CODED | — | Parked (Phase 5). |
| Multiple addresses per wallet | n/a — by design | — | A wallet is its script, the script hash is the payment credential, so there is exactly one address. The old per-index address scan existed only because scripts were re-derived from xpubs; it is deleted. |
| Multi-asset in build | ❌ NOT CODED | — | Token params exist but are not wired to the interactive wrapper. ADA only. |

### On-chain validation record

All against live preprod through a local Ogmios (`queryLedgerState/utxo` supplies UTxOs
directly, so Kupo is not required for a script address).

**Key-hash model, full cycle (2026-08-17), tx `a99b9ddf43cb5c8b103a76e05590d9def382ffbd80a2526a629276d0f689cde6`:**
three fresh cosigner wallets created → each publishes only a key hash → a 2-of-3
script built from those hashes alone → script address funded with 5 ADA (tx
`16ad3daca7305754f844e0b952aa4ffe915ae65a9242d6f0724da184c933e44f`) → **the wallet
directory deleted outright**, leaving only the CBOR hex → recovered through Door 1
to the byte-identical address → checkpoint exported → balance read → 2 ADA spend
built → signed by two cosigners with their own personal wallets → assembly verified
both signatures against the body hash → submitted → confirmed. This is the
disaster-recovery spine proven end to end on real funds.

**Relay through the TUI (2026-08-17), tx `92dbf17798c409070479cafb35bc671d3bfc7b090b3ace53deb69bb9b3222f98`
(2-of-2) and `d16149e6251c6442bfa80e9ec2520f6e716d470eef3b115dd45eee5138700fca` (2-of-3):**
driven entirely through the real menus as three separate people. The admin builds and
exports the unsigned CBOR; a cosigner **who does not have the wallet at all** imports the
file, is offered the wallet rebuilt from the script inside it, is auto-identified as
cosigner 2, reviews destination and amount, signs, and exports the accumulated CBOR;
the admin imports that, **sees "1 of 2 collected, ✓ signed cosigner 2"**, signs last and
submits. Both confirmed on chain. This is the operating model the system exists for:
signers sign the CBOR in this software, and the admin triggers the transaction once the
signatures are in.

**Foreign-script parsing against the official CIP-29 test vectors (2026-08-17):** all four
vectors — bare `sig`, nested `all[sig, any[after 42, sig]]`, a bare timelock, and
`atLeast 2 of 3` — parse, hash, and derive an address correctly. These are shapes this
program never generates, which is the point: recovery has to read whatever a dead
provider left. The nested vector is what exposed the `any`/`all` threshold bug fixed by
`script_min_signatures`. A timelock-only script (no keys at all) is refused with a clear
message rather than producing an unusable wallet.

Reproduce with the vectors from [CIP-29](https://cips.cardano.org/cip/CIP-29) through
Multisig Wallets → Recover or import.

**Foreign wallet recovered AND spent (2026-08-17), tx
`aaa7997ad62bb823e46db34b49eb8f300461047cc44fda1902b93d024a31849b`:** the decisive test,
because importing a script nobody can sign proves parsing, never recovery.

The wallet was made foreign in the two ways that actually break recovery, while the
mnemonics stayed in hand so the spending half was testable at all:

1. **The script and its address were produced by `cardano-cli` 11.0**, not by this program.
   `cardano-cli address build --payment-script-file` and `cardano-cli hash script` gave
   `3f7b50f81bc03126e4c8b17ff93c973f759bc5a273fee3e4221a0375` /
   `addr_test1wqlhk58cr0qrzfhyezchl7fujulhtx795felaclyygdqxagzzyu2s`, and this program
   derived **byte-identical** values from the CBOR alone. Independent software agreeing on
   the address is what proves the script is being read the way the rest of Cardano reads it.
2. **The cosigner keys sat where a provider would put them,** not at this program's
   defaults: `m/1854'/1815'/0'/0/0`, `m/1854'/1815'/1'/0/0`, and `m/1852'/1815'/0'/0/3`.
   Two of the three are invisible to a naive account-0/address-0 check — the run confirms
   `at usual position: False` for both, and the widened search locating each at its real path.

No CardanoInterface multisig wallet ever existed for this script; the only input was the
hex string. Sequence: fund 5 ADA → recover from CBOR alone → the recovered address equals
the funded one → two cosigners sign with keys at the provider's paths → assembly verifies
both against the body hash → submit → **2 ADA landed at the recipient, 2.819055 ADA change
back to the script**, both confirmed in the local Kupo index at slot 131321390.

**Real third-party scripts from the preprod chain (2026-08-17):** script addresses were
harvested from the local Kupo index and their scripts fetched by hash
(`GET /scripts/{hash}`). Of a 40-address sample, 4 were native scripts (the rest Plutus);
two were written by other people's software — an `all` 2-of-2
(`5eab1e12ba9af51aa5fdf16f7f91fbec0c0a154bb6758abde1f7d1fc`) and an `atLeast` **1-of-2**
(`503c72cf30047c46ff5e0f551e8ff093176e68933fcd136cdbf9af25`). Both parse, report the
correct signature requirement, and **re-derive their on-chain address exactly** — the
check that matters, since a recovered wallet pointing at a different address would point
at no funds. The 1-of-2 was also recovered through the real TUI door. This is the closest
available stand-in for a sunset provider's output: scripts this program did not write,
taken from the chain rather than from a spec.

**Cosigner keys at unusual derivation positions (2026-08-17):** `find_cosigner_derivation`
searches both standards across 3 accounts x 20 addresses, primary position first. Verified
against keys deliberately placed at `m/1854'/1815'/1'/0/0`, `m/1852'/1815'/0'/0/5` and
`m/1854'/1815'/2'/0/7`: each is found, and the node it returns signs a body hash that
verifies with a witness key hash matching the script. Previously only account 0 / address 0
was checked, so such a wallet imported cleanly and was then unsignable — presented to the
user as "you are not a cosigner".

Earlier validated spends (2026-08-13/14) are recorded in `AGENTS.md`: the first recovery
spend, the file-carrier operator-collect round, the first relay round, and a raw-`.cbor`
wallet spent into a freshly created CI multisig — plus two real bugs those runs caught
and fixed (the min-UTxO protocol-parameter mapping, and float money math).

## Dependencies

| Component | Source | Purpose |
|---|---|---|
| PyCardano | In CardanoInterface | Script building, tx construction, CBOR serialization, witness verification, CIP-8 |
| Ogmios | Local node bridge | Tx submission, chain queries, protocol parameters |
| Kupo | Local UTxO indexer | UTxO discovery at script addresses |
| Blockfrost | Remote REST backend | Alternative backend (requires API key) |
| CIP-8 | Built into PyCardano | Auth message signing |
| Cardano native scripts | On-chain | The script grammar (`sig`, `all`, `any`, `atLeast`, `after`, `before`) |
| CIP-5 | Bech32 prefixes | Address/key-hash encoding |

## Dynamic Threshold Rules

`calculate_threshold(total_cosigners, percentage)` uses **floor**:

```
required = floor(total * percentage / 100)
```

Special case: 100% → always return `total`.

| Total Cosigners | Percentage | Required Signatures |
|---|---|---|
| 2 | 100% | 2-of-2 |
| 3 | 67% | 2-of-3 |
| 3 | 100% | 3-of-3 |
| 4 | 50% | 2-of-4 |
| 5 | 60% | 3-of-5 |
| 6 | 50% | 3-of-6 |
| 6 | 67% | 4-of-6 |
| 6 | 100% | 6-of-6 |

## Function Reference

Functions that exist in `CardanoInterface.py`. Signatures as in source.

### Cosigner Identity

```
COSIGNER_DERIVATION_PATHS: dict[str, str]     # label -> path template (CIP-1852, CIP-1854)
cosigner_key_hash_candidates(mnemonic, account_index=0, address_index=0) -> dict[str, bytes]
key_hash_from_vkey(vkey_bytes) -> bytes
extract_key_hashes_from_script(script) -> tuple[list[bytes], int | None, str | None]

_wallet_cosigner_key_hashes(wallet_dir, password) -> dict[str, bytes]   # derives + caches
_read_cosigner_key_hash_cache(wallet_dir) -> dict[str, bytes]           # no password needed
_match_cosigner_identities(user, user_password, script_key_hashes, unlock_password=None)
    -> list[CosignerIdentity]
```

The xpub helpers (`derive_account_xpub_from_mnemonic`, `derive_child_vkey_from_xpub`,
`bech32_xpub_from_bytes`, `bech32_xpub_to_bytes`, …) are **deleted**. Nothing in this
system derives from, stores, or exchanges an xpub.

### Script Building

```
build_native_script_from_key_hashes(threshold, key_hashes) -> NativeScript
script_to_address(script_hash_bytes, stake_script_hash=None) -> str
script_hash_from_script(script) -> bytes
calculate_threshold(total_cosigners, percentage) -> int
classify_cbor(cbor_hex) -> "native_script" | "unsigned_tx" | "witnessed_tx" | "unknown"
```

### Wallet / Recovery / Exchange

```
create_multisig_wallet(wallet_name, key_hashes, percentage, network=None,
                       labels=None, our_key_hash=None, admin_cosigner_index=None) -> dict
import_script_cbor(cbor_hex, wallet_name, network=None, labels=None,
                   provenance="recovered_cbor") -> dict
  # Door 1: recover a wallet from a raw native-script CBOR.

_write_multisig_wallet(wallet_dir, script, config, network) -> (hash, address, cbor)
  # The single writer. Creation and both recovery doors all land here, so a wallet is
  # byte-for-byte the same on disk however it arrived.

export_wallet_checkpoint(wallet_dir) -> dict
export_transaction_package(wallet_dir, session_dir, stage) -> dict
import_package(package_path, current_user, user_password) -> dict   # auto-detects kind

_load_multisig_wallet(wallet_dir) -> MultisigWalletInfo
  # Reads the script as the authority: key hashes and threshold come from it, never
  # from config.json. config supplies only names, provenance, and policy.

build_multisig_transaction(wallet_dir, recipient, amount_lovelace, ...) -> dict
sign_multisig_transaction(wallet_dir, unsigned_cbor_hex=None, session_dir=None,
                          password=None, signer_wallet_dir=None,
                          accumulate=False) -> dict | None
  # Signs with the node's personal wallet. accumulate=True is the relay hop.

assemble_multisig_transaction(wallet_dir, session_dir) -> dict | None
restore_multisig_participation(wallet_dir, current_user, user_password,
                               unlock_password=None) -> dict | None
scan_multisig_addresses(wallet_dir) -> dict     # one script, one address
```

### Interactive Wrappers

```
multisig_wallet_create(current_user, user_password)
show_my_cosigner_key(current_user, user_password)      # from a wallet you already have
_interactive_import_any(current_user, user_password)   # the unified door
_interactive_import_script_cbor(current_user, user_password)
_interactive_restore_multisig(current_user, user_password)     # "Which cosigner am I?"
_interactive_build_multisig(current_user, user_password)
_interactive_sign_multisig(current_user, user_password)        # starts from the transaction
_interactive_assemble_multisig(current_user, user_password)

_select_multisig_wallet(user, password, title) -> str | None
_resolve_signing_wallet(user, password, wallet) -> str | None  # auto-picks the signer
_wallet_for_incoming_transaction(user, password, cbor_hex) -> str | None
_session_label(sessions_dir, session_id) -> str   # "1.25 ADA  to addr...  1 of 2 signed"
```

## Known Gaps

> The administration features below were previously described in this document as
> "parked". That framing is withdrawn (operator, 2026-08-17): they are required
> management functionality, and they are tracked as outstanding work in `BACKLOG.md`.

1. **Multi-asset not supported in build** — ADA only; token params exist but are unwired to the interactive wrapper.
2. **No delegation** — stake scripts and delegation exchange are not implemented. A multisig wallet cannot delegate its stake.
3. **Admin gating not enforced** — `admin_cosigner_index` is written at creation and read by `_initiator_check`, but no wrapper enforces it, so the wallet runs gating-off whatever the operator chose. **Required work.**
4. **Tamper-evident admin-signed checkpoints not implemented** — checkpoint import verifies the script hash and re-derives the threshold from the script, but does not authenticate the admin designation, so anyone able to edit a checkpoint can name themselves admin. **Required work.**
5. **Cosigner matching is bounded** — `find_cosigner_derivation` searches both standards across `COSIGNER_SEARCH_ACCOUNTS` (3) accounts x `COSIGNER_SEARCH_INDICES` (20) addresses, usual position first. That covers every placement seen so far, but a provider using something further out would still import fine and fail to match at signing. Widen the constants if one turns up.
6. **Timelocks are read but not created** — `after`/`before` parse and display correctly from a recovered script, and the ledger enforces them; the interactive creator only emits `atLeast`.
7. **A recovered wallet has no labels** — by design, labels are local and die with the machine, so a recovered wallet shows `cosigner 1..N` until someone names them. There is no interactive way to add labels to an existing wallet yet.
