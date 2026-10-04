"""Settings → Routines: list, create, edit, pause, run and delete the desktop's scheduled agent tasks."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from . import routines as R


class _Jobs(QThread):
    done = Signal(list)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            self.done.emit(list(self.client.list_jobs(True)))
        except Exception:  # noqa: BLE001
            self.done.emit([])


class RoutineDialog(QDialog):
    """Create or edit one routine. ``values()`` → {name, schedule, prompt, skills}."""

    def __init__(self, parent: QWidget | None, job: dict | None = None, preset_key: str = "custom"):
        super().__init__(parent, objectName="settings")
        self.setWindowTitle("Routine")
        self.setModal(True)
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.addWidget(QLabel("A RITE ON A SCHEDULE", objectName="dlgTitle"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        root.addLayout(form)
        self.preset = QComboBox()
        for p in R.PRESETS:
            self.preset.addItem(p.name, p.key)
        self.name = QLineEdit()
        self.schedule = QLineEdit()
        self.schedule.setPlaceholderText("every 1d at 08:00 · every 6h · weekdays at 9am · 0 18 * * 5")
        self.prompt = QPlainTextEdit()
        self.prompt.setFixedHeight(110)
        self.prompt.setPlaceholderText("What the assistant should do each time. It answers to the desktop.")
        self.hint = QLabel("", objectName="hint")
        self.hint.setWordWrap(True)
        self._skills: tuple[str, ...] = ()
        if job is None:
            form.addRow("Preset", self.preset)
            self.preset.currentIndexChanged.connect(self._apply_preset)
        form.addRow("Name", self.name)
        form.addRow("Schedule", self.schedule)
        form.addRow("Prompt", self.prompt)
        form.addRow("", self.hint)
        if job is not None:
            self.name.setText(str(job.get("name") or ""))
            self.schedule.setText(R.schedule_text(job))
            self.prompt.setPlainText(str(job.get("prompt") or ""))
            self._skills = tuple(job.get("skills") or ())
        else:
            self.preset.setCurrentIndex(max(0, self.preset.findData(preset_key)))
            self._apply_preset()
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("CANCEL")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("INSCRIBE", objectName="primary")
        ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)

    def _apply_preset(self) -> None:
        p = R.preset(self.preset.currentData())
        self.name.setText(p.name if p.key != "custom" else "")
        self.schedule.setText(p.schedule)
        self.prompt.setPlainText(p.prompt)
        self.hint.setText(p.hint)
        self._skills = p.skills

    def _accept(self) -> None:
        v = self.values()
        if not v["name"] or not v["schedule"] or not v["prompt"]:
            self.hint.setText("Name, schedule and prompt are all needed.")
            return
        self.accept()

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "schedule": self.schedule.text().strip(),
                "prompt": self.prompt.toPlainText().strip(), "skills": list(self._skills)}


class RoutinesTab(QWidget):
    def __init__(self, client, parent: QWidget | None = None):
        super().__init__(parent)
        self.client = client
        self.jobs: list[dict] = []
        self._fetch: _Jobs | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)
        intro = QLabel("Scheduled rites run the assistant on the local model and deliver to this desktop. "
                       "Reminders you set in chat are listed too.", objectName="hint")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.list = QListWidget(objectName="sessionList")
        self.list.setWordWrap(True)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        self.list.itemDoubleClicked.connect(lambda _i: self.edit())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_new = QPushButton("NEW…")
        self.btn_new.clicked.connect(lambda: self.new())      # clicked(bool) must not become preset_key
        self.btn_edit = QPushButton("EDIT…")
        self.btn_edit.clicked.connect(self.edit)
        self.btn_pause = QPushButton("PAUSE")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_run = QPushButton("RUN NOW")
        self.btn_run.clicked.connect(self.run_now)
        self.btn_delete = QPushButton("DELETE")
        self.btn_delete.clicked.connect(self.delete)
        for b in (self.btn_new, self.btn_edit, self.btn_pause, self.btn_run, self.btn_delete):
            row.addWidget(b)
        lay.addLayout(row)
        self.status = QLabel("", objectName="hint")
        lay.addWidget(self.status)
        self._sync_buttons()

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        if self._fetch is not None and self._fetch.isRunning():
            return
        self.status.setText("consulting the gateway…")
        self._fetch = _Jobs(self.client, self)
        self._fetch.done.connect(self._populate)
        self._fetch.start()

    def _populate(self, jobs: list) -> None:
        if self._fetch is not None:
            self._fetch.deleteLater()
            self._fetch = None
        self.jobs = [j for j in jobs if R.is_routine(j)]
        self.list.clear()
        for j in self.jobs:
            first, second = R.summary(j)
            item = QListWidgetItem(f"{first}\n{second}")
            item.setData(Qt.UserRole, j.get("id"))
            self.list.addItem(item)
        self.status.setText("" if self.jobs else "No routines yet. NEW… offers a morning briefing to start with.")
        self._sync_buttons()

    def selected(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        jid = item.data(Qt.UserRole)
        return next((j for j in self.jobs if j.get("id") == jid), None)

    def _sync_buttons(self) -> None:
        j = self.selected()
        editable = j is not None and not R.is_reminder(j)
        self.btn_edit.setEnabled(editable)
        self.btn_pause.setEnabled(j is not None)
        self.btn_run.setEnabled(j is not None)
        self.btn_delete.setEnabled(j is not None)
        self.btn_pause.setText("RESUME" if j is not None and R.paused(j) else "PAUSE")

    # ------------------------------------------------------------------ actions
    def _call(self, what: str, fn) -> bool:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Mikronous", f"Could not {what}: {exc}")
            return False
        self.refresh()
        return True

    def new(self, preset_key: str = "briefing") -> None:
        dlg = RoutineDialog(self, None, preset_key)
        dlg.setStyleSheet(self.window().styleSheet())
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        if self._call("create the routine", lambda: self.client.create_job(v["name"], v["schedule"], v["prompt"],
                                                                           deliver=R.DELIVER, skills=v["skills"])):
            self.status.setText(f"++ {v['name'].upper()} INSCRIBED ++")

    def edit(self) -> None:
        j = self.selected()
        if j is None or R.is_reminder(j):
            return
        dlg = RoutineDialog(self, j)
        dlg.setStyleSheet(self.window().styleSheet())
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        self._call("update the routine", lambda: self.client.update_job(j["id"], name=v["name"], schedule=v["schedule"],
                                                                        prompt=v["prompt"]))

    def toggle_pause(self) -> None:
        j = self.selected()
        if j is None:
            return
        self._call("pause/resume", lambda: self.client.job_action(j["id"], "resume" if R.paused(j) else "pause"))

    def run_now(self) -> None:
        j = self.selected()
        if j is None:
            return
        if self._call("run the routine", lambda: self.client.job_action(j["id"], "run")):
            self.status.setText("++ RITE DISPATCHED · THE ANSWER ARRIVES ON THE SLATE ++")

    def delete(self) -> None:
        j = self.selected()
        if j is None:
            return
        if QMessageBox.question(self, "Delete routine", f"Delete “{j.get('name')}”?", QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        self._call("delete the routine", lambda: self.client.delete_job(j["id"]))
