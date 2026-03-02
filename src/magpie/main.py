#!/usr/bin/env python3
"""
magpie_desktop.py — hledger desktop dashboard client
2026 - By Vanderson 
========================================================
Install deps:   pip install PySide6
Requires:       hledger  (https://hledger.org/install.html)

Run:            python3 magpiedesktop.py
                python3 magpie_desktop.py --config path/to/config.json
"""

import sys
import os
import re
import json
import shutil
import subprocess
import csv
import io
from datetime import date as date_type, datetime
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QListWidget, QListWidgetItem,
    QFrame, QSplitter, QComboBox, QSpinBox, QToolBar, QStatusBar,
    QSizePolicy, QScrollArea, QDialog, QDialogButtonBox, QGridLayout,
    QToolButton, QButtonGroup, QAbstractButton,
    QCompleter, QMessageBox,  QFileDialog
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QTimer, QPropertyAnimation, QEasingCurve,
    QSize, QRect, QPoint, QObject, QStringListModel, QPointF, QRectF,
)
from PySide6.QtGui import (
    QFont, QFontDatabase, QColor, QPalette, QIcon, QPixmap,
    QPainter, QPen, QBrush, QLinearGradient, QTextCursor,
    QSyntaxHighlighter, QTextCharFormat, QKeySequence, QShortcut,
    QAction, QPolygonF, QPainterPath,
)

# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Report:
    name: str
    cmd: str
    locked: bool = False

@dataclass
class Filters:
    account: str = ""
    start_date: str = ""
    end_date: str = ""
    period: str = ""
    depth: int = 0

    def is_empty(self) -> bool:
        return not any([self.account, self.start_date,
                        self.end_date, self.period, self.depth])

DEFAULT_REPORTS: List[Report] = [
    Report("Register",          "hledger register"),
    Report("Expenses",          "hledger balance type:x"),
    Report("Assets",            "hledger balance type:a"),
    Report("Revenue",           "hledger balance type:r"),
    Report("Liabilities",       "hledger balance type:l"),
    Report("Income Statement",  "hledger incomestatement"),
    Report("Balance Sheet",     "hledger balancesheet"),
    Report("Forcast",           "hledger bal --forecast"),
    
    Report("Accounts",          "hledger accounts --tree", locked=True),
    Report("Commodities",       "hledger commodities",     locked=True),
]

PERIOD_FLAGS = {
    "Weekly":    "-W",
    "Monthly":   "-M",
    "Quarterly": "-Q",
    "Yearly":    "-Y",
}

def load_config(path: str) -> List[Report]:
    try:
        with open(path) as f:
            data = json.load(f)
        reports = []
        for r in data.get("reports", []):
            reports.append(Report(
                name=r.get("name", "Unnamed"),
                cmd=r.get("cmd", "hledger balance"),
                locked=r.get("locked", False),
            ))
        return reports or DEFAULT_REPORTS
    except Exception:
        return DEFAULT_REPORTS

# ─────────────────────────────────────────────────────────────────────────────
# Transaction data & journal helpers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Posting:
    account: str = ""
    amount: str  = ""
    currency: str = "$"

@dataclass
class Transaction:
    date: str        = ""
    description: str = ""
    payee: str       = ""
    note: str        = ""
    status: str      = ""   # "", "*", "!"
    tags: str        = ""
    postings: List[Posting] = field(default_factory=lambda: [Posting(), Posting()])

    def to_journal_entry(self) -> str:
        """Render the transaction as a hledger journal entry string."""
        lines = []
        # Header line:  DATE[=DATE2] [STATUS] [PAYEE |] DESCRIPTION  [; tags]
        header = self.date
        if self.status:
            header += f"  {self.status}"
        desc = self.description.strip()
        payee = self.payee.strip()
        if payee:
            header += f"  {payee} | {desc}"
        else:
            header += f"  {desc}"
        if self.note.strip():
            header += f"  ; {self.note.strip()}"
        if self.tags.strip():
            header += f"  ; {self.tags.strip()}"
        lines.append(header)

        # Postings
        auto_count = sum(1 for p in self.postings if p.account and not p.amount.strip())
        for p in self.postings:
            if not p.account.strip():
                continue
            amt = p.amount.strip()
            if amt:
                # Normalise: if it looks like a bare number, prepend currency
                if re.match(r'^-?[\d,]+\.?\d*$', amt):
                    amt = f"{p.currency}{amt}"
                lines.append(f"    {p.account:<38}  {amt}")
            else:
                lines.append(f"    {p.account}")
        lines.append("")
        return "\n".join(lines)

    def validate(self) -> List[str]:
        """Return list of error strings; empty list = valid."""
        errors = []
        if not self.date.strip():
            errors.append("Date is required.")
        else:
            try:
                date_type.fromisoformat(self.date.strip())
            except ValueError:
                errors.append("Date must be in YYYY-MM-DD format.")
        if not self.description.strip():
            errors.append("Description is required.")
        filled = [p for p in self.postings if p.account.strip()]
        if len(filled) < 2:
            errors.append("At least two postings (account lines) are required.")
        # At most one posting may have an empty amount (auto-balance)
        empty_amt = sum(1 for p in filled if not p.amount.strip())
        if empty_amt > 1:
            errors.append("Only one posting may have a blank amount (auto-balance).")
        return errors


def get_journal_file() -> Optional[str]:
    """Return the active hledger journal file path, or None."""
    if not shutil.which("hledger"):
        return None
    # Try environment variable first
    jfile = os.environ.get("LEDGER_FILE") or os.environ.get("HLEDGER_FILE")
    if jfile and os.path.exists(jfile):
        return jfile
    # hledger --file flag default
    default = os.path.expanduser("~/.hledger.journal")
    if os.path.exists(default):
        return default
    return None


def append_transaction(txn: Transaction, journal_path: str) -> Tuple[bool, str]:
    """Append the transaction to the journal file. Returns (success, message)."""
    entry = txn.to_journal_entry()
    try:
        with open(journal_path, "a", encoding="utf-8") as f:
            # Ensure we're on a new line
            f.write("\n" + entry)
        return True, f"Transaction saved to {journal_path}"
    except Exception as e:
        return False, f"Failed to write: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Account-list fetcher thread
# ─────────────────────────────────────────────────────────────────────────────

class AccountFetcher(QThread):
    done = Signal(list)   # list[str]

    def run(self):
        if not shutil.which("hledger"):
            self.done.emit([])
            return
        try:
            r = subprocess.run(["hledger", "accounts"],
                               capture_output=True, text=True, timeout=10)
            accounts = [a.strip() for a in r.stdout.splitlines() if a.strip()]
            self.done.emit(accounts)
        except Exception:
            self.done.emit([])


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread
# ─────────────────────────────────────────────────────────────────────────────

class ReportWorker(QThread):
    finished = Signal(str, bool)   # (output, is_error)

    def __init__(self, report: Report, filters: Filters):
        super().__init__()
        self.report  = report
        self.filters = filters

    def run(self):
        if not shutil.which("hledger"):
            self.finished.emit(
                "⚠  hledger not found in PATH.\n\n"
                "Install hledger to get started:\n"
                "  macOS   →  brew install hledger\n"
                "  Debian  →  apt install hledger\n"
                "  Arch    →  pacman -S hledger\n"
                "  Other   →  https://hledger.org/install.html\n",
                True,
            )
            return

        parts = self.report.cmd.split()
        if not self.report.locked:
            f = self.filters
            if f.account:
                parts.append(f.account)
            if f.start_date:
                parts += ["-b", f.start_date]
            if f.end_date:
                parts += ["-e", f.end_date]
            if f.period and f.period in PERIOD_FLAGS:
                parts.append(PERIOD_FLAGS[f.period])
            if f.depth > 0:
                parts += ["--depth", str(f.depth)]

        try:

            env = os.environ.copy()
            env["COLUMNS"] = "110" ## Increase the column
            result = subprocess.run(parts, capture_output=True,
                                    text=True, timeout=30, env=env)
            out = result.stdout

            if result.returncode != 0 and result.stderr:
                out += "\n[stderr]\n" + result.stderr
            self.finished.emit(out or "(no output)", result.returncode != 0)
        except subprocess.TimeoutExpired:
            self.finished.emit("⚠  Command timed out after 30 seconds.", True)
        except Exception as e:
            self.finished.emit(f"⚠  Error: {e}\nCommand: {' '.join(parts)}", True)

# ─────────────────────────────────────────────────────────────────────────────
# Palette & style constants
# ─────────────────────────────────────────────────────────────────────────────

# Rich dark slate palette  — inspired by financial terminals
C_BG_DEEP   = "#0d1117"   # deepest background
C_BG_PANEL  = "#161b22"   # sidebar / panels
C_BG_CARD   = "#1c2128"   # card surfaces
C_BG_HOVER  = "#262d36"   # hover state
C_BORDER    = "#30363d"   # subtle borders
C_ACCENT    = "#58a6ff"   # primary blue
C_ACCENT2   = "#3fb950"   # green (positive)
C_ACCENT3   = "#f78166"   # red/orange (expenses)
C_ACCENT4   = "#FFBB00"   # purple (liabilities)
C_ACCENT5   = "#ffa657"   # amber (locked badge)
C_TEXT_PRI  = "#e6edf3"   # primary text
C_TEXT_SEC  = "#8b949e"   # secondary/muted text
C_TEXT_DIM  = "#484f58"   # very dim
C_SEL_BG    = "#1f6feb33" # selection background (semi-transparent)
C_SEL_BORDER= "#1f6feb"   # selection border

REPORT_ICONS = {
    "Register":         "⊞",
    "Expenses":         "↑",
    "Assets":           "◈",
    "Revenue":          "↓",
    "Liabilities":      "⊖",
    "Income Statement": "≡",
    "Balance Sheet":    "⊟",
    "Accounts":         "⊕",
    "Commodities":      "◉",
}

REPORT_COLORS = {
    "Register":         C_ACCENT,
    "Expenses":         C_ACCENT3,
    "Assets":           C_ACCENT2,
    "Revenue":          C_ACCENT2,
    "Liabilities":      C_ACCENT4,
    "Income Statement": C_ACCENT,
    "Balance Sheet":    C_ACCENT,
    "Accounts":         C_ACCENT5,
    "Commodities":      C_ACCENT5,
}

# ─────────────────────────────────────────────────────────────────────────────
# Custom widgets
# ─────────────────────────────────────────────────────────────────────────────

class SidebarItem(QWidget):
    clicked = Signal()

    def __init__(self, report: Report, parent=None):
        super().__init__(parent)
        self.report   = report
        self._selected = False
        self._hovered  = False
        self.setFixedHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(12)

        icon_color = REPORT_COLORS.get(report.name, C_ACCENT)
        icon_char  = REPORT_ICONS.get(report.name, "●")

        self.icon_lbl = QLabel(icon_char)
        self.icon_lbl.setFixedSize(28, 28)
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        self.icon_lbl.setStyleSheet(f"""
            QLabel {{
                color: {icon_color};
                font-size: 16px;
                font-weight: bold;
                background: {icon_color}22;
                border-radius: 6px;
            }}
        """)

        self.name_lbl = QLabel(report.name)
        self.name_lbl.setStyleSheet(f"""
            QLabel {{ color: {C_TEXT_PRI}; font-size: 13px; font-weight: 500; }}
        """)

        lay.addWidget(self.icon_lbl)
        lay.addWidget(self.name_lbl, 1)

        if report.locked:
            badge = QLabel("LOCKED")
            badge.setStyleSheet(f"""
                QLabel {{
                    color: {C_ACCENT5};
                    background: {C_ACCENT5}22;
                    border: 1px solid {C_ACCENT5}55;
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: bold;
                    padding: 2px 5px;
                    letter-spacing: 0.5px;
                }}
            """)
            lay.addWidget(badge)

        self._update_style()

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()

    def _update_style(self):
        if self._selected:
            bg = C_SEL_BG
            border_left = f"3px solid {C_SEL_BORDER}"
        elif self._hovered:
            bg = C_BG_HOVER + "88"
            border_left = f"3px solid transparent"
        else:
            bg = "transparent"
            border_left = f"3px solid transparent"

        self.setStyleSheet(f"""
            SidebarItem {{
                background: {bg};
                border-left: {border_left};
                border-radius: 0px;
            }}
        """)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def enterEvent(self, e):
        self._hovered = True
        self._update_style()

    def leaveEvent(self, e):
        self._hovered = False
        self._update_style()


class PillButton(QPushButton):
    """Toggle pill button for period selector."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setFixedHeight(28)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()
        self.toggled.connect(lambda _: self._update_style())

    def _update_style(self):
        if self.isChecked():
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {C_ACCENT};
                    color: #000;
                    border: none;
                    border-radius: 14px;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 0 14px;
                    letter-spacing: 0.3px;
                }}
                QPushButton:hover {{ background: {C_ACCENT}dd; }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {C_BG_HOVER};
                    color: {C_TEXT_SEC};
                    border: 1px solid {C_BORDER};
                    border-radius: 14px;
                    font-size: 11px;
                    font-weight: 500;
                    padding: 0 14px;
                }}
                QPushButton:hover {{
                    background: {C_BG_HOVER};
                    color: {C_TEXT_PRI};
                    border-color: {C_ACCENT}88;
                }}
            """)


class FilterInput(QWidget):
    """Labelled input field with clear button."""
    value_changed = Signal(str)

    def __init__(self, label: str, placeholder: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px; "
                          f"font-weight: bold; letter-spacing: 0.8px;")
        lay.addWidget(lbl)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFixedHeight(32)
        self.edit.setStyleSheet(f"""
            QLineEdit {{
                background: {C_BG_DEEP};
                color: {C_TEXT_PRI};
                border: 1px solid {C_BORDER};
                border-radius: 6px;
                padding: 0 10px;
                font-size: 12px;
                selection-background-color: {C_ACCENT}55;
            }}
            QLineEdit:focus {{
                border-color: {C_ACCENT}88;
            }}
        """)
        self.edit.textChanged.connect(self.value_changed)

        self.clear_btn = QPushButton("✕")
        self.clear_btn.setFixedSize(28, 28)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setToolTip("Clear")
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_TEXT_DIM};
                border: none;
                border-radius: 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {C_BG_HOVER};
                color: {C_TEXT_SEC};
            }}
        """)
        self.clear_btn.clicked.connect(lambda: self.set_value(""))

        row.addWidget(self.edit)
        row.addWidget(self.clear_btn)
        lay.addLayout(row)

    def value(self) -> str:
        return self.edit.text().strip()

    def set_value(self, v: str):
        self.edit.setText(v)


class OutputHighlighter(QSyntaxHighlighter):
    """Basic syntax highlighting for hledger output."""

    def __init__(self, doc):
        super().__init__(doc)
        # Amount positive
        self._green = QTextCharFormat()
        self._green.setForeground(QColor(C_ACCENT2))

        # Amount negative / expenses
        self._red = QTextCharFormat()
        self._red.setForeground(QColor(C_ACCENT3))

        # Totals / separators
        self._dim = QTextCharFormat()
        self._dim.setForeground(QColor(C_TEXT_DIM))

        # Account names (indented)
        self._blue = QTextCharFormat()
        self._blue.setForeground(QColor(C_ACCENT))

        # Dates (YYYY-MM-DD or YYYY/MM/DD)
        self._date = QTextCharFormat()
        self._date.setForeground(QColor(C_ACCENT5))

    def highlightBlock(self, text: str):
        import re

        # Separator lines
        if re.match(r'^\s*[-=]+\s*$', text):
            self.setFormat(0, len(text), self._dim)
            return

        # Dates
        for m in re.finditer(r'\b\d{4}[-/]\d{2}[-/]\d{2}\b', text):
            self.setFormat(m.start(), m.end() - m.start(), self._date)

        # Positive amounts (no leading minus, contains digit + currency symbol)
        for m in re.finditer(r'(?<!\-)\b[\$£€₹¥₩]?\s*[\d,]+\.?\d*\b', text):
            # skip dates already formatted
            chunk = text[m.start():m.end()]
            if re.search(r'[.$£€₹¥₩]', chunk) or (m.start() > 0 and text[m.start()-1] not in '-'):
                self.setFormat(m.start(), m.end() - m.start(), self._green)

        # Negative amounts
        for m in re.finditer(r'-\s*[\$£€₹¥₩]?\s*[\d,]+\.?\d*', text):
            self.setFormat(m.start(), m.end() - m.start(), self._red)

        # Account paths (contain colon)
        for m in re.finditer(r'\b[\w]+(?::[\w]+)+\b', text):
            self.setFormat(m.start(), m.end() - m.start(), self._blue)


# ─────────────────────────────────────────────────────────────────────────────
# Add Transaction Dialog
# ─────────────────────────────────────────────────────────────────────────────

COMMON_CURRENCIES = ["$", "£", "€", "¥", "₹", "₩", "CHF", "CAD", "AUD"]

def _field_style(accent=C_ACCENT) -> str:
    return f"""
        QLineEdit, QComboBox {{
            background: {C_BG_DEEP};
            color: {C_TEXT_PRI};
            border: 1px solid {C_BORDER};
            border-radius: 7px;
            padding: 0 10px;
            font-size: 13px;
            selection-background-color: {accent}55;
            min-height: 34px;
        }}
        QLineEdit:focus, QComboBox:focus {{
            border-color: {accent}99;
            background: {C_BG_PANEL};
        }}
        QLineEdit:hover, QComboBox:hover {{
            border-color: {C_BORDER};
            background: {C_BG_CARD};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox::down-arrow {{
            width: 10px;
            height: 10px;
        }}
        QComboBox QAbstractItemView {{
            background: {C_BG_CARD};
            color: {C_TEXT_PRI};
            border: 1px solid {C_BORDER};
            selection-background-color: {accent}33;
            outline: none;
        }}
    """


class PostingRow(QWidget):
    """One posting row: [account QCompleter] [currency] [amount] [remove]"""
    remove_requested = Signal(object)
    changed          = Signal()

    def __init__(self, accounts: List[str], index: int, parent=None):
        super().__init__(parent)
        self._index = index
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)

        # Row number badge
        self._num = QLabel(str(index + 1))
        self._num.setFixedSize(22, 22)
        self._num.setAlignment(Qt.AlignCenter)
        self._num.setStyleSheet(f"""
            QLabel {{
                color: {C_TEXT_DIM};
                background: {C_BG_HOVER};
                border-radius: 11px;
                font-size: 10px;
                font-weight: bold;
            }}
        """)
        lay.addWidget(self._num)

        # Account field with QCompleter
        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("account name  (e.g. expenses:food)")
        self.account_edit.setMinimumHeight(36)
        self.account_edit.setStyleSheet(_field_style(C_ACCENT))
        self.account_edit.textChanged.connect(lambda _: self.changed.emit())
        self._set_completer(accounts)
        lay.addWidget(self.account_edit, 4)

        # Currency combo
        self.currency_combo = QComboBox()
        self.currency_combo.setFixedWidth(72)
        self.currency_combo.setFixedHeight(36)
        self.currency_combo.addItems(COMMON_CURRENCIES)
        self.currency_combo.setStyleSheet(_field_style(C_ACCENT))
        self.currency_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        lay.addWidget(self.currency_combo)

        # Amount field
        self.amount_edit = QLineEdit()
        self.amount_edit.setPlaceholderText("amount  (blank = auto-balance)")
        self.amount_edit.setMinimumHeight(36)
        self.amount_edit.setFixedWidth(168)
        self.amount_edit.setStyleSheet(_field_style(C_ACCENT2))
        self.amount_edit.textChanged.connect(lambda _: self.changed.emit())
        lay.addWidget(self.amount_edit)

        # Remove button
        self._rm = QPushButton("✕")
        self._rm.setFixedSize(30, 30)
        self._rm.setCursor(Qt.PointingHandCursor)
        self._rm.setToolTip("Remove this posting")
        self._rm.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_TEXT_DIM};
                border: 1px solid transparent;
                border-radius: 6px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {C_ACCENT3}22;
                color: {C_ACCENT3};
                border-color: {C_ACCENT3}44;
            }}
        """)
        self._rm.clicked.connect(lambda: self.remove_requested.emit(self))
        lay.addWidget(self._rm)

    def _set_completer(self, accounts: List[str]):
        model = QStringListModel(accounts)
        self.comp = QCompleter(accounts, self.account_edit)
        self.comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.comp.setFilterMode(Qt.MatchContains)
        self.comp.setCompletionMode(QCompleter.PopupCompletion)
        self.comp.popup().setWindowFlag(Qt.Popup)
        self.comp.setMaxVisibleItems(12)
        self.comp.popup().setStyleSheet(f"""
            QAbstractItemView {{
                background: {C_BG_CARD};
                color: {C_TEXT_PRI};
                border: 1px solid {C_ACCENT}55;
                border-radius: 6px;
                selection-background-color: {C_ACCENT}33;
                selection-color: {C_TEXT_PRI};
                font-size: 13px;
                padding: 4px;
                outline: none;
            }}
            QAbstractItemView::item {{
                padding: 4px 8px;
                border-radius: 4px;
            }}
            QAbstractItemView::item:hover {{
                background: {C_ACCENT}22;
            }}
        """)
        self.account_edit.setCompleter(self.comp)
        self.comp.complete(self.account_edit.cursorRect())

    def update_accounts(self, accounts: List[str]):
        self._set_completer(accounts)

    def update_index(self, index: int):
        self._index = index
        self._num.setText(str(index + 1))

    def to_posting(self) -> Posting:
        return Posting(
            account=self.account_edit.text().strip(),
            amount=self.amount_edit.text().strip(),
            currency=self.currency_combo.currentText(),
        )


class AddTransactionDialog(QDialog):
    transaction_saved = Signal()

    def __init__(self, parent=None, accounts: Optional[List[str]] = None):
        super().__init__(parent)
        self._accounts = accounts or []
        self._posting_rows: List[PostingRow] = []
        self._journal_path = get_journal_file()

        self.setWindowTitle("Add Transaction")
        self.setMinimumSize(720, 680)
        self.resize(820, 740)
        self.setModal(True)
        self.setStyleSheet(f"""
            QDialog {{
                background: {C_BG_DEEP};
            }}
            QLabel {{
                color: {C_TEXT_PRI};
                background: transparent;
            }}
        """)

        self._build_ui()
        self._prefill_date()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header bar
        header = QWidget()
        header.setFixedHeight(60)
        header.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(24, 0, 24, 0)

        icon = QLabel("✦")
        icon.setStyleSheet(f"color: {C_ACCENT2}; font-size: 18px;")
        title = QLabel("New Transaction")
        title.setStyleSheet(f"""
            color: {C_TEXT_PRI};
            font-size: 17px;
            font-weight: bold;
            letter-spacing: -0.3px;
        """)
        h_lay.addWidget(icon)
        h_lay.addSpacing(10)
        h_lay.addWidget(title, 1)

        # Journal file indicator
        if self._journal_path:
            jlbl = QLabel(f"→  {self._journal_path}")
            jlbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 11px;")
        else:
            jlbl = QLabel("⚠  No journal file found")
            jlbl.setStyleSheet(f"color: {C_ACCENT3}; font-size: 11px;")
        h_lay.addWidget(jlbl)

        outer.addWidget(header)

        # ── Scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {C_BG_DEEP}; border: none; }}
        """)

        body = QWidget()
        body.setStyleSheet(f"background: {C_BG_DEEP};")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(28, 24, 28, 16)
        body_lay.setSpacing(20)

        body_lay.addWidget(self._build_meta_section())
        
        body_lay.addWidget(self._build_preview_section())
        body_lay.addWidget(self._build_postings_section())
        
        body_lay.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ── Footer bar
        outer.addWidget(self._build_footer())

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"""
            color: {C_TEXT_DIM};
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 1.4px;
            margin-bottom: 2px;
        """)
        return lbl

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"""
            color: {C_TEXT_SEC};
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.3px;
            margin-bottom: 2px;
        """)
        return lbl

    def _build_meta_section(self) -> QWidget:
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 20)
        lay.setSpacing(14)

        lay.addWidget(self._section_label("TRANSACTION DETAILS"))

        # Row 1: Date + Status
        row1 = QHBoxLayout()
        row1.setSpacing(16)

        date_col = QVBoxLayout()
        date_col.setSpacing(4)
        date_col.addWidget(self._field_label("Date *"))
        self._date_edit = QLineEdit()
        self._date_edit.setPlaceholderText("YYYY-MM-DD")
        self._date_edit.setFixedHeight(36)
        self._date_edit.setStyleSheet(_field_style(C_ACCENT))
        self._date_edit.textChanged.connect(self._update_preview)
        date_col.addWidget(self._date_edit)
        row1.addLayout(date_col, 2)

        status_col = QVBoxLayout()
        status_col.setSpacing(4)
        status_col.addWidget(self._field_label("Status"))
        self._status_combo = QComboBox()
        self._status_combo.setFixedHeight(36)
        self._status_combo.addItems(["Unmarked", "* Cleared", "! Pending"])
        self._status_combo.setStyleSheet(_field_style(C_ACCENT))
        self._status_combo.currentIndexChanged.connect(self._update_preview)
        status_col.addWidget(self._status_combo)
        row1.addLayout(status_col, 1)

        lay.addLayout(row1)

        # Row 2: Description
        desc_col = QVBoxLayout()
        desc_col.setSpacing(4)
        desc_col.addWidget(self._field_label("Description *"))
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("What was this transaction for?")
        self._desc_edit.setFixedHeight(36)
        self._desc_edit.setStyleSheet(_field_style(C_ACCENT))
        self._desc_edit.textChanged.connect(self._update_preview)
        desc_col.addWidget(self._desc_edit)
        lay.addLayout(desc_col)

        # Row 3: Payee + Note
        row3 = QHBoxLayout()
        row3.setSpacing(16)

        payee_col = QVBoxLayout()
        payee_col.setSpacing(4)
        payee_col.addWidget(self._field_label("Payee"))
        self._payee_edit = QLineEdit()
        self._payee_edit.setPlaceholderText("Optional payee name")
        self._payee_edit.setFixedHeight(36)
        self._payee_edit.setStyleSheet(_field_style(C_ACCENT))
        self._payee_edit.textChanged.connect(self._update_preview)
        payee_col.addWidget(self._payee_edit)
        row3.addLayout(payee_col, 1)

        note_col = QVBoxLayout()
        note_col.setSpacing(4)
        note_col.addWidget(self._field_label("Note / Comment"))
        self._note_edit = QLineEdit()
        self._note_edit.setPlaceholderText("; comment appended to header")
        self._note_edit.setFixedHeight(36)
        self._note_edit.setStyleSheet(_field_style(C_TEXT_DIM))
        self._note_edit.textChanged.connect(self._update_preview)
        note_col.addWidget(self._note_edit)
        row3.addLayout(note_col, 1)

        lay.addLayout(row3)

        # Tags
        tags_col = QVBoxLayout()
        tags_col.setSpacing(4)
        tags_col.addWidget(self._field_label("Tags"))
        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("tag1:value1, tag2:value2")
        self._tags_edit.setFixedHeight(36)
        self._tags_edit.setStyleSheet(_field_style(C_TEXT_DIM))
        self._tags_edit.textChanged.connect(self._update_preview)
        tags_col.addWidget(self._tags_edit)
        lay.addLayout(tags_col)

        return card

    def _build_postings_section(self) -> QWidget:
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 20)
        lay.setSpacing(12)

        # Header row
        hdr = QHBoxLayout()
        hdr.addWidget(self._section_label("POSTINGS"))
        hdr.addStretch()
        add_btn = QPushButton("+ Add Posting")
        add_btn.setFixedHeight(28)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_ACCENT}22;
                color: {C_ACCENT};
                border: 1px solid {C_ACCENT}44;
                border-radius: 7px;
                font-size: 11px;
                font-weight: bold;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background: {C_ACCENT}44;
                border-color: {C_ACCENT}88;
            }}
        """)
        add_btn.clicked.connect(self._add_posting_row)
        hdr.addWidget(add_btn)
        lay.addLayout(hdr)

        # Column labels
        col_hdr = QHBoxLayout()
        col_hdr.setContentsMargins(30, 0, 38, 0)
        col_hdr.setSpacing(8)
        for txt, stretch in [("Account", 4), ("Curr.", 0), ("Amount", 0)]:
            l = QLabel(txt)
            l.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px; "
                            f"font-weight: bold; letter-spacing: 0.8px;")
            if stretch:
                col_hdr.addWidget(l, stretch)
            else:
                col_hdr.addWidget(l)
        col_hdr.addStretch(1)
        lay.addLayout(col_hdr)

        # Posting rows container
        self._postings_container = QWidget()
        self._postings_container.setStyleSheet("background: transparent;")
        self._postings_layout = QVBoxLayout(self._postings_container)
        self._postings_layout.setContentsMargins(0, 0, 0, 0)
        self._postings_layout.setSpacing(6)
        lay.addWidget(self._postings_container)

        # Add 2 default rows
        self._add_posting_row()
        self._add_posting_row()

        # Balance hint
        self._balance_hint = QLabel("")
        self._balance_hint.setStyleSheet(f"font-size: 11px; color: {C_TEXT_DIM}; "
                                          f"margin-top: 4px;")
        lay.addWidget(self._balance_hint)

        return card

    def _build_preview_section(self) -> QWidget:
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 20)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addWidget(self._section_label("JOURNAL PREVIEW"))
        hdr.addStretch()
        copy_btn = QPushButton("Copy")
        copy_btn.setFixedHeight(24)
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_TEXT_DIM};
                border: 1px solid {C_BORDER};
                border-radius: 5px;
                font-size: 10px;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                color: {C_TEXT_PRI};
                border-color: {C_ACCENT}66;
            }}
        """)
        copy_btn.clicked.connect(self._copy_preview)
        hdr.addWidget(copy_btn)
        lay.addLayout(hdr)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setFixedHeight(140)
        self._preview.setLineWrapMode(QTextEdit.NoWrap)
        font = QFont("JetBrains Mono", 12)
        font.setStyleHint(QFont.Monospace)
        self._preview.setFont(font)
        self._preview.setStyleSheet(f"""
            QTextEdit {{
                background: {C_BG_PANEL};
                color: {C_ACCENT2};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                padding: 12px 14px;
                font-size: 12px;
                line-height: 1.6;
            }}
        """)
        lay.addWidget(self._preview)
        return card

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(64)
        footer.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-top: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(12)

        self._error_lbl = QLabel("")
        self._error_lbl.setStyleSheet(f"color: {C_ACCENT3}; font-size: 12px;")
        self._error_lbl.setWordWrap(True)
        lay.addWidget(self._error_lbl, 1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(100, 38)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_TEXT_SEC};
                border: 1px solid {C_BORDER};
                border-radius: 9px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {C_BG_HOVER};
                color: {C_TEXT_PRI};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)

        self._save_btn = QPushButton("  ✦  Save Transaction")
        self._save_btn.setFixedHeight(38)
        self._save_btn.setMinimumWidth(190)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_ACCENT2}, stop:1 #2ea043);
                color: #000;
                border: none;
                border-radius: 9px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 20px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_ACCENT2}dd, stop:1 #2ea043dd);
            }}
            QPushButton:pressed {{
                background: #2ea043;
            }}
            QPushButton:disabled {{
                background: {C_BG_HOVER};
                color: {C_TEXT_DIM};
            }}
        """)
        self._save_btn.clicked.connect(self._on_save)

        lay.addWidget(cancel_btn)
        lay.addWidget(self._save_btn)
        return footer

    def _card(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_CARD};
                border: 1px solid {C_BORDER};
                border-radius: 10px;
            }}
        """)
        return w

    # ── posting row management ────────────────────────────────────────────────

    def _add_posting_row(self):
        idx = len(self._posting_rows)
        row = PostingRow(self._accounts, idx)
        row.remove_requested.connect(self._remove_posting_row)
        row.changed.connect(self._update_preview)
        self._posting_rows.append(row)
        self._postings_layout.addWidget(row)
        #self._update_preview()
        
    def _remove_posting_row(self, row: "PostingRow"):
        if len(self._posting_rows) <= 2:
            return   # keep minimum 2
        self._posting_rows.remove(row)
        self._postings_layout.removeWidget(row)
        row.deleteLater()
        for i, r in enumerate(self._posting_rows):
            r.update_index(i)
        self._update_preview()

    def update_accounts(self, accounts: List[str]):
        self._accounts = accounts
        for row in self._posting_rows:
            row.update_accounts(accounts)

    # ── logic ─────────────────────────────────────────────────────────────────

    def _prefill_date(self):
        self._date_edit.setText(date_type.today().isoformat())

    def _build_transaction(self) -> Transaction:
        status_map = {"Unmarked": "", "* Cleared": "*", "! Pending": "!"}
        return Transaction(
            date=self._date_edit.text().strip(),
            description=self._desc_edit.text().strip(),
            payee=self._payee_edit.text().strip(),
            note=self._note_edit.text().strip(),
            status=status_map.get(self._status_combo.currentText(), ""),
            tags=self._tags_edit.text().strip(),
            postings=[r.to_posting() for r in self._posting_rows],
        )

    def _update_preview(self):
        txn = self._build_transaction()
        self._preview.setPlainText(txn.to_journal_entry())
        self._update_balance_hint(txn)

    def _update_balance_hint(self, txn: Transaction):
        """Try to show a balance check."""
        total = 0.0
        has_blank = False
        ok = True
        for p in txn.postings:
            if not p.account:
                continue
            if not p.amount:
                has_blank = True
                continue
            amt_str = re.sub(r'[^\d.\-]', '', p.amount)
            try:
                total += float(amt_str)
            except ValueError:
                ok = False
                break
        if not ok:
            self._balance_hint.setText("")
        elif has_blank:
            self._balance_hint.setText(
                f"  Auto-balance posting will receive: "
                f"{'-' if total > 0 else ''}{abs(total):.2f}")
            self._balance_hint.setStyleSheet(
                f"font-size: 11px; color: {C_ACCENT5};")
        elif abs(total) < 0.005:
            self._balance_hint.setText("  ✓ Balanced")
            self._balance_hint.setStyleSheet(
                f"font-size: 11px; color: {C_ACCENT2};")
        else:
            self._balance_hint.setText(
                f"  ⚠ Out of balance by {total:+.2f} — add a blank amount or fix amounts")
            self._balance_hint.setStyleSheet(
                f"font-size: 11px; color: {C_ACCENT3};")

    def _copy_preview(self):
        QApplication.clipboard().setText(self._preview.toPlainText())

    def _on_save(self):
        txn = self._build_transaction()
        errors = txn.validate()
        if errors:
            self._error_lbl.setText("  ".join(errors))
            return
        self._error_lbl.setText("")

        if not self._journal_path:
            # Ask user to choose / create one
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Choose journal file to save to",
                os.path.expanduser("~/.hledger.journal"),
                "Journal files (*.journal *.ledger *.hledger);;All files (*)",
            )
            if not path:
                return
            self._journal_path = path

        ok, msg = append_transaction(txn, self._journal_path)
        if ok:
            self.transaction_saved.emit()
            self.accept()
        else:
            self._error_lbl.setText(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Historical Plot — data worker
# ─────────────────────────────────────────────────────────────────────────────

PLOT_SERIES_COLORS = [
    "#58a6ff",  # blue
    "#3fb950",  # green
    "#f78166",  # red-orange
    "#d2a8ff",  # purple
    "#ffa657",  # amber
    "#79c0ff",  # light blue
    "#56d364",  # light green
    "#ff7b72",  # salmon
]

class PlotDataWorker(QThread):
    """
    Runs hledger balance with periodic + csv output to get time-series data.
    Emits: finished(labels, series)
      labels: List[str]              — period labels  e.g. ["2024-01", ...]
      series: Dict[str, List[float]] — account -> list of values per period
    """
    finished = Signal(list, dict)
    error    = Signal(str)

    def __init__(self, account_filter: str, period: str,
                 start: str, end: str, negate: bool = False):
        super().__init__()
        self.account_filter = account_filter.strip()
        self.period         = period        # "Monthly" | "Quarterly" | "Yearly" | "Weekly"
        self.start_date          = start.strip()
        self.end_date            = end.strip()
        self.negate         = negate

    def run(self):
        if not shutil.which("hledger"):
            self.error.emit("hledger not found in PATH.")
            return

        period_flag = {
            "Weekly":    "-W",
            "Monthly":   "-M",
            "Quarterly": "-Q",
            "Yearly":    "-Y",
        }.get(self.period, "-M")

        cmd = ["hledger", "balance", "--no-total", "-O", "csv", period_flag]
        if self.account_filter:
            cmd.append(self.account_filter)
        if self.start_date:
            cmd += ["-b", self.start_date]
        if self.end_date:
            cmd += ["-e", self.end_date]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            self.error.emit("hledger timed out after 30 s.")
            return
        except Exception as e:
            self.error.emit(str(e))
            return

        if result.returncode != 0:
            self.error.emit(result.stderr.strip() or "hledger returned an error.")
            return

        labels, series = self._parse_csv(result.stdout)
        self.finished.emit(labels, series)

    @staticmethod
    def _parse_csv(raw: str) -> Tuple[List[str], Dict[str, List[float]]]:
        """
        hledger balance -O csv --monthly produces:
          account,2024-01,2024-02,...
          expenses:food,100,120,...
          ...
        """
        reader = csv.reader(io.StringIO(raw))
        rows   = [r for r in reader if r]
        if not rows:
            return [], {}

        header = rows[0]
        # First column is "account", rest are period labels
        labels = [h.strip() for h in header[1:]]

        series: Dict[str, List[float]] = {}
        for row in rows[1:]:
            if not row:
                continue
            acct = row[0].strip()
            if not acct or acct.lower() in ("total", ""):
                continue
            vals = []
            for cell in row[1:]:
                # Strip currency symbols and parse
                num = re.sub(r'[^\d.\-]', '', cell.strip())
                try:
                    vals.append(float(num) if num else 0.0)
                except ValueError:
                    vals.append(0.0)
            if len(vals) == len(labels):
                series[acct] = vals

        return labels, series


# ─────────────────────────────────────────────────────────────────────────────
# ChartWidget — pure QPainter line + bar chart
# ─────────────────────────────────────────────────────────────────────────────

class ChartWidget(QWidget):
    """
    Renders a multi-series line chart or bar chart.
    Supports hover tooltip showing values for all series at the hovered period.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels:  List[str]              = []
        self._series:  Dict[str, List[float]] = {}
        self._mode:    str                    = "line"   # "line" | "bar"
        self._hover_x: int                    = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(340)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # margins
        self._ml = 72   # left  (y-axis labels)
        self._mr = 24   # right
        self._mt = 28   # top   (legend)
        self._mb = 52   # bottom (x-axis labels)

    def set_data(self, labels: List[str], series: Dict[str, List[float]]):
        self._labels = labels
        self._series = series
        self._hover_x = -1
        self.update()

    def set_mode(self, mode: str):
        self._mode = mode
        self.update()

    # ── geometry helpers ──────────────────────────────────────────────────────

    def _chart_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        return QRectF(self._ml, self._mt,
                      w - self._ml - self._mr,
                      h - self._mt - self._mb)

    def _all_values(self) -> List[float]:
        vals = []
        for v in self._series.values():
            vals.extend(v)
        return vals

    def _y_range(self) -> Tuple[float, float]:
        vals = self._all_values()
        if not vals:
            return 0.0, 1.0
        lo, hi = min(vals), max(vals)
        if lo == hi:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.12
        return lo - pad, hi + pad

    def _x_for(self, idx: int, cr: QRectF) -> float:
        n = max(1, len(self._labels))
        return cr.left() + (idx + 0.5) * (cr.width() / n)

    def _y_for(self, val: float, cr: QRectF, lo: float, hi: float) -> float:
        frac = (val - lo) / (hi - lo) if hi != lo else 0.5
        return cr.bottom() - frac * cr.height()

    def _col(self, i: int) -> QColor:
        return QColor(PLOT_SERIES_COLORS[i % len(PLOT_SERIES_COLORS)])

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        self._draw_background(p)

        if not self._labels or not self._series:
            self._draw_empty(p)
            p.end()
            return

        cr  = self._chart_rect()
        lo, hi = self._y_range()

        self._draw_grid(p, cr, lo, hi)
        self._draw_axes(p, cr, lo, hi)

        if self._mode == "bar":
            self._draw_bars(p, cr, lo, hi)
        else:
            self._draw_lines(p, cr, lo, hi)

        self._draw_x_labels(p, cr)
        self._draw_legend(p)

        if self._hover_x >= 0:
            self._draw_tooltip(p, cr, lo, hi)

        p.end()

    def _draw_background(self, p: QPainter):
        p.fillRect(self.rect(), QColor(C_BG_DEEP))

    def _draw_empty(self, p: QPainter):
        p.setPen(QColor(C_TEXT_DIM))
        p.setFont(QFont("sans-serif", 13))
        p.drawText(self.rect(), Qt.AlignCenter,
                   "No data — run a query to see the chart")

    def _draw_grid(self, p: QPainter, cr: QRectF, lo: float, hi: float):
        pen = QPen(QColor(C_BORDER))
        pen.setWidth(1)
        pen.setStyle(Qt.DotLine)
        p.setPen(pen)

        n_lines = 6
        for i in range(n_lines + 1):
            frac = i / n_lines
            y    = cr.bottom() - frac * cr.height()
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))

    def _draw_axes(self, p: QPainter, cr: QRectF, lo: float, hi: float):
        # Y-axis line
        pen = QPen(QColor(C_BORDER))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawLine(QPointF(cr.left(), cr.top()),
                   QPointF(cr.left(), cr.bottom()))
        # X-axis line
        zero_y = self._y_for(0, cr, lo, hi)
        if cr.top() <= zero_y <= cr.bottom():
            zero_pen = QPen(QColor(C_TEXT_DIM))
            zero_pen.setWidth(1)
            p.setPen(zero_pen)
            p.drawLine(QPointF(cr.left(), zero_y), QPointF(cr.right(), zero_y))

        # Y labels
        font = QFont("JetBrains Mono", 9)
        p.setFont(font)
        n_lines = 6
        for i in range(n_lines + 1):
            frac = i / n_lines
            val  = lo + frac * (hi - lo)
            y    = cr.bottom() - frac * cr.height()
            label = self._fmt_val(val)
            p.setPen(QColor(C_TEXT_DIM))
            p.drawText(QRectF(0, y - 10, cr.left() - 6, 20),
                       Qt.AlignRight | Qt.AlignVCenter, label)

    def _draw_x_labels(self, p: QPainter, cr: QRectF):
        if not self._labels:
            return
        n      = len(self._labels)
        font   = QFont("JetBrains Mono", 9)
        p.setFont(font)
        p.setPen(QColor(C_TEXT_DIM))

        # Show at most ~12 labels to avoid crowding
        step = max(1, n // 12)
        for i, lbl in enumerate(self._labels):
            if i % step != 0 and i != n - 1:
                continue
            x = self._x_for(i, cr)
            rect = QRectF(x - 40, cr.bottom() + 6, 80, 20)
            p.drawText(rect, Qt.AlignCenter, lbl[:10])

    def _draw_legend(self, p: QPainter):
        if not self._series:
            return
        font = QFont("JetBrains Mono", 10)
        p.setFont(font)
        x = self._ml
        y = 8
        for i, name in enumerate(self._series.keys()):
            col = self._col(i)
            p.fillRect(QRectF(x, y + 2, 14, 10), col)
            p.setPen(QColor(C_TEXT_SEC))
            p.drawText(QRectF(x + 18, y - 1, 200, 16), Qt.AlignLeft, name[:30])
            x += min(220, 14 + 8 + len(name) * 7 + 16)
            if x > self.width() - 80:
                x  = self._ml
                y += 18

    def _draw_lines(self, p: QPainter, cr: QRectF, lo: float, hi: float):
        n = len(self._labels)
        if n < 1:
            return

        for si, (name, vals) in enumerate(self._series.items()):
            col = self._col(si)

            # Filled area under curve
            path = QPainterPath()
            pts  = [QPointF(self._x_for(i, cr), self._y_for(v, cr, lo, hi))
                    for i, v in enumerate(vals)]
            if pts:
                path.moveTo(pts[0].x(), cr.bottom())
                for pt in pts:
                    path.lineTo(pt)
                path.lineTo(pts[-1].x(), cr.bottom())
                path.closeSubpath()
                grad = QLinearGradient(0, cr.top(), 0, cr.bottom())
                grad.setColorAt(0, QColor(col.red(), col.green(), col.blue(), 55))
                grad.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 5))
                p.fillPath(path, QBrush(grad))

            # Line
            pen = QPen(col, 2.0)
            p.setPen(pen)
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])

            # Dots
            p.setBrush(col)
            dot_r = 3.5 if n <= 24 else 2.0
            for pt in pts:
                p.drawEllipse(pt, dot_r, dot_r)

            # Hover dot highlight
            if 0 <= self._hover_x < len(pts):
                p.setBrush(QColor(C_BG_DEEP))
                pen2 = QPen(col, 2.5)
                p.setPen(pen2)
                p.drawEllipse(pts[self._hover_x], dot_r + 2.5, dot_r + 2.5)

        p.setBrush(Qt.NoBrush)

    def _draw_bars(self, p: QPainter, cr: QRectF, lo: float, hi: float):
        n      = len(self._labels)
        ns     = len(self._series)
        if n < 1 or ns < 1:
            return

        slot_w  = cr.width() / n
        bar_pad = slot_w * 0.1
        bar_w   = (slot_w - 2 * bar_pad) / ns
        zero_y  = self._y_for(0, cr, lo, hi)
        zero_y  = max(cr.top(), min(cr.bottom(), zero_y))

        for si, (name, vals) in enumerate(self._series.items()):
            col = self._col(si)
            for i, v in enumerate(vals):
                x  = cr.left() + i * slot_w + bar_pad + si * bar_w
                y  = self._y_for(v, cr, lo, hi)
                yt = min(y, zero_y)
                yb = max(y, zero_y)
                h  = max(1.0, yb - yt)

                is_hovered = (i == self._hover_x)
                alpha = 220 if is_hovered else 160
                fill  = QColor(col.red(), col.green(), col.blue(), alpha)
                p.fillRect(QRectF(x, yt, bar_w, h), fill)

    def _draw_tooltip(self, p: QPainter, cr: QRectF, lo: float, hi: float):
        idx = self._hover_x
        if idx < 0 or idx >= len(self._labels):
            return

        label = self._labels[idx]
        lines = [label]
        for i, (name, vals) in enumerate(self._series.items()):
            if idx < len(vals):
                lines.append(f"{name[:24]}: {self._fmt_val(vals[idx])}")

        font = QFont("JetBrains Mono", 10)
        p.setFont(font)
        fm  = p.fontMetrics()
        tw  = max(fm.horizontalAdvance(l) for l in lines) + 20
        th  = len(lines) * 18 + 12
        tx  = self._x_for(idx, cr) + 12
        ty  = cr.top() + 12
        if tx + tw > self.width() - 8:
            tx = self._x_for(idx, cr) - tw - 12

        # Background
        bg_rect = QRectF(tx - 4, ty - 4, tw + 8, th + 8)
        p.fillRect(bg_rect, QColor(C_BG_CARD))
        border_pen = QPen(QColor(C_ACCENT))
        border_pen.setWidth(1)
        p.setPen(border_pen)
        p.drawRect(bg_rect)

        # Vertical crosshair
        xc = self._x_for(idx, cr)
        cp = QPen(QColor(C_ACCENT))
        cp.setWidth(1)
        cp.setStyle(Qt.DashLine)
        p.setPen(cp)
        p.drawLine(QPointF(xc, cr.top()), QPointF(xc, cr.bottom()))

        # Text
        for j, line in enumerate(lines):
            if j == 0:
                p.setPen(QColor(C_ACCENT5))
            else:
                col = self._col(j - 1)
                p.setPen(col)
            p.drawText(QRectF(tx, ty + j * 18, tw, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, line)

    @staticmethod
    def _fmt_val(v: float) -> str:
        if abs(v) >= 1_000_000:
            return f"{v/1_000_000:.1f}M"
        if abs(v) >= 1_000:
            return f"{v/1_000:.1f}k"
        return f"{v:.2f}"

    # ── mouse tracking ────────────────────────────────────────────────────────

    def mouseMoveEvent(self, e):
        cr  = self._chart_rect()
        mx  = e.position().x()
        n   = len(self._labels)
        if n > 0 and cr.left() <= mx <= cr.right():
            slot_w    = cr.width() / n
            idx       = int((mx - cr.left()) / slot_w)
            idx       = max(0, min(n - 1, idx))
            if idx != self._hover_x:
                self._hover_x = idx
                self.update()
        else:
            if self._hover_x != -1:
                self._hover_x = -1
                self.update()

    def leaveEvent(self, _):
        if self._hover_x != -1:
            self._hover_x = -1
            self.update()


# ─────────────────────────────────────────────────────────────────────────────
# HistoricalPlotDialog
# ─────────────────────────────────────────────────────────────────────────────

class HistoricalPlotDialog(QDialog):

    def __init__(self, parent=None, accounts: Optional[List[str]] = None):
        super().__init__(parent)
        self._accounts = accounts or []
        self._worker: Optional[PlotDataWorker] = None

        self.setWindowTitle("Historical Plot")
        self.setMinimumSize(860, 600)
        self.resize(1060, 680)
        self.setModal(False)   # non-modal so user can see the main window
        self.setStyleSheet(f"""
            QDialog {{ background: {C_BG_DEEP}; }}
            QLabel  {{ color: {C_TEXT_PRI}; background: transparent; }}
        """)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_controls())
        outer.addWidget(self._build_chart_area(), 1)
        outer.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        hdr = QWidget()
        hdr.setFixedHeight(56)
        hdr.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(24, 0, 24, 0)

        icon = QLabel("◈")
        icon.setStyleSheet(f"color: {C_ACCENT}; font-size: 20px;")
        title = QLabel("Historical Plot")
        title.setStyleSheet(f"""
            color: {C_TEXT_PRI};
            font-size: 17px;
            font-weight: bold;
            letter-spacing: -0.3px;
        """)
        sub = QLabel("Time-series view of account balances")
        sub.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px;")

        lay.addWidget(icon)
        lay.addSpacing(10)
        lay.addWidget(title)
        lay.addSpacing(16)
        lay.addWidget(sub)
        lay.addStretch()
        return hdr

    def _build_controls(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(72)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_CARD};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 10, 24, 10)
        lay.setSpacing(16)

        # Account filter
        acc_col = QVBoxLayout()
        acc_col.setSpacing(3)
        acc_col.addWidget(self._lbl("ACCOUNT"))
        self._acc_edit = QLineEdit()
        self._acc_edit.setPlaceholderText("e.g. expenses  (blank = all)")
        self._acc_edit.setFixedHeight(34)
        self._acc_edit.setStyleSheet(self._input_style())
        self._set_completer(self._acc_edit)
        acc_col.addWidget(self._acc_edit)
        lay.addLayout(acc_col, 3)

        self._vsep(lay)

        # Date range
        from_col = QVBoxLayout()
        from_col.setSpacing(3)
        from_col.addWidget(self._lbl("FROM"))
        self._from_edit = QLineEdit()
        self._from_edit.setPlaceholderText("YYYY-MM-DD")
        self._from_edit.setFixedHeight(34)
        self._from_edit.setStyleSheet(self._input_style())
        from_col.addWidget(self._from_edit)
        lay.addLayout(from_col, 1)

        to_col = QVBoxLayout()
        to_col.setSpacing(3)
        to_col.addWidget(self._lbl("TO"))
        self._to_edit = QLineEdit()
        self._to_edit.setPlaceholderText("YYYY-MM-DD")
        self._to_edit.setFixedHeight(34)
        self._to_edit.setStyleSheet(self._input_style())
        to_col.addWidget(self._to_edit)
        lay.addLayout(to_col, 1)

        self._vsep(lay)

        # Period
        per_col = QVBoxLayout()
        per_col.setSpacing(3)
        per_col.addWidget(self._lbl("PERIOD"))
        self._period_combo = QComboBox()
        self._period_combo.addItems(["Monthly", "Weekly", "Quarterly", "Yearly"])
        self._period_combo.setFixedHeight(34)
        self._period_combo.setStyleSheet(self._input_style())
        per_col.addWidget(self._period_combo)
        lay.addLayout(per_col)

        self._vsep(lay)

        # Chart type toggle
        type_col = QVBoxLayout()
        type_col.setSpacing(3)
        type_col.addWidget(self._lbl("CHART"))
        type_row = QHBoxLayout()
        type_row.setSpacing(4)
        self._line_btn = PillButton("Line")
        self._line_btn.setChecked(True)
        self._bar_btn  = PillButton("Bar")
        self._line_btn.toggled.connect(self._on_chart_type)
        self._bar_btn.toggled.connect(self._on_chart_type)
        type_row.addWidget(self._line_btn)
        type_row.addWidget(self._bar_btn)
        type_col.addLayout(type_row)
        lay.addLayout(type_col)

        self._vsep(lay)

        # Run button
        run_col = QVBoxLayout()
        run_col.setSpacing(3)
        run_col.addWidget(self._lbl(" "))
        self._run_btn = QPushButton("  ▶  Plot")
        self._run_btn.setFixedHeight(34)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_ACCENT};
                color: #000;
                border: none;
                border-radius: 8px;
                font-size: 12px;
                font-weight: bold;
                padding: 0 18px;
            }}
            QPushButton:hover {{ background: {C_ACCENT}cc; }}
            QPushButton:pressed {{ background: {C_ACCENT}99; }}
            QPushButton:disabled {{
                background: {C_BG_HOVER};
                color: {C_TEXT_DIM};
            }}
        """)
        self._run_btn.clicked.connect(self._run_plot)
        run_col.addWidget(self._run_btn)
        lay.addLayout(run_col)

        return bar

    def _build_chart_area(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {C_BG_DEEP};")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(16, 12, 16, 8)
        lay.setSpacing(0)

        self._chart = ChartWidget()
        lay.addWidget(self._chart, 1)

        self._msg_lbl = QLabel("Configure the query above and click Plot to draw a chart.")
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        self._msg_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 13px;")
        lay.addWidget(self._msg_lbl)

        return container

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(36)
        footer.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-top: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(16, 0, 16, 0)

        self._series_lbl = QLabel("")
        self._series_lbl.setStyleSheet(f"font-size: 11px; color: {C_TEXT_DIM};")
        lay.addWidget(self._series_lbl, 1)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(26)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_TEXT_SEC};
                border: 1px solid {C_BORDER};
                border-radius: 6px;
                font-size: 11px;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background: {C_BG_HOVER};
                color: {C_TEXT_PRI};
            }}
        """)
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

        return footer

    # ── helpers ───────────────────────────────────────────────────────────────

    def _lbl(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px; "
                        f"font-weight: bold; letter-spacing: 0.8px;")
        return l

    def _input_style(self) -> str:
        return f"""
            QLineEdit, QComboBox {{
                background: {C_BG_DEEP};
                color: {C_TEXT_PRI};
                border: 1px solid {C_BORDER};
                border-radius: 7px;
                padding: 0 10px;
                font-size: 12px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border-color: {C_ACCENT}99;
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background: {C_BG_CARD};
                color: {C_TEXT_PRI};
                border: 1px solid {C_BORDER};
                selection-background-color: {C_ACCENT}33;
                outline: none;
            }}
        """

    def _vsep(self, lay: QHBoxLayout):
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setStyleSheet(f"color: {C_BORDER};")
        lay.addWidget(line)

    def _set_completer(self, edit: QLineEdit):
        if not self._accounts:
            return
        model = QStringListModel(self._accounts)
        comp  = QCompleter(self._accounts, edit)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        comp.popup().setWindowFlag(Qt.Popup)
        comp.setMaxVisibleItems(12)
        comp.popup().setStyleSheet(f"""
            QAbstractItemView {{
                background: {C_BG_CARD};
                color: {C_TEXT_PRI};
                border: 1px solid {C_ACCENT}55;
                border-radius: 6px;
                selection-background-color: {C_ACCENT}33;
                font-size: 12px;
                padding: 4px;
                outline: none;
            }}
            QAbstractItemView::item {{ padding: 4px 8px; }}
        """)
        edit.setCompleter(comp)
        comp.complete(edit.cursorRect())

    def update_accounts(self, accounts: List[str]):
        self._accounts = accounts
        self._set_completer(self._acc_edit)

    # ── logic ─────────────────────────────────────────────────────────────────

    def _on_chart_type(self):
        if self.sender() is self._line_btn and self._line_btn.isChecked():
            self._bar_btn.setAutoExclusive(False)
            #self._bar_btn.blockSignals(True)
            self._bar_btn.setChecked(False)
            #self._bar_btn.blockSignals(False)
            self._chart.set_mode("line")
        elif self.sender() is self._bar_btn and self._bar_btn.isChecked():
            self._bar_btn.setAutoExclusive(False)
           # self._line_btn.blockSignals(True)
            self._line_btn.setChecked(False)
           # self._line_btn.blockSignals(False)
            self._chart.set_mode("bar")

    def _run_plot(self):
        if self._worker and self._worker.isRunning():
            return

        self._run_btn.setEnabled(False)
        self._msg_lbl.setText("Running hledger…")
        self._series_lbl.setText("")

        self._worker = PlotDataWorker(
            account_filter=self._acc_edit.text(),
            period=self._period_combo.currentText(),
            start=self._from_edit.text(),
            end=self._to_edit.text(),
        )
        print(type(self._worker))
        self._worker.finished.connect(self._on_data)
        self._worker.error.connect(self._on_error)
        print(type(self._worker))
        self._worker.start()

    def _on_data(self, labels: List[str], series: Dict[str, List[float]]):
        self._run_btn.setEnabled(True)
        if not labels or not series:
            self._msg_lbl.setText("No data returned. Try a broader account filter or date range.")
            self._msg_lbl.setVisible(True)
            self._chart.set_data([], {})
            return

        self._msg_lbl.setVisible(False)
        self._chart.set_data(labels, series)
        n = len(series)
        p = len(labels)
        self._series_lbl.setText(
            f"{n} series  ·  {p} periods  ·  "
            f"{self._period_combo.currentText().lower()}"
        )

    def _on_error(self, msg: str):
        self._run_btn.setEnabled(True)
        self._msg_lbl.setText(f"⚠  {msg}")
        self._msg_lbl.setVisible(True)



class JournalHighlighter(QSyntaxHighlighter):
    """Syntax highlighting for hledger / ledger journal files."""

    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(700)
            if italic:
                f.setFontItalic(True)
            return f

        self._rules = [
            # Comment lines  ; ...
            (r'^\s*;.*$',          fmt(C_TEXT_DIM, italic=True)),
            (r'^\s*#.*$',          fmt(C_TEXT_DIM, italic=True)),
            # Directive lines  account / commodity / include / etc.
            (r'^[a-z]+\s+.*$',     fmt(C_ACCENT4)),
            # Date at start of line  YYYY-MM-DD or YYYY/MM/DD
            (r'^\d{4}[-/]\d{2}[-/]\d{2}\b', fmt(C_ACCENT5, bold=True)),
            # Status markers  * !
            (r'(?<=\s)[*!](?=\s)', fmt(C_ACCENT2, bold=True)),
            # Account names with colon hierarchy
            (r'^\s{2,}[\w:]+(?::[\w:]+)+', fmt(C_ACCENT)),
            # Positive amounts
            (r'(?<!\-)\b[\$£€₹¥₩]?\s*[\d,]+\.?\d*\b', fmt(C_ACCENT2)),
            # Negative amounts
            (r'-\s*[\$£€₹¥₩]?\s*[\d,]+\.?\d*', fmt(C_ACCENT3)),
            # Currency symbols alone
            (r'[\$£€₹¥₩]', fmt(C_ACCENT5)),
            # Inline comment  ; after posting
            (r'\s+;.*$', fmt(C_TEXT_DIM, italic=True)),
            (r'\s+#.*$', fmt(C_TEXT_DIM, italic=True)),
        ]

        import re
        self._compiled = [(re.compile(p), f) for p, f in self._rules]

    def highlightBlock(self, text: str):
        for pattern, fmt in self._compiled:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)

class JournalEditorDialog(QDialog):
    """Full-screen journal editor with syntax highlighting, search, and save."""

    saved = Signal()   # emitted after a successful write

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path        = path
        self._modified    = False
        self._search_open = False

        self.setWindowTitle(f"Journal Editor — {os.path.basename(path)}")
        self.setMinimumSize(900, 620)
        self.resize(1100, 760)
        self.setModal(False)
        self.setStyleSheet(f"""
            QDialog  {{ background: {C_BG_DEEP}; }}
            QLabel   {{ color: {C_TEXT_PRI}; background: transparent; }}
            QLineEdit {{
                background: {C_BG_DEEP};
                color: {C_TEXT_PRI};
                border: 1px solid {C_BORDER};
                border-radius: 6px;
                padding: 0 8px;
                font-size: 13px;
                selection-background-color: {C_ACCENT}44;
            }}
            QLineEdit:focus {{ border-color: {C_ACCENT}88; }}
        """)

        self._build_ui()
        self._load_file()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_toolbar())
        self._search_bar = self._build_search_bar()
        outer.addWidget(self._search_bar)
        outer.addWidget(self._build_editor(), 1)
        outer.addWidget(self._build_statusbar())

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(52)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(8)

        icon = QLabel("✎")
        icon.setStyleSheet(f"color: {C_ACCENT5}; font-size: 18px;")

        self._title_lbl = QLabel(os.path.basename(self._path))
        self._title_lbl.setStyleSheet(f"""
            color: {C_TEXT_PRI};
            font-size: 15px;
            font-weight: bold;
        """)

        self._modified_dot = QLabel("●")
        self._modified_dot.setStyleSheet(f"color: {C_ACCENT3}; font-size: 14px;")
        self._modified_dot.setVisible(False)
        self._modified_dot.setToolTip("Unsaved changes")

        lay.addWidget(icon)
        lay.addSpacing(6)
        lay.addWidget(self._title_lbl)
        lay.addWidget(self._modified_dot)
        lay.addStretch()

        def tbtn(label, tooltip, slot, color=C_TEXT_SEC):
            b = QPushButton(label)
            b.setFixedHeight(32)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tooltip)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {C_BG_HOVER};
                    color: {color};
                    border: 1px solid {C_BORDER};
                    border-radius: 7px;
                    font-size: 12px;
                    font-weight: 500;
                    padding: 0 14px;
                }}
                QPushButton:hover {{
                    background: {color}22;
                    border-color: {color}66;
                    color: {color};
                }}
                QPushButton:pressed {{ opacity: 0.8; }}
            """)
            b.clicked.connect(slot)
            return b

        lay.addWidget(tbtn("⌕  Find", "Find text  (Ctrl+F)",
                           self._toggle_search, C_ACCENT))
        lay.addSpacing(4)
        lay.addWidget(tbtn("⟲  Reload", "Discard changes and reload from disk",
                           self._reload_file, C_TEXT_SEC))
        lay.addSpacing(4)
        self._save_btn = tbtn("✓  Save", "Save  (Ctrl+S)",
                              self._save_file, C_ACCENT2)
        lay.addWidget(self._save_btn)

        return bar

    def _build_search_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_CARD};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 6, 16, 6)
        lay.setSpacing(8)

        lbl = QLabel("Find:")
        lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 11px; font-weight: bold;")
        lay.addWidget(lbl)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("search text…")
        self._search_edit.setFixedHeight(30)
        self._search_edit.setMaximumWidth(320)
        self._search_edit.textChanged.connect(self._do_search)
        self._search_edit.returnPressed.connect(self._find_next)
        lay.addWidget(self._search_edit)

        def sbtn(label, slot):
            b = QPushButton(label)
            b.setFixedSize(28, 28)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {C_BG_HOVER};
                    color: {C_TEXT_SEC};
                    border: 1px solid {C_BORDER};
                    border-radius: 5px;
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background: {C_ACCENT}22;
                    color: {C_ACCENT};
                    border-color: {C_ACCENT}55;
                }}
            """)
            b.clicked.connect(slot)
            return b

        lay.addWidget(sbtn("↑", self._find_prev))
        lay.addWidget(sbtn("↓", self._find_next))

        self._match_lbl = QLabel("")
        self._match_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self._match_lbl)
        lay.addStretch()

        close = QPushButton("✕")
        close.setFixedSize(24, 24)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_TEXT_DIM};
                border: none;
                font-size: 11px;
            }}
            QPushButton:hover {{ color: {C_ACCENT3}; }}
        """)
        close.clicked.connect(self._close_search)
        lay.addWidget(close)

        bar.setVisible(False)
        return bar

    def _build_editor(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {C_BG_DEEP};")
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Line-number gutter
        self._gutter = _LineNumberGutter(self)
        lay.addWidget(self._gutter)

        # Main editor
        self._editor = QTextEdit()
        self._editor.setAcceptRichText(False)
        self._editor.setLineWrapMode(QTextEdit.NoWrap)
        font = QFont("JetBrains Mono", 13)
        font.setStyleHint(QFont.Monospace)
        self._editor.setFont(font)
        self._editor.setStyleSheet(f"""
            QTextEdit {{
                background: {C_BG_DEEP};
                color: {C_TEXT_PRI};
                border: none;
                padding: 12px 16px;
                selection-background-color: {C_ACCENT}44;
                line-height: 1.5;
            }}
            QScrollBar:vertical {{
                background: {C_BG_PANEL};
                width: 10px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C_BORDER};
                border-radius: 5px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {C_TEXT_DIM}; }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self._editor.textChanged.connect(self._on_modified)
        self._editor.cursorPositionChanged.connect(self._update_cursor_pos)
        self._editor.verticalScrollBar().valueChanged.connect(
            self._gutter.update)

        self._highlighter = JournalHighlighter(self._editor.document())

        lay.addWidget(self._editor, 1)
        self._gutter.editor = self._editor
        return container

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(28)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-top: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(20)

        self._path_lbl = QLabel(self._path)
        self._path_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px;")
        lay.addWidget(self._path_lbl, 1)

        self._cursor_lbl = QLabel("Ln 1, Col 1")
        self._cursor_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px;")
        lay.addWidget(self._cursor_lbl)

        self._lines_lbl = QLabel("")
        self._lines_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px;")
        lay.addWidget(self._lines_lbl)

        hint = QLabel(
            f"<span style='color:{C_ACCENT};'>Ctrl+S</span>"
            f"<span style='color:{C_TEXT_DIM};'> save  </span>"
            f"<span style='color:{C_ACCENT};'>Ctrl+F</span>"
            f"<span style='color:{C_TEXT_DIM};'> find</span>")
        hint.setStyleSheet("font-size: 10px;")
        lay.addWidget(hint)

        return bar

    # ── file I/O ──────────────────────────────────────────────────────────────

    def _load_file(self):
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Cannot open file", str(e))
            return

        self._editor.blockSignals(True)
        self._editor.setPlainText(text)
        self._editor.blockSignals(False)
        self._modified = False
        self._modified_dot.setVisible(False)
        self._update_line_count()

    def _reload_file(self):
        if self._modified:
            ans = QMessageBox.question(
                self, "Discard changes?",
                "You have unsaved changes. Reload from disk and lose them?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if ans != QMessageBox.Yes:
                return
        self._load_file()

    def _save_file(self):
        text = self._editor.toPlainText()
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return

        self._modified = False
        self._modified_dot.setVisible(False)
        self.saved.emit()

        # Brief flash on the save button
        orig = self._save_btn.text()
        self._save_btn.setText("✓  Saved!")
        QTimer.singleShot(1200, lambda: self._save_btn.setText(orig))

    # ── search ────────────────────────────────────────────────────────────────

    def _toggle_search(self):
        visible = not self._search_open
        self._search_open = visible
        self._search_bar.setVisible(visible)
        if visible:
            self._search_edit.setFocus()
            self._search_edit.selectAll()
        else:
            self._clear_highlights()

    def _close_search(self):
        self._search_open = False
        self._search_bar.setVisible(False)
        self._clear_highlights()
        self._editor.setFocus()

    def _do_search(self, query: str):
        self._clear_highlights()
        if not query:
            self._match_lbl.setText("")
            return

        # Highlight all matches
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(C_ACCENT5 + "55"))
        fmt.setForeground(QColor(C_BG_DEEP))

        doc    = self._editor.document()
        cursor = QTextCursor(doc)
        count  = 0
        while True:
            cursor = doc.find(query, cursor,
                              QTextDocument.FindFlags())  # type: ignore
            if cursor.isNull():
                break
            cursor.mergeCharFormat(fmt)
            count += 1

        self._match_lbl.setText(f"{count} match{'es' if count != 1 else ''}"
                                 if count else "no matches")
        self._match_lbl.setStyleSheet(
            f"color: {C_ACCENT2 if count else C_ACCENT3}; font-size: 11px;")

        # Jump to first
        if count:
            self._find_next()

    def _find_next(self):
        q = self._search_edit.text()
        if not q:
            return
        found = self._editor.find(q)
        if not found:
            # Wrap around
            self._editor.moveCursor(QTextCursor.Start)
            self._editor.find(q)

    def _find_prev(self):
        q = self._search_edit.text()
        if not q:
            return
        from PySide6.QtGui import QTextDocument
        found = self._editor.find(q, QTextDocument.FindBackward)
        if not found:
            self._editor.moveCursor(QTextCursor.End)
            self._editor.find(q, QTextDocument.FindBackward)

    def _clear_highlights(self):
        cursor = self._editor.textCursor()
        cursor.select(QTextCursor.Document)
        fmt = QTextCharFormat()
        fmt.setBackground(Qt.transparent)
        cursor.mergeCharFormat(fmt)
        # Re-run highlighter to restore syntax colours
        self._highlighter.rehighlight()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _on_modified(self):
        if not self._modified:
            self._modified = True
            self._modified_dot.setVisible(True)
        self._update_line_count()
        self._gutter.update()

    def _update_cursor_pos(self):
        cur  = self._editor.textCursor()
        line = cur.blockNumber() + 1
        col  = cur.columnNumber() + 1
        self._cursor_lbl.setText(f"Ln {line}, Col {col}")

    def _update_line_count(self):
        n = self._editor.document().blockCount()
        self._lines_lbl.setText(f"{n} lines")

    # ── keyboard shortcuts ────────────────────────────────────────────────────

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Save):
            self._save_file()
        elif e.key() == Qt.Key_F and e.modifiers() == Qt.ControlModifier:
            self._toggle_search()
        elif e.key() == Qt.Key_Escape and self._search_open:
            self._close_search()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        if self._modified:
            ans = QMessageBox.question(
                self, "Unsaved changes",
                "Save before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if ans == QMessageBox.Save:
                self._save_file()
                e.accept()
            elif ans == QMessageBox.Discard:
                e.accept()
            else:
                e.ignore()
        else:
            e.accept()
            
class _LineNumberGutter(QWidget):
    """Narrow gutter that paints line numbers aligned with the editor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor = None   # set by _build_editor after construction
        self.setFixedWidth(52)
        self.setStyleSheet(f"background: {C_BG_PANEL};")

    def paintEvent(self, _):
        if self.editor is None:
            return

        p       = QPainter(self)
        font    = QFont("JetBrains Mono", 10)
        p.setFont(font)
        fm      = p.fontMetrics()

        doc     = self.editor.document()
        voffset = self.editor.verticalScrollBar().value()
        content_y = self.editor.contentsMargins().top()

        block      = doc.begin()
        block_top  = int(doc.documentLayout()
                         .blockBoundingRect(block).top()) - voffset + content_y

        while block.isValid() and block_top <= self.height():
            rect = doc.documentLayout().blockBoundingRect(block)
            top  = int(rect.top()) - voffset + content_y
            h    = int(rect.height())

            if top + h >= 0:
                num = str(block.blockNumber() + 1)
                p.setPen(QColor(C_TEXT_DIM))
                p.drawText(0, top, self.width() - 8, h,
                           Qt.AlignRight | Qt.AlignVCenter, num)

            block     = block.next()
            block_top = top + h

        p.end()

# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self, reports: List[Report]):
        super().__init__()
        self.reports       = reports
        self.sel           = 0
        self.filters       = Filters()
        self._worker: Optional[ReportWorker] = None
        self._accounts: List[str] = []

        self.setWindowTitle("Magpie — hledger Dashboard Client")
        self.setMinimumSize(1000, 650)
        self.resize(1280, 800)

        self._apply_global_style()
        self._build_ui()
        self._wire_shortcuts()
        self._select_report(0)
        self._fetch_accounts()

    # ── style ─────────────────────────────────────────────────────────────────

    def _apply_global_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {C_BG_DEEP};
                color: {C_TEXT_PRI};
                font-family: 'JetBrains Mono', 'Cascadia Code', 'Fira Code',
                             'SF Mono', 'Consolas', 'Menlo', monospace;
            }}
            QScrollBar:vertical {{
                background: {C_BG_DEEP};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C_BORDER};
                border-radius: 4px;
                min-height: 40px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {C_TEXT_DIM};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                background: {C_BG_DEEP};
                height: 8px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {C_BORDER};
                border-radius: 4px;
                min-width: 40px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {C_TEXT_DIM};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}
            QToolTip {{
                background: {C_BG_CARD};
                color: {C_TEXT_PRI};
                border: 1px solid {C_BORDER};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }}
        """)

    # ── layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_lay = QHBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        root_lay.addWidget(self._build_sidebar())
        root_lay.addWidget(self._build_main_area(), 1)

    # ── sidebar ───────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(230)
        sidebar.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-right: 1px solid {C_BORDER};
            }}
        """)
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── Logo / header
        logo_row = QWidget()
        logo_row.setFixedHeight(64)
        logo_row.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_DEEP};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        logo_lay = QHBoxLayout(logo_row)
        logo_lay.setContentsMargins(20, 0, 16, 0)

        magpie_icon = QLabel("🐦")
        magpie_icon.setStyleSheet("font-size: 22px;")
        title_lbl = QLabel("magpie")
        title_lbl.setStyleSheet(f"""
            color: {C_TEXT_PRI};
            font-size: 18px;
            font-weight: bold;
            letter-spacing: -0.5px;
        """)
        sub_lbl = QLabel("hledger")
        sub_lbl.setStyleSheet(f"""
            color: {C_ACCENT};
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 1px;
        """)

        txt_col = QVBoxLayout()
        txt_col.setSpacing(0)
        txt_col.addWidget(title_lbl)
        txt_col.addWidget(sub_lbl)

        logo_lay.addWidget(magpie_icon)
        logo_lay.addLayout(txt_col, 1)
        logo_lay.addStretch()

        lay.addWidget(logo_row)

        # ── Reports label
        sec_lbl = QLabel("REPORTS")
        sec_lbl.setContentsMargins(20, 18, 0, 8)
        sec_lbl.setStyleSheet(f"""
            color: {C_TEXT_DIM};
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 1.5px;
        """)
        lay.addWidget(sec_lbl)

        # ── Report items
        self._sidebar_items: List[SidebarItem] = []
        for i, r in enumerate(self.reports):
            item = SidebarItem(r)
            item.clicked.connect(lambda idx=i: self._select_report(idx))
            self._sidebar_items.append(item)
            lay.addWidget(item)

        lay.addStretch()

        # ── Add Transaction button
        add_txn_btn = QPushButton("  ✦  Add Transaction")
        add_txn_btn.setFixedHeight(40)
        add_txn_btn.setCursor(Qt.PointingHandCursor)
        add_txn_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_ACCENT2}cc, stop:1 #2ea043cc);
                color: #000;
                border: none;
                border-radius: 9px;
                font-size: 12px;
                font-weight: bold;
                margin: 0 12px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_ACCENT2}, stop:1 #2ea043);
            }}
            QPushButton:pressed {{ background: #2ea043; }}
        """)
        add_txn_btn.clicked.connect(self._open_add_transaction)
        lay.addWidget(add_txn_btn)
        lay.addSpacing(6)

        # ── Plot button
        plot_btn = QPushButton("  ◈  Historical Plot")
        plot_btn.setFixedHeight(40)
        plot_btn.setCursor(Qt.PointingHandCursor)
        plot_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_ACCENT}22;
                color: {C_ACCENT};
                border: 1px solid {C_ACCENT}44;
                border-radius: 9px;
                font-size: 12px;
                font-weight: bold;
                margin: 0 12px;
            }}
            QPushButton:hover {{
                background: {C_ACCENT}44;
                border-color: {C_ACCENT}88;
            }}
            QPushButton:pressed {{ background: {C_ACCENT}33; }}
        """)
        plot_btn.clicked.connect(self._open_plot)
        lay.addWidget(plot_btn)
        lay.addSpacing(8)
        
        edit_btn = QPushButton("  ✎  Edit Journal")
        edit_btn.setFixedHeight(40)
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_ACCENT5}22;
                color: {C_ACCENT5};
                border: 1px solid {C_ACCENT5}44;
                border-radius: 9px;
                font-size: 12px;
                font-weight: bold;
                margin: 0 12px;
            }}
            QPushButton:hover {{
                background: {C_ACCENT5}44;
                border-color: {C_ACCENT5}88;
            }}
            QPushButton:pressed {{ background: {C_ACCENT5}33; }}
        """)
        edit_btn.clicked.connect(self._open_journal_editor)
        lay.addWidget(edit_btn)
        lay.addSpacing(8)

        # ── Bottom: hledger status
        self._hledger_badge = QLabel()
        self._hledger_badge.setContentsMargins(16, 8, 16, 12)
        self._hledger_badge.setWordWrap(True)
        self._hledger_badge.setStyleSheet(f"font-size: 11px; color: {C_TEXT_DIM};")
        self._update_hledger_badge()
        lay.addWidget(self._hledger_badge)

        return sidebar

    def _update_hledger_badge(self):
        if shutil.which("hledger"):
            try:
                v = subprocess.run(["hledger", "--version"],
                                   capture_output=True, text=True, timeout=3)
                ver = v.stdout.strip().split("\n")[0]
            except Exception:
                ver = "hledger"
            self._hledger_badge.setText(f"✓ {ver}")
            self._hledger_badge.setStyleSheet(
                f"font-size: 11px; color: {C_ACCENT2}; "
                f"padding: 6px 16px 12px 16px;")
        else:
            self._hledger_badge.setText("⚠ hledger not found")
            self._hledger_badge.setStyleSheet(
                f"font-size: 11px; color: {C_ACCENT3}; "
                f"padding: 6px 16px 12px 16px;")

    # ── main area ─────────────────────────────────────────────────────────────

    def _build_main_area(self) -> QWidget:
        area = QWidget()
        area.setStyleSheet(f"background: {C_BG_DEEP};")
        lay = QVBoxLayout(area)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._build_topbar())
        lay.addWidget(self._build_filter_bar())
        lay.addWidget(self._build_content(), 1)
        lay.addWidget(self._build_status_bar())

        return area

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(64)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 0, 16, 0)
        lay.setSpacing(12)

        self._report_title = QLabel("Select a report")
        self._report_title.setStyleSheet(f"""
            color: {C_TEXT_PRI};
            font-size: 18px;
            font-weight: bold;
            letter-spacing: -0.3px;
        """)

        self._locked_badge = QLabel("LOCKED — filters disabled")
        self._locked_badge.setVisible(False)
        self._locked_badge.setStyleSheet(f"""
            QLabel {{
                color: {C_ACCENT5};
                background: {C_ACCENT5}18;
                border: 1px solid {C_ACCENT5}44;
                border-radius: 6px;
                font-size: 10px;
                font-weight: bold;
                padding: 3px 10px;
                letter-spacing: 0.5px;
            }}
        """)

        self._reload_btn = QPushButton("  ↺  Reload")
        self._reload_btn.setFixedHeight(34)
        self._reload_btn.setCursor(Qt.PointingHandCursor)
        self._reload_btn.setToolTip("Reload report  (R)")
        self._reload_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_BG_HOVER};
                color: {C_TEXT_PRI};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                font-size: 12px;
                font-weight: 500;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background: {C_ACCENT}22;
                border-color: {C_ACCENT}88;
                color: {C_ACCENT};
            }}
            QPushButton:pressed {{ background: {C_ACCENT}33; }}
        """)
        self._reload_btn.clicked.connect(self._reload)

        self._clear_btn = QPushButton("  ✕  Clear filters")
        self._clear_btn.setFixedHeight(34)
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setToolTip("Clear all filters  (X)")
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_BG_HOVER};
                color: {C_TEXT_SEC};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                font-size: 12px;
                font-weight: 500;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background: {C_ACCENT3}22;
                border-color: {C_ACCENT3}88;
                color: {C_ACCENT3};
            }}
            QPushButton:pressed {{ background: {C_ACCENT3}33; }}
        """)
        self._clear_btn.clicked.connect(self._clear_filters)

        lay.addWidget(self._report_title)
        lay.addWidget(self._locked_badge)
        lay.addStretch()
        lay.addWidget(self._reload_btn)
        lay.addWidget(self._clear_btn)

        return bar

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(80)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_CARD};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 10, 24, 10)
        lay.setSpacing(16)

        # Account filter
        self._fi_account = FilterInput("ACCOUNT", "e.g. expenses:food")
        self._fi_account.value_changed.connect(
            lambda v: self._set_filter("account", v))
        lay.addWidget(self._fi_account, 2)

        self._separator(lay)

        # Date range
        self._fi_start = FilterInput("FROM", "YYYY-MM-DD")
        self._fi_start.value_changed.connect(
            lambda v: self._set_filter("start_date", v))
        lay.addWidget(self._fi_start, 1)

        self._fi_end = FilterInput("TO", "YYYY-MM-DD")
        self._fi_end.value_changed.connect(
            lambda v: self._set_filter("end_date", v))
        lay.addWidget(self._fi_end, 1)

        self._separator(lay)

        # Period
        period_col = QVBoxLayout()
        period_col.setSpacing(4)
        per_lbl = QLabel("PERIOD")
        per_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px; "
                              f"font-weight: bold; letter-spacing: 0.8px;")
        period_col.addWidget(per_lbl)

        period_row = QHBoxLayout()
        period_row.setSpacing(4)
        self._period_btns: dict = {}
        for p in ["Weekly", "Monthly", "Quarterly", "Yearly"]:
            btn = PillButton(p[0])
            btn.setToolTip(p)
            btn.toggled.connect(lambda checked, period=p:
                                self._toggle_period(period, checked))
            self._period_btns[p] = btn
            period_row.addWidget(btn)

        period_col.addLayout(period_row)
        lay.addLayout(period_col)

        self._separator(lay)

        # Depth
        depth_col = QVBoxLayout()
        depth_col.setSpacing(4)
        dep_lbl = QLabel("DEPTH")
        dep_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px; "
                              f"font-weight: bold; letter-spacing: 0.8px;")
        depth_col.addWidget(dep_lbl)

        depth_row = QHBoxLayout()
        depth_row.setSpacing(4)

        self._depth_dec = QPushButton("−")
        self._depth_dec.setFixedSize(28, 28)
        self._depth_dec.setCursor(Qt.PointingHandCursor)
        self._depth_dec.setStyleSheet(self._stepper_style())
        self._depth_dec.clicked.connect(
            lambda: self._change_depth(-1))

        self._depth_lbl = QLabel("─")
        self._depth_lbl.setFixedWidth(26)
        self._depth_lbl.setAlignment(Qt.AlignCenter)
        self._depth_lbl.setStyleSheet(
            f"color: {C_TEXT_PRI}; font-size: 13px; font-weight: bold;")

        self._depth_inc = QPushButton("+")
        self._depth_inc.setFixedSize(28, 28)
        self._depth_inc.setCursor(Qt.PointingHandCursor)
        self._depth_inc.setStyleSheet(self._stepper_style())
        self._depth_inc.clicked.connect(
            lambda: self._change_depth(1))

        depth_row.addWidget(self._depth_dec)
        depth_row.addWidget(self._depth_lbl)
        depth_row.addWidget(self._depth_inc)

        depth_col.addLayout(depth_row)
        lay.addLayout(depth_col)

        return bar

    def _stepper_style(self) -> str:
        return f"""
            QPushButton {{
                background: {C_BG_HOVER};
                color: {C_TEXT_PRI};
                border: 1px solid {C_BORDER};
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {C_ACCENT}33;
                border-color: {C_ACCENT}88;
                color: {C_ACCENT};
            }}
        """

    def _separator(self, lay: QHBoxLayout):
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setStyleSheet(f"color: {C_BORDER};")
        lay.addWidget(line)

    def _build_content(self) -> QWidget:
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # loading overlay label
        self._loading_lbl = QLabel("Loading…")
        self._loading_lbl.setAlignment(Qt.AlignCenter)
        self._loading_lbl.setVisible(False)
        self._loading_lbl.setStyleSheet(f"""
            QLabel {{
                color: {C_TEXT_DIM};
                font-size: 14px;
                background: {C_BG_DEEP};
            }}
        """)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setLineWrapMode(QTextEdit.NoWrap)
        self._output.setStyleSheet(f"""
            QTextEdit {{
                background: {C_BG_DEEP};
                color: {C_TEXT_PRI};
                border: none;
                padding: 20px 24px;
                font-size: 13px;
                line-height: 1.6;
                selection-background-color: {C_ACCENT}44;
            }}
        """)
        font = QFont("JetBrains Mono", 13)
        font.setStyleHint(QFont.Monospace)
        self._output.setFont(font)

        self._highlighter = OutputHighlighter(self._output.document())

        lay.addWidget(self._loading_lbl)
        lay.addWidget(self._output, 1)

        return container

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(32)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {C_BG_PANEL};
                border-top: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(16)

        def hint(key, label):
            w = QLabel(f"<span style='color:{C_ACCENT};'>{key}</span>"
                       f"<span style='color:{C_TEXT_DIM};'> {label}</span>")
            w.setStyleSheet("font-size: 11px;")
            return w

        lay.addWidget(hint("↑↓", "navigate"))
        lay.addWidget(hint("N", "add transaction"))
        lay.addWidget(hint("P", "plot"))
        lay.addWidget(hint("R", "reload"))
        lay.addWidget(hint("X", "clear filters"))
        lay.addWidget(hint("?", "help"))
        lay.addWidget(hint("Ctrl+Q", "quit"))
        lay.addStretch()

        self._status_msg = QLabel("")
        self._status_msg.setStyleSheet(
            f"font-size: 11px; color: {C_TEXT_DIM};")
        lay.addWidget(self._status_msg)

        return bar

    # ── shortcuts ─────────────────────────────────────────────────────────────

    def _wire_shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(fn)

        sc("R",      self._reload)
        sc("X",      self._clear_filters)
        sc("?",      self._show_help)
        sc("N",      self._open_add_transaction)
        sc("P",      self._open_plot)
        sc("E",  self._open_journal_editor)
        sc("Ctrl+Q", self.close)
        sc("J",      lambda: self._scroll_output(1))
        sc("K",      lambda: self._scroll_output(-1))
        sc("G",      self._scroll_bottom)
        sc("Ctrl+Home", self._scroll_top)

        # Report navigation
        sc("Down",   self._next_report)
        sc("Up",     self._prev_report)

    # ── logic ─────────────────────────────────────────────────────────────────

    def _open_add_transaction(self):
        dlg = AddTransactionDialog(self, accounts=self._accounts)
        dlg.transaction_saved.connect(self._on_transaction_saved)
        dlg.exec()

    def _open_plot(self):
        if hasattr(self, "_plot_dialog") and self._plot_dialog and self._plot_dialog.isVisible():
            self._plot_dialog.raise_()
            self._plot_dialog.activateWindow()
            return
        self._plot_dialog = HistoricalPlotDialog(self, accounts=self._accounts)
        self._plot_dialog.show()

    def _on_transaction_saved(self):
        self._status_msg.setText("✦ Transaction saved — reloading…")
        self._status_msg.setStyleSheet(f"font-size: 11px; color: {C_ACCENT2};")
        QTimer.singleShot(400, self._reload)
        QTimer.singleShot(400, self._fetch_accounts)

    def _fetch_accounts(self):
        fetcher = AccountFetcher(self)
        fetcher.done.connect(self._on_accounts_fetched)
        fetcher.start()

    def _on_accounts_fetched(self, accounts: List[str]):
        self._accounts = accounts
        if hasattr(self, "_plot_dialog") and self._plot_dialog:
            self._plot_dialog.update_accounts(accounts)

    def _select_report(self, idx: int):
        self.sel = idx
        for i, item in enumerate(self._sidebar_items):
            item.set_selected(i == idx)
        report = self.reports[idx]
        self._report_title.setText(report.name)
        self._locked_badge.setVisible(report.locked)
        self._reload()

    def _next_report(self):
        self._select_report((self.sel + 1) % len(self.reports))

    def _prev_report(self):
        self._select_report((self.sel - 1) % len(self.reports))

    def _reload(self):
        if self._worker and self._worker.isRunning():
            return

        self._output.setPlainText("")
        self._loading_lbl.setVisible(True)
        self._status_msg.setText("Running…")

        report = self.reports[self.sel]
        self._worker = ReportWorker(report, self.filters)
        self._worker.finished.connect(self._on_report_done)
        self._worker.start()

    def _on_report_done(self, text: str, is_error: bool):
        self._loading_lbl.setVisible(False)
        self._output.setPlainText(text)
        if is_error:
            self._status_msg.setText("⚠ Command returned an error")
            self._status_msg.setStyleSheet(
                f"font-size: 11px; color: {C_ACCENT3};")
        else:
            lines = text.count("\n")
            self._status_msg.setText(f"{lines} lines")
            self._status_msg.setStyleSheet(
                f"font-size: 11px; color: {C_TEXT_DIM};")

    def _set_filter(self, key: str, value: str):
        setattr(self.filters, key, value.strip())
        # Debounce: reload after 500 ms of inactivity
        if not hasattr(self, "_debounce"):
            self._debounce = QTimer()
            self._debounce.setSingleShot(True)
            self._debounce.timeout.connect(self._reload)
        self._debounce.start(500)

    def _toggle_period(self, period: str, checked: bool):
        if checked:
            # Uncheck others
            for p, btn in self._period_btns.items():
          #      print(period)
                btn.setAutoExclusive(True)
                if p != period:
                    
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
            self.filters.period = period
        else:
            if self.filters.period == period:
                self.filters.period = ""
        self._reload()

    def _change_depth(self, delta: int):
        new = max(0, self.filters.depth + delta)
        self.filters.depth = new
        self._depth_lbl.setText(str(new) if new > 0 else "─")
        self._reload()

    def _clear_filters(self):
        self.filters = Filters()
        self._fi_account.set_value("")
        self._fi_start.set_value("")
        self._fi_end.set_value("")
        for btn in self._period_btns.values():
            #btn.blockSignals(True)
            btn.setAutoExclusive(False)
            btn.setChecked(False)
            btn.setAutoExclusive(True)
            #btn.blockSignals(False)
        self._depth_lbl.setText("─")
        self._reload()

    def _scroll_output(self, direction: int):
        sb = self._output.verticalScrollBar()
        sb.setValue(sb.value() + direction * 200)

    def _scroll_top(self):
        self._output.moveCursor(QTextCursor.Start)

    def _scroll_bottom(self):
        self._output.moveCursor(QTextCursor.End)

    def _show_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setMinimumWidth(520)
        dlg.setStyleSheet(f"""
            QDialog {{
                background: {C_BG_CARD};
                border: 1px solid {C_BORDER};
                border-radius: 12px;
            }}
            QLabel {{ color: {C_TEXT_PRI}; }}
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(0)

        title = QLabel("Keyboard Shortcuts")
        title.setStyleSheet(f"""
            color: {C_TEXT_PRI};
            font-size: 17px;
            font-weight: bold;
            margin-bottom: 16px;
        """)
        lay.addWidget(title)

        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(24)
        grid.setColumnMinimumWidth(0, 140)

        shortcuts = [
            ("Navigation", ""),
            ("↑ / ↓",           "Previous / Next report"),
            ("J / Page Down",   "Scroll output down"),
            ("K / Page Up",     "Scroll output up"),
            ("Ctrl+Home",       "Scroll to top"),
            ("G",               "Scroll to bottom"),
            ("", ""),
            ("Filters", ""),
            ("Account field",   "Filter by account name"),
            ("From / To",       "Set date range"),
            ("W M Q Y buttons", "Toggle period"),
            ("+ / −",           "Adjust account depth"),
            ("X",               "Clear all filters"),
            ("", ""),
            ("General", ""),
            ("N",               "Add new transaction"),
            ("P",               "Open historical plot"),
            ("R",               "Reload current report"),
            ("?",               "Show this help"),
            ("Ctrl+Q",          "Quit"),
        ]

        row = 0
        for key, desc in shortcuts:
            if not key and not desc:
                spacer = QLabel("")
                spacer.setFixedHeight(4)
                grid.addWidget(spacer, row, 0, 1, 2)
            elif not desc:
                # Section header
                hdr = QLabel(key.upper())
                hdr.setStyleSheet(f"""
                    color: {C_TEXT_DIM};
                    font-size: 10px;
                    font-weight: bold;
                    letter-spacing: 1.2px;
                    margin-top: 4px;
                """)
                grid.addWidget(hdr, row, 0, 1, 2)
            else:
                k = QLabel(key)
                k.setStyleSheet(f"""
                    background: {C_BG_HOVER};
                    color: {C_ACCENT};
                    border: 1px solid {C_BORDER};
                    border-radius: 5px;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 3px 8px;
                """)
                d = QLabel(desc)
                d.setStyleSheet(f"color: {C_TEXT_SEC}; font-size: 12px;")
                grid.addWidget(k, row, 0)
                grid.addWidget(d, row, 1)
            row += 1

        lay.addLayout(grid)
        lay.addSpacing(20)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(36)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_ACCENT};
                color: #000;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 24px;
            }}
            QPushButton:hover {{ background: {C_ACCENT}cc; }}
        """)
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn, 0, Qt.AlignRight)

        dlg.exec()
        
    def _open_journal_editor(self):
                path = get_journal_file()
                if not path:
                    from PySide6.QtWidgets import QFileDialog
                    path, _ = QFileDialog.getOpenFileName(
                        self, "Open journal file", os.path.expanduser("~"),
                        "Journal files (*.journal *.ledger *.hledger);;All files (*)",
                    )
                    if not path:
                        return
                dlg = JournalEditorDialog(path, self)
                dlg.saved.connect(self._on_journal_saved)
                dlg.show()

    def _on_journal_saved(self):
        self._status_msg.setText("✎ Journal saved — reloading…")
        self._status_msg.setStyleSheet(
            f"font-size: 11px; color: {C_ACCENT5};")
        QTimer.singleShot(300, self._reload)
        QTimer.singleShot(300, self._fetch_accounts)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Load config
    reports = DEFAULT_REPORTS
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 < len(sys.argv):
            reports = load_config(sys.argv[idx + 1])
    else:
        for cp in ["config.json",
                   os.path.expanduser("~/.config/raven/config.json"),
                   os.path.expanduser("~/.raven.json")]:
            if os.path.exists(cp):
                reports = load_config(cp)
                break

    app = QApplication(sys.argv)
    app.setApplicationName("raven")
    app.setOrganizationName("raven")

    # High-DPI
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    win = MainWindow(reports)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
