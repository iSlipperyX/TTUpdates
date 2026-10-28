"""Dark theme, colours and the app icon (matches ClipMonster's look, with a pink/cyan accent)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

BG = "#0F1117"
PANEL = "#161922"
PANEL_2 = "#1D2130"
BORDER = "#2A2F40"
TEXT = "#E6E8EF"
MUTED = "#8A90A6"
ACCENT = "#F43F72"
ACCENT_HOVER = "#FF5C8A"
ACCENT_2 = "#22D3EE"
GOOD = "#34D399"
WARN = "#FBBF24"
BAD = "#F87171"
SELECT = "#3A1F2C"


def score_colour(total: float | None) -> str:
    if total is None:
        return MUTED
    if total >= 80:
        return GOOD
    if total >= 65:
        return ACCENT_2
    if total >= 50:
        return WARN
    return BAD


STATUS_COLOURS = {"new": MUTED, "queued": ACCENT_2, "uploading": WARN, "posted": GOOD, "failed": BAD}

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QWidget#panel, QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#panelCurrent {{ background: {PANEL}; border: 1px solid {ACCENT}; border-radius: 10px; }}
QLabel#title {{ font-size: 15pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 11pt; font-weight: 600; color: {TEXT}; }}
QLabel#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#brand {{ font-size: 14pt; font-weight: 800; color: {TEXT}; letter-spacing: 0.5px; }}
QLabel#bigNumber {{ font-size: 18pt; font-weight: 700; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}

QPushButton {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 7px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {PANEL_2}; }}
QPushButton#primary {{ background: {ACCENT}; border: none; color: white; font-weight: 600; padding: 7px 16px; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: #6B2A40; color: #CDB3BD; }}
QPushButton#danger:hover {{ border-color: {BAD}; color: {BAD}; }}
QPushButton:checked {{ background: {SELECT}; border-color: {ACCENT}; }}
QPushButton#nav {{ background: transparent; border: none; border-radius: 8px; padding: 8px 14px; color: {MUTED}; font-weight: 600; }}
QPushButton#nav:hover {{ color: {TEXT}; background: {PANEL}; }}
QPushButton#nav:checked {{ color: {TEXT}; background: {PANEL_2}; }}
QPushButton#segFirst, QPushButton#segMid, QPushButton#segLast {{ padding: 6px 16px; font-weight: 600; color: {MUTED}; }}
QPushButton#segFirst {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
QPushButton#segMid {{ border-radius: 0; border-left: none; }}
QPushButton#segLast {{ border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; }}
QPushButton#segFirst:checked, QPushButton#segMid:checked, QPushButton#segLast:checked {{ color: {TEXT}; }}
QPushButton#accountSwitch {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 16px; padding: 5px 12px 5px 6px; font-weight: 600; text-align: left; }}
QPushButton#accountSwitch:hover {{ border-color: {ACCENT}; }}
QPushButton#accountSwitch::menu-indicator {{ subcontrol-position: right center; subcontrol-origin: padding; right: 8px; }}
QPushButton#nicheSwitch {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 16px; min-height: 20px; padding: 5px 24px 5px 12px; font-weight: 600; text-align: left; }}
QPushButton#nicheSwitch:hover {{ border-color: {ACCENT}; }}
QPushButton#nicheSwitch[filtered="true"] {{ border-color: {ACCENT}; color: {ACCENT_2}; }}
QPushButton#nicheSwitch::menu-indicator {{ subcontrol-position: right center; subcontrol-origin: padding; right: 8px; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QDateTimeEdit, QTimeEdit {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus,
QDateTimeEdit:focus, QTimeEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QComboBox QAbstractItemView {{ background: {PANEL_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 7px 12px; color: {MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

QListWidget, QTableWidget, QTreeWidget, QTableView {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; outline: none; }}
QListWidget::item {{ border-radius: 8px; }}
QListWidget::item:selected {{ background: {SELECT}; }}
QTableWidget, QTableView {{ gridline-color: transparent; alternate-background-color: #181B25; }}
QTableWidget::item, QTableView::item {{ padding: 4px; border: none; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {SELECT}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QProgressBar {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 5px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); border-radius: 4px; }}

QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid {BORDER}; background: {BG}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSplitter::handle {{ background: {BG}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding-top: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QMenu {{ background: {PANEL_2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, colour in (
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, BG), (QPalette.AlternateBase, PANEL),
        (QPalette.Text, TEXT), (QPalette.Button, PANEL_2), (QPalette.ButtonText, TEXT),
        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#FFFFFF"), (QPalette.ToolTipBase, PANEL_2),
        (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, MUTED),
    ):
        pal.setColor(role, QColor(colour))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """A pink-to-violet tile with a play triangle and a rising score bar chart."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0, QColor("#FF4D8D"))
    grad.setColorAt(1, QColor("#6D28D9"))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92), s * 0.22, s * 0.22)

    tri = QPainterPath()
    tri.moveTo(QPointF(s * 0.24, s * 0.22))
    tri.lineTo(QPointF(s * 0.24, s * 0.58))
    tri.lineTo(QPointF(s * 0.54, s * 0.40))
    tri.closeSubpath()
    p.setBrush(QColor("white"))
    p.drawPath(tri)

    p.setBrush(QColor("#22D3EE"))
    for i, h in enumerate((0.14, 0.24, 0.36)):
        x = s * (0.50 + i * 0.12)
        p.drawRoundedRect(QRectF(x, s * 0.80 - s * h, s * 0.085, s * h), s * 0.02, s * 0.02)
    pen = QPen(QColor("white"), max(1.0, s * 0.03), Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.18, s * 0.80), QPointF(s * 0.84, s * 0.80))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon
