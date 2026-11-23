"""Read a deed file into reviewed COGO courses.

Pages are sent to the vision model as images. Nothing is written to the map
until the courses are loaded into Traverse and the user creates points.
"""
import os

from qgis.core import QgsApplication, QgsSettings, QgsTask
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)
try:
    from qgis.gui import QgsAuthConfigSelect
except ImportError:
    QgsAuthConfigSelect = None

from .ai_client import AIError, PROVIDERS, XAI_MODEL, api_key, pick
from .ai_client import Client
from .deed_ai import (CALL_SCHEMA, CALLS_PROMPT, FIND_PROMPT, checked_rows, find_description,
                      ocr_prompt, quote_in_text)
from .engine import closure, traverse
from .legal import parse_description

PREFIX = "CogoWorkbench/ai/"
FILTER = "Deeds (*.pdf *.png *.jpg *.jpeg *.tif *.tiff);;All files (*)"
UNITS = ("US survey feet", "International feet", "Meters", "CRS units")


def _saved(settings, provider):
    values = {"provider": provider}
    for key, default in (("url", PROVIDERS[provider][1]), ("ocr_model", ""), ("llm_model", ""), ("authcfg", "")):
        values[key] = settings.value(PREFIX + provider + "/" + key, default, type=str)
    return values


class DeedReaderDialog(QDialog):
    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.rows = []
        self._task = None
        self.setWindowTitle("Read deed")
        self.resize(880, 720)
        settings = QgsSettings()
        provider = settings.value(PREFIX + "provider", "ollama", type=str)
        if provider not in PROVIDERS:
            provider = "ollama"
        layout = QVBoxLayout(self)

        path_row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("PDF, PNG, JPG or TIF. Every page is read as an image.")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        layout.addLayout(path_row)

        form = QFormLayout()
        service = QHBoxLayout()
        self.provider = QComboBox()
        for key, (label, _url) in PROVIDERS.items():
            self.provider.addItem(label, key)
        self.provider.setCurrentIndex(max(0, self.provider.findData(provider)))
        self.url = QLineEdit()
        find = QPushButton("Find models")
        find.clicked.connect(self._load_models)
        service.addWidget(self.provider)
        service.addWidget(self.url, 1)
        service.addWidget(find)
        form.addRow("AI service", service)
        models = QHBoxLayout()
        self.ocr_model = QComboBox()
        self.ocr_model.setEditable(True)
        self.llm_model = QComboBox()
        self.llm_model.setEditable(True)
        self.dpi = QSpinBox()
        self.dpi.setRange(100, 400)
        self.dpi.setValue(200)
        self.dpi.setSuffix(" dpi")
        models.addWidget(QLabel("Read pages"))
        models.addWidget(self.ocr_model, 1)
        models.addWidget(QLabel("Unread calls"))
        models.addWidget(self.llm_model, 1)
        models.addWidget(self.dpi)
        form.addRow("Models", models)
        layout.addLayout(form)
        self.auth = None
        if QgsAuthConfigSelect is not None:
            try:
                self.auth = QgsAuthConfigSelect()
            except Exception:
                # A locked authentication database must not block the local Ollama path.
                self.auth = None
        if self.auth is not None:
            layout.addWidget(self.auth)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

        steps = QHBoxLayout()
        read = QPushButton("1. Read document")
        read.clicked.connect(self._read)
        copy = QPushButton("Copy description")
        copy.clicked.connect(self._find_ai)
        extract = QPushButton("2. Extract calls")
        extract.clicked.connect(self._extract)
        ask = QPushButton("Ask AI about unread calls")
        ask.clicked.connect(self._ask)
        for button in (read, copy, extract, ask):
            steps.addWidget(button)
        layout.addLayout(steps)
        self.status = QLabel("Pick a deed and read it. Text already in a PDF is ignored; the model sees the page image.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.deed_text = QPlainTextEdit()
        self.deed_text.setPlaceholderText("The model's reading of the pages. Fix misread characters here.")
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("The legal description. Fix it, then extract calls again.")
        self.desc.textChanged.connect(self._invalidate)
        layout.addWidget(QLabel("Deed text"))
        layout.addWidget(self.deed_text, 2)
        layout.addWidget(QLabel("Legal description"))
        layout.addWidget(self.desc, 2)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Part", "Source clause", "Call", "Read by"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 2)
        self.closure = QLabel()
        self.closure.setWordWrap(True)
        layout.addWidget(self.closure)

        bottom = QHBoxLayout()
        self.units = QComboBox()
        self.units.addItems(UNITS)
        self.reviewed = QCheckBox("I checked every call against the deed")
        self.reviewed.toggled.connect(self._sync)
        load = QPushButton("Load courses into Traverse")
        load.clicked.connect(self._load)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(QLabel("Record units"))
        bottom.addWidget(self.units)
        bottom.addWidget(self.reviewed, 1)
        bottom.addWidget(load)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.load_btn = load
        self.provider.currentIndexChanged.connect(self._show_provider)
        self._show_provider()
        self._sync()

    def _config(self):
        provider = self.provider.currentData()
        values = _saved(QgsSettings(), provider)
        values.update(url=self.url.text().strip(), ocr_model=self.ocr_model.currentText().strip(),
                      llm_model=self.llm_model.currentText().strip(),
                      authcfg=self.auth.configId() if self.auth is not None else values["authcfg"])
        if not values["ocr_model"]:
            values["ocr_model"] = XAI_MODEL if provider == "xai" else "deepseek-ocr"
        if not values["llm_model"]:
            values["llm_model"] = XAI_MODEL if provider == "xai" else "qwen2.5vl"
        return values

    def _save(self):
        values = self._config()
        settings = QgsSettings()
        provider = values["provider"]
        for key in ("url", "ocr_model", "llm_model", "authcfg"):
            settings.setValue(PREFIX + provider + "/" + key, values.get(key, ""))
        settings.setValue(PREFIX + "provider", provider)
        if provider == "ollama":
            Client(provider, values["url"])
        else:
            Client(provider, values["url"], api_key(values.get("authcfg", "")))

    def _show_provider(self):
        provider = self.provider.currentData()
        values = _saved(QgsSettings(), provider)
        self.url.setText(values["url"] or PROVIDERS[provider][1])
        self.ocr_model.setCurrentText(values["ocr_model"])
        self.llm_model.setCurrentText(values["llm_model"])
        if self.auth is not None:
            self.auth.setVisible(provider != "ollama")
            if values["authcfg"]:
                self.auth.setConfigId(values["authcfg"])
        if provider == "ollama":
            self.notice.setText("Ollama stays on this computer. Preferred page reader: ollama pull deepseek-ocr. "
                                "Fallbacks: qwen2.5vl, gemma4.")
        elif provider == "xai":
            self.notice.setText("xAI (Grok, model %s) sends each page image to https://api.x.ai. "
                                "Set XAI_API_KEY, or choose a QGIS authentication config with the key "
                                "in the password. Hosted use can cost money." % XAI_MODEL)
        else:
            self.notice.setText("A custom OpenAI-compatible server. Use HTTPS unless it is on this computer. "
                                "The API key is XAI_API_KEY or the authentication config password.")

    def _client(self):
        values = self._config()
        key = "" if values["provider"] == "ollama" else api_key(values.get("authcfg", ""))
        return Client(values["provider"], values["url"], key)

    def _browse(self):
        start = QgsSettings().value(PREFIX + "dir", "", type=str)
        path, _filter = QFileDialog.getOpenFileName(self, "Deed", start, FILTER)
        if path:
            self.path.setText(path)
            QgsSettings().setValue(PREFIX + "dir", os.path.dirname(path))

    def _load_models(self):
        try:
            models = self._client().list_models()
        except AIError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        provider = self.provider.currentData()
        pairs = ((self.ocr_model, ("deepseek-ocr", "qwen2.5vl", "gemma4", XAI_MODEL)),
                 (self.llm_model, ("qwen2.5vl", "gemma4", XAI_MODEL)))
        for combo, prefs in pairs:
            current = combo.currentText()
            combo.clear()
            combo.addItems(models)
            combo.setCurrentText(current or (XAI_MODEL if provider == "xai" else pick(models, *prefs)))

    def _run(self, label, work, done):
        if self._task is not None:
            QMessageBox.information(self, self.windowTitle(), "Wait for the current request to finish.")
            return
        try:
            self._save()
        except AIError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        self.status.setText(label + "…")
        self.setCursor(Qt.CursorShape.BusyCursor)

        def finished(exception, result=None):
            self._task = None
            if sip.isdeleted(self):
                return
            self.unsetCursor()
            if exception is not None:
                self.status.setText(str(exception))
                return
            done(result)

        self._task = QgsTask.fromFunction(label, work, on_finished=finished)
        QgsApplication.taskManager().addTask(self._task)

    def _read(self):
        path = self.path.text().strip()
        if not os.path.isfile(path):
            QMessageBox.information(self, self.windowTitle(), "Pick the deed file first.")
            return
        try:
            client = self._client()
        except AIError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        model, dpi = self._config()["ocr_model"], self.dpi.value()

        def render(task):
            from .deed_pages import load
            return load(path, dpi)

        def rendered(images):
            if not images:
                self.status.setText("Couldn't render the deed pages.")
                return

            def ocr(task):
                pages = []
                for index, image in enumerate(images):
                    if task.isCanceled():
                        raise RuntimeError("Cancelled")
                    task.setProgress(100.0 * index / len(images))
                    pages.append(client.chat(model, ocr_prompt(model), [image]))
                return pages

            def done(pages):
                text = "\n\n".join("----- page %d -----\n%s" % (i + 1, page.strip()) for i, page in enumerate(pages))
                self.deed_text.setPlainText(text)
                found = find_description(text)
                self.desc.setPlainText(found or text)
                self._extract()
                if not found:
                    self.status.setText("Read %d page(s). No 'Begin at' / 'Commence at' was found, so the "
                                        "whole reading is in the description. Trim it, then extract again." % len(pages))

            self._run("Reading pages with %s" % model, ocr, done)

        self._run("Rendering %s" % os.path.basename(path), render, rendered)

    def _find_ai(self):
        text = self.deed_text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, self.windowTitle(), "Read the document first.")
            return
        try:
            client = self._client()
        except AIError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        model = self._config()["llm_model"]

        def work(task):
            return client.chat(model, FIND_PROMPT % text[:20000])

        def done(result):
            if not quote_in_text(result, text):
                self.status.setText("The model's copy was not word for word from the deed, so it was not used.")
                return
            self.desc.setPlainText(result.strip())
            self._extract()

        self._run("Copying the description", work, done)

    def _invalidate(self):
        self.rows = []
        self._courses = []
        self.table.setRowCount(0)
        self.reviewed.setChecked(False)
        self._sync()

    def _extract(self):
        self._invalidate()
        text = self.desc.toPlainText().strip()
        if not text:
            self.status.setText("The legal description is empty.")
            return
        try:
            parsed = parse_description(text)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.rows = []
        for row in parsed:
            self.rows.append({"phase": row.phase, "source": row.source, "course": row.course,
                              "by": "Parser" if row.course else "", "note": row.issue})
        self._show()

    def _describe(self, course):
        if course.kind == "Curve":
            return "Curve %s, %s %s, R %s, %s %s" % (
                course.side, course.basis, course.direction, course.radius, course.measure, course.distance)
        return "%s, %s" % (course.direction, course.distance)

    def _show(self):
        self.table.setRowCount(0)
        bad = 0
        for row in self.rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            if row["course"] is not None and row["phase"] == "Boundary" and not row["note"]:
                call, color = self._describe(row["course"]), QColor("#1b5e20")
            elif row["note"]:
                call, color = row["note"], QColor("#b3261e")
                if row["phase"] == "Boundary":
                    bad += 1
            elif row["phase"] != "Boundary" and row["course"] is not None:
                call, color = "Excluded tie: " + self._describe(row["course"]), QColor("#666666")
            else:
                call, color = "Context, not a course", QColor("#666666")
            if row["by"] == "AI" and row["course"] is not None:
                color = QColor("#a35a00")
            for column, value in enumerate([row["phase"], row["source"], call, row["by"]]):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setForeground(color)
                self.table.setItem(index, column, item)
        self.table.resizeRowsToContents()
        courses = [row["course"] for row in self.rows
                   if row["phase"] == "Boundary" and row["course"] is not None and not row["note"]]
        self._courses = [] if bad else courses
        if courses:
            end = closure(traverse((0, 0), courses), (0, 0))
            where = "closes" if end[2] < 1e-6 else "misclose %.3f, 1:%s" % (
                end[2], "{:,}".format(int(end[3] / end[2])))
            self.closure.setText("%d boundary course(s) from (0, 0): %s. %s" % (
                len(courses), where, "Unread calls are still in red." if bad else
                "Check them, choose units, then load. Create corner points after you set the start."))
        else:
            self.closure.setText("No boundary courses yet.")
        self.status.setText("Parser read %d. %s" % (
            sum(1 for row in self.rows if row["by"] == "Parser" and row["phase"] == "Boundary"),
            "Red rows can be sent to the model, which must quote the deed." if bad else
            "Review every call against the deed before loading."))
        self._sync()

    def _ask(self):
        if not self.rows:
            sources, indexes = [self.desc.toPlainText().strip()], [None]
        else:
            indexes = [i for i, row in enumerate(self.rows)
                       if row["phase"] == "Boundary" and row["course"] is None and row["note"]]
            sources = [self.rows[i]["source"] for i in indexes]
        sources = [item for item in sources if item]
        if not sources:
            QMessageBox.information(self, self.windowTitle(), "Every boundary call was read already.")
            return
        try:
            client = self._client()
        except AIError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        model = self._config()["llm_model"]

        def work(task):
            answers = []
            for index, source in enumerate(sources):
                if task.isCanceled():
                    raise RuntimeError("Cancelled")
                task.setProgress(100.0 * index / len(sources))
                answers.append(client.chat_json(model, CALLS_PROMPT % source, CALL_SCHEMA))
            return answers

        def done(answers):
            if indexes == [None]:
                self.rows = []
                for answer in answers:
                    self.rows.extend(checked_rows(answer, self.desc.toPlainText()))
            else:
                for index, answer in reversed(list(zip(indexes, answers))):
                    built = checked_rows(answer, self.rows[index]["source"])
                    if not built:
                        self.rows[index]["note"] = "AI found no call in this clause."
                    elif len(built) == 1 and built[0]["course"] is not None:
                        self.rows[index].update(course=built[0]["course"], by="AI", note="",
                                                source=built[0]["source"] or self.rows[index]["source"])
                    elif len(built) == 1:
                        self.rows[index]["note"] = built[0]["note"]
                    else:
                        self.rows[index:index + 1] = built
            self._show()

        self._run("Reading %d unread clause(s)" % len(sources), work, done)

    def _sync(self):
        ready = bool(getattr(self, "_courses", None))
        self.reviewed.setEnabled(ready)
        self.load_btn.setEnabled(ready and self.reviewed.isChecked())

    def _load(self):
        courses = getattr(self, "_courses", None)
        if not courses or not self.reviewed.isChecked():
            QMessageBox.information(self, self.windowTitle(), "Check every call against the deed before loading.")
            return
        try:
            self.dock.load_courses(courses, self.units.currentText())
        except ValueError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        self.accept()
