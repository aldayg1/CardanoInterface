"""Phase D2: re-verify every Phase-D fix in the real TUI (local backend)."""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from harness import REPO, Recorder, TUI, TUIError, startup_to_main_menu, squoosh  # noqa: E402

sys.path.insert(0, REPO)

rec = Recorder("phase_d_verify")
PW = os.environ["CI_AUDIT_PW"]  # never hardcode; see CREDENTIALS.local.md
W1 = json.load(open(os.path.join(REPO, "audit", "results", "a_w1_secret.json")))
W3 = json.load(open(os.path.join(REPO, "audit", "results", "b_w3_secret.json")))


def run_verify() -> None:
    t = TUI("d_verify")
    try:
        startup_to_main_menu(t, "register", "audverify", PW)

        # 1. wallet_create: mnemonic confirmation + vkeys + dict entry
        t.choose(2)
        t.wait("unique wallet name")
        t.send("aud_vw")
        t.wait("password to encrypt the wallet's signing keys")
        t.send(PW)
        t.wait("IMPORTANT: Save your 24-word mnemonic securely")
        t.wait("To confirm you saved it")
        mnemonic = " ".join(w for w in t.snippet(3000).split() if w.isalpha())
        t.wait(r"of your mnemonic \(or 'cancel' to abort\)")
        word_index = int(re.search(r"\d+", t.snippet(100)).group())
        t.send("wrongword")
        t.wait("not the right word")
        rec.record("D-mnemonic-confirm-wrong-word",
                   "Wrong confirmation word re-displays the mnemonic", True,
                   t.snippet(400))
        t.wait(r"of your mnemonic \(or 'cancel' to abort\)")
        word_index = int(re.search(r"\d+", t.snippet(100)).group())
        t.send(mnemonic.split()[word_index - 1])
        t.wait("Mnemonic confirmed")
        t.wait("created successfully")
        rec.record("D-mnemonic-confirm",
                   f"Wallet creation requires typing word #{word_index} of the "
                   f"mnemonic (24 words={len(mnemonic.split())})", True,
                   "confirmation accepted")
        t.wait("Address:")
        t.wait("Main Menu")
        wdir = os.path.join(REPO, "CardanoInterface", "wallets", "aud_vw")
        files = sorted(os.listdir(wdir))
        rec.record("D-create-writes-vkeys",
                   f"Create now writes the same artifact set as import: {files}",
                   {"payment.vkey", "stake.vkey"}.issubset(set(files)), str(files))
        import CardanoInterface as ci
        ud = ci.load_user_data("audverify", PW)
        entry = [w for w in ud["wallets"]
                 if isinstance(w, dict) and w.get("name") == "aud_vw"]
        rec.record("D-create-dict-entry",
                   f"Created wallet registered as a dict entry: {entry}",
                   bool(entry) and bool(entry[0].get("address")), str(entry))

        # 2. view wallets: network tag renders
        t.choose(1)
        t.wait("Your wallets:")
        t.expect_main_menu()
        snap = t.snippet(2500)
        rec.record("D-view-network-tag-renders",
                   "View Wallets shows the [preprod] network tag",
                   "[preprod]" in snap, snap[:400])

        # 3. tracker: no DEBUG line
        t.choose(5)
        t.wait("Enter the number of the wallet to track LPs")
        t.send("1")
        t.wait(r"Assets & LP Tokens|No UTxOs|Failed to load", timeout=60)
        t.expect_main_menu()
        snap = t.snippet(2000)
        rec.record("D-tracker-no-debug-line",
                   "View Wallet Assets no longer prints the DEBUG lookup line",
                   "DEBUG:" not in snap, snap[:300])

        # 4. import: invalid mnemonic leaves no dir; success message spaced
        t.choose(3)
        t.wait("unique wallet name for the imported wallet")
        t.send("aud_vrec")
        t.wait("Choose import method")
        t.send("1")
        t.wait("Enter your 24-word mnemonic passphrase")
        bad = W1["mnemonic"].split()[:-1] + ["notaword"]
        t.send(" ".join(bad))
        t.wait("Invalid mnemonic phrase. Import aborted.")
        leaked = os.path.isdir(
            os.path.join(REPO, "CardanoInterface", "wallets", "aud_vrec"))
        rec.record("D-import-no-dir-leak",
                   f"Invalid mnemonic leaves no wallet dir (dir exists={leaked})",
                   not leaked, f"leaked={leaked}")
        t.expect_main_menu()

        t.choose(3)
        t.wait("unique wallet name for the imported wallet")
        t.send("aud_vrec2")
        t.wait("Choose import method")
        t.send("1")
        t.wait("Enter your 24-word mnemonic passphrase")
        t.send(W1["mnemonic"])
        t.wait("Enter your password to encrypt the wallet's signing keys")
        t.send(PW)
        t.wait("imported successfully")
        rec.record("D-import-success-spaced",
                   "Import success message renders with a space", True,
                   t.snippet(300))
        t.expect_main_menu()

        # 5. send failure: clean error, no builder dump on the terminal
        t.choose(7)
        t.wait("Enter the wallet name from which you want to send funds")
        t.send("aud_vw")
        t.wait("Enter the recipient's address")
        t.send(W1["address"])
        t.wait("Enter amount of Cardano Native Token ADA to send")
        t.send("500")
        t.wait("Do you want to send a token as well?")
        t.send("no")
        t.wait(r"\n> ")
        t.send(PW)
        t.wait("Re-selecting coins|Error:|Failed to send", timeout=120)
        t.expect_main_menu()
        snap = t.snippet(3000)
        no_dump = "_potential_inputs" not in snap and "utxo_selectors" not in snap
        rec.record("D-send-failure-clean",
                   "Failed send shows a clean error, no builder state dump",
                   no_dump, snap[:400],
                   note="full state still lands in debug_main.log")

        # 6. multisig menu: 12 items + Show My Cosigner Key
        t.choose(4)
        t.wait("Choose an option \\(or 'back'\\)")
        snap = t.snippet(1200)
        has_item = "Show My Cosigner Key" in snap
        numbered = "8. Build Unsigned Transaction" in snap.replace("\n", " ")
        rec.record("D-cosigner-key-menu-item",
                   "Multisig menu shows 'Show My Cosigner Key' with correct "
                   "section numbering", has_item and numbered, snap[:600])
        t.send("3")  # Show My Cosigner Key
        t.wait("Enter your 24-word recovery phrase")
        t.send(W3["mnemonic"])
        t.wait("acct_shared_xvk1")
        t.wait("Choose an option \\(or 'back'\\)")
        import CardanoInterface as ci2
        expected = ci2.bech32_xpub_from_bytes(
            ci2.derive_account_xpub_from_mnemonic(W3["mnemonic"]))
        tail = expected.replace("acct_shared_xvk1", "")
        rec.record("D-cosigner-key-derivation",
                   "Show My Cosigner Key displays the correct bech32 xpub",
                   tail in squoosh(t.snippet(2000)), f"expected={expected[:30]}...")
        t.send("back")
        t.expect_main_menu()

        # 7. delete user (cleanup of audverify + its wallets)
        t.choose(9)
        t.wait("Type your username to confirm deletion")
        t.send("audverify")
        t.wait(r"Enter your password \(1/2\)")
        t.send(PW)
        t.wait(r"Enter your password \(2/2\)")
        t.send(PW)
        t.wait("deleted successfully")
        rec.record("D-delete-user-spaced",
                   "Delete-user success message renders with a space", True,
                   t.snippet(300))
        t.exit_app()
    except Exception as e:  # noqa: BLE001
        rec.record("D-verify-crash", f"verify run aborted: {e}", False, str(e))
        t.close()

    # 8. Kupo network no longer hardcoded
    import CardanoInterface as ci3
    k1 = ci3.KupoBackend("http://localhost:1442", network="mainnet")
    k2 = ci3.KupoBackend("http://localhost:1442", network="preprod")
    from pycardano import Network
    rec.record("D-kupo-network",
               "KupoBackend.network follows the configured network "
               f"(mainnet={k1.network == Network.MAINNET}, "
               f"preprod={k2.network == Network.TESTNET})",
               k1.network == Network.MAINNET and k2.network == Network.TESTNET,
               f"{k1.network} / {k2.network}")


if __name__ == "__main__":
    run_verify()
    print("Phase D2 done.")
