import os
import sys
import traceback
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from core.brain import TraccyBrain
from tools.api_tester import test_api_endpoint
from tools.code_scanner import list_project_files, read_code_file
from voice.voice_engine import VoiceEngine


class AIWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, brain: TraccyBrain, prompt: str, target_tab: str = "console", stateless: bool = False):
        super().__init__()
        self.brain = brain
        self.prompt = prompt
        self.target_tab = target_tab
        self.stateless = stateless
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            reply = self.brain.ask(self.prompt, stateless=self.stateless)
        except Exception as e:
            reply = f"[AI ERROR]: {str(e)}"
        if not self._cancelled:
            self.finished.emit(reply, self.target_tab)


class APIWorker(QThread):
    finished = Signal(dict)

    def __init__(self, method: str, url: str, token: str, body: str, runs: int):
        super().__init__()
        self.method = method
        self.url = url
        self.token = token
        self.body = body
        self.runs = runs

    def run(self):
        try:
            result = test_api_endpoint(self.method, self.url, self.token, self.body, self.runs)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        self.finished.emit(result)


class ArcReactorRing(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self.angle = 0
        self.active = False
        self._hud_font = QFont("Consolas", 13)
        self._hud_font.setBold(True)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(35)

    def _tick(self):
        speed = 7 if self.active else 2
        self.angle = (self.angle + speed) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        radius = min(w, h) // 2 - 14

        pen_outer = QPen(QColor("#00E5FF"), 3)
        painter.setPen(pen_outer)
        painter.drawArc(cx - radius, cy - radius, radius * 2, radius * 2, self.angle * 16, 110 * 16)
        painter.drawArc(cx - radius, cy - radius, radius * 2, radius * 2, (self.angle + 180) * 16, 110 * 16)

        inner_r = int(radius * 0.72)
        pen_inner = QPen(QColor("#22C55E") if self.active else QColor("#38BDF8"), 2)
        painter.setPen(pen_inner)
        painter.drawArc(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2, -self.angle * 24, 240 * 16)

        painter.setPen(QColor("#E0F2FE"))
        painter.setFont(self._hud_font)
        painter.drawText(self.rect(), Qt.AlignCenter, "TRACCY")


class TraccyDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TRACCY — Personal AI Operating System")
        self.resize(1280, 780)

        self.brain = TraccyBrain()
        self.voice = VoiceEngine(self)
        self._active_workers = []
        self.ai_worker = None
        self.api_worker = None
        self.project_dir = os.getcwd()

        self._apply_theme()
        self._build_ui()

        # Connect Voice Signals safely after UI creation
        self.voice.heard_text.connect(self._on_voice_heard)
        self.voice.status_changed.connect(self._on_voice_status)
        self.voice.stop_triggered.connect(self._handle_stop)
        self.voice.mic_level.connect(self.mic_bar.setValue)

    def _register_worker(self, worker: QThread):
        self._active_workers.append(worker)
        worker.finished.connect(lambda *_, w=worker: self._cleanup_worker(w))

    def _cleanup_worker(self, worker: QThread):
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        worker.deleteLater()

    def closeEvent(self, event):
        """Cleanly halts voice streams and background QThreads when closing the window."""
        self.voice.stop_all()
        for worker in list(self._active_workers):
            if hasattr(worker, "cancel"):
                worker.cancel()
            if worker.isRunning():
                worker.quit()
                worker.wait(1500)
        event.accept()

    def _apply_theme(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #0B1120;
                color: #E2E8F0;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
            }
            QFrame#Card {
                background-color: #111827;
                border: 1px solid #1E293B;
                border-radius: 10px;
            }
            QPushButton {
                background-color: #1E293B;
                color: #38BDF8;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #0F172A;
                border-color: #00E5FF;
            }
            QPushButton#StopBtn {
                background-color: #7F1D1D;
                color: #FECACA;
                border: 1px solid #EF4444;
            }
            QPushButton#StopBtn:hover {
                background-color: #991B1B;
            }
            QLineEdit, QTextEdit, QComboBox, QSpinBox {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #1E293B;
                border-radius: 6px;
                padding: 7px;
                font-family: 'Consolas', monospace;
                font-size: 13px;
            }
            QTabWidget::pane {
                border: 1px solid #1E293B;
                border-radius: 6px;
            }
            QTabBar::tab {
                background: #111827;
                color: #94A3B8;
                padding: 8px 16px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #1E293B;
                color: #00E5FF;
                font-weight: bold;
            }
            """
        )

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # --- COMPACT TOP BAR ---
        top_bar = QFrame()
        top_bar.setObjectName("Card")
        top_bar.setMaximumHeight(54)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 6, 14, 6)

        title = QLabel("TRACCY // PERSONAL AI OPERATING SYSTEM")
        title.setStyleSheet("color: #00E5FF; font-family: 'Consolas'; font-size: 14px; font-weight: bold;")

        self.status_badge = QLabel("● READY  |  VOICE + CODE SCANNER + API BENCHMARK")
        self.status_badge.setStyleSheet("color: #22C55E; font-family: 'Consolas'; font-size: 12px;")

        stop_btn = QPushButton("■ STOP")
        stop_btn.setObjectName("StopBtn")
        stop_btn.clicked.connect(self._handle_stop)

        top_layout.addWidget(title)
        top_layout.addStretch()
        top_layout.addWidget(self.status_badge)
        top_layout.addSpacing(12)
        top_layout.addWidget(stop_btn)
        main_layout.addWidget(top_bar)

        # --- MAIN HYBRID SPLITTER ---
        splitter = QSplitter(Qt.Horizontal)

        # LEFT: HUD
        hud_card = QFrame()
        hud_card.setObjectName("Card")
        hud_layout = QVBoxLayout(hud_card)

        hud_header = QLabel("VOICE & DIAGNOSTIC HUD")
        hud_header.setStyleSheet("color: #38BDF8; font-family: 'Consolas'; font-weight: bold;")
        hud_header.setAlignment(Qt.AlignCenter)

        self.ring = ArcReactorRing()

        self.voice_status = QLabel("Voice Engine: Standby")
        self.voice_status.setAlignment(Qt.AlignCenter)
        self.voice_status.setWordWrap(True)
        self.voice_status.setStyleSheet("color: #94A3B8; font-size: 12px;")

        self.mic_bar = QProgressBar()
        self.mic_bar.setRange(0, 100)
        self.mic_bar.setValue(0)
        self.mic_bar.setTextVisible(False)
        self.mic_bar.setMaximumHeight(8)
        self.mic_bar.setStyleSheet(
            "QProgressBar { background-color: #0F172A; border-radius: 4px; }"
            "QProgressBar::chunk { background-color: #22C55E; border-radius: 4px; }"
        )

        self.listen_btn = QPushButton("🎙 Start Voice Listening")
        self.listen_btn.clicked.connect(self._toggle_voice)

        self.tts_checkbox = QCheckBox("🔊 Speak Replies Aloud")
        self.tts_checkbox.setChecked(True)
        self.tts_checkbox.setStyleSheet("color: #CBD5E1; font-size: 12px;")

        quick_label = QLabel("QUICK ENGINEERING ACTIONS")
        quick_label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: bold; margin-top: 10px;")

        btn_select_proj = QPushButton("📂 Select Project Folder")
        btn_select_proj.clicked.connect(self._choose_project_folder)

        btn_bug = QPushButton("🐞 Open Bug Scanner Tab")
        btn_bug.clicked.connect(lambda: self.tabs.setCurrentIndex(1))

        btn_api = QPushButton("⚡ Open API & Latency Tester")
        btn_api.clicked.connect(lambda: self.tabs.setCurrentIndex(2))

        hud_layout.addWidget(hud_header)
        hud_layout.addWidget(self.ring)
        hud_layout.addWidget(self.voice_status)
        hud_layout.addWidget(self.mic_bar)
        hud_layout.addWidget(self.listen_btn)
        hud_layout.addWidget(self.tts_checkbox)
        hud_layout.addWidget(quick_label)
        hud_layout.addWidget(btn_select_proj)
        hud_layout.addWidget(btn_bug)
        hud_layout.addWidget(btn_api)
        hud_layout.addStretch()

        # RIGHT: Workspace
        dev_card = QFrame()
        dev_card.setObjectName("Card")
        dev_layout = QVBoxLayout(dev_card)

        self.tabs = QTabWidget()

        # TAB 1: Command Console
        console_tab = QWidget()
        console_layout = QVBoxLayout(console_tab)

        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_log.append("[TRACCY]: System online and stabilized. Speak or type a command below.\n")

        input_row = QHBoxLayout()
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("Ask TRACCY anything or click 'Start Voice Listening'...")
        self.prompt_input.returnPressed.connect(self._send_command)

        send_btn = QPushButton("Execute")
        send_btn.clicked.connect(self._send_command)

        input_row.addWidget(self.prompt_input)
        input_row.addWidget(send_btn)

        console_layout.addWidget(self.chat_log)
        console_layout.addLayout(input_row)

        # TAB 2: Real Code & Bug Scanner
        scanner_tab = QWidget()
        scanner_layout = QVBoxLayout(scanner_tab)

        proj_row = QHBoxLayout()
        self.proj_label = QLabel(f"Project: {self.project_dir}")
        self.proj_label.setStyleSheet("color: #38BDF8; font-family: 'Consolas'; font-size: 12px;")
        browse_btn = QPushButton("Browse Folder")
        browse_btn.clicked.connect(self._choose_project_folder)
        proj_row.addWidget(self.proj_label)
        proj_row.addStretch()
        proj_row.addWidget(browse_btn)

        file_row = QHBoxLayout()
        self.file_combo = QComboBox()
        self._refresh_file_list()
        scan_btn = QPushButton("🐞 Analyze File for Bugs & Improvements")
        scan_btn.clicked.connect(self._scan_selected_file)
        file_row.addWidget(self.file_combo, stretch=1)
        file_row.addWidget(scan_btn)

        self.diag_log = QTextEdit()
        self.diag_log.setReadOnly(True)
        self.diag_log.setText(
            "=== REAL CODE & BUG SCANNER ===\n"
            "1. Click 'Browse Folder' and select FarmerExpenseTracker (or any project).\n"
            "2. Choose a file from the dropdown.\n"
            "3. Click 'Analyze File for Bugs & Improvements'."
        )

        scanner_layout.addLayout(proj_row)
        scanner_layout.addLayout(file_row)
        scanner_layout.addWidget(self.diag_log)

        # TAB 3: Live API Tester & Latency Benchmarker
        api_tab = QWidget()
        api_layout = QVBoxLayout(api_tab)

        url_row = QHBoxLayout()
        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE"])
        self.url_input = QLineEdit("https://farmexpenseapp.duckdns.org/api/farm_expense")
        self.runs_spin = QSpinBox()
        self.runs_spin.setRange(1, 50)
        self.runs_spin.setValue(3)
        self.runs_spin.setPrefix("Runs: ")
        run_api_btn = QPushButton("⚡ Run Test & Benchmark")
        run_api_btn.clicked.connect(self._run_api_benchmark)

        url_row.addWidget(self.method_combo)
        url_row.addWidget(self.url_input, stretch=1)
        url_row.addWidget(self.runs_spin)
        url_row.addWidget(run_api_btn)

        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Optional JWT Bearer Token (paste token without 'Bearer ')...")

        self.body_input = QLineEdit()
        self.body_input.setPlaceholderText('Optional JSON Body (e.g., {"email": "test@example.com", "password": "123"})...')

        self.perf_log = QTextEdit()
        self.perf_log.setReadOnly(True)
        self.perf_log.setText(
            "=== LIVE API TESTER & LATENCY BENCHMARK ===\n"
            "Enter an endpoint URL and click 'Run Test & Benchmark' to measure status codes and avg/min/max latency."
        )

        api_layout.addLayout(url_row)
        api_layout.addWidget(self.token_input)
        api_layout.addWidget(self.body_input)
        api_layout.addWidget(self.perf_log)

        self.tabs.addTab(console_tab, "Command Console")
        self.tabs.addTab(scanner_tab, "Bug & Code Scanner")
        self.tabs.addTab(api_tab, "API & Performance Tester")

        dev_layout.addWidget(self.tabs)

        splitter.addWidget(hud_card)
        splitter.addWidget(dev_card)
        splitter.setSizes([310, 970])

        main_layout.addWidget(splitter)

    def _choose_project_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Target Project Folder", self.project_dir)
        if folder:
            self.project_dir = folder
            self.proj_label.setText(f"Project: {self.project_dir}")
            self._refresh_file_list()
            self.tabs.setCurrentIndex(1)

    def _refresh_file_list(self):
        self.file_combo.clear()
        files = list_project_files(self.project_dir)
        if not files:
            self.file_combo.addItem("No supported source files found in this folder")
            return
        for f in files:
            rel = os.path.relpath(f, self.project_dir)
            self.file_combo.addItem(rel, userData=f)

    def _scan_selected_file(self):
        full_path = self.file_combo.currentData()
        if not full_path:
            self.diag_log.setText("[ERROR]: Select a valid project folder with source files first.")
            return

        code = read_code_file(full_path)
        filename = os.path.basename(full_path)
        self.diag_log.setText(f"Scanning {filename} with TRACCY AI Core...\nPlease wait...")
        self._set_busy(True, f"SCANNING {filename.upper()}")

        prompt = (
            f"Analyze this file ({filename}) for:\n"
            "1. Critical bugs, null/edge-case risks, or logic errors\n"
            "2. Security vulnerabilities (SQL injection, auth flaws, exposed secrets)\n"
            "3. Concrete code improvements with fixed snippets\n\n"
            f"CODE:\n```\n{code}\n```"
        )
        self.ai_worker = AIWorker(self.brain, prompt, target_tab="diag", stateless=True)
        self._register_worker(self.ai_worker)
        self.ai_worker.finished.connect(self._on_ai_reply)
        self.ai_worker.start()

    def _run_api_benchmark(self):
        url = self.url_input.text().strip()
        if not url:
            return
        self._set_busy(True, "BENCHMARKING API")
        self.perf_log.setText(f"Running {self.runs_spin.value()}x {self.method_combo.currentText()} requests to {url}...")

        self.api_worker = APIWorker(
            self.method_combo.currentText(),
            url,
            self.token_input.text(),
            self.body_input.text(),
            self.runs_spin.value(),
        )
        self._register_worker(self.api_worker)
        self.api_worker.finished.connect(self._on_api_result)
        self.api_worker.start()

    def _on_api_result(self, res: dict):
        self._set_busy(False)
        if not res.get("ok"):
            self.perf_log.setText(f"[API ERROR]: {res.get('error')}")
            return

        report = (
            f"=== API BENCHMARK REPORT ===\n"
            f"Endpoint : {res['method']} {res['url']}\n"
            f"HTTP Code: {res['status']}\n"
            f"Requests : {res['runs']} runs\n"
            f"Avg Time : {res['avg_ms']} ms\n"
            f"Min / Max: {res['min_ms']} ms  /  {res['max_ms']} ms\n"
            f"\n--- RESPONSE PREVIEW ---\n{res['body']}"
        )
        self.perf_log.setText(report)
        if self.tts_checkbox.isChecked():
            self.voice.speak(f"API returned status {res['status']}. Average latency is {res['avg_ms']} milliseconds.")

    def _set_busy(self, busy: bool, label: str = "THINKING"):
        self.ring.active = busy or self.voice.listening
        if busy:
            self.status_badge.setText(f"● {label}...")
            self.status_badge.setStyleSheet("color: #F59E0B; font-family: 'Consolas'; font-size: 12px;")
        else:
            self.status_badge.setText("● READY  |  VOICE + CODE SCANNER + API BENCHMARK")
            self.status_badge.setStyleSheet("color: #22C55E; font-family: 'Consolas'; font-size: 12px;")

    def _toggle_voice(self):
        if not self.voice.listening:
            self.voice.start_listening()
            self.ring.active = True
            self.listen_btn.setText("⏹ Stop Voice Listening")
        else:
            self.voice.stop_all()
            self.ring.active = False
            self.listen_btn.setText("🎙 Start Voice Listening")

    def _on_voice_status(self, text: str, color: str):
        self.voice_status.setText(text)
        self.voice_status.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 12px;")
        self.ring.active = self.voice.listening or self.voice.speaking

    def _on_voice_heard(self, spoken_text: str):
        self.tabs.setCurrentIndex(0)
        self.prompt_input.setText(spoken_text)
        self._send_command()

    def _handle_stop(self):
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
        self.voice.stop_all()
        self.listen_btn.setText("🎙 Start Voice Listening")
        self._set_busy(False)
        self.status_badge.setText("● STOPPED  |  EXECUTION & VOICE HALTED")
        self.status_badge.setStyleSheet("color: #EF4444; font-family: 'Consolas'; font-size: 12px;")
        self.chat_log.append("[SYSTEM]: Voice and execution halted immediately.\n")

    def _send_command(self):
        text = self.prompt_input.text().strip()
        if not text:
            return
        self.prompt_input.clear()
        self.chat_log.append(f"[YOU]: {text}")

        if text.lower() in {"stop", "traccy stop"}:
            self._handle_stop()
            return

        self._set_busy(True, "ANALYZING")
        self.ai_worker = AIWorker(self.brain, text, target_tab="console", stateless=False)
        self._register_worker(self.ai_worker)
        self.ai_worker.finished.connect(self._on_ai_reply)
        self.ai_worker.start()

    def _on_ai_reply(self, reply: str, target_tab: str):
        self._set_busy(False)
        if target_tab == "diag":
            self.diag_log.setText(reply)
            if self.tts_checkbox.isChecked():
                self.voice.speak("Code scan complete. Summary is ready on screen.")
        else:
            self.chat_log.append(f"[TRACCY]:\n{reply}\n")
            if self.tts_checkbox.isChecked():
                self.voice.speak(reply)


def main():
    app = QApplication(sys.argv)
    try:
        window = TraccyDashboard()
        window.show()
        sys.exit(app.exec())
    except Exception:
        traceback.print_exc()
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()