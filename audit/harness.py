"""Pexpect harness for driving the CardanoInterface TUI through real PTY sessions.

Evidence rules: every check records pass/fail plus the raw observed transcript
snippet (ANSI-stripped) so the audit report can quote what the user actually saw.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

import pexpect

REPO = "/home/dd/dev/CardanoInterface"
LOG_DIR = os.path.join(REPO, "audit", "logs")
RESULTS_DIR = os.path.join(REPO, "audit", "results")

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>]|\r")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def squoosh(text: str) -> str:
    """Remove ALL whitespace — Rich soft-wraps long tokens (addresses, tx ids)
    at column 80; squooshing makes substring/regex checks wrap-tolerant."""
    return "".join(text.split())


@dataclass
class Check:
    check_id: str
    description: str
    passed: bool
    evidence: str
    note: str = ""


@dataclass
class Recorder:
    phase: str
    checks: list[Check] = field(default_factory=list)

    def record(self, check_id: str, description: str, passed: bool, evidence: str,
               note: str = "") -> None:
        self.checks.append(
            Check(check_id, description, passed, strip_ansi(evidence)[-2000:], note))
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {check_id}: {description}")
        if note:
            print(f"        note: {note}")
        self.flush()

    def flush(self) -> None:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        path = os.path.join(RESULTS_DIR, f"{self.phase}.json")
        with open(path, "w") as f:
            json.dump(
                [{"id": c.check_id, "desc": c.description, "passed": c.passed,
                  "evidence": c.evidence, "note": c.note} for c in self.checks],
                f, indent=2)


class TUIError(Exception):
    pass


class TUI:
    """One live CardanoInterface session in a PTY."""

    def __init__(self, name: str, timeout: int = 90):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.name = name
        self.timeout = timeout
        self.log_path = os.path.join(LOG_DIR, f"{name}.ansi.log")
        self._log = open(self.log_path, "w", encoding="utf-8")
        self.child = pexpect.spawn(
            "uv", ["run", "CardanoInterface.py"],
            cwd=REPO, encoding="utf-8", codec_errors="replace",
            timeout=timeout, dimensions=(40, 120),
            # TERM=dumb: Rich emits zero ANSI and prompt_toolkit skips CPR/bracketed-
            # paste queries — plain-text matching works. Colored rendering is
            # verified separately (audit/logs/*.ansi.log from a colored session).
            env={**os.environ, "TERM": "dumb", "COLUMNS": "120"},
        )
        self.child.logfile_read = self._log
        self.last_snippet = ""
        self.at_menu = False

    def wait(self, pattern: str, timeout: int | None = None) -> str:
        """Wait for a plain-text pattern; returns the transcript up to it."""
        try:
            self.child.expect(pattern, timeout=timeout or self.timeout)
        except (pexpect.TIMEOUT, pexpect.EOF) as e:
            tail = strip_ansi(self.child.before or "")[-1500:] if self.child.before else ""
            raise TUIError(
                f"[{self.name}] timeout/EOF waiting for {pattern!r}. "
                f"exit={getattr(self.child, 'exitstatus', None)} tail:\n{tail}"
            ) from e
        self.last_snippet = strip_ansi(self.child.before or "")
        return self.last_snippet

    def send(self, line: str) -> None:
        self.child.sendline(line)

    def snippet(self, size: int = 800) -> str:
        return strip_ansi(self.child.before or "")[-size:]

    def expect_main_menu(self) -> str:
        out = self.wait(r"Please choose an option by number")
        self.at_menu = True
        return out

    def choose(self, number: int) -> None:
        """Send a main-menu choice. The menu prompt prints once per return-to-
        menu; at_menu tracks whether it has been consumed already."""
        if not self.at_menu:
            self.expect_main_menu()
        self.send(str(number))
        self.at_menu = False

    def exit_app(self) -> None:
        try:
            self.expect_main_menu()
            self.send("exit")
            self.wait("Goodbye", timeout=30)
        except TUIError:
            pass
        self.child.terminate(force=True)
        self._log.close()

    def close(self) -> None:
        self.child.terminate(force=True)
        self._log.close()


def startup_to_main_menu(t: TUI, action: str, username: str, password: str,
                         network: str = "2", backend: str = "2",
                         blockfrost_key: str | None = None,
                         ogmios_url: str = "", kupo_url: str = "") -> None:
    """Drive: login/register -> network (with one invalid input) -> backend -> main menu."""
    t.wait(r"Do you want to \(login/register/exit\)\?")
    t.send(action)
    if action == "register":
        t.wait("Please choose a username")
        t.send(username)
        t.wait("Enter your password")
        t.send(password)
        t.wait("Confirm your password")
        t.send(password)
        t.wait("registered successfully")
    else:
        t.wait("User login")
        t.send(username)
        t.wait(r"\n> ")  # spinner password prompt renders no label in dumb mode
        t.send(password)
        t.wait("Welcome back")
    t.wait("Select a Cardano network")
    t.send("9")  # invalid on purpose: must re-prompt, not crash
    t.wait("Invalid choice")
    t.send(network)
    t.wait("Network set to")
    t.wait("Select a backend")
    t.send("7")  # invalid on purpose
    t.wait("Invalid choice")
    t.send(backend)
    if backend == "1":
        t.wait("Enter your Blockfrost")
        t.send(blockfrost_key or "")
        t.wait("Connected to")
    else:
        t.wait(r"Enter Ogmios URL")
        t.send(ogmios_url)
        t.wait(r"Enter Kupo URL")
        t.send(kupo_url)
        t.wait("Connected to local Ogmios")
    t.expect_main_menu()


def read_bf(path: str, key: str) -> object:
    import urllib.request

    req = urllib.request.Request(
        f"https://cardano-preprod.blockfrost.io/api/v0/{path}",
        headers={"project_id": key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def bf_key() -> str:
    with open("/tmp/bf_key") as f:
        return f.read().strip()
