"""Settings dialog: a themed form over ``mikronous_tray.prefs``. The window applies the result."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from . import prefs as P


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None, current: P.Prefs, *, model: str = "", kde_shortcut: str = "",
                 kde_shortcut_selection: str = "", kde_shortcut_vox: str = "", client=None):
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
        self.semantic = QCheckBox("semantic search (finds meaning, not just words)")
        self.semantic.setChecked(current.semantic)
        form.addRow("", self.semantic)
        form.addRow("", _hint("Runs a small CPU embedding model (~150 MB, downloaded once) next to the main one; the first pass "
                              "over your folders takes a few minutes in the background. Log: embed-setup.log in the config dir."))

        form.addRow(_rule())
        from . import voice_io
        self.stt_model = QComboBox()
        for size in voice_io.STT_MODELS:
            self.stt_model.addItem({"tiny": "tiny — fastest, rough", "base": "base — quick, fine for commands",
                                    "small": "small — better accuracy", "turbo": "turbo — best, needs a GPU"}[size], size)
        self.stt_model.setCurrentIndex(voice_io.STT_MODELS.index(current.stt_model) if current.stt_model in voice_io.STT_MODELS else 1)
        form.addRow("Voice input", self.stt_model)
        stt_ok, stt_msg = voice_io.stt_available()
        form.addRow("", _hint(("Hold VOX (or tap the voice key) and speak; the model downloads once on first use (~150 MB for base). "
                               if stt_ok else "Not available: " + stt_msg + ". ") + "Everything runs on this machine."))
        self.tts = QCheckBox("read replies aloud (Piper, local)")
        self.tts.setChecked(current.tts)
        form.addRow("Voice output", self.tts)
        self.tts_voice = QLineEdit(current.tts_voice)
        self.tts_voice.setPlaceholderText(voice_io.DEFAULT_TTS_VOICE)
        form.addRow("Piper voice", self.tts_voice)
        tts_ok, tts_msg = voice_io.tts_available()
        form.addRow("", _hint("Voice names as on the Piper voices list, e.g. en_US-lessac-medium, en_GB-alba-medium; downloaded once (~60 MB)."
                              if tts_ok else "Not available: " + tts_msg + "."))

        form.addRow(_rule())
        if P.hotkey_editable():
            self.hotkey = QLineEdit(current.hotkey)
            self.hotkey.setPlaceholderText("Ctrl+Alt+Space")
            form.addRow("Hotkey", self.hotkey)
            self.hotkey_selection = QLineEdit(current.hotkey_selection)
            self.hotkey_selection.setPlaceholderText("Ctrl+Alt+Shift+Space")
            form.addRow("Selection key", self.hotkey_selection)
            self.hotkey_vox = QLineEdit(current.hotkey_vox)
            self.hotkey_vox.setPlaceholderText("Ctrl+Alt+V")
            form.addRow("Voice key", self.hotkey_vox)
        else:
            self.hotkey = None
            self.hotkey_selection = None
            self.hotkey_vox = None
            form.addRow("Hotkey", QLabel(kde_shortcut or "not set"))
            form.addRow("Selection key", QLabel(kde_shortcut_selection or "not set"))
            form.addRow("Voice key", QLabel(kde_shortcut_vox or "not set"))
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
        try:
            from mikronous_cli.paths import LLAMA_ENV, read_env
            vision = bool(read_env(LLAMA_ENV).get("LLAMA_MMPROJ", "").strip())
        except Exception:  # noqa: BLE001
            vision = False
        form.addRow("Cogitator", QLabel((model or "unknown") + ("  · sees images" if vision else "  · text only")))
        form.addRow("", _hint("Change the model from a terminal: mik model recommend --apply (add --vision for one that can "
                              "answer about your screen), or mik model use <preset|gguf>."))

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
            hotkey_vox=(self.hotkey_vox.text().strip() if self.hotkey_vox is not None else self._initial.hotkey_vox) or self._initial.hotkey_vox,
            stt_model=self.stt_model.currentData(),
            tts=self.tts.isChecked(),
            tts_voice=self.tts_voice.text().strip() or self._initial.tts_voice,
            semantic=self.semantic.isChecked(),
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
