"""Settings dialog: a themed form over ``mikronous_tray.prefs``. The window applies the result."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from . import prefs as P


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None, current: P.Prefs, *, model: str = "", kde_shortcut: str = "",
                 kde_shortcut_selection: str = "", client=None):
        super().__init__(parent, objectName="settings")
        self.setWindowTitle("Mikronous settings")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._initial = current
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)
        root.addWidget(QLabel("SETTINGS OF THE SLATE", objectName="dlgTitle"))

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        slate = QWidget()
        form = QFormLayout(slate)
        form.setContentsMargins(4, 10, 4, 4)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        self.tabs.addTab(slate, "SLATE")
        self.routines = None
        if client is not None:
            from .routines_tab import RoutinesTab
            self.routines = RoutinesTab(client)
            self.tabs.addTab(self.routines, "ROUTINES")
            self.tabs.currentChanged.connect(lambda i: self.routines.refresh() if i == 1 else None)

        self.voice = QComboBox()
        for lvl in P.VOICES:
            self.voice.addItem({"plain": "plain — no theme", "light": "light — a phrase and a sign-off",
                                "full": "full — tech-priest"}.get(lvl, lvl), lvl)
        if current.voice in P.VOICES:
            self.voice.setCurrentIndex(P.VOICES.index(current.voice))
        else:
            self.voice.addItem(f"{current.voice} (custom SOUL.md)", current.voice)
            self.voice.setCurrentIndex(self.voice.count() - 1)
        form.addRow("Voice", self.voice)
        form.addRow("", _hint("New chats use the new voice (Ctrl+N)."))

        self.approvals = QComboBox()
        for mode, text in (("smart", "smart — card only for risky commands"), ("manual", "manual — card for every flagged command"),
                           ("off", "off — never ask")):
            self.approvals.addItem(text, mode)
        self.approvals.setCurrentIndex(P.APPROVAL_MODES.index(current.approvals) if current.approvals in P.APPROVAL_MODES else 0)
        form.addRow("Sanctions", self.approvals)

        self.internet = QCheckBox("web search, page reading and the browser")
        self.internet.setChecked(current.internet)
        form.addRow("Internet", self.internet)
        form.addRow("", _hint("Off = fully offline. The model itself always runs locally."))

        form.addRow(_rule())
        self.notes_dir = _path_row(self, current.notes_dir, directory=True)
        form.addRow("Notes folder", self.notes_dir)
        self.docs_dirs = QLineEdit(current.docs_dirs)
        self.docs_dirs.setPlaceholderText("~/Documents, ~/Projects")
        form.addRow("File search", self.docs_dirs)
        form.addRow("", _hint("Folders the file search indexes, comma-separated. Applies after the gateway restarts."))

        form.addRow(_rule())
        if P.hotkey_editable():
            self.hotkey = QLineEdit(current.hotkey)
            self.hotkey.setPlaceholderText("Ctrl+Alt+Space")
            form.addRow("Hotkey", self.hotkey)
            self.hotkey_selection = QLineEdit(current.hotkey_selection)
            self.hotkey_selection.setPlaceholderText("Ctrl+Alt+Shift+Space")
            form.addRow("Selection key", self.hotkey_selection)
        else:
            self.hotkey = None
            self.hotkey_selection = None
            form.addRow("Hotkey", QLabel(kde_shortcut or "not set"))
            form.addRow("Selection key", QLabel(kde_shortcut_selection or "not set"))
            form.addRow("", _hint("Change them in System Settings → Shortcuts → Mikronous (or re-run the installer with "
                                  "MIKRONOUS_HOTKEY / MIKRONOUS_HOTKEY_SELECTION)."))
        self.translate_lang = QLineEdit(current.translate_lang)
        form.addRow("Translate to", self.translate_lang)
        form.addRow("", _hint("Select text anywhere, press the selection key: Explain · Summarise · Rewrite · Translate · Ask."))
        self.litany = QCheckBox("type out the boot litany when the slate first opens")
        self.litany.setChecked(current.litany)
        form.addRow("Litany", self.litany)
        self.keep_model = QCheckBox("keep the model loaded (VRAM) when the tray quits")
        self.keep_model.setChecked(current.keep_model)
        form.addRow("On quit", self.keep_model)

        form.addRow(_rule())
        form.addRow("Cogitator", QLabel(model or "unknown"))
        form.addRow("", _hint("Change the model from a terminal: mik model recommend --apply, or mik model use <gguf>."))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("CANCEL")
        cancel.clicked.connect(self.reject)
        save = QPushButton("INSCRIBE", objectName="primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def values(self) -> P.Prefs:
        return P.Prefs(
            voice=self.voice.currentData(),
            approvals=self.approvals.currentData(),
            internet=self.internet.isChecked(),
            litany=self.litany.isChecked(),
            keep_model=self.keep_model.isChecked(),
            notes_dir=self.notes_dir.edit.text().strip() or self._initial.notes_dir,
            docs_dirs=self.docs_dirs.text().strip() or self._initial.docs_dirs,
            hotkey=(self.hotkey.text().strip() if self.hotkey is not None else self._initial.hotkey) or self._initial.hotkey,
            hotkey_selection=(self.hotkey_selection.text().strip() if self.hotkey_selection is not None
                              else self._initial.hotkey_selection) or self._initial.hotkey_selection,
            translate_lang=self.translate_lang.text().strip() or self._initial.translate_lang,
        )


class _PathRow(QWidget):
    def __init__(self, parent, text: str, directory: bool):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = QLineEdit(text)
        lay.addWidget(self.edit, 1)
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(lambda: self._browse(directory))
        lay.addWidget(b)

    def _browse(self, directory: bool) -> None:
        from pathlib import Path
        start = str(Path(self.edit.text() or "~").expanduser())
        chosen = QFileDialog.getExistingDirectory(self, "Choose folder", start) if directory else QFileDialog.getOpenFileName(self, "Choose file", start)[0]
        if chosen:
            self.edit.setText(chosen)


def _path_row(parent, text: str, directory: bool) -> _PathRow:
    return _PathRow(parent, text, directory)


def _hint(text: str) -> QLabel:
    lbl = QLabel(text, objectName="hint")
    lbl.setWordWrap(True)
    return lbl


def _rule() -> QFrame:
    return QFrame(objectName="rule")
