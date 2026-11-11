"""Purple Green Flux design system for the Qt UI.

Token values come from the "Purple Green Flux" design system (tokens.json). The web components
(pill buttons, chips, cards) are translated into one application-wide Qt style sheet that is
generated from the tokens, the current theme (Night = dark, Day = light) and the UI font size.
"""
from __future__ import annotations

import os

from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QPalette
from PySide6.QtWidgets import QApplication

# ----------------------------------------------------------------------------- tokens
TOKENS = {
    "bg":             ("#0c0b10", "#f6f4f9"),
    "surface":        ("#15141b", "#ffffff"),
    "surface-raised": ("#1d1b25", "#ffffff"),
    "line":           ("#2e2b39", "#e3dfea"),
    "line-strong":    ("#6a6580", "#8a8599"),
    "ink":            ("#f4f2f8", "#14121a"),
    "ink-muted":      ("#a7a2b6", "#5f5a6e"),
    "violet":         ("#8f7bff", "#5b3df5"),
    "on-violet":      ("#0c0b10", "#ffffff"),
    "violet-text":    ("#b9adff", "#5b3df5"),
    "violet-soft":    ("#2a2160", "#ebe7ff"),
    "mint":           ("#3df5b0", "#3df5b0"),
    "on-mint":        ("#06302a", "#06302a"),
    "mint-text":      ("#3df5b0", "#0b7a57"),
    "mint-soft":      ("#0f3a2f", "#dcfbef"),
    "lime":           ("#cdf56a", "#cdf56a"),
    "on-lime":        ("#14121a", "#14121a"),
    "danger":         ("#ff6b85", "#c4234a"),
    "warning":        ("#ffb547", "#9a5b00"),
    "focus":          ("#cdf56a", "#5b3df5"),
    # grad-ribbon (violet → mint): progress bars only
    "ribbon-a":       ("#8f7bff", "#8f7bff"),
    "ribbon-b":       ("#3df5b0", "#3df5b0"),
}
THEMES = {"dark": "Night", "light": "Day"}

SANS = "Pretendard Variable"
MONO = "JetBrains Mono"

_current = {"theme": "dark"}


def color(name: str, theme: str | None = None) -> str:
    dark, light = TOKENS[name]
    return dark if (theme or _current["theme"]) == "dark" else light


def qcolor(name: str) -> QColor:
    return QColor(color(name))


# ----------------------------------------------------------------------------- fonts
def _font_dir() -> str:
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base, "assets", "fonts")


_fonts_loaded = []


def load_fonts() -> bool:
    """Register the bundled Pretendard / JetBrains Mono fonts. Returns True if Pretendard is available."""
    if not _fonts_loaded:
        d = _font_dir()
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith((".ttf", ".otf")):
                    QFontDatabase.addApplicationFont(os.path.join(d, fn))
        _fonts_loaded.append(True)
    return SANS in QFontDatabase.families()


def ui_font(point_size: int) -> QFont:
    f = QFont(SANS if load_fonts() else QApplication.font().family())
    f.setPointSize(point_size)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def mono_font(base: QFont | None = None, weight: int = 400) -> QFont:
    base = base or QApplication.font()
    f = QFont(MONO)
    f.setStyleHint(QFont.Monospace)
    f.setPointSizeF(base.pointSizeF() * 0.95)
    f.setWeight(QFont.Weight(weight))
    return f


# ----------------------------------------------------------------------------- icons (tiny SVGs)
def _icon_dir() -> str:
    from ..core.settings import config_dir
    d = os.path.join(config_dir(), "theme")
    os.makedirs(d, exist_ok=True)
    return d


def _svg(name: str, body: str, stroke: str) -> str:
    path = os.path.join(_icon_dir(), f"{name}-{stroke.lstrip('#')}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
                    f'fill="none" stroke="{stroke}" stroke-width="2.5" stroke-linecap="round" '
                    f'stroke-linejoin="round">{body}</svg>')
    return path.replace("\\", "/")


CHEVRON_DOWN = '<polyline points="6 9 12 15 18 9"/>'
CHEVRON_UP = '<polyline points="6 15 12 9 18 15"/>'
CHECK = '<polyline points="5 12.5 10 17.5 19 7"/>'


# ----------------------------------------------------------------------------- style sheet
def stylesheet(theme: str, font_px: int) -> str:
    c = {k: color(k, theme) for k in TOKENS}
    f = max(10, font_px)
    s = f / 16.0                                     # token px are designed for 16px body text

    def r(px):                                       # scale a token length
        return max(1, round(px * s))

    # pill controls: radius = half of the total height
    ctl_h = round(f * 1.35)                          # content height (desktop density: tighter than web)
    pad_v, pad_h = round(f * 0.22), round(f * 0.85)
    btn_total = ctl_h + 2 * pad_v + 2
    pill = btn_total // 2
    chip_pad_h = round(f * 0.75)
    in_pad = round(f * 0.35)
    ind = round(f * 1.0)                             # checkbox size
    input_bg = c["bg"] if theme == "dark" else c["surface"]
    card_border = c["line"]
    chev = _svg("chev-down", CHEVRON_DOWN, c["ink-muted"])
    chev_up = _svg("chev-up", CHEVRON_UP, c["ink-muted"])
    check = _svg("check", CHECK, c["on-violet"])

    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QWidget {{ color: {c['ink']}; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QLabel[role="muted"] {{ color: {c['ink-muted']}; }}
QLabel[role="accent"] {{ color: {c['violet-text']}; }}
QLabel[role="success"] {{ color: {c['mint-text']}; }}
QLabel[role="cover"] {{ background: {c['surface']}; border: 1px dashed {c['line-strong']};
    border-radius: {r(16)}px; color: {c['ink-muted']}; }}
QLabel[role="viewer"] {{ background: {c['surface']}; color: {c['ink-muted']};
    border: 1px solid {c['line']}; border-radius: {r(16)}px; }}
QToolTip {{ background: {c['surface-raised']}; color: {c['ink']}; border: 1px solid {c['line']};
    padding: {r(6)}px {r(10)}px; border-radius: {r(10)}px; }}

/* ---- scroll areas are transparent so cards and bg show through ---- */
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QSplitter::handle {{ background: transparent; }}

/* ---- menu / toolbar ---- */
QMenuBar {{ background: {c['bg']}; color: {c['ink-muted']}; padding: {r(2)}px {r(8)}px; }}
QMenuBar::item {{ background: transparent; padding: {r(6)}px {r(12)}px; border-radius: {pill}px; }}
QMenuBar::item:selected {{ background: {c['surface-raised']}; color: {c['ink']}; }}
QMenu {{ background: {c['surface-raised']}; border: 1px solid {c['line']}; border-radius: {r(16)}px;
    padding: {r(8)}px; }}
QMenu::item {{ padding: {r(8)}px {r(20)}px; border-radius: {r(10)}px; background: transparent; }}
QMenu::item:selected {{ background: {c['violet-soft']}; color: {c['ink']}; }}
QMenu::item:disabled {{ color: {c['line-strong']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: {r(6)}px {r(8)}px; }}
QToolBar {{ background: {c['bg']}; border: none; border-bottom: 1px solid {c['line']};
    padding: {r(6)}px {r(10)}px; spacing: {r(4)}px; }}
QToolBar::separator {{ background: transparent; width: {r(6)}px; }}
QToolButton {{ background: transparent; color: {c['ink']}; border: 1px solid transparent;
    border-radius: {pill}px; padding: {pad_v}px {round(f * 0.45)}px; min-height: {ctl_h}px; }}
QToolButton:hover {{ background: {c['surface-raised']}; border-color: {c['line']}; }}
QToolButton:pressed {{ background: {c['violet-soft']}; }}
QToolButton::menu-indicator {{ image: none; }}

/* ---- cards (Flux Card): QGroupBox ---- */
QGroupBox {{ background: {c['surface']}; border: 1px solid {card_border}; border-radius: {r(20)}px;
    margin-top: {round(f * 1.4)}px; padding: {r(12)}px {r(14)}px {r(10)}px {r(14)}px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: {r(8)}px;
    padding: 0 {r(4)}px; color: {c['ink-muted']}; font-weight: 600; }}

/* ---- buttons: outline pill (default), primary (violet), chip (lime when selected) ---- */
QPushButton {{ background: transparent; color: {c['ink']}; border: 1px solid {c['line-strong']};
    border-radius: {pill}px; padding: {pad_v}px {pad_h}px; min-height: {ctl_h}px; font-weight: 600; }}
QPushButton:hover {{ background: {c['surface-raised']}; }}
QPushButton:pressed {{ background: {c['violet-soft']}; }}
QPushButton:focus {{ border-color: {c['focus']}; }}
QPushButton:disabled {{ color: {c['line-strong']}; border-color: {c['line']}; background: transparent; }}
QPushButton[variant="primary"] {{ background: {c['violet']}; color: {c['on-violet']};
    border: 1px solid {c['violet']}; font-weight: 700; padding: {pad_v}px {round(pad_h * 1.4)}px; }}
QPushButton[variant="primary"]:hover {{ background: {c['violet-text'] if theme == 'dark' else c['violet']};
    border-color: {c['violet-text']}; }}
QPushButton[variant="primary"]:focus {{ border-color: {c['focus']}; }}
QPushButton[variant="primary"]:disabled {{ background: {c['violet-soft']}; color: {c['ink-muted']};
    border-color: {c['violet-soft']}; }}
QPushButton[variant="chip"] {{ background: transparent; color: {c['ink-muted']}; border: 1px solid {c['line']};
    padding: {pad_v}px {chip_pad_h}px; }}
QPushButton[variant="chip"]:hover {{ color: {c['ink']}; border-color: {c['line-strong']}; background: transparent; }}
QPushButton[variant="chip"]:checked {{ background: {c['lime']}; color: {c['on-lime']}; border-color: {c['lime']}; }}
QPushButton[variant="chip"]:pressed {{ background: {c['violet-soft']}; color: {c['ink']}; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{ background: {input_bg}; color: {c['ink']};
    border: 1px solid {c['line-strong']}; border-radius: {r(10)}px; padding: {in_pad}px {r(10)}px;
    selection-background-color: {c['violet']}; selection-color: {c['on-violet']}; }}
QLineEdit, QComboBox, QSpinBox {{ min-height: {ctl_h}px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {c['focus']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {c['ink-muted']}; border-color: {c['line']}; }}
QLineEdit[readOnly="true"] {{ color: {c['ink-muted']}; }}
QComboBox::drop-down {{ border: none; width: {round(f * 1.6)}px; }}
QComboBox::down-arrow {{ image: url("{chev}"); width: {round(f * 0.8)}px; height: {round(f * 0.8)}px; }}
QComboBox QAbstractItemView {{ background: {c['surface-raised']}; color: {c['ink']}; border: 1px solid {c['line']};
    selection-background-color: {c['violet-soft']}; selection-color: {c['ink']}; outline: 0; padding: {r(4)}px; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: {round(f * 1.3)}px; background: transparent; }}
QSpinBox::up-arrow {{ image: url("{chev_up}"); width: {round(f * 0.7)}px; height: {round(f * 0.7)}px; }}
QSpinBox::down-arrow {{ image: url("{chev}"); width: {round(f * 0.7)}px; height: {round(f * 0.7)}px; }}
QPlainTextEdit[role="log"] {{ background: {c['surface']}; color: {c['ink-muted']}; border: 1px solid {c['line']};
    border-radius: {r(16)}px; padding: {r(8)}px {r(12)}px; }}

/* ---- check boxes ---- */
QCheckBox {{ spacing: {r(10)}px; }}
QCheckBox::indicator, QTableView::indicator {{ width: {ind}px; height: {ind}px; border-radius: {r(6)}px;
    border: 1px solid {c['line-strong']}; background: {input_bg}; }}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {c['violet-text']}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{ background: {c['violet']};
    border-color: {c['violet']}; image: url("{check}"); }}

/* ---- tables & lists ---- */
QTableView, QListView, QTreeView {{ background: {c['surface']}; alternate-background-color: {c['surface']};
    color: {c['ink']}; border: 1px solid {c['line']}; border-radius: {r(16)}px; gridline-color: {c['line']};
    selection-background-color: {c['violet-soft']}; selection-color: {c['ink']}; outline: 0; }}
QTableView::item, QListView::item {{ padding: 0 {r(6)}px; }}
QTableView::item:selected, QListView::item:selected {{ background: {c['violet-soft']}; color: {c['ink']}; }}
QTableView QLineEdit {{ border-radius: 0; padding: 0 {r(4)}px; min-height: 0; border: 1px solid {c['focus']}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['ink-muted']}; border: none;
    border-bottom: 1px solid {c['line']}; border-right: 1px solid {c['line']}; padding: {r(8)}px {r(8)}px;
    font-weight: 600; }}
QHeaderView::section:last {{ border-right: none; }}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}

/* ---- tabs → chips ---- */
QTabWidget::pane {{ border: 1px solid {c['line']}; border-radius: {r(16)}px; background: {c['surface']};
    top: -1px; }}
QTabBar::tab {{ background: transparent; color: {c['ink-muted']}; border: 1px solid {c['line']};
    border-radius: {pill}px; padding: {pad_v}px {chip_pad_h}px; margin: 0 {r(8)}px {r(8)}px 0; font-weight: 600;
    min-height: {ctl_h}px; }}
QTabBar::tab:hover {{ color: {c['ink']}; border-color: {c['line-strong']}; }}
QTabBar::tab:selected {{ background: {c['lime']}; color: {c['on-lime']}; border-color: {c['lime']}; }}

/* ---- progress (grad-ribbon), slider, scroll bars ---- */
QProgressBar {{ background: {c['surface-raised']}; border: 1px solid {c['line']}; border-radius: {r(9)}px;
    min-height: {r(18)}px; max-height: {r(18)}px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: {r(8)}px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 {c['ribbon-a']}, stop:1 {c['ribbon-b']}); }}
QSlider::groove:horizontal {{ height: {r(6)}px; background: {c['line']}; border-radius: {r(3)}px; }}
QSlider::sub-page:horizontal {{ background: {c['violet']}; border-radius: {r(3)}px; }}
QSlider::handle:horizontal {{ background: {c['violet']}; border: none;
    width: {r(18)}px; height: {r(18)}px; margin: -{(r(18) - r(6)) // 2}px 0; border-radius: {r(18) // 2}px; }}
QSlider::handle:horizontal:hover {{ background: {c['violet-text']}; }}
QScrollBar:vertical {{ background: transparent; width: {r(12)}px; margin: {r(4)}px 0; }}
QScrollBar:horizontal {{ background: transparent; height: {r(12)}px; margin: 0 {r(4)}px; }}
QScrollBar::handle {{ background: {c['line-strong']}; border-radius: {r(6)}px; min-height: {r(32)}px;
    min-width: {r(32)}px; }}
QScrollBar::handle:hover {{ background: {c['ink-muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QDialogButtonBox QPushButton {{ min-width: {round(f * 4)}px; }}
"""


def palette(theme: str) -> QPalette:
    """Palette for things style sheets do not reach (native dialogs, item views' text roles)."""
    p = QPalette()
    g = lambda n: QColor(color(n, theme))  # noqa: E731
    for role, tok in ((QPalette.Window, "bg"), (QPalette.WindowText, "ink"), (QPalette.Base, "surface"),
                      (QPalette.AlternateBase, "surface"), (QPalette.Text, "ink"), (QPalette.Button, "surface-raised"),
                      (QPalette.ButtonText, "ink"), (QPalette.Highlight, "violet"),
                      (QPalette.HighlightedText, "on-violet"), (QPalette.ToolTipBase, "surface-raised"),
                      (QPalette.ToolTipText, "ink"), (QPalette.PlaceholderText, "ink-muted"),
                      (QPalette.Link, "violet-text"), (QPalette.BrightText, "lime")):
        p.setColor(role, g(tok))
    p.setColor(QPalette.Disabled, QPalette.Text, g("line-strong"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, g("line-strong"))
    p.setColor(QPalette.Disabled, QPalette.WindowText, g("line-strong"))
    return p


def font_px(font: QFont) -> int:
    """Pixel size of the UI font at the logical 96 dpi Qt uses for layout (point size × 4/3).
    Computed from the point size so it does not depend on the platform's font database."""
    pt = font.pointSizeF()
    if pt > 0:
        return round(pt * 96 / 72)
    return max(10, font.pixelSize(), QFontInfo(font).pixelSize())


def apply(theme: str = "dark"):
    """Apply the Flux theme to the whole application (call again after a font-size change)."""
    app = QApplication.instance()
    theme = theme if theme in THEMES else "dark"
    _current["theme"] = theme
    app.setPalette(palette(theme))
    app.setStyleSheet(stylesheet(theme, font_px(app.font())))


def current() -> str:
    return _current["theme"]
