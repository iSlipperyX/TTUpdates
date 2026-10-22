"""Small reusable widgets: score ring, score bars, thumbnails, number formatting."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

from ..scoring import PART_LABELS, grade
from . import theme


def fmt_num(n: int | float | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f}{suffix}".replace(".0", "") if v < 100 else f"{v:.0f}{suffix}"
    return f"{int(n)}"


def fmt_dur(sec: float) -> str:
    sec = int(round(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


def label(text: str = "", obj: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    return lab


def panel() -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    return f


@lru_cache(maxsize=512)
def _rounded(path: str, w: int, h: int, mtime: float) -> QPixmap:
    src = QPixmap(path)
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), 8, 8)
    p.setClipPath(clip)
    p.fillRect(out.rect(), QColor(theme.PANEL_2))
    if not src.isNull():
        scaled = src.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawPixmap((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
    p.end()
    return out


def thumb_pixmap(path: str, w: int, h: int) -> QPixmap:
    import os

    try:
        mtime = os.path.getmtime(path) if path else 0.0
    except OSError:
        mtime = 0.0
    return _rounded(path or "", w, h, mtime)


class Thumb(QLabel):
    def __init__(self, w: int, h: int):
        super().__init__()
        self._w, self._h = w, h
        self.setFixedSize(w, h)
        self.set_path("")

    def set_path(self, path: str) -> None:
        self.setPixmap(thumb_pixmap(path, self._w, self._h))


class ScoreRing(QWidget):
    """Big circular score with the letter grade underneath."""

    def __init__(self, size: int = 96):
        super().__init__()
        self.setFixedSize(size, size)
        self.value: float | None = None

    def set_value(self, v: float | None) -> None:
        self.value = v
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height())
        r = QRectF(6, 6, s - 12, s - 12)
        p.setPen(QPen(QColor(theme.BORDER), 7, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 0, 360 * 16)
        col = QColor(theme.score_colour(self.value))
        if self.value is not None:
            p.setPen(QPen(col, 7, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, 90 * 16, int(-360 * 16 * max(0.0, min(100.0, self.value)) / 100))
        p.setPen(QColor(theme.TEXT))
        f = QFont(self.font())
        f.setPointSizeF(s / 5.2)
        f.setBold(True)
        p.setFont(f)
        txt = "—" if self.value is None else f"{self.value:.0f}"
        p.drawText(QRectF(0, -s * 0.06, s, s), Qt.AlignCenter, txt)
        f.setPointSizeF(s / 11)
        p.setFont(f)
        p.setPen(col)
        if self.value is not None:
            p.drawText(QRectF(0, s * 0.22, s, s), Qt.AlignCenter, f"Grade {grade(self.value)}")
        p.end()


class Bar(QWidget):
    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, v: float) -> None:
        self.value = v
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BORDER))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 4, 4)
        p.setBrush(QColor(theme.score_colour(self.value)))
        p.drawRoundedRect(QRectF(0, 0, self.width() * max(0.0, min(100.0, self.value)) / 100, self.height()), 4, 4)
        p.end()


class ScoreBreakdown(QWidget):
    """One row per score part: name, bar, value; the explanation is shown underneath."""

    ORDER = ("hook", "format", "duration", "pacing", "audio", "visual", "source", "ai")

    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(2)
        self.rows: dict[str, tuple[QLabel, Bar, QLabel, QLabel]] = {}
        for r, key in enumerate(self.ORDER):
            name, bar, val = label(PART_LABELS[key]), Bar(), label("")
            note = label("", "muted")
            note.setStyleSheet("font-size: 8.5pt;")
            val.setFixedWidth(30)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.grid.addWidget(name, r * 2, 0)
            self.grid.addWidget(bar, r * 2, 1)
            self.grid.addWidget(val, r * 2, 2)
            self.grid.addWidget(note, r * 2 + 1, 0, 1, 3)
            self.rows[key] = (name, bar, val, note)
        self.grid.setColumnStretch(1, 1)

    def set_score(self, score: dict | None) -> None:
        parts = (score or {}).get("parts", {})
        notes = (score or {}).get("notes", {})
        for key, (name, bar, val, note) in self.rows.items():
            show = key in parts
            for w in (name, bar, val, note):
                w.setVisible(show)
            if show:
                bar.set_value(parts[key])
                val.setText(f"{parts[key]:.0f}")
                note.setText(notes.get(key, ""))
                note.setVisible(bool(notes.get(key)))


class ScorePillDelegate(QStyledItemDelegate):
    """Draws the score column as a coloured pill with the number and grade."""

    def paint(self, painter, option, index):
        value = index.data(Qt.UserRole)
        super().paint(painter, option, index)  # selection background; the cell has no display text
        if value is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(6, 0, -6, 0)
        pill = QRectF(r.x(), r.center().y() - 11, min(r.width(), 64), 22)
        col = QColor(theme.score_colour(value))
        bg = QColor(col)
        bg.setAlpha(40)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(pill, 11, 11)
        painter.setPen(col)
        f = QFont(option.font)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(pill, Qt.AlignCenter, f"{value:.0f}  {grade(value)}")
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(80, 28)


class StatCard(QWidget):
    def __init__(self, title: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(0)
        self.value = label("—", "bigNumber")
        self.sub = label("", "muted")
        self.sub.setStyleSheet("font-size: 8.5pt;")
        lay.addWidget(label(title, "muted"))
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        self.setObjectName("panel")
        self.setAttribute(Qt.WA_StyledBackground, True)

    def set(self, value: str, sub: str = "", sub_colour: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        self.sub.setStyleSheet(f"font-size: 8.5pt; color: {sub_colour or theme.MUTED};")


ACCOUNT_COLOURS = ("#F43F72", "#22D3EE", "#A78BFA", "#34D399", "#FBBF24", "#FB923C", "#60A5FA", "#F472B6")


def account_colour(account_id: str) -> str:
    """A steady colour per account, so it's easy to see at a glance which one you're looking at."""
    return ACCOUNT_COLOURS[sum(map(ord, account_id or "")) % len(ACCOUNT_COLOURS)]


@lru_cache(maxsize=64)
def account_badge(account_id: str, text: str, size: int = 22) -> QPixmap:
    """A coloured circle with the account's first letter."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(account_colour(account_id)))
    p.drawEllipse(QRectF(0, 0, size, size))
    letter = next((c for c in text if c.isalnum()), "?").upper()
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 0.5))
    p.setFont(f)
    p.setPen(QColor("#0F1117"))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, letter)
    p.end()
    return pm


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay
