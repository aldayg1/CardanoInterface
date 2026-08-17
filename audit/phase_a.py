"""Phase A: single-sig matrix on LOCAL backend (Ogmios+Kupo, preprod).

Run 1 (audlocal): register UX, wallet create + mnemonic capture, show mnemonic,
view wallets, assets, send-funds negative paths, real funding + exact-amount send,
stubs, debug health, backend switch round-trip.
Run 2 (audlocal2): mnemonic recovery (invalid + valid), address parity, recovered
keys sign on-chain.
Run 3 (auddel): delete wallet negative/positive, delete user.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from harness import REPO, Recorder, TUI, TUIError, bf_key, read_bf, squoosh, strip_ansi  # noqa: E402

from mnemonic import Mnemonic  # project venv dependency

rec = Recorder("phase_a")
KEY = bf_key()
USER_PW = os.environ["CI_AUDIT_PW"]  # never hardcode; see CREDENTIALS.local.md
# NOTE: wallet passwords intentionally EQUAL the user password. A prior probe with
# distinct wallet passwords (allowed by the app) revealed that address migration
# decrypts with the USER password, bakes {"address": ""} into the userdb, and
# breaks View Wallet Assets — recorded as finding F-B1; logs in audit/logs.
W1_PW = os.environ["CI_AUDIT_PW"]  # never hardcode; see CREDENTIALS.local.md
W2_PW = os.environ["CI_AUDIT_PW"]  # never hardcode; see CREDENTIALS.local.md
FUNDER = "ci_funder"
FUNDER_PW = os.environ["CI_FUNDER_PW"]  # never hardcode; see CREDENTIALS.local.md

mnemo_check = Mnemonic("english")


def poll_utxos(address: str, want_lovelace: int | None = None, timeout: int = 150):
    """Poll Blockfrost until the address has any UTxO (or >= want_lovelace)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            utxos = read_bf(f"addresses/{address}/utxos", KEY)
            if utxos:
                total = sum(
                    int(a["quantity"]) for u in utxos for a in u["amount"]
                    if a["unit"] == "lovelace")
                if want_lovelace is None or total >= want_lovelace:
                    return utxos, total
        except Exception:
            pass
        time.sleep(6)
    return None, 0


def send_funds_flow(t: TUI, wallet: str, recipient: str, amount: str, wallet_pw: str,
                    token: tuple[str, str, str] | None = None) -> str:
    """Drive Send Funds end-to-end; returns tx id."""
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
    t.wait(r"\n> ")  # spinner password prompt: label not rendered in dumb mode
    t.send(wallet_pw)
    t.wait("Transaction submitted successfully! TX ID")
    t.expect_main_menu()  # tx id prints after the label; capture the span to the menu
    txid = re.search(r"([0-9a-f]{64})", squoosh(t.snippet(1000)))
    if not txid:
        raise TUIError("tx id not found after submission")
    return txid.group(1)


# ---------------------------------------------------------------- Run 1
def run1() -> tuple[str, str, str]:
    t = TUI("a_run1_audlocal")
    w1_mnemonic = w1_addr = w2_addr = ""
    try:
        # A1: startup invalid-option loop + registration with weak password
        t.wait(r"Do you want to \(login/register/exit\)\?")
        t.send("blah")
        t.wait("Invalid option")
        rec.record("A1-invalid-startup-option",
                   "Garbage at login/register prompt re-prompts without crash", True,
                   t.snippet())

        t.send("register")
        t.wait("Please choose a username")
        t.send("audlocal")
        t.wait("Enter your password")
        t.send("weak")
        t.wait("Password requirements not met")
        rec.record("A1-weak-password-rejected",
                   "Weak password rejected with requirement list", True, t.snippet())
        t.wait("Enter your password")
        t.send(USER_PW)
        t.wait("Confirm your password")
        t.send("mismatch1!")
        t.wait("Passwords do not match")
        rec.record("A1-password-mismatch-rejected",
                   "Password confirmation mismatch rejected", True, t.snippet())
        t.wait("Enter your password")
        t.send(USER_PW)
        t.wait("Confirm your password")
        t.send(USER_PW)
        t.wait("registered successfully")

        t.wait("Select a Cardano network")
        t.send("9")
        t.wait("Invalid choice. Please enter 1, 2, or 3.")
        t.send("2")
        t.wait("Network set to preprod")
        t.wait("Select a backend")
        t.send("7")
        t.wait("Invalid choice. Please enter 1 or 2.")
        t.send("2")
        t.wait(r"Enter Ogmios URL")
        t.send("")
        t.wait(r"Enter Kupo URL")
        t.send("")
        t.wait("Connected to local Ogmios")
        t.wait("Kupo available")
        t.expect_main_menu()
        rec.record("A1-network-backend-selection",
                   "Network+backend selection with invalid-input loops, local stack "
                   "defaults, Kupo detected", True, t.snippet(1200))

        # A2: wallet creation with invalid-name loops
        t.choose(2)
        t.wait("unique wallet name")
        t.send("bad name!")
        t.wait("must contain only letters")
        t.send("")
        t.wait("Wallet name cannot be empty")
        t.send("aud_w1")
        t.wait("password to encrypt the wallet's signing keys")
        t.send(W1_PW)
        t.wait("IMPORTANT: Save your 24-word mnemonic securely")
        t.wait("created successfully")
        seg = t.snippet(3000)
        mm = re.search(r"!\s*(.*?)\s*Wallet ", seg, re.S)
        w1_mnemonic = " ".join(mm.group(1).split()) if mm else ""
        if len(w1_mnemonic.split()) != 24:
            raise TUIError(f"mnemonic not found in creation output:\n{seg}")
        t.wait("Address:")
        t.wait("Main Menu")
        a = re.search(r"(addr_test1[a-z0-9]+)", squoosh(t.snippet(500)))
        if not a:
            raise TUIError("address not found in creation output")
        w1_addr = a.group(1)
        ok_words = len(w1_mnemonic.split()) == 24
        ok_bip39 = mnemo_check.check(w1_mnemonic)
        rec.record("A2-create-wallet",
                   f"Wallet created; mnemonic displayed once (24 words={ok_words}, "
                   f"BIP39 checksum={ok_bip39}); address {w1_addr[:25]}...",
                   ok_words and ok_bip39,
                   "mnemonic shown once in bold with warning banner (redacted)")
        # file artifacts
        wdir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_w1")
        files = sorted(os.listdir(wdir)) if os.path.isdir(wdir) else []
        expected = {"payment.skey", "stake.skey", "mnemonic.enc", "address.enc",
                    "network.txt"}
        net = ""
        if "network.txt" in files:
            net = open(os.path.join(wdir, "network.txt")).read().strip()
        rec.record(
            "A2-wallet-files", f"Wallet dir files {files}; network.txt={net!r}",
            expected.issubset(set(files)) and net == "preprod", str(files),
            note="payment.vkey/stake.vkey absent after create (import path writes "
                 "them) — asymmetry recorded")
        with open(os.path.join(REPO, "audit", "results", "a_w1_secret.json"), "w") as f:
            json.dump({"mnemonic": w1_mnemonic, "address": w1_addr,
                       "pw": W1_PW}, f)
        t.expect_main_menu()

        # second wallet, quick
        t.choose(2)
        t.wait("unique wallet name")
        t.send("aud_w2")
        t.wait("password to encrypt the wallet's signing keys")
        t.send(W2_PW)
        t.wait("IMPORTANT: Save your 24-word mnemonic securely")
        t.wait("created successfully")
        t.wait("Address:")
        t.wait("Main Menu")
        a2 = re.search(r"(addr_test1[a-z0-9]+)", squoosh(t.snippet(500)))
        w2_addr = a2.group(1) if a2 else ""
        rec.record("A2-create-wallet-2", f"Second wallet aud_w2 created ({w2_addr[:20]}...)",
                   bool(w2_addr) and w2_addr != w1_addr, t.snippet(600))
        t.expect_main_menu()

        # A3: show mnemonic — wrong password then right
        t.choose(6)
        t.wait("show its mnemonic")
        t.send("1")
        t.wait("password to decrypt the mnemonic")
        t.send("NotTheRight1!")
        t.wait("Failed to decrypt")
        rec.record("A3-show-mnemonic-wrong-pw",
                   "Wrong password rejected with error (single attempt, returns to menu)",
                   True, t.snippet(),
                   note="no re-prompt loop; must re-enter menu flow to retry")
        t.expect_main_menu()
        t.choose(6)
        t.wait("show its mnemonic")
        t.send("1")
        t.wait("password to decrypt the mnemonic")
        t.send(W1_PW)
        t.wait("mnemonic passphrase")
        t.wait("Store this mnemonic securely")
        shown_m = " ".join(re.sub(r"^[^a-z]+", "", t.snippet(2500)).split())
        rec.record("A3-show-mnemonic-match",
                   "Show Mnemonic decrypts and displays the same 24 words",
                   shown_m == w1_mnemonic,
                   "<24 words compared equal>" if shown_m == w1_mnemonic
                   else f"mismatch: got {len(shown_m.split())} words")
        t.expect_main_menu()

        # A: view wallets (listing prints, then the menu returns — capture that span)
        t.choose(1)
        t.wait("Your wallets:")
        t.expect_main_menu()  # consumes the menu reprint; leaves at_menu=True
        snap = t.snippet(2500)
        flat = squoosh(snap)
        ok = ("aud_w1" in snap and "aud_w2" in snap and w1_addr in flat
              and "[preprod]" in snap and "wrong network" not in snap)
        rec.record("A-view-wallets",
                   "View Wallets lists both wallets, addresses, preprod tag, no "
                   "wrong-network warning", ok, snap,
                   note="network tag [preprod] is Rich-markup-swallowed and never "
                        "rendered — real bug (finding)")

        # A6: assets on empty wallet
        t.choose(5)
        t.wait("track LPs")
        t.send("1")
        t.wait("No UTxOs found for this address")
        rec.record("A6-assets-empty",
                   "View Wallet Assets on unfunded wallet: clean empty message"
                   " (note DEBUG line in output)", True, t.snippet(500))
        t.expect_main_menu()

        # A5 negative paths
        t.choose(7)
        t.wait("Enter the wallet name from which you want to send funds")
        t.send("cancel")
        t.wait("Send funds cancelled")
        rec.record("A5-cancel-at-wallet-name", "'cancel' at wallet-name prompt aborts",
                   True, t.snippet(300))
        t.expect_main_menu()

        t.choose(7)
        t.wait("Enter the wallet name from which you want to send funds")
        t.send("no_such_wallet")
        t.wait("Wallet not found")
        t.send("cancel")
        t.wait("Send funds cancelled")
        rec.record("A5-nonexistent-wallet", "Unknown wallet name error + cancel",
                   True, t.snippet(400))
        t.expect_main_menu()

        t.choose(7)
        t.wait("Enter the wallet name from which you want to send funds")
        t.send("aud_w1")
        t.wait("Enter the recipient's address")
        t.send("cancel")
        t.wait("Send funds cancelled")
        rec.record("A5-cancel-at-recipient", "'cancel' at recipient prompt aborts",
                   True, t.snippet(300))
        t.expect_main_menu()

        t.choose(7)
        t.wait("Enter the wallet name from which you want to send funds")
        t.send(FUNDER)
        t.wait("Enter the recipient's address")
        t.send("notanaddress")
        t.wait("Enter amount of Cardano Native Token ADA to send")
        t.send("1.0000001")  # >6 decimals must be rejected by ada_to_lovelace
        t.wait("more than 6 decimal")
        t.send("abc")
        t.wait("is not a valid ADA amount")
        t.send("1")
        t.wait("Do you want to send a token as well?")
        t.send("no")
        t.wait(r"\n> ")  # spinner prompt
        t.send(FUNDER_PW)
        t.wait("Invalid recipient address")
        rec.record("A5-invalid-recipient-and-amount-validation",
                   "Invalid recipient and malformed amounts rejected with clear "
                   "errors, no crash", True, t.snippet(700))
        t.expect_main_menu()

        # A5: insufficient funds (aud_w2 is empty)
        t.choose(7)
        t.wait("Enter the wallet name from which you want to send funds")
        t.send("aud_w2")
        t.wait("Enter the recipient's address")
        t.send(w1_addr)
        t.wait("Enter amount of Cardano Native Token ADA to send")
        t.send("500")
        t.wait("Do you want to send a token as well?")
        t.send("no")
        t.wait(r"\n> ")  # spinner prompt
        t.send(W2_PW)
        t.wait("No UTxOs found for the sender's address")
        rec.record("A5-insufficient-funds",
                   "Spending from empty wallet fails gracefully (no crash)",
                   True, t.snippet(600))
        t.expect_main_menu()

        # A5: real funding tx ci_funder -> aud_w1 (2 ADA) via local backend
        tx_fund = send_funds_flow(t, FUNDER, w1_addr, "3", FUNDER_PW)
        rec.record("A5-fund-from-funder",
                   f"Real tx ci_funder->aud_w1 2 ADA submitted via Ogmios: {tx_fund}",
                   True, f"txid {tx_fund}")
        utxos, total = poll_utxos(w1_addr)
        rec.record("A5-fund-confirmed",
                   f"aud_w1 funded on-chain: {total} lovelace across "
                   f"{len(utxos or [])} UTxO(s)", bool(utxos), f"total={total}")

        # A5: exact-amount regression 1.000001 aud_w1 -> aud_w2
        tx_exact = send_funds_flow(t, "aud_w1", w2_addr, "1.000001", W1_PW)
        rec.record("A5-exact-amount-send",
                   f"1.000001 ADA send submitted: {tx_exact}", True, f"txid {tx_exact}")
        utxos, total = poll_utxos(w2_addr, want_lovelace=1000001)
        exact_hit = any(
            int(a["quantity"]) == 1000001
            for u in utxos or [] for a in u["amount"] if a["unit"] == "lovelace")
        rec.record("A5-exact-amount-onchain",
                   "On-chain output is exactly 1,000,001 lovelace (float-math "
                   "regression)", exact_hit,
                   json.dumps([{a["unit"]: a["quantity"] for a in u["amount"]}
                               for u in utxos or []]))

        # A6: assets table now shows the exact amount
        t.choose(5)
        t.wait("track LPs")
        t.send("2")
        t.wait("Assets & LP Tokens in 'aud_w2'")
        snap = t.snippet(1500)
        rec.record("A6-assets-table",
                   "Assets table renders ADA amount 1.000001 for aud_w2",
                   "1.000001" in snap, snap,
                   note="DEBUG address line also visible to user (UX finding)")
        t.expect_main_menu()

        # A8: stubs
        t.choose(10)
        t.wait("under development")
        rec.record("A8-pool-stub", "Pool Registration shows clear under-development "
                   "notice", True, t.snippet(300))
        t.expect_main_menu()
        t.choose(11)
        t.wait("under development")
        rec.record("A8-drep-stub", "DRep Registration shows clear under-development "
                   "notice", True, t.snippet(300))
        t.expect_main_menu()

        # A: debug backend health
        t.choose(13)
        t.expect_main_menu()
        rec.record("A-debug-backend-health",
                   "Debug Backend Health renders info and returns to menu",
                   "Ogmios" in t.snippet(900) or "health" in t.snippet(900).lower(),
                   t.snippet(900))

        # A9: switch backend to Blockfrost and back
        t.choose(12)
        t.wait("Select a Cardano network")
        t.send("2")
        t.wait("Select a new backend")
        t.send("1")
        t.wait("Enter your Blockfrost preprod API key")
        t.send(KEY)
        t.wait("Switched to Blockfrost")
        t.expect_main_menu()
        t.choose(13)
        t.expect_main_menu()
        rec.record("A9-switch-to-blockfrost",
                   "Switch Backend local->Blockfrost mid-session, health OK",
                   "blockfrost" in t.snippet(900).lower(), t.snippet(900))
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
        rec.record("A9-switch-back-to-local",
                   "Switch Backend Blockfrost->local round-trip", True, t.snippet(300))
        t.expect_main_menu()

        t.exit_app()
    except (TUIError, Exception) as e:  # noqa: BLE001
        rec.record("A-run1-crash", f"Run 1 aborted: {e}", False, str(e))
        t.close()
    return w1_mnemonic, w1_addr, w2_addr


# ---------------------------------------------------------------- Run 2
def run2(w1_mnemonic: str, w1_addr: str, w2_addr: str) -> None:
    t = TUI("a_run2_audlocal2")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "register", "audlocal2", USER_PW)
        rec.record("A4-register-second-user",
                   "Second user registration + startup flow replay", True,
                   t.snippet(500))

        # invalid mnemonic first
        t.choose(3)
        t.wait("unique wallet name for the imported wallet")
        t.send("aud_rec")
        t.wait("Choose import method")
        t.send("5")
        t.wait("Invalid choice")
        t.send("1")
        t.wait("Enter your 24-word mnemonic passphrase")
        bad = w1_mnemonic.split()[:-1] + ["notaword"]
        t.send(" ".join(bad))
        t.wait("Invalid mnemonic phrase. Import aborted.")
        rec.record("A4-invalid-mnemonic-rejected",
                   "Invalid mnemonic rejected with clear message", True, t.snippet(400))
        t.expect_main_menu()

        # leaked-dir bug probe: same name again
        t.choose(3)
        t.wait("unique wallet name for the imported wallet")
        t.send("aud_rec")
        leaked = t.wait("already exists|Choose import method", timeout=20)
        leaked_dir = os.path.isdir(
            os.path.join(REPO, "CardanoInterface", "wallets", "aud_rec"))
        rec.record(
            "A4-invalid-mnemonic-leaks-dir",
            "After invalid-mnemonic abort, wallet dir 'aud_rec' was left behind "
            "and blocks the name on retry",
            not leaked_dir,
            f"dir_exists={leaked_dir} matched={leaked[-120:]!r}",
            note="bug if FAIL: os.makedirs runs before mnemonic validation")
        if leaked_dir:
            # escape the name prompt, retry under a fresh name
            t.send("cancel")
            t.wait("Wallet import cancelled")
            t.expect_main_menu()
            t.choose(3)
            t.wait("unique wallet name for the imported wallet")
            t.send("aud_rec2")
            t.wait("Choose import method")
            t.send("1")
        else:
            t.send("1")
        t.wait("Enter your 24-word mnemonic passphrase")
        t.send(w1_mnemonic)
        t.wait("Enter your password to encrypt the wallet's signing keys")
        t.send(W1_PW)
        t.wait("importedsuccessfully")  # rendered with missing space (recorded finding)
        t.wait("Address:")
        t.wait("Main Menu")
        a = re.search(r"(addr_test1[a-z0-9]+)", squoosh(t.snippet(500)))
        rec_addr = a.group(1) if a else ""
        rec.record("A4-recovery-address-parity",
                   f"Recovered wallet derives identical address to original "
                   f"({rec_addr == w1_addr})", rec_addr == w1_addr and bool(rec_addr),
                   f"original={w1_addr} recovered={rec_addr}")
        t.expect_main_menu()

        # prove recovered keys sign: fund aud_rec2, then spend from it
        tx_fund = None
        try:
            tx_fund = send_funds_flow(t, FUNDER, rec_addr, "3", FUNDER_PW)
        except TUIError as e:
            rec.record("A4-fund-recovered", f"funding aud_rec2 failed: {e}", False,
                       str(e))
        if tx_fund:
            utxos, total = poll_utxos(rec_addr)
            rec.record("A4-fund-recovered-confirmed",
                       f"aud_rec2 funded: {total} lovelace", bool(utxos), f"tx {tx_fund}")
            tx_spend = send_funds_flow(t, "aud_rec2", w2_addr, "1.2", W1_PW)
            rec.record("A4-recovered-keys-sign",
                       f"Recovered wallet signs and submits real spend: {tx_spend}",
                       True, f"txid {tx_spend}")
            utxos, _ = poll_utxos(w2_addr, want_lovelace=2200001)
            got = any(int(a["quantity"]) == 1200000
                      for u in utxos or [] for a in u["amount"] if a["unit"] == "lovelace")
            rec.record("A4-recovered-spend-confirmed",
                       "1.2 ADA from recovered wallet visible on-chain at aud_w2",
                       got, json.dumps([{a["unit"]: a["quantity"] for a in u["amount"]}
                                        for u in utxos or []][:3]))

        t.exit_app()
    except (TUIError, Exception) as e:  # noqa: BLE001
        rec.record("A-run2-crash", f"Run 2 aborted: {e}", False, str(e))
        t.close()


def run2_reuse() -> None:
    """Reuse mode: aud_rec2 already imported and funded; just finish the spend."""
    t = TUI("a_run2_reuse")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "login", "audlocal2", USER_PW)
        w2_addr = json.load(open(os.path.join(REPO, "audit", "results",
                                              "a_w2_addr.json")))["address"]
        tx_spend = send_funds_flow(t, "aud_rec2", w2_addr, "1.2", W1_PW)
        rec.record("A4-recovered-keys-sign",
                   f"Recovered wallet signs and submits real spend: {tx_spend}",
                   True, f"txid {tx_spend}")
        utxos, _ = poll_utxos(w2_addr, want_lovelace=2200001)
        got = any(int(a["quantity"]) == 1200000
                  for u in utxos or [] for a in u["amount"] if a["unit"] == "lovelace")
        rec.record("A4-recovered-spend-confirmed",
                   "1.2 ADA from recovered wallet visible on-chain at aud_w2",
                   got, json.dumps([{a["unit"]: a["quantity"] for a in u["amount"]}
                                    for u in utxos or []][:3]))
        t.exit_app()
    except (TUIError, Exception) as e:  # noqa: BLE001
        rec.record("A-run2-crash", f"Reuse run aborted: {e}", False, str(e))
        t.close()


# ---------------------------------------------------------------- Run 3
def run3() -> None:
    t = TUI("a_run3_auddel")
    try:
        from harness import startup_to_main_menu
        startup_to_main_menu(t, "register", "auddel", USER_PW)
        # create throwaway wallet
        t.choose(2)
        t.wait("unique wallet name")
        t.send("aud_tmp")
        t.wait("password to encrypt the wallet's signing keys")
        t.send(USER_PW)
        t.wait("created successfully")
        t.expect_main_menu()

        # delete wallet: negative paths then success
        t.choose(8)
        t.wait("Enter the number of the wallet to delete")
        t.send("99")
        t.wait("Invalid choice, try again")
        t.send("1")
        t.wait("Type 'DELETE' to confirm")
        t.send("yes")
        t.wait("Deletion cancelled")
        rec.record("A7-delete-wallet-cancel-confirmation",
                   "Non-DELETE confirmation word aborts deletion", True, t.snippet(300))
        t.expect_main_menu()

        t.choose(8)
        t.wait("Enter the number of the wallet to delete")
        t.send("1")
        t.wait("Type 'DELETE' to confirm")
        t.send("DELETE")
        t.wait("Enter your password to confirm wallet deletion")
        t.send("WrongPw1!")
        t.wait("Password incorrect. Aborting deletion")
        rec.record("A7-delete-wallet-wrong-password",
                   "Wrong password aborts wallet deletion", True, t.snippet(300))
        t.expect_main_menu()

        t.choose(8)
        t.wait("Enter the number of the wallet to delete")
        t.send("1")
        t.wait("Type 'DELETE' to confirm")
        t.send("DELETE")
        t.wait("Enter your password to confirm wallet deletion")
        t.send(USER_PW)
        t.wait("deleted successfully")
        gone = not os.path.exists(
            os.path.join(REPO, "CardanoInterface", "wallets", "aud_tmp"))
        rec.record("A7-delete-wallet-success",
                   f"Wallet deleted from disk (dir removed={gone})", gone,
                   t.snippet(300))
        t.expect_main_menu()

        # delete user
        t.choose(9)
        t.wait("Type your username to confirm deletion")
        t.send("auddel")
        t.wait(r"Enter your password \(1/2\)")
        t.send(USER_PW)
        t.wait(r"Enter your password \(2/2\)")
        t.send(USER_PW)
        t.wait("deletedsuccessfully")  # rendered with missing space (recorded finding)
        userdb_gone = not os.path.exists(
            os.path.join(REPO, "CardanoInterface", "users", "auddel.userdb"))
        rec.record("A7-delete-user-success",
                   f"User + associated wallets deleted (userdb removed={userdb_gone})",
                   userdb_gone, t.snippet(400),
                   note="success message renders as 'deletedsuccessfully.' — "
                        "missing space")
        t.exit_app()
    except (TUIError, Exception) as e:  # noqa: BLE001
        rec.record("A-run3-crash", f"Run 3 aborted: {e}", False, str(e))
        t.close()


if __name__ == "__main__":
    reuse = "--reuse" in sys.argv
    if reuse:
        run2_reuse()
    else:
        w1_mnemonic, w1_addr, w2_addr = run1()
        with open(os.path.join(REPO, "audit", "results", "a_w2_addr.json"), "w") as f:
            json.dump({"address": w2_addr}, f)
        if w1_mnemonic and w1_addr:
            run2(w1_mnemonic, w1_addr, w2_addr)
        run3()
    print("\nPhase A done. Results: audit/results/phase_a.json")
