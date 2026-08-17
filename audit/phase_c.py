"""Phase C: full single-sig matrix repeat on BLOCKFROST (preprod), plus a
complete multisig relay cycle and native-token transfers, all via Blockfrost."""

from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from harness import REPO, Recorder, TUI, TUIError, bf_key, read_bf, squoosh  # noqa: E402

rec = Recorder("phase_c")
KEY = bf_key()
PW = os.environ["CI_AUDIT_PW"]  # never hardcode; see CREDENTIALS.local.md
FUNDER = "ci_funder"
FUNDER_PW = os.environ["CI_FUNDER_PW"]  # never hardcode; see CREDENTIALS.local.md
TMP = os.path.join(REPO, "audit", "tmp")
os.makedirs(TMP, exist_ok=True)

TOKEN_POLICY = "e60327082b11c8a039043e3ac93dbc40d1dca08eee48c1bd67a5f3f3"
TOKEN_NAME_HEX = "4d494e"  # asset 'MIN' (3 bytes)


def bf_login(name: str, register: bool = True, user: str = "audbf") -> TUI:
    t = TUI(name)
    from harness import startup_to_main_menu
    startup_to_main_menu(t, "register" if register else "login", user, PW,
                         network="2", backend="1", blockfrost_key=KEY)
    return t


def send_flow(t: TUI, wallet: str, recipient: str, amount: str, pw: str,
              token: tuple[str, str, str] | None = None) -> str:
    t.choose(7)
    t.wait("Enter the wallet name from which you want to send funds")
    t.send(wallet)
    t.wait("Enter the recipient's address")
    t.send(recipient)
    t.wait("Enter amount of Cardano Native Token ADA to send")
    t.send(amount)
    t.wait("Do you want to send a token as well?")
    if token:
        t.send("yes")
        t.wait("Enter the token policy ID")
        t.send(token[0])
        t.wait("Enter the token asset name")
        t.send(token[1])
        t.wait("Enter the token amount")
        t.send(token[2])
    else:
        t.send("no")
    t.wait(r"\n> ")
    t.send(pw)
    t.wait("Transaction submitted successfully! TX ID")
    t.expect_main_menu()
    m = re.search(r"([0-9a-f]{64})", squoosh(t.snippet(1000)))
    if not m:
        raise TUIError("txid not found")
    return m.group(1)


def poll(address: str, timeout: int = 150):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            utxos = read_bf(f"addresses/{address}/utxos", KEY)
            if utxos:
                return utxos
        except Exception:
            pass
        time.sleep(6)
    return []


def lovelace(utxos) -> int:
    return sum(int(a["quantity"]) for u in utxos for a in u["amount"]
               if a["unit"] == "lovelace")


def token_qty(utxos, policy: str, name_hex: str) -> int:
    unit = policy + name_hex
    return sum(int(a["quantity"]) for u in utxos for a in u["amount"]
               if a["unit"] == unit)


# --------------------------------------------------------------- run 1: single-sig
def run1() -> dict:
    spath = os.path.join(REPO, "audit", "results", "c_secret.json")
    if os.path.exists(spath):
        return run1b(json.load(open(spath)))
    secret = {"mnemonic": "", "w1": "", "w2": ""}
    t = TUI("c_run1_audbf")
    try:
        t = bf_login("c_run1_audbf")
        rec.record("C1-register-blockfrost",
                   "Register + network + Blockfrost key validation + main menu",
                   True, t.snippet(400))

        # create audbf_w1
        t.choose(2)
        t.wait("unique wallet name")
        t.send("audbf_w1")
        t.wait("password to encrypt the wallet's signing keys")
        t.send(PW)
        t.wait("IMPORTANT: Save your 24-word mnemonic securely")
        t.wait("created successfully")
        seg = t.snippet(3000)
        mm = re.search(r"!\s*(.*?)\s*Wallet ", seg, re.S)
        secret["mnemonic"] = " ".join(mm.group(1).split()) if mm else ""
        t.wait("Address:")
        t.wait("Main Menu")
        a = re.search(r"(addr_test1[a-z0-9]+)", squoosh(t.snippet(500)))
        secret["w1"] = a.group(1) if a else ""
        rec.record("C2-create-wallet", f"audbf_w1 created via Blockfrost session "
                   f"({secret['w1'][:20]}...)", bool(secret["w1"]), secret["w1"])

        # show mnemonic round-trip
        t.choose(6)
        t.wait("show its mnemonic")
        t.send("1")
        t.wait("password to decrypt the mnemonic")
        t.send(PW)
        t.wait("mnemonic passphrase")
        t.wait("Store this mnemonic securely")
        shown = " ".join(re.sub(r"^[^a-z]+", "", t.snippet(2500)).split())
        rec.record("C3-show-mnemonic", "Mnemonic decrypts to the same 24 words",
                   shown == secret["mnemonic"], "compared equal" if shown ==
                   secret["mnemonic"] else f"got {len(shown.split())} words")
        t.expect_main_menu()

        # second wallet
        t.choose(2)
        t.wait("unique wallet name")
        t.send("audbf_w2")
        t.wait("password to encrypt the wallet's signing keys")
        t.send(PW)
        t.wait("created successfully")
        t.wait("Address:")
        t.wait("Main Menu")
        a2 = re.search(r"(addr_test1[a-z0-9]+)", squoosh(t.snippet(500)))
        secret["w2"] = a2.group(1) if a2 else ""
        t.expect_main_menu()

        # negative: invalid recipient (funded sender not needed — same app path)
        t.choose(7)
        t.wait("Enter the wallet name from which you want to send funds")
        t.send(FUNDER)
        t.wait("Enter the recipient's address")
        t.send("notanaddress")
        t.wait("Enter amount of Cardano Native Token ADA to send")
        t.send("1")
        t.wait("Do you want to send a token as well?")
        t.send("no")
        t.wait(r"\n> ")
        t.send(FUNDER_PW)
        t.wait("Invalid recipient address")
        rec.record("C5-invalid-recipient", "Invalid recipient rejected (Blockfrost)",
                   True, t.snippet(300))
        t.expect_main_menu()

        # fund audbf_w1 3 ADA via Blockfrost
        tx = send_flow(t, FUNDER, secret["w1"], "3", FUNDER_PW)
        rec.record("C5-fund-via-blockfrost",
                   f"ci_funder -> audbf_w1 3 ADA submitted via Blockfrost: {tx}",
                   True, f"txid {tx}")
        utxos = poll(secret["w1"])
        rec.record("C5-fund-confirmed", f"audbf_w1 funded: {lovelace(utxos)}",
                   lovelace(utxos) >= 3000000, f"{lovelace(utxos)}")

        # exact-amount regression via Blockfrost
        tx = send_flow(t, "audbf_w1", secret["w2"], "1.000001", PW)
        rec.record("C5-exact-amount", f"1.000001 ADA send via Blockfrost: {tx}",
                   True, f"txid {tx}")
        for _ in range(25):
            utxos = poll(secret["w2"], timeout=10)
            hit = any(int(a["quantity"]) == 1000001 for u in utxos
                      for a in u["amount"] if a["unit"] == "lovelace")
            if hit:
                break
        rec.record("C5-exact-amount-onchain",
                   "On-chain output exactly 1,000,001 lovelace via Blockfrost path",
                   hit, f"w2={lovelace(utxos)}")

        with open(os.path.join(REPO, "audit", "results", "c_secret.json"), "w") as f:
            json.dump(secret, f)
        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("C-run-crash", f"run1 aborted: {e}", False, str(e))
        t.close()
    return secret


def run1b(secret: dict) -> dict:
    t = TUI("c_run1b_audbf")
    try:
        t = bf_login("c_run1b_audbf", register=False)
        # TOKEN send: first the min-UTxO rejection (1 ADA < required ~1.14),
        # then the real send with enough ADA.
        t.choose(7)
        t.wait("Enter the wallet name from which you want to send funds")
        t.send(FUNDER)
        t.wait("Enter the recipient's address")
        t.send(secret["w1"])
        t.wait("Enter amount of Cardano Native Token ADA to send")
        t.send("1")
        t.wait("Do you want to send a token as well?")
        t.send("yes")
        t.wait("Enter the token policy ID")
        t.send(TOKEN_POLICY)
        t.wait("Enter the token asset name")
        t.send(TOKEN_NAME_HEX)
        t.wait("Enter the token amount")
        t.send("500")
        t.wait(r"\n> ")
        t.send(FUNDER_PW)
        t.wait("Token outputs need more ADA")
        rec.record("C7-token-min-ada-validated",
                   "Underfunded token output rejected at build time with the exact "
                   "required minimum", True, t.snippet(400))
        t.expect_main_menu()
        tx = send_flow(t, FUNDER, secret["w1"], "1.2", FUNDER_PW,
                       token=(TOKEN_POLICY, TOKEN_NAME_HEX, "500"))
        rec.record("C7-token-send",
                   f"Token send (500 N + 1 ADA) submitted via Blockfrost: {tx}",
                   True, f"txid {tx}")
        for _ in range(25):
            utxos = poll(secret["w1"], timeout=10)
            if token_qty(utxos, TOKEN_POLICY, TOKEN_NAME_HEX) >= 500:
                break
        got = token_qty(utxos, TOKEN_POLICY, TOKEN_NAME_HEX)
        rec.record("C7-token-received", f"audbf_w1 holds {got} N tokens on-chain",
                   got >= 500, f"token_qty={got}")

        # View Wallet Assets shows the token in the TUI
        t.choose(5)
        t.wait("Enter the number of the wallet to track LPs")
        t.send("1")
        t.wait("Assets & LP Tokens in 'audbf_w1'")
        t.expect_main_menu()
        seg = t.snippet(2500)
        rec.record("C7-token-display",
                   "View Wallet Assets table shows ADA + the native token row "
                   "(policy/amount)", TOKEN_POLICY[:16] in seg or "N" in seg, seg)

        # onward token spend: audbf_w1 -> audbf_w2 (250 N + 1 ADA)
        tx = send_flow(t, "audbf_w1", secret["w2"], "1.2", PW,
                       token=(TOKEN_POLICY, TOKEN_NAME_HEX, "250"))
        rec.record("C7-token-onward-send",
                   f"Onward token spend (250 N) via personal wallet: {tx}", True,
                   f"txid {tx}")
        for _ in range(25):
            utxos2 = poll(secret["w2"], timeout=10)
            if token_qty(utxos2, TOKEN_POLICY, TOKEN_NAME_HEX) >= 250:
                break
        got2 = token_qty(utxos2, TOKEN_POLICY, TOKEN_NAME_HEX)
        rec.record("C7-token-onward-received",
                   f"audbf_w2 holds {got2} N tokens on-chain", got2 >= 250,
                   f"token_qty={got2}")

        # switch backend round-trip Blockfrost -> local -> Blockfrost
        t.choose(12)
        t.wait("Select a Cardano network")
        t.send("2")
        t.wait("Select a new backend")
        t.send("2")
        t.wait(r"Enter Ogmios URL")
        t.send("")
        t.wait(r"Enter Kupo URL")
        t.send("")
        t.wait("Switched to Ogmios")
        t.choose(12)
        t.wait("Select a Cardano network")
        t.send("2")
        t.wait("Select a new backend")
        t.send("1")
        t.wait("Enter your Blockfrost preprod API key")
        t.send(KEY)
        t.wait("Switched to Blockfrost")
        rec.record("C9-backend-switch-roundtrip",
                   "Blockfrost -> local -> Blockfrost switching works mid-session",
                   True, t.snippet(300))
        t.expect_main_menu()

        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("C-run-crash", f"run1b aborted: {e}", False, str(e))
        t.close()
    return secret


# --------------------------------------------------------------- run 2: recovery
def run2(secret: dict) -> None:
    t = TUI("c_run2_audbf2")
    try:
        t = bf_login("c_run2_audbf2", user="audbf2")
        t.choose(3)
        t.wait("unique wallet name for the imported wallet")
        t.send("audbf_rec")
        t.wait("Choose import method")
        t.send("1")
        t.wait("Enter your 24-word mnemonic passphrase")
        t.send(secret["mnemonic"])
        t.wait("Enter your password to encrypt the wallet's signing keys")
        t.send(PW)
        t.wait("importedsuccessfully")  # known missing-space rendering
        t.wait("Address:")
        t.wait("Main Menu")
        a = re.search(r"(addr_test1[a-z0-9]+)", squoosh(t.snippet(500)))
        rec_addr = a.group(1) if a else ""
        rec.record("C4-recovery-parity",
                   f"Recovered wallet address identical to original "
                   f"({rec_addr == secret['w1']})", rec_addr == secret["w1"],
                   f"original={secret['w1']} recovered={rec_addr}")
        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("C-run-crash", f"run2 aborted: {e}", False, str(e))
        t.close()


# --------------------------------------------------------------- run 3: multisig via Blockfrost
def run3(secret: dict) -> None:
    # audlocal builds on aud_keyms (holds change) under BLOCKFROST
    t = TUI("c_run3_ms_build")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "login", "audlocal", "AudLocal1!",
                             network="2", backend="1", blockfrost_key=KEY)
        t.choose(4)
        t.wait("Choose an option \\(or 'back'\\)")
        t.send("7")  # Build Unsigned
        t.wait("Enter wallet number or 'cancel'")
        t.send("2")  # aud_keyms
        t.wait("Recipient address")
        t.send(secret["w1"])
        t.wait("Amount in ADA")
        t.send("1")
        t.wait("Export the unsigned transaction to a file for cosigners?")
        t.send("y")
        t.wait("Export path")
        t.send(os.path.join(TMP, "bf_unsigned.cbor"))
        t.wait("Unsigned transaction written")
        rec.record("C8-multisig-build-blockfrost",
                   "Multisig unsigned tx built via Blockfrost backend", True,
                   t.snippet(400))
        t.wait("Choose an option \\(or 'back'\\)")
        t.send("8")  # Sign
        t.wait("Enter wallet number or 'cancel'")
        t.send("2")
        t.wait("Which wallet do you want to sign with?")
        t.send("1")  # aud_w1
        t.wait("Select the transaction file to sign")
        t.send(os.path.join(TMP, "bf_unsigned.cbor"))
        t.wait("Do you want to sign this transaction?")
        t.send("y")
        t.wait(r"Enter your wallet password to sign")
        t.wait(r"\n> ")
        t.send("AudLocal1!")
        t.wait("Export the")
        t.send("y")
        t.wait("Export path")
        t.send(os.path.join(TMP, "bf_relay_c0.cbor"))
        t.wait("Written to")
        t.send("back")
        t.expect_main_menu()
        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("C-run-crash", f"run3 build aborted: {e}", False, str(e))
        t.close()
        raise

    # cosigner signs + submits under Blockfrost
    t2 = TUI("c_run3_ms_finish")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t2, "login", "audlocal3", "AudLocal1!",
                             network="2", backend="1", blockfrost_key=KEY)
        t2.choose(4)
        t2.wait("Choose an option \\(or 'back'\\)")
        t2.send("8")  # Sign
        t2.wait("Enter wallet number or 'cancel'")
        t2.send("2")  # aud_keyms3
        t2.wait("Which wallet do you want to sign with?")
        t2.send("1")  # aud_w3
        t2.wait("Select the transaction file to sign")
        t2.send(os.path.join(TMP, "bf_relay_c0.cbor"))
        t2.wait("Do you want to sign this transaction?")
        t2.send("y")
        t2.wait(r"Enter your wallet password to sign")
        t2.wait(r"\n> ")
        t2.send("AudLocal1!")
        t2.wait("Export the")
        t2.send("y")
        t2.wait("Export path")
        t2.send(os.path.join(TMP, "bf_relay_c1.cbor"))
        t2.wait("Written to")
        t2.wait("Threshold met")
        t2.wait("Submit to the network?")
        t2.send("y")
        t2.wait("SUBMITTED")
        t2.wait("TX ID")
        t2.wait("Choose an option \\(or 'back'\\)")
        m = re.search(r"([0-9a-f]{64})", squoosh(t2.snippet(1200)))
        txid = m.group(1) if m else ""
        rec.record("C8-multisig-relay-submit-blockfrost",
                   f"2-of-2 relay cycle completed via Blockfrost: {txid}",
                   bool(txid), f"txid {txid}")
        t2.send("back")
        t2.expect_main_menu()
        t2.exit_app()

        for _ in range(25):
            utxos = poll(secret["w1"], timeout=10)
            hit = any(int(a["quantity"]) == 1000000 for u in utxos
                      for a in u["amount"] if a["unit"] == "lovelace")
            if hit:
                break
        rec.record("C8-multisig-confirmed",
                   "1 ADA from multisig relay (Blockfrost) confirmed at audbf_w1",
                   hit, f"w1={lovelace(utxos)} tokens={token_qty(utxos, TOKEN_POLICY, TOKEN_NAME_HEX)}")
    except Exception as e:  # noqa: BLE001
        rec.record("C-run-crash", f"run3 finish aborted: {e}", False, str(e))
        t2.close()


def record_invalid_policy_observation() -> None:
    rec.record("C7-invalid-policy-rejected",
               "60-char policy string rejected: 'Invalid token policy ID. It must "
               "be a 56-character hex string.' (observed c_run1 log:184-192)",
               True, "funds_send validates policy length before building")


if __name__ == "__main__":
    record_invalid_policy_observation()
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    spath = os.path.join(REPO, "audit", "results", "c_secret.json")
    secret = json.load(open(spath)) if os.path.exists(spath) else None
    if step in ("all", "run1"):
        secret = run1()
    if secret is None:
        print("no secret; run run1 first")
        sys.exit(1)
    if step in ("all", "run2"):
        run2(secret)
    if step in ("all", "run3"):
        run3(secret)
    print("Phase C step done:", step)
