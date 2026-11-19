import os
import re
import sys
import traceback
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextCursor
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
from core.permissions import PermissionManager
from core.planner import CollaborativePlanner
from memory.db import TraccyMemory
from tools.api_tester import test_api_endpoint
from tools.code_scanner import list_project_files, read_code_file
from voice.voice_engine import VoiceEngine


class AIWorker(QThread):
    token_received = Signal(str, str)       # (token_chunk, target_tab)
    sentence_ready = Signal(str, str)       # (complete_sentence, detected_lang)
    finished = Signal(str, str, bool)       # (full_reply, target_tab, is_plan)

    def __init__(
        self,
        brain: TraccyBrain,
        prompt: str,
        target_tab: str = "console",
        stateless: bool = False,
        is_plan: bool = False,
        max_tokens: int = 160,
        detected_lang: str = "en",
    ):
        super().__init__()
        self.brain = brain
        self.prompt = prompt
        self.target_tab = target_tab
        self.stateless = stateless
        self.is_plan = is_plan
        self.max_tokens = max_tokens
        self.detected_lang = detected_lang
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        collected = []
        sentence_buf = ""
        try:
            for token in self.brain.ask_stream(
                self.prompt,
                stateless=self.stateless,
                max_tokens=self.max_tokens,
                detected_lang=self.detected_lang,
            ):
                if self._cancelled:
                    return
                collected.append(token)
                self.token_received.emit(token, self.target_tab)

                # Stream sentences directly to TTS as soon as punctuation arrives
                if self.target_tab == "console":
                    sentence_buf += token
                    parts = re.split(r"(?<=[.!?।])\s+", sentence_buf)
                    if len(parts) > 1:
                        for complete in parts[:-1]:
                            if complete.strip():
                                self.sentence_ready.emit(complete.strip(), self.detected_lang)
                        sentence_buf = parts[-1]
        except Exception as e:
            err = f"[AI ERROR]: {str(e)}"
            collected.append(err)
            if not self._cancelled:
                self.token_received.emit(err, self.target_tab)

        if not self._cancelled:
            if self.target_tab == "console" and sentence_buf.strip():
                self.sentence_ready.emit(sentence_buf.strip(), self.detected_lang)
            self.finished.emit("".join(collected), self.target_tab, self.is_plan)


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
        self.setMinimumSize(170, 170)
        self.angle = 0
        self.active = False
        self.voice_energy = 0
        self._hud_font = QFont("Consolas", 13)
        self._hud_font.setBold(True)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(35)

    def set_voice_energy(self, level: int):
        self.voice_energy = max(0, min(100, level))

    def _tick(self):
        speed = 7 if self.active else 2
        self.angle = (self.angle + speed) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        pulse = int((self.voice_energy / 100.0) * 6)
        radius = min(w, h) // 2 - 16 + pulse

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
        self.setWindowTitle("TRACCY — Conversational AI Voice Companion")
        self.resize(1280, 820)

        self.memory = TraccyMemory()
        self.brain = TraccyBrain(memory=self.memory)
        self.planner = CollaborativePlanner()

        saved_dir = self.memory.get_setting("project_dir", os.getcwd())
        self.project_dir = saved_dir if os.path.isdir(saved_dir) else os.getcwd()
        self.permissions = PermissionManager(approved_project_dir=self.project_dir)

        self.voice = VoiceEngine(self)
        self._active_workers = []
        self.ai_worker = None
        self.api_worker = None
        self._last_user_goal = ""
        self._last_lang = "en"
        self._stream_started = False

        self._apply_theme()
        self._build_ui()
        self._restore_chat_history()

        # Connect Voice Signals
        self.voice.heard_text.connect(self._on_voice_heard)
        self.voice.interim_text.connect(self._on_interim_speech)
        self.voice.status_changed.connect(self._on_voice_status)
        self.voice.stop_triggered.connect(self._handle_stop)
        self.voice.mic_level.connect(self._on_mic_level)

    def _on_mic_level(self, level: int):
        self.mic_bar.setValue(level)
        self.ring.set_voice_energy(level)

    def _on_interim_speech(self, text: str):
        self.live_transcript_label.setText(f"💬 Heard: \"{text}\"")

    def _register_worker(self, worker: QThread):
        self._active_workers.append(worker)
        worker.finished.connect(lambda *_, w=worker: self._cleanup_worker(w))

    def _cleanup_worker(self, worker: QThread):
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        if self.ai_worker is worker:
            self.ai_worker = None
        if self.api_worker is worker:
            self.api_worker = None
        worker.deleteLater()

    def _cancel_current_ai_worker(self):
        if self.ai_worker is not None:
            try:
                if self.ai_worker.isRunning():
                    self.ai_worker.cancel()
            except RuntimeError:
                pass
            self.ai_worker = None

    def closeEvent(self, event):
        self.voice.stop_all()
        self._cancel_current_ai_worker()
        for worker in list(self._active_workers):
            try:
                if hasattr(worker, "cancel"):
                    worker.cancel()
                if worker.isRunning():
                    worker.quit()
                    worker.wait(1500)
            except RuntimeError:
                pass
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
            QFrame#ApprovalBar {
                background-color: #0F172A;
                border: 1px solid #00E5FF;
                border-radius: 8px;
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
            QPushButton#PTTBtn {
                background-color: #0C4A6E;
                color: #E0F2FE;
                border: 1px solid #00E5FF;
                padding: 10px;
                font-size: 13px;
            }
            QPushButton#PTTBtn:pressed {
                background-color: #0369A1;
            }
            QPushButton#ApproveBtn {
                background-color: #14532D;
                color: #BBF7D0;
                border: 1px solid #22C55E;
            }
            QPushButton#StopBtn {
                background-color: #7F1D1D;
                color: #FECACA;
                border: 1px solid #EF4444;
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

        # --- TOP STATUS BAR ---
        top_bar = QFrame()
        top_bar.setObjectName("Card")
        top_bar.setMaximumHeight(54)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 6, 14, 6)

        title = QLabel("TRACCY // CONVERSATIONAL AI COMPANION")
        title.setStyleSheet("color: #00E5FF; font-family: 'Consolas'; font-size: 14px; font-weight: bold;")

        self.status_badge = QLabel("● READY  |  QWEN2.5 + WHISPER + NEURAL VOICE")
        self.status_badge.setStyleSheet("color: #22C55E; font-family: 'Consolas'; font-size: 12px;")

        stop_btn = QPushButton("■ STOP SPEAKING")
        stop_btn.setObjectName("StopBtn")
        stop_btn.clicked.connect(self._handle_stop)

        top_layout.addWidget(title)
        top_layout.addStretch()
        top_layout.addWidget(self.status_badge)
        top_layout.addSpacing(12)
        top_layout.addWidget(stop_btn)
        main_layout.addWidget(top_bar)

        # --- MAIN SPLITTER ---
        splitter = QSplitter(Qt.Horizontal)

        # LEFT: VOICE HUD
        hud_card = QFrame()
        hud_card.setObjectName("Card")
        hud_layout = QVBoxLayout(hud_card)

        hud_header = QLabel("CONVERSATIONAL VOICE HUD")
        hud_header.setStyleSheet("color: #38BDF8; font-family: 'Consolas'; font-weight: bold;")
        hud_header.setAlignment(Qt.AlignCenter)

        self.ring = ArcReactorRing()

        self.voice_status = QLabel("Voice Engine: Standby")
        self.voice_status.setAlignment(Qt.AlignCenter)
        self.voice_status.setWordWrap(True)
        self.voice_status.setStyleSheet("color: #94A3B8; font-size: 12px;")

        self.live_transcript_label = QLabel("💬 Heard: —")
        self.live_transcript_label.setAlignment(Qt.AlignCenter)
        self.live_transcript_label.setWordWrap(True)
        self.live_transcript_label.setStyleSheet("color: #38BDF8; font-size: 12px; font-style: italic;")

        self.mic_bar = QProgressBar()
        self.mic_bar.setRange(0, 100)
        self.mic_bar.setValue(0)
        self.mic_bar.setTextVisible(False)
        self.mic_bar.setMaximumHeight(8)
        self.mic_bar.setStyleSheet(
            "QProgressBar { background-color: #0F172A; border-radius: 4px; }"
            "QProgressBar::chunk { background-color: #22C55E; border-radius: 4px; }"
        )

        # Push-to-Talk Button (Hold to speak, release to send)
        self.ptt_btn = QPushButton("🎤 Hold to Talk (Push-to-Talk)")
        self.ptt_btn.setObjectName("PTTBtn")
        self.ptt_btn.pressed.connect(self.voice.begin_push_to_talk)
        self.ptt_btn.released.connect(self.voice.end_push_to_talk)

        # Hands-Free Continuous Conversation Toggle
        self.listen_btn = QPushButton("🎙 Start Hands-Free Conversation")
        self.listen_btn.clicked.connect(self._toggle_voice)

        self.tts_checkbox = QCheckBox("🔊 Speak Replies Aloud")
        self.tts_checkbox.setChecked(True)
        self.tts_checkbox.setStyleSheet("color: #CBD5E1; font-size: 12px;")

        self.cloud_checkbox = QCheckBox("⚡ Hybrid Cloud Brain (If API Key Set)")
        self.cloud_checkbox.setChecked(False)
        self.cloud_checkbox.setStyleSheet("color: #CBD5E1; font-size: 12px;")
        self.cloud_checkbox.toggled.connect(self._toggle_hybrid_cloud)

        btn_clear_chat = QPushButton("🧹 New Conversation Topic")
        btn_clear_chat.clicked.connect(self._clear_context)

        quick_label = QLabel("ENGINEERING TOOLS")
        quick_label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: bold; margin-top: 8px;")

        btn_select_proj = QPushButton("📂 Select Approved Project Folder")
        btn_select_proj.clicked.connect(self._choose_project_folder)

        btn_bug = QPushButton("🐞 Open Bug Scanner Tab")
        btn_bug.clicked.connect(lambda: self.tabs.setCurrentIndex(1))

        btn_api = QPushButton("⚡ Open API & Latency Tester")
        btn_api.clicked.connect(lambda: self.tabs.setCurrentIndex(2))

        hud_layout.addWidget(hud_header)
        hud_layout.addWidget(self.ring)
        hud_layout.addWidget(self.voice_status)
        hud_layout.addWidget(self.live_transcript_label)
        hud_layout.addWidget(self.mic_bar)
        hud_layout.addWidget(self.ptt_btn)
        hud_layout.addWidget(self.listen_btn)
        hud_layout.addWidget(self.tts_checkbox)
        hud_layout.addWidget(self.cloud_checkbox)
        hud_layout.addWidget(btn_clear_chat)
        hud_layout.addWidget(quick_label)
        hud_layout.addWidget(btn_select_proj)
        hud_layout.addWidget(btn_bug)
        hud_layout.addWidget(btn_api)
        hud_layout.addStretch()

        # RIGHT: WORKSPACE TABS
        dev_card = QFrame()
        dev_card.setObjectName("Card")
        dev_layout = QVBoxLayout(dev_card)

        self.tabs = QTabWidget()

        # TAB 1: Conversational Console
        console_tab = QWidget()
        console_layout = QVBoxLayout(console_tab)

        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)

        self.approval_bar = QFrame()
        self.approval_bar.setObjectName("ApprovalBar")
        self.approval_bar.setVisible(False)
        approval_layout = QHBoxLayout(self.approval_bar)
        approval_layout.setContentsMargins(12, 8, 12, 8)

        self.approval_label = QLabel("📋 PLAN AWAITING YOUR APPROVAL")
        self.approval_label.setStyleSheet("color: #00E5FF; font-family: 'Consolas'; font-weight: bold;")

        btn_approve = QPushButton("✅ Approve & Execute Plan")
        btn_approve.setObjectName("ApproveBtn")
        btn_approve.clicked.connect(self._approve_active_plan)

        btn_reject = QPushButton("✖ Reject / Modify")
        btn_reject.setObjectName("StopBtn")
        btn_reject.clicked.connect(self._reject_active_plan)

        approval_layout.addWidget(self.approval_label)
        approval_layout.addStretch()
        approval_layout.addWidget(btn_approve)
        approval_layout.addWidget(btn_reject)

        input_row = QHBoxLayout()
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("Talk naturally in English or Gujarati, or type your thought...")
        self.prompt_input.returnPressed.connect(self._send_command)

        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send_command)

        input_row.addWidget(self.prompt_input)
        input_row.addWidget(send_btn)

        console_layout.addWidget(self.chat_log)
        console_layout.addWidget(self.approval_bar)
        console_layout.addLayout(input_row)

        # TAB 2: Code & Bug Scanner
        scanner_tab = QWidget()
        scanner_layout = QVBoxLayout(scanner_tab)

        proj_row = QHBoxLayout()
        self.proj_label = QLabel(f"Approved Folder: {self.project_dir}")
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

        # TAB 3: API Tester & Latency Benchmarker
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
        self.token_input.setPlaceholderText("Optional JWT Bearer Token...")

        self.body_input = QLineEdit()
        self.body_input.setPlaceholderText('Optional JSON Body...')

        self.perf_log = QTextEdit()
        self.perf_log.setReadOnly(True)
        self.perf_log.setText(
            "=== LIVE API TESTER & LATENCY BENCHMARK ===\n"
            "Enter an endpoint URL and click 'Run Test & Benchmark'."
        )

        api_layout.addLayout(url_row)
        api_layout.addWidget(self.token_input)
        api_layout.addWidget(self.body_input)
        api_layout.addWidget(self.perf_log)

        self.tabs.addTab(console_tab, "Conversational Console")
        self.tabs.addTab(scanner_tab, "Bug & Code Scanner")
        self.tabs.addTab(api_tab, "API & Performance Tester")

        dev_layout.addWidget(self.tabs)

        splitter.addWidget(hud_card)
        splitter.addWidget(dev_card)
        splitter.setSizes([320, 960])

        main_layout.addWidget(splitter)

    def _toggle_hybrid_cloud(self, checked: bool):
        if checked and not self.brain.groq_api_key:
            self.cloud_checkbox.setChecked(False)
            self.chat_log.append(
                "[SYSTEM]: Set GROQ_API_KEY in Windows Environment Variables to enable Hybrid Cloud mode.\n"
            )
            return
        self.brain.hybrid_cloud_enabled = checked

    def _clear_context(self):
        self.brain.clear_memory()
        self.planner.reject_plan()
        self.approval_bar.setVisible(False)
        self.live_transcript_label.setText("💬 Heard: —")
        self.chat_log.clear()
        self.chat_log.append("[TRACCY]: Fresh topic started. What's on your mind?\n")

    def _restore_chat_history(self):
        past = self.memory.get_recent_messages(limit=4)
        if past:
            self.chat_log.append("[MEMORY]: Previous session loaded.\n")
            for msg in past:
                prefix = "[YOU]" if msg["role"] == "user" else "[TRACCY]"
                self.chat_log.append(f"{prefix}: {msg['content']}\n")
        else:
            self.chat_log.append(
                "[TRACCY]: Conversational Voice Companion online. Hold Push-to-Talk or start Hands-Free conversation.\n"
            )

    def _choose_project_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Approved Project Folder", self.project_dir)
        if folder:
            self.project_dir = folder
            self.permissions.set_approved_project(folder)
            self.memory.set_setting("project_dir", folder)
            self.proj_label.setText(f"Approved Folder: {self.project_dir}")
            self._refresh_file_list()
            self.chat_log.append(f"[PERMISSIONS]: Approved workspace set to '{self.project_dir}'.\n")

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

        allowed, reason = self.permissions.check_action("ANALYZE", full_path)
        if not allowed:
            self.diag_log.setText(f"[PERMISSION BLOCKED]: {reason}")
            return

        self._cancel_current_ai_worker()
        self._stream_started = False

        code = read_code_file(full_path)
        filename = os.path.basename(full_path)
        self.diag_log.setText(f"Scanning {filename}...\nPlease wait...")
        self._set_busy(True, f"SCANNING {filename.upper()}")

        prompt = (
            f"Analyze this file ({filename}) for:\n"
            "1. Critical bugs, null/edge-case risks, or logic errors\n"
            "2. Security vulnerabilities\n"
            "3. Concrete code improvements\n\n"
            f"CODE:\n```\n{code}\n```"
        )
        self.ai_worker = AIWorker(
            self.brain, prompt, target_tab="diag", stateless=True, is_plan=False, max_tokens=900
        )
        self._register_worker(self.ai_worker)
        self.ai_worker.token_received.connect(self._on_ai_token)
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
            self.status_badge.setText("● READY  |  QWEN2.5 + WHISPER + NEURAL VOICE")
            self.status_badge.setStyleSheet("color: #22C55E; font-family: 'Consolas'; font-size: 12px;")

    def _toggle_voice(self):
        if not self.voice.listening:
            self.voice.start_listening()
            self.ring.active = True
            self.listen_btn.setText("⏹ Stop Hands-Free Conversation")
        else:
            self.voice.stop_all()
            self.ring.active = False
            self.listen_btn.setText("🎙 Start Hands-Free Conversation")

    def _on_voice_status(self, text: str, color: str):
        self.voice_status.setText(text)
        self.voice_status.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 12px;")
        self.ring.active = self.voice.listening or self.voice.speaking

    def _on_voice_heard(self, spoken_text: str, detected_lang: str):
        self.tabs.setCurrentIndex(0)
        self._last_lang = detected_lang
        self.prompt_input.setText(spoken_text)
        self._send_command(detected_lang=detected_lang)

    def _handle_stop(self):
        self._cancel_current_ai_worker()
        self.voice.interrupt_speech_only()
        self._set_busy(False)
        self.status_badge.setText("● INTERRUPTED  |  READY TO LISTEN")
        self.status_badge.setStyleSheet("color: #38BDF8; font-family: 'Consolas'; font-size: 12px;")

    def _approve_active_plan(self):
        if self.planner.current_plan_id is not None:
            self.memory.update_plan_status(self.planner.current_plan_id, "APPROVED")
        self.planner.approve_plan()
        self.permissions.active_plan_approved = True
        self.approval_bar.setVisible(False)

        self._cancel_current_ai_worker()
        self._stream_started = False

        self.chat_log.append(
            f"[PERMISSIONS]: Plan #{self.planner.current_plan_id} APPROVED for '{self.project_dir}'. Executing..."
        )
        exec_prompt = (
            f"The user has APPROVED your plan for: \"{self.planner.current_goal}\".\n"
            f"Approved workspace: {self.project_dir}\n"
            "Now provide the complete implementation code and verification steps clearly."
        )
        self._set_busy(True, "EXECUTING APPROVED PLAN")
        self.ai_worker = AIWorker(
            self.brain,
            exec_prompt,
            target_tab="console",
            stateless=False,
            is_plan=False,
            max_tokens=1200,
            detected_lang=self._last_lang,
        )
        self._register_worker(self.ai_worker)
        self.ai_worker.token_received.connect(self._on_ai_token)
        self.ai_worker.finished.connect(self._on_ai_reply)
        self.ai_worker.start()

    def _reject_active_plan(self):
        if self.planner.current_plan_id is not None:
            self.memory.update_plan_status(self.planner.current_plan_id, "REJECTED")
        self.planner.reject_plan()
        self.permissions.active_plan_approved = False
        self.approval_bar.setVisible(False)
        msg = "Plan cancelled. Tell me what you would like to change."
        self.chat_log.append(f"[TRACCY]: {msg}\n")
        if self.tts_checkbox.isChecked():
            self.voice.speak(msg, self._last_lang)

    def _send_command(self, detected_lang: str = "en"):
        text = self.prompt_input.text().strip()
        if not text:
            return
        self.prompt_input.clear()
        self.voice.interrupt_speech_only()
        self.chat_log.append(f"[YOU]: {text}")

        lower = text.lower()
        if lower in {"stop", "traccy stop"}:
            self._handle_stop()
            return

        if self.planner.status == "AWAITING_APPROVAL":
            if lower in {"approve", "approve plan", "yes approve", "execute", "go ahead", "start"}:
                self._approve_active_plan()
                return
            if lower in {"reject", "cancel plan", "no"}:
                self._reject_active_plan()
                return

        self._cancel_current_ai_worker()
        self._stream_started = False

        self._last_user_goal = text
        is_plan_req = self.brain.context.should_trigger_formal_plan(text)
        prompt_to_send = (
            self.planner.build_planning_prompt(text, self.project_dir) if is_plan_req else text
        )

        self._set_busy(True, "PLANNING" if is_plan_req else "THINKING")
        self.ai_worker = AIWorker(
            self.brain,
            prompt_to_send,
            target_tab="console",
            stateless=False,
            is_plan=is_plan_req,
            max_tokens=160,
            detected_lang=detected_lang,
        )
        self._register_worker(self.ai_worker)
        self.ai_worker.token_received.connect(self._on_ai_token)
        self.ai_worker.sentence_ready.connect(self._on_ai_sentence)
        self.ai_worker.finished.connect(self._on_ai_reply)
        self.ai_worker.start()

    def _on_ai_token(self, token: str, target_tab: str):
        box = self.diag_log if target_tab == "diag" else self.chat_log
        if not self._stream_started:
            self._stream_started = True
            if target_tab == "diag":
                box.clear()
            else:
                box.append("[TRACCY]: ")
        cursor = box.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(token)
        box.setTextCursor(cursor)
        box.ensureCursorVisible()

    def _on_ai_sentence(self, sentence: str, lang: str):
        if self.tts_checkbox.isChecked():
            self.voice.speak_sentence(sentence, lang)

    def _on_ai_reply(self, reply: str, target_tab: str, is_plan: bool):
        self._set_busy(False)
        if target_tab == "diag":
            if not self._stream_started:
                self.diag_log.setText(reply)
            if self.tts_checkbox.isChecked():
                self.voice.speak("Code scan complete. Summary is ready on screen.")
            return

        if not self._stream_started:
            self.chat_log.append(f"[TRACCY]: {reply}")
        self.chat_log.append("")

        if is_plan:
            plan_id = self.memory.save_plan(self._last_user_goal, reply, self.project_dir)
            self.planner.set_pending_plan(plan_id, self._last_user_goal, reply)
            self.approval_label.setText(f"📋 PLAN #{plan_id} READY — AWAITING YOUR APPROVAL")
            self.approval_bar.setVisible(True)


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