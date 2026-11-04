"""Shared widgets: wrapping flow layout and the quick-title button bar."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLayout, QPlainTextEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..core.settings import DEFAULT_TITLE_PRESETS  # noqa: E402


class FlowLayout(QLayout):
    """Lays widgets out left→right and wraps to the next line (buttons never get cut off)."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(QRect(0, 0, w, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect, dry):
        x, y, line_h = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + sp, 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class TitlePresetBar(QWidget):
    """'빠른 제목:' + one button per preset title + '✏ 목록 편집'. Emits chosen(title)."""
    chosen = Signal(str)
    edited = Signal(list)

    def __init__(self, presets: list[str], parent=None):
        super().__init__(parent)
        self.presets = list(presets)
        self.flow = FlowLayout(self)
        sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self._rebuild()

    def _rebuild(self):
        while self.flow.count():
            w = self.flow.takeAt(0).widget()
            if w:
                w.deleteLater()
        lbl = QLabel("빠른 제목:")
        lbl.setToolTip("트랙을 선택하고 버튼을 누르면 제목이 들어가고 다음 트랙으로 넘어갑니다.\n"
                       "여러 트랙을 선택하면 한꺼번에 들어갑니다.")
        self.flow.addWidget(lbl)
        for title in self.presets:
            b = QPushButton(title)
            b.setFocusPolicy(Qt.NoFocus)          # keep the table's selection/focus
            b.clicked.connect(lambda _=False, t=title: self.chosen.emit(t))
            self.flow.addWidget(b)
        e = QPushButton("✏ 목록 편집")
        e.setFocusPolicy(Qt.NoFocus)
        e.clicked.connect(self.edit)
        self.flow.addWidget(e)
        self.updateGeometry()

    def set_presets(self, presets: list[str]):
        self.presets = list(presets)
        self._rebuild()

    def edit(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("빠른 제목 목록 편집")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("한 줄에 하나씩 입력하세요. 순서대로 버튼이 만들어집니다."))
        text = QPlainTextEdit("\n".join(self.presets))
        lay.addWidget(text)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        bb.button(QDialogButtonBox.RestoreDefaults).setText("기본 목록으로")
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            lambda: text.setPlainText("\n".join(DEFAULT_TITLE_PRESETS)))
        lay.addWidget(bb)
        dlg.resize(self.fontMetrics().horizontalAdvance("M") * 40, self.fontMetrics().height() * 18)
        if dlg.exec():
            lines = [ln.strip() for ln in text.toPlainText().splitlines() if ln.strip()]
            self.set_presets(list(dict.fromkeys(lines)))      # drop duplicates, keep order
            self.edited.emit(self.presets)


def apply_title_to_table(table, title_col: int, title: str) -> list[int]:
    """Write `title` into the selected rows (or the current row), then select the row after the last one.
    Returns the rows that were changed."""
    rows = sorted({i.row() for i in table.selectedIndexes()}) or (
        [table.currentRow()] if table.currentRow() >= 0 else [])
    if not rows:
        return []
    for r in rows:
        it = table.item(r, title_col)
        if it is not None:
            it.setText(title)              # itemChanged → the owner updates its data
    nxt = rows[-1] + 1
    if len(rows) == 1 and nxt < table.rowCount():
        table.setCurrentCell(nxt, title_col)
        table.selectRow(nxt)
    return rows
