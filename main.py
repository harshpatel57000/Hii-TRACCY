import sys
import random
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ArcReactorRing(QWidget):
    """Futuristic animated HUD core ring for TRACCY."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.angle = 0
        self.active = False

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(35)

    def _tick(self):
        speed = 6 if self.active else 2
        self.angle = (self.angle + speed) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        radius = min(w, h) // 2 - 16

        # Outer segmented ring
        pen_outer = QPen(QColor("#00E5FF"), 3)
        painter.setPen(pen_outer)
        painter.drawArc(
            cx - radius, cy - radius, radius * 2, radius * 2, self.angle * 16, 110 * 16
        )
        painter.drawArc(
            cx - radius,
            cy - radius,
            radius * 2,
            radius * 2,
            (self.angle + 180) * 16,
            110 * 16,
        )

        # Inner counter-rotating ring
        inner_r = int(radius * 0.72)
        pen_inner = QPen(QColor("#38BDF8") if not self.active else QColor("#22C55E"), 2)
        painter.setPen(pen_inner)
        painter.drawArc(
            cx - inner_r,
            cy - inner_r,
            inner_r * 2,
            inner_r * 2,
            -self.angle * 24,
            240 * 16,
        )

        # Center label
        painter.setPen(QColor("#E0F2FE"))
        painter.setFont(QFont("Consolas", 13, QFont.Bold))
        painter.drawText(self.rect(), Qt.AlignCenter, "TRACCY")


class TraccyDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TRACCY — AI Engineering & Operations Assistant")
        self.resize(1240, 760)
        self._apply_theme()
        self._build_ui()

    def _apply_theme(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #0B1120;
                color: #E2E8F0;
                font-family: 'Segoe UI', sans-serif;
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
                padding: 8px 14px;
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
            QLineEdit, QTextEdit {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #1E293B;
                border-radius: 6px;
                padding: 8px;
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
        main_layout.setSpacing(12)

        # --- TOP BAR ---
        top_bar = QFrame()
        top_bar.setObjectName("Card")
        top_layout = QHBoxLayout(top_bar)

        title = QLabel("TRACCY // HYBRID ENGINEERING CORE")
        title.setStyleSheet("color: #00E5FF; font-family: 'Consolas'; font-size: 15px; font-weight: bold;")

        self.status_badge = QLabel("● READY  |  LOCAL: OLLAMA  |  TARGET: FARMER EXPENSE TRACKER")
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

        # LEFT: Iron Man HUD & Quick Actions
        hud_card = QFrame()
        hud_card.setObjectName("Card")
        hud_layout = QVBoxLayout(hud_card)

        hud_header = QLabel("VOICE & DIAGNOSTIC HUD")
        hud_header.setStyleSheet("color: #38BDF8; font-family: 'Consolas'; font-weight: bold;")
        hud_header.setAlignment(Qt.AlignCenter)

        self.ring = ArcReactorRing()

        self.voice_status = QLabel("Voice Engine: Standby (Click Listen or Type)")
        self.voice_status.setAlignment(Qt.AlignCenter)
        self.voice_status.setStyleSheet("color: #94A3B8; font-size: 12px;")

        listen_btn = QPushButton("🎙 Start Voice Listening")
        listen_btn.clicked.connect(self._toggle_voice)

        quick_label = QLabel("QUICK ENGINEERING ACTIONS")
        quick_label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: bold; margin-top: 10px;")

        btn_bug = QPushButton("🐞 Scan Project for Bugs")
        btn_bug.clicked.connect(lambda: self._run_quick_task("Scan FarmerExpenseTracker for bugs and code smells"))

        btn_api = QPushButton("⚡ Run API Health & Endpoint Tests")
        btn_api.clicked.connect(lambda: self._run_quick_task("Test Spring Boot REST API endpoints"))

        btn_perf = QPushButton("📊 Benchmark API Performance")
        btn_perf.clicked.connect(lambda: self._run_quick_task("Check API latency and system performance"))

        hud_layout.addWidget(hud_header)
        hud_layout.addWidget(self.ring)
        hud_layout.addWidget(self.voice_status)
        hud_layout.addWidget(listen_btn)
        hud_layout.addWidget(quick_label)
        hud_layout.addWidget(btn_bug)
        hud_layout.addWidget(btn_api)
        hud_layout.addWidget(btn_perf)
        hud_layout.addStretch()

        # RIGHT: Professional Developer Workspace
        dev_card = QFrame()
        dev_card.setObjectName("Card")
        dev_layout = QVBoxLayout(dev_card)

        self.tabs = QTabWidget()

        # Tab 1: Command & Chat Console
        console_tab = QWidget()
        console_layout = QVBoxLayout(console_tab)

        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_log.append("[TRACCY]: Online and ready. Ask me to inspect code, test an API, check performance, or run a task.\n")

        input_row = QHBoxLayout()
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("Command TRACCY (e.g., 'Find bugs in AuthController.java' or 'Test GET /api/expenses')...")
        self.prompt_input.returnPressed.connect(self._send_command)

        send_btn = QPushButton("Execute")
        send_btn.clicked.connect(self._send_command)

        input_row.addWidget(self.prompt_input)
        input_row.addWidget(send_btn)

        console_layout.addWidget(self.chat_log)
        console_layout.addLayout(input_row)

        # Tab 2: API & Bug Diagnostics Output
        self.diag_log = QTextEdit()
        self.diag_log.setReadOnly(True)
        self.diag_log.setText("=== TRACCY DIAGNOSTICS & TEST RUNNER ===\nWaiting for first scan or API test run...")

        # Tab 3: Performance & System Monitor
        self.perf_log = QTextEdit()
        self.perf_log.setReadOnly(True)
        self.perf_log.setText("=== PERFORMANCE & INFRASTRUCTURE TELEMETRY ===\n• CPU / Memory Monitor: Ready\n• API Latency Profiler: Ready\n• AWS EC2 / Docker Bridge: Pending Phase 3")

        self.tabs.addTab(console_tab, "Command Console")
        self.tabs.addTab(self.diag_log, "Bug & API Diagnostics")
        self.tabs.addTab(self.perf_log, "Performance Monitor")

        dev_layout.addWidget(self.tabs)

        splitter.addWidget(hud_card)
        splitter.addWidget(dev_card)
        splitter.setSizes([340, 900])

        main_layout.addWidget(splitter)

    def _toggle_voice(self):
        self.ring.active = not self.ring.active
        if self.ring.active:
            self.voice_status.setText("Voice Engine: LISTENING... (Say 'Stop' anytime)")
            self.voice_status.setStyleSheet("color: #22C55E; font-weight: bold; font-size: 12px;")
        else:
            self.voice_status.setText("Voice Engine: Standby")
            self.voice_status.setStyleSheet("color: #94A3B8; font-size: 12px;")

    def _handle_stop(self):
        self.ring.active = False
        self.voice_status.setText("Voice Engine: INTERRUPTED (Stopped)")
        self.voice_status.setStyleSheet("color: #EF4444; font-weight: bold; font-size: 12px;")
        self.chat_log.append("[SYSTEM]: Execution and voice output halted immediately.\n")

    def _run_quick_task(self, task_text):
        self.prompt_input.setText(task_text)
        self._send_command()

    def _send_command(self):
        text = self.prompt_input.text().strip()
        if not text:
            return
        self.prompt_input.clear()
        self.chat_log.append(f"[YOU]: {text}")

        if text.lower() == "stop":
            self._handle_stop()
            return

        # Placeholder response until we wire Ollama + LangGraph in Step 3
        self.chat_log.append(
            f"[TRACCY]: Task queued -> '{text}'. Next step: connect Ollama + tool runners to execute and verify this.\n"
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TraccyDashboard()
    window.show()
    sys.exit(app.exec())