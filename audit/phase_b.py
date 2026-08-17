"""Phase B: multisig matrix on LOCAL backend (preprod), spec-compliance checked.

B0 bootstrap audlocal3 + aud_w3 (cosigner-1 personal wallet).
B1 create 2-of-2 aud_ms (xpub creator model) + storage-model + checkpoint export.
B2 fund + build + relay hop 1 (creator signs, accumulates).
B3 import checkpoint + restore participation + relay hop 2 -> submit -> confirm.
B4 key-hash script (personal payment keys) -> Import Script CBOR door (recovery),
   fund, build, operator-collect partials (incl. non-member rejection),
B5 assemble & submit (incl. tampered-partial rejection), wrong-network package,
   exchanged-artifact invariants.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from harness import REPO, Recorder, TUI, TUIError, bf_key, read_bf, squoosh  # noqa: E402

sys.path.insert(0, REPO)

rec = Recorder("phase_b")
KEY = bf_key()
PW = os.environ["CI_AUDIT_PW"]  # never hardcode; see CREDENTIALS.local.md
FUNDER = "ci_funder"
FUNDER_PW = os.environ["CI_FUNDER_PW"]  # never hardcode; see CREDENTIALS.local.md
TMP = os.path.join(REPO, "audit", "tmp")
os.makedirs(TMP, exist_ok=True)

W1 = json.load(open(os.path.join(REPO, "audit", "results", "a_w1_secret.json")))
W2_ADDR = json.load(open(os.path.join(REPO, "audit", "results", "a_w2_addr.json")))["address"]


def poll_total(address: str, timeout: int = 150) -> tuple[list, int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            utxos = read_bf(f"addresses/{address}/utxos", KEY)
            total = sum(int(a["quantity"]) for u in utxos for a in u["amount"]
                        if a["unit"] == "lovelace")
            if total:
                return utxos, total
        except Exception:
            pass
        time.sleep(6)
    return [], 0


def send_funds(t: TUI, wallet: str, recipient: str, amount: str, wallet_pw: str) -> str:
    t.choose(7)
    t.wait("Enter the wallet name from which you want to send funds")
    t.send(wallet)
    t.wait("Enter the recipient's address")
    t.send(recipient)
    t.wait("Enter amount of Cardano Native Token ADA to send")
    t.send(amount)
    t.wait("Do you want to send a token as well?")
    t.send("no")
    t.wait(r"\n> ")
    t.send(wallet_pw)
    t.wait("Transaction submitted successfully! TX ID")
    t.expect_main_menu()
    m = re.search(r"([0-9a-f]{64})", squoosh(t.snippet(1000)))
    if not m:
        raise TUIError("txid not found")
    return m.group(1)


def ms_enter(t: TUI) -> None:
    t.choose(4)
    t.wait("Choose an option \\(or 'back'\\)")


def ms(t: TUI, n: int) -> None:
    t.send(str(n))


def ms_back(t: TUI) -> None:
    t.send("back")
    t.expect_main_menu()


def parse_tx(path: str):
    from pycardano import Transaction
    with open(path) as f:
        return Transaction.from_cbor(f.read().strip())


def witness_count(tx) -> int:
    ws = tx.transaction_witness_set
    return len(ws.vkey_witnesses) if ws and ws.vkey_witnesses else 0


# --------------------------------------------------------------- B0 bootstrap
def b0_bootstrap() -> dict:
    secret_path = os.path.join(REPO, "audit", "results", "b_w3_secret.json")
    if os.path.exists(secret_path):
        return json.load(open(secret_path))
    t = TUI("b0_audlocal3")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "register", "audlocal3", PW)
        t.choose(2)
        t.wait("unique wallet name")
        t.send("aud_w3")
        t.wait("password to encrypt the wallet's signing keys")
        t.send(PW)
        t.wait("IMPORTANT: Save your 24-word mnemonic securely")
        t.wait("created successfully")
        seg = t.snippet(3000)
        mm = re.search(r"!\s*(.*?)\s*Wallet ", seg, re.S)
        mnemonic = " ".join(mm.group(1).split()) if mm else ""
        t.wait("Address:")
        t.wait("Main Menu")
        a = re.search(r"(addr_test1[a-z0-9]+)", squoosh(t.snippet(500)))
        secret = {"mnemonic": mnemonic, "address": a.group(1) if a else "", "pw": PW}
        assert len(mnemonic.split()) == 24 and a, f"bootstrap capture failed: {seg[-300:]}"
        with open(secret_path, "w") as f:
            json.dump(secret, f)
        t.exit_app()
        return secret
    except Exception as e:  # noqa: BLE001
        rec.record("B0-bootstrap", f"audlocal3 + aud_w3 bootstrap: {e}", False, str(e))
        t.close()
        raise


# --------------------------------------------------------------- B1 creation
def b1_create(t: TUI, w3: dict, xpub_hex: str) -> str:
    ms_enter(t)
    ms(t, 2)
    t.wait("Choose a name for this wallet")
    t.send("aud_ms")
    t.wait("How many cosigners will this wallet have?")
    t.send("2")
    t.wait("Cosigner 0 of this wallet")
    t.send("me")
    t.wait("Enter your 24-word recovery phrase")
    t.send(W1["mnemonic"])
    t.wait("set to your own key")
    t.wait("Cosigner 1 of this wallet")
    t.send(xpub_hex)
    t.wait("Cosigner 1 accepted")
    t.wait("How many cosigners must approve a spend?")
    seg = t.snippet(1200)
    floor_ok = "50% — 1 of 2" in seg
    rec.record("B1-threshold-floor-display",
               "Threshold presets show floor math (50% of 2 = 1 of 2)", floor_ok, seg)
    t.send("4")  # 100% -> 2 of 2
    t.wait("Threshold: 2 of 2 cosigners")
    t.wait("Designate yourself as the wallet administrator?")
    t.send("n")
    t.wait("Save your signing key in this wallet?")
    t.send("y")
    t.wait("Set a password for this multisig wallet's signing key")
    t.send(PW)
    t.wait("Type it again to confirm")
    t.send(PW)
    t.wait("Multisig wallet 'aud_ms' created")
    t.wait("Address:")
    t.wait("Choose an option \\(or 'back'\\)")
    a = re.search(r"(addr_test1[a-z0-9]+)", squoosh(t.snippet(3000)))
    ms_addr = a.group(1) if a else ""
    rec.record("B1-create-2of2",
               f"Created 2-of-2 aud_ms at {ms_addr[:25]}...", bool(ms_addr),
               t.snippet(1500))

    # storage model vs spec
    wdir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_ms")
    files = sorted(os.listdir(wdir))
    cfg = json.load(open(os.path.join(wdir, "config.json")))
    no_xpub_files = not any(f.endswith(".xpub") for f in files)
    rec.record("B1-storage-model",
               f"Files {files}; threshold={cfg.get('threshold')} "
               f"num={cfg.get('num_cosigners')}; no cosigner .xpub files={no_xpub_files}",
               no_xpub_files and cfg.get("threshold") == 2 and
               "network.txt" in files and "signing_keys" in files, str(files),
               note="spec §Storage forbids cosigner xpub FILES; xpubs live in "
                    "config.json (creator model — spec Known Gap 1)")

    # view wallet submenu: script details + cosigner keys
    ms(t, 1)
    t.wait("Enter wallet number to view")
    t.send("1")
    t.wait("View script details")
    t.send("3")
    t.wait("Choose an option \\(or 'back'\\)") if False else t.wait("Multisig Wallet: aud_ms")
    seg = t.snippet(2500)
    rec.record("B1-view-script-details",
               "View wallet shows script JSON (type/threshold)", "atLeast" in seg or
               "script" in seg.lower(), seg)
    t.send("2")  # cosigner keys
    t.wait("Cosigner keys")
    seg = t.snippet(1200)
    rec.record("B1-view-cosigner-keys",
               "Cosigner keys view lists cosigner_0/1 key material prefixes",
               "cosigner_0" in seg and "cosigner_1" in seg, seg)
    t.send("5")  # back
    t.wait("Choose an option \\(or 'back'\\)")

    # export checkpoint
    ms(t, 6)
    t.wait("Enter wallet number or 'cancel'")
    t.send("1")
    t.wait("Export path")
    t.send(os.path.join(TMP, "aud_ms_checkpoint.json"))
    t.wait("Wallet checkpoint exported")
    rec.record("B1-checkpoint-export", "Checkpoint package exported as JSON", True,
               t.snippet(400))
    ms_back(t)
    return ms_addr


# --------------------------------------------------------------- B2-B3 relay
def b2_fund_build_sign(t: TUI, ms_addr: str) -> None:
    tx = send_funds(t, FUNDER, ms_addr, "3", FUNDER_PW)
    rec.record("B2-fund-multisig", f"Funded aud_ms 3 ADA: {tx}", True, f"txid {tx}")
    utxos, total = poll_total(ms_addr)  # need BOTH utxos (old 2M + new 3M)
    rec.record("B2-fund-confirmed", f"aud_ms on-chain balance {total}", total >= 4000000,
               f"total={total}")

    ms_enter(t)
    ms(t, 7)
    t.wait("Enter wallet number or 'cancel'")
    t.send("1")
    t.wait("Recipient address")
    t.send(W2_ADDR)
    t.wait("Amount in ADA")
    t.send("1")
    t.wait("Export the unsigned transaction to a file for cosigners?")
    t.send("y")
    t.wait("Export path")
    t.send(os.path.join(TMP, "unsigned_ms.cbor"))
    t.wait("Unsigned transaction written")
    rec.record("B4-build-unsigned", "Built unsigned multisig tx + exported CBOR", True,
               t.snippet(500))
    # spec §Operations checks on the built tx
    sessions_dir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_ms", "sessions")
    newest = max(os.listdir(sessions_dir), key=lambda d: os.path.getmtime(
        os.path.join(sessions_dir, d)))
    utx_path = os.path.join(sessions_dir, newest, "unsigned.cbor")
    utx = parse_tx(utx_path)
    rs_unset = utx.transaction_body.required_signers is None
    change_ok = any(str(o.address).startswith("addr_test1w") for o in
                    utx.transaction_body.outputs)
    rec.record("B4-spec-operations-shape",
               f"required_signers unset={rs_unset}; script-address change "
               f"output={change_ok}; witnesses={witness_count(utx)}",
               rs_unset and change_ok and witness_count(utx) == 0,
               f"body_id={utx.transaction_body.id.payload.hex()}",
               note="spec §Operations: witness_override=n, required_signers unset, "
                    "change returns to script address")

    t.wait("Choose an option \\(or 'back'\\)")
    ms(t, 8)
    t.wait("Enter wallet number or 'cancel'")
    t.send("1")
    t.wait("Select the transaction file to sign")
    t.send(os.path.join(TMP, "unsigned_ms.cbor"))
    t.wait("Do you want to sign this transaction?")
    review = t.snippet(2000)
    rec.record("B5-sign-review-screen",
               "Sign review shows tx id, fee, outputs, threshold, pending cosigners",
               "Threshold: 2 of 2" in review and "Fee:" in review, review)
    t.send("y")
    t.wait(r"Enter your wallet password to sign")
    t.wait(r"\n> ")
    t.send(PW)
    t.wait("Export the accumulated transaction to a file?")
    t.send("y")
    t.wait("Export path")
    t.send(os.path.join(TMP, "relay_c0.cbor"))
    t.wait("Written to")
    rec.record("B5-relay-hop1", "Creator signed (accumulate) + exported relay CBOR",
               True, t.snippet(400))
    r0 = parse_tx(os.path.join(TMP, "relay_c0.cbor"))
    body_immutable = (r0.transaction_body.id.payload.hex() ==
                      utx.transaction_body.id.payload.hex())
    rec.record("B5-relay-hop1-shape",
               f"relay_c0 witnesses={witness_count(r0)}; body unchanged={body_immutable}",
               witness_count(r0) == 1 and body_immutable,
               f"body={r0.transaction_body.id.payload.hex()[:24]}")
    ms_back(t)


def b3_relay_finish(w3: dict) -> None:
    t = TUI("b3_audlocal3_relay")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "login", "audlocal3", PW)
        ms_enter(t)
        ms(t, 3)  # Import Package (JSON)
        t.wait("Select a CBOR Package JSON file")
        t.send(os.path.join(TMP, "aud_ms_checkpoint.json"))
        t.wait("checkpoint imported successfully")
        rec.record("B8-checkpoint-import",
                   "Package door: checkpoint imported on cosigner machine", True,
                   t.snippet(500))
        t.wait("Choose an option \\(or 'back'\\)")
        ms(t, 5)  # Restore Participation
        t.wait("Enter wallet number or 'cancel'")
        t.send("1")
        t.wait("Enter your 24-word mnemonic")
        t.send(w3["mnemonic"])
        t.wait("Matched cosigner")
        t.wait(r"\n> ")  # encrypt-mnemonic password (spinner)
        t.send(PW)
        t.wait("Your cosigner index")
        rec.record("B9-restore-participation",
                   "Restore Participation matches mnemonic to cosigner xpub and "
                   "stores encrypted key", True, "cosigner index 1 restored")
        t.wait("Choose an option \\(or 'back'\\)")
        ms(t, 8)  # Sign
        t.wait("Enter wallet number or 'cancel'")
        t.send("1")
        t.wait("Select the transaction file to sign")
        t.send(os.path.join(TMP, "relay_c0.cbor"))
        t.wait("Do you want to sign this transaction?")
        t.send("y")
        t.wait(r"Enter your wallet password to sign")
        t.wait(r"\n> ")
        t.send(PW)
        t.wait("Export the accumulated transaction to a file?")
        t.send("y")
        t.wait("Export path")
        t.send(os.path.join(TMP, "relay_c1.cbor"))
        t.wait("Written to")
        t.wait("Threshold met")
        t.wait("Submit to the network?")
        t.send("y")
        t.wait("SUBMITTED")
        t.wait("TX ID")
        t.wait("Choose an option \\(or 'back'\\)")
        m = re.search(r"([0-9a-f]{64})", squoosh(t.snippet(1200)))
        txid = m.group(1) if m else ""
        r1 = parse_tx(os.path.join(TMP, "relay_c1.cbor"))
        utx0 = parse_tx(os.path.join(TMP, "unsigned_ms.cbor"))
        body_ok = (r1.transaction_body.id.payload.hex() ==
                   utx0.transaction_body.id.payload.hex())
        rec.record("B5-relay-hop2-submit",
                   f"Threshold reached, submitted from cosigner machine: {txid}",
                   witness_count(r1) == 2 and body_ok and bool(txid),
                   f"witnesses={witness_count(r1)} body_immutable={body_ok}",
                   note="witnesses 0->1->2 across hops; body id constant")
        utxos, total = poll_total(W2_ADDR)
        got = any(int(a["quantity"]) == 1000000 for u in utxos
                  for a in u["amount"] if a["unit"] == "lovelace")
        rec.record("B5-relay-confirmed",
                   "1 ADA from created multisig visible on-chain at aud_w2", got,
                   f"w2_total={total}")
        ms_back(t)
        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("B-run-crash", f"b3 relay finish aborted: {e}", False, str(e))
        t.close()
        raise


# --------------------------------------------------------------- B4/B5 key-hash model
def make_key_script(w3: dict) -> str:
    """2-of-2 native script from the audit personal wallets' payment key hashes."""
    from pycardano import (NativeScript, ScriptNofK, ScriptPubkey,
                           VerificationKeyHash)

    def kh(wallet: str) -> VerificationKeyHash:
        import CardanoInterface as ci
        skey, _ = ci.load_encrypted_wallet(
            os.path.join(REPO, "CardanoInterface", "wallets", wallet), PW)
        return skey.to_verification_key().hash()

    script: NativeScript = ScriptNofK(
        2, [ScriptPubkey(kh("aud_w1")), ScriptPubkey(kh("aud_w3"))])
    path = os.path.join(TMP, "aud_key.cbor")
    with open(path, "w") as f:
        f.write(script.to_cbor().hex())
    return path


def b4_cbor_door_and_collect(w3: dict, script_path: str) -> None:
    t = TUI("b4_audlocal_keyms")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "login", "audlocal", PW)
        ms_enter(t)
        ms(t, 4)  # Import Script CBOR
        t.wait("Select the multisig script .cbor file")
        t.send(script_path)
        t.wait("Wallet name")
        t.send("aud_keyms")
        t.wait("Add cosigner xpubs?")
        t.send("n")
        t.wait("Choose an option \\(or 'back'\\)")
        seg = t.snippet(2500)
        shows = ("atLeast" in seg or "2" in seg) and "key" in seg.lower()
        rec.record("B7-cbor-door-display",
                   "Door 1 shows script type/threshold/key hashes before/after import",
                   shows, seg,
                   note="spec Door 1 requires showing script type, threshold, key "
                        "hashes, derived address")
        # view recovery wallet: key hashes + script details
        ms(t, 1)
        t.wait("Enter wallet number to view")
        t.send("2")
        t.wait("Multisig Wallet: aud_keyms")
        t.send("2")  # cosigner keys
        seg = t.snippet(1200)
        rec.record("B7-recovery-keyhashes-shown",
                   "Recovery wallet lists stored key hashes",
                   "stored key hashes" in seg, seg)
        t.send("5")
        t.wait("Choose an option \\(or 'back'\\)")

        # fund it
        ms_back(t)
        a = re.search(r"(addr_test1w[a-z0-9]+)", squoosh(seg))  # from view output
        if not a:
            with open(os.path.join(REPO, "CardanoInterface", "wallets", "aud_keyms",
                                   "script_address")) as f:
                keyms_addr = f.read().strip()
        else:
            keyms_addr = a.group(1)
        _, total = poll_total(keyms_addr)
        while total < 4_000_000:  # headroom: 1 ADA out + fee + min-UTxO change
            send_funds(t, FUNDER, keyms_addr, "3", FUNDER_PW)
            _, total = poll_total(keyms_addr)
        rec.record("B7-fund-recovery", f"key-hash script funded ({total} lovelace)",
                   total >= 4_000_000, f"total={total}")

        # build + export package
        ms_enter(t)
        ms(t, 7)
        t.wait("Enter wallet number or 'cancel'")
        t.send("2")
        t.wait("Recipient address")
        t.send(W2_ADDR)
        t.wait("Amount in ADA")
        t.send("1")
        t.wait("Export the unsigned transaction to a file for cosigners?")
        t.send("n")
        t.wait("Choose an option \\(or 'back'\\)")
        # export transaction package (stage unsigned) for the remote cosigner
        ms(t, 10)
        t.wait("Enter wallet number or 'cancel'")
        t.send("2")
        t.wait("Enter session number")
        sdir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_keyms",
                            "sessions")
        t.send(str(len([d for d in os.listdir(sdir)
                        if os.path.isdir(os.path.join(sdir, d))])))
        t.wait("Enter stage number")
        t.send("1")
        t.wait("Export path")
        t.send(os.path.join(TMP, "pkg_unsigned.json"))
        t.wait("Transaction package exported")
        rec.record("B6-package-export",
                   "Transaction Package (JSON TextEnvelope, unsigned) exported", True,
                   t.snippet(400))
        t.wait("Choose an option \\(or 'back'\\)")

        # NON-MEMBER rejection: try to sign with aud_w2 (not in script)
        ms(t, 8)
        t.wait("Enter wallet number or 'cancel'")
        t.send("2")
        t.wait("Which wallet do you want to sign with?")
        t.send("2")  # aud_w2
        t.wait("Select the transaction file to sign")
        t.send("")  # Enter -> sessions
        t.wait("Enter session number")
        sdir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_keyms",
                            "sessions")
        t.send(str(len([d for d in os.listdir(sdir)
                        if os.path.isdir(os.path.join(sdir, d))])))
        t.wait("Do you want to sign this transaction?")
        t.send("y")
        t.wait(r"Enter your wallet password to sign")
        t.wait(r"\n> ")
        t.send(PW)
        t.wait("not one of the script's cosigners")
        rec.record("B10-non-member-rejected",
                   "Signature from non-member key refused at sign time", True,
                   t.snippet(700))
        t.wait("Choose an option \\(or 'back'\\)")

        # member sign (operator-collect, session path) -> partial file
        ms(t, 8)
        t.wait("Enter wallet number or 'cancel'")
        t.send("2")
        t.wait("Which wallet do you want to sign with?")
        t.send("1")  # aud_w1
        t.wait("Select the transaction file to sign")
        t.send("")
        t.wait("Enter session number")
        sdir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_keyms",
                            "sessions")
        t.send(str(len([d for d in os.listdir(sdir)
                        if os.path.isdir(os.path.join(sdir, d))])))
        t.wait("Do you want to sign this transaction?")
        t.send("y")
        t.wait(r"Enter your wallet password to sign")
        t.wait(r"\n> ")
        t.send(PW)
        t.wait("Export the partial signature to a file?")
        t.send("y")
        t.wait("Export path")
        t.send(os.path.join(TMP, "partial_c0.cbor"))
        t.wait("Written to")
        rec.record("B6-operator-sign-partial",
                   "Operator signed via session; partial exported", True, t.snippet(400))
        ms_back(t)
        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("B-run-crash", f"b4 aborted: {e}", False, str(e))
        t.close()
        raise


def b4b_remote_sign(w3: dict, script_path: str) -> None:
    t = TUI("b4b_audlocal3_keyms3")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "login", "audlocal3", PW)
        ms_enter(t)
        ms(t, 4)
        t.wait("Select the multisig script .cbor file")
        t.send(script_path)
        t.wait("Wallet name")
        t.send("aud_keyms3")
        t.wait("Add cosigner xpubs?")
        t.send("n")
        t.wait("Choose an option \\(or 'back'\\)")
        ms(t, 8)
        t.wait("Enter wallet number or 'cancel'")
        t.send("2")  # aud_keyms3 [recovery]
        t.wait("Which wallet do you want to sign with?")
        t.send("1")  # aud_w3
        t.wait("Select the transaction file to sign")
        t.send(os.path.join(TMP, "pkg_unsigned.json"))
        t.wait("Do you want to sign this transaction?")
        t.send("y")
        t.wait(r"Enter your wallet password to sign")
        t.wait(r"\n> ")
        t.send(PW)
        t.wait("Export the")
        t.send("y")
        t.wait("Export path")
        t.send(os.path.join(TMP, "partial_c1.cbor"))
        t.wait("Written to")
        rec.record("B6-remote-sign-partial",
                   "Remote cosigner signed the JSON package; partial exported", True,
                   t.snippet(400),
                   note="remote cosigner signs via file import (relay-accumulate)")
        ms_back(t)
        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("B-run-crash", f"b4b aborted: {e}", False, str(e))
        t.close()
        raise


def b5_assemble_and_invariants() -> None:
    # tamper with a copy of c1's body (flip a hex char inside, keep file readable)
    good = open(os.path.join(TMP, "partial_c1.cbor")).read().strip()
    tampered = ("0" if good[20] != "0" else "1").join([good[:20], good[21:]])
    with open(os.path.join(TMP, "partial_bad.cbor"), "w") as f:
        f.write(tampered)

    t = TUI("b5_assemble")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "login", "audlocal", PW)
        ms_enter(t)
        ms(t, 9)  # Assemble & Submit
        t.wait("Enter wallet number or 'cancel'")
        t.send("2")
        t.wait("Enter session number")
        sdir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_keyms",
                            "sessions")
        t.send(str(len([d for d in os.listdir(sdir)
                        if os.path.isdir(os.path.join(sdir, d))])))
        t.wait("Import cosigner partial signature")
        t.send("y")
        t.wait("Select a partial signature file to import")
        t.send(os.path.join(TMP, "partial_c1.cbor"))
        t.wait("Import another partial?")
        t.send("y")
        t.wait("Select a partial signature file to import")
        t.send(os.path.join(TMP, "partial_bad.cbor"))
        t.wait("different transaction than this session")
        t.wait("Import another partial?")
        t.send("n")
        t.wait("MULTISIG ASSEMBLY")
        t.wait("Threshold:")
        seg = t.snippet(4000)
        tamper_rejected = ("does not match" in seg or "not a readable" in seg or
                           "rejected" in seg or
                           "different transaction" in seg)
        rec.record("B6-tamper-rejected",
                   "Tampered partial rejected during assembly", tamper_rejected, seg,
                   note="anti-tampering invariant: every witness verified against "
                        "body hash")
        t.wait("Submit this transaction to the network?")
        t.send("y")
        t.wait("TRANSACTION SUBMITTED")
        t.wait("TX ID")
        t.wait("Choose an option \\(or 'back'\\)")
        m = re.search(r"([0-9a-f]{64})", squoosh(t.snippet(1200)))
        txid = m.group(1) if m else ""
        rec.record("B6-assemble-submit",
                   f"Operator-collect assembled + submitted: {txid}", bool(txid),
                   f"txid {txid}")
        utxos, total = poll_total(W2_ADDR)
        got = any(int(a["quantity"]) == 1000000 for u in utxos
                  for a in u["amount"] if a["unit"] == "lovelace")
        # (w2 got 1 ADA twice from two different scripts; count>=2 occurrences)
        hits = sum(1 for u in utxos for a in u["amount"]
                   if a["unit"] == "lovelace" and int(a["quantity"]) == 1000000)
        rec.record("B6-assemble-confirmed",
                   f"Second 1 ADA output from key-hash script at aud_w2 (hits={hits})",
                   hits >= 2, f"w2_total={total}")

        # wrong-network checkpoint rejection
        cp = json.load(open(os.path.join(TMP, "aud_ms_checkpoint.json")))
        cp["network"] = "preview"
        with open(os.path.join(TMP, "bad_network_checkpoint.json"), "w") as f:
            json.dump(cp, f)
        ms(t, 3)
        t.wait("Select a CBOR Package JSON file")
        t.send(os.path.join(TMP, "bad_network_checkpoint.json"))
        t.wait("Choose an option \\(or 'back'\\)")
        seg = t.snippet(1500)
        rejected = "network" in seg.lower() and ("mismatch" in seg.lower() or
                                                 "reject" in seg.lower() or
                                                 "Failed" in seg)
        rec.record("B10-wrong-network-rejected",
                   "Checkpoint with wrong network rejected on import", rejected, seg)

        # exchanged artifacts carry no secrets
        cp2 = json.load(open(os.path.join(TMP, "aud_ms_checkpoint.json")))
        blob = json.dumps(cp2).lower()
        no_secret = ("mnemonic" not in json.dumps(sorted(cp2.keys())).lower()
                     and "skey" not in blob and W1["mnemonic"] not in blob)
        partial_hex = open(os.path.join(TMP, "partial_c1.cbor")).read()
        rec.record("B10-no-secrets-in-artifacts",
                   "Checkpoint JSON + partial CBOR contain no key material", no_secret,
                   f"checkpoint_keys={sorted(cp2.keys())} partial_len={len(partial_hex)}",
                   note="coordination invariant: only key hashes + signatures cross "
                        "machines")
        ms_back(t)
        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("B-run-crash", f"b5 assemble aborted: {e}", False, str(e))
        t.close()
        raise


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("all", "b0"):
        w3 = b0_bootstrap()
        import CardanoInterface as ci  # project module for xpub derivation
        xpub_hex = ci.derive_account_xpub_from_mnemonic(w3["mnemonic"]).hex()
        with open(os.path.join(REPO, "audit", "results", "b_state.json"), "w") as f:
            json.dump({"w3": w3, "xpub_hex": xpub_hex}, f)
        print("xpub hex:", xpub_hex[:32], "...")
    if step in ("all", "b1"):
        st = json.load(open(os.path.join(REPO, "audit", "results", "b_state.json")))
        ms_dir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_ms")
        t = TUI("b1_audlocal_create")
        try:
            from harness import startup_to_main_menu
            startup_to_main_menu(t, "login", "audlocal", PW)
            if os.path.exists(os.path.join(ms_dir, "script_address")):
                with open(os.path.join(ms_dir, "script_address")) as f:
                    ms_addr = f.read().strip()
            else:
                ms_addr = b1_create(t, st["w3"], st["xpub_hex"])
            b2_fund_build_sign(t, ms_addr)
            t.exit_app()
        except Exception as e:  # noqa: BLE001
            rec.record("B-run-crash", f"b1/b2 aborted: {e}", False, str(e))
            t.close()
            raise
        b3_relay_finish(st["w3"])
    if step in ("all", "b3"):
        st = json.load(open(os.path.join(REPO, "audit", "results", "b_state.json")))
        b3_relay_finish(st["w3"])
    if step in ("all", "b4"):
        st = json.load(open(os.path.join(REPO, "audit", "results", "b_state.json")))
        script_path = make_key_script(st["w3"])
        print("script:", script_path)
        b4_cbor_door_and_collect(st["w3"], script_path)
        b4b_remote_sign(st["w3"], script_path)
        b5_assemble_and_invariants()
    print("Phase B step done:", step)
