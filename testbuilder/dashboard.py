"""Testbuilder dashboard — the operator's control surface for the test suite
builder (launched from the toolkit's Hyper+U Utilities panel).

Read-only status: the knowledge-base index (per-symbol builds, verification
state, refinement rounds), the accumulated lessons, and the assessment queue
(drafts awaiting the mandatory human/agent review). Generation itself runs
through `uv run python -m testbuilder.cli` — the dashboard never bypasses the
assess gate; it shows what is waiting on it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import testbuilder.dashboard as _pkg

    raise SystemExit(_pkg.main())

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from testbuilder import knowledge

REPO = Path(__file__).resolve().parent.parent
REVIEW_DIR = REPO / "tests" / "_review"


def _review_queue_lines() -> list[str]:
    if not REVIEW_DIR.exists():
        return ["(no review dir — nothing generated yet)"]
    drafts = sorted(REVIEW_DIR.glob("draft_*.py"))
    lines = [f"drafts awaiting assessment: {len(drafts)}"]
    approved = REVIEW_DIR / "approved.json"
    lines.append(f"approved.json present: {approved.exists()}")
    if approved.exists():
        try:
            recorded = json.loads(approved.read_text(encoding="utf-8"))
            lines.append(f"approved symbols: {', '.join(recorded)}")
        except json.JSONDecodeError:
            lines.append("approved.json: INVALID JSON")
    for d in drafts:
        lines.append(f"  - {d.name}")
    return lines


class RefreshController:
    """Runs the unattended refresh as a child process and streams its output
    into the dashboard log pane. The refresh is the final phase: staleness
    scan, delete+remake, machine validation, verify, record, report."""

    def __init__(self, log_pane: QTextEdit, refresh_btn: QPushButton) -> None:
        self.log = log_pane
        self.btn = refresh_btn
        self.proc: QProcess | None = None

    def start(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.log.append("refresh already running")
            return
        self.btn.setEnabled(False)
        self.log.append("── launching unattended refresh…")
        self.proc = QProcess(self.btn)
        self.proc.setWorkingDirectory(str(REPO))
        self.proc.readyReadStandardOutput.connect(self._stream)
        self.proc.readyReadStandardError.connect(self._stream_err)
        self.proc.finished.connect(self._done)
        self.proc.start("uv", ["run", "python", "-m", "testbuilder.cli",
                               ".", "CardanoInterface.py", "--refresh"])

    def _stream(self) -> None:
        if self.proc:
            out = self.proc.readAllStandardOutput().data().decode(errors="replace")
            self.log.append(out.rstrip())

    def _stream_err(self) -> None:
        if self.proc:
            out = self.proc.readAllStandardError().data().decode(errors="replace")
            self.log.append(out.rstrip())

    def _done(self, code: int, _status) -> None:
        self.btn.setEnabled(True)
        verdict = "finished cleanly" if code == 0 else f"exit {code} — see log"
        self.log.append(f"── refresh {verdict}. Post-hoc agent validation of "
                        "kb/last_report.md is the designed next step.")


def build_window() -> QMainWindow:
    win = QMainWindow()
    win.setWindowTitle("Testbuilder — CardanoInterface")
    win.resize(900, 640)
    central = QWidget()
    layout = QVBoxLayout(central)

    index_entries = knowledge.index_entries()
    layout.addWidget(QLabel(
        f"Knowledge base — {len(index_entries)} symbol record(s) "
        f"({knowledge.KB_DIR.relative_to(REPO)})"))
    table = QTableWidget(len(index_entries), 5)
    table.setHorizontalHeaderLabels(
        ["Symbol", "Concern", "Date", "Verify", "Rounds"])
    table.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.Stretch)
    for row, entry in enumerate(index_entries):
        for col, key in enumerate(("symbol", "concern", "date",
                                   "verify", "rounds")):
            item = QTableWidgetItem(str(entry.get(key, "?")))
            if key == "verify":
                item.setForeground(
                    window_label_color(entry.get("verify") == "pass"))
            table.setItem(row, col, item)
    layout.addWidget(table)

    defective = knowledge.defective_symbols()
    metrics = knowledge.metrics()
    trend = " | ".join(
        f"{m.get('date')}: stale={m.get('stale')} regen={m.get('regenerated')} "
        f"defective={m.get('defective')}" for m in metrics)
    layout.addWidget(QLabel(
        f"Defects branded: {len(defective)}"
        + (f" — {', '.join(sym for sym, _ in defective)}" if defective else "")
        + f"    Refresh trend: {trend or '(no refresh runs yet)'}"))
    layout.addWidget(QLabel("Lessons (model failure modes, durable):"))
    lessons_box = QTextEdit()
    lessons_box.setReadOnly(True)
    lessons_box.setPlainText(knowledge.lessons(limit=12))
    lessons_box.setMaximumHeight(180)
    layout.addWidget(lessons_box)

    layout.addWidget(QLabel("Assessment queue (tests/_review/):"))
    queue_box = QTextEdit()
    queue_box.setReadOnly(True)
    queue_box.setPlainText("\n".join(_review_queue_lines()))
    queue_box.setMaximumHeight(140)
    layout.addWidget(queue_box)

    refresh_btn = QPushButton("Refresh suite (unattended: remake stale, "
                              "machine-validate, verify, record, report)")
    layout.addWidget(refresh_btn)
    log_pane = QTextEdit()
    log_pane.setReadOnly(True)
    log_pane.setMaximumHeight(200)
    log_pane.setPlaceholderText("refresh output appears here")
    layout.addWidget(log_pane)
    controller = RefreshController(log_pane, refresh_btn)
    refresh_btn.clicked.connect(controller.start)

    win.setCentralWidget(central)
    return win


def window_label_color(ok: bool):  # noqa: ANN202 - Qt colour type varies
    from PyQt6.QtGui import QColor
    return QColor(0, 128, 0) if ok else QColor(200, 0, 0)


def main() -> int:
    if "--selfcheck" in sys.argv:
        app = QApplication(sys.argv)
        win = build_window()
        win.show()
        entries = knowledge.index_entries()
        print(f"testbuilder dashboard OK — {len(entries)} KB record(s), "
              f"{len(knowledge.lessons().splitlines())} lesson line(s), "
              f"review dir present: {REVIEW_DIR.exists()}")
        app.processEvents()
        return 0
    app = QApplication(sys.argv)
    win = build_window()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
