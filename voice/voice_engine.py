from collections import deque
import os
import queue
import re
import subprocess
import threading
import time
import numpy as np
import sounddevice as sd
import speech_recognition as sr
from PySide6.QtCore import QObject, Signal

SAMPLE_RATE = 16000
BLOCK_SIZE = 1600
SILENCE_AFTER_SPEECH = 2.4   # Gives you 2.4s to pause & think mid-sentence without cutting off
PRE_BUFFER_BLOCKS = 8        # Keeps 0.8s before you start speaking so first words aren't lost
MIN_SPEECH_SECONDS = 0.6     # Ignores brief coughs/clicks

INSTANT_COMMANDS = {
    "stop", "traccy stop", "stop traccy", "halt", "cancel",
    "approve", "approve plan", "execute", "go ahead", "reject"
}

WAKE_WORDS = ("hey traccy", "hey tracy", "hi traccy", "ok traccy", "traccy")


class VoiceEngine(QObject):
    heard_text = Signal(str)
    status_changed = Signal(str, str)
    mic_level = Signal(int)
    stop_triggered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.listening = False
        self.speaking = False
        self.wake_word_required = False  # Can toggle ON/OFF from HUD
        self._stop_flag = False
        self._speech_proc = None
        self._speech_queue = queue.Queue()
        self.recognizer = sr.Recognizer()
        self.speech_threshold = 200.0

        threading.Thread(target=self._speech_queue_loop, daemon=True).start()

    def start_listening(self):
        if self.listening:
            return
        self.listening = True
        self._stop_flag = False
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def stop_all(self):
        self.listening = False
        self._stop_flag = True
        self.speaking = False
        self.mic_level.emit(0)

        while not self._speech_queue.empty():
            try:
                self._speech_queue.get_nowait()
            except queue.Empty:
                break

        if self._speech_proc is not None:
            try:
                self._speech_proc.kill()
            except Exception:
                pass
            self._speech_proc = None

        self.status_changed.emit("Voice Engine: STOPPED", "#EF4444")

    def speak(self, text: str):
        if not text:
            return
        self._stop_flag = False
        if self._speech_proc is not None:
            try:
                self._speech_proc.kill()
            except Exception:
                pass
        self._speech_queue.put(text)

    def _clean_for_speech(self, text: str) -> str:
        text = re.sub(r"```[\s\S]*?```", " Code snippet displayed on screen. ", text)
        text = re.sub(r"[#*`>_~\"'$;\\/\[\]{}()-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:450]

    def _speech_queue_loop(self):
        while True:
            raw_text = self._speech_queue.get()
            if self._stop_flag:
                continue

            clean_text = self._clean_for_speech(raw_text)
            if not clean_text:
                continue

            self.speaking = True
            self.status_changed.emit("🔊 SPEAKING... (Say 'Stop' or click STOP)", "#38BDF8")
            try:
                env = os.environ.copy()
                env["TRACCY_SPEECH"] = clean_text
                ps_script = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$synth.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::NotSet); "
                    "$synth.Rate = 2; "
                    "$synth.Volume = 100; "
                    "$synth.Speak($env:TRACCY_SPEECH);"
                )
                self._speech_proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self._speech_proc.wait()
            except Exception as e:
                self.status_changed.emit(f"Speaker Error: {str(e)}", "#EF4444")
            finally:
                self._speech_proc = None
                self.speaking = False
                if self.listening and not self._stop_flag:
                    self._emit_ready_status()
                elif not self._stop_flag:
                    self.status_changed.emit("Voice Engine: Standby", "#94A3B8")

    def _emit_ready_status(self):
        if self.wake_word_required:
            self.status_changed.emit("🎙 Standby — Say 'Hey TRACCY' to speak...", "#22C55E")
        else:
            self.status_changed.emit("🎙 Listening — Speak your full sentence freely...", "#22C55E")

    def _calibrate_noise(self, stream):
        noise_levels = []
        for _ in range(6):
            if not self.listening:
                return
            indata, _ = stream.read(BLOCK_SIZE)
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
            noise_levels.append(rms)
        ambient = float(np.mean(noise_levels)) if noise_levels else 100.0
        self.speech_threshold = max(150.0, ambient * 2.3)

    def _listen_until_done(self, stream) -> sr.AudioData | None:
        pre_buffer = deque(maxlen=PRE_BUFFER_BLOCKS)
        chunks = []
        started = False
        voice_blocks = 0
        last_voice_time = 0.0

        while self.listening and not self.speaking:
            indata, _ = stream.read(BLOCK_SIZE)
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))

            level = min(100, int((rms / (self.speech_threshold * 3.0)) * 100))
            self.mic_level.emit(level)

            now = time.time()
            if rms >= self.speech_threshold:
                if not started:
                    started = True
                    chunks.extend(pre_buffer)
                    self.status_changed.emit("🔴 Hearing you — Take your time, pause 2.4s when done...", "#00E5FF")
                voice_blocks += 1
                last_voice_time = now
                chunks.append(indata.copy())
            else:
                if not started:
                    pre_buffer.append(indata.copy())
                else:
                    chunks.append(indata.copy())
                    if (now - last_voice_time) >= SILENCE_AFTER_SPEECH:
                        break

        self.mic_level.emit(0)
        if not chunks or not self.listening or (voice_blocks * 0.1) < MIN_SPEECH_SECONDS:
            return None

        full_audio = np.concatenate(chunks, axis=0)
        return sr.AudioData(full_audio.tobytes(), SAMPLE_RATE, 2)

    def _extract_after_wake_word(self, text: str) -> str | None:
        lower = text.lower().strip()
        if lower in INSTANT_COMMANDS:
            return text
        for wake in WAKE_WORDS:
            if lower.startswith(wake):
                cleaned = text[len(wake):].lstrip(" ,.!?")
                return cleaned if cleaned else None
        return None

    def _listen_loop(self):
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK_SIZE) as stream:
                self.status_changed.emit("🎚 Calibrating room noise (0.5s)...", "#F59E0B")
                self._calibrate_noise(stream)

                while self.listening:
                    if self.speaking:
                        sd.sleep(150)
                        continue

                    self._emit_ready_status()
                    audio = self._listen_until_done(stream)
                    if audio is None or not self.listening:
                        continue

                    self.status_changed.emit("⏳ Transcribing full sentence...", "#F59E0B")
                    try:
                        text = self.recognizer.recognize_google(audio, language="en-IN").strip()
                    except sr.UnknownValueError:
                        continue
                    except sr.RequestError as e:
                        self.status_changed.emit(f"⚠️ Speech API Error (Check Internet): {e}", "#EF4444")
                        sd.sleep(2000)
                        continue

                    if not text:
                        continue

                    # Wake-word filter if enabled
                    if self.wake_word_required:
                        extracted = self._extract_after_wake_word(text)
                        if not extracted:
                            self.status_changed.emit(f"💤 Ignored (Say 'Hey TRACCY'): '{text}'", "#64748B")
                            continue
                        text = extracted

                    # Ignore tiny 1-word accidental noise unless it's a valid command
                    if len(text.split()) < 2 and text.lower() not in INSTANT_COMMANDS:
                        self.status_changed.emit(f"💤 Short fragment ignored: '{text}'", "#64748B")
                        continue

                    self.status_changed.emit(f"✅ Heard: '{text}'", "#00E5FF")

                    if text.lower() in {"stop", "traccy stop", "stop traccy", "halt", "cancel"}:
                        self.stop_all()
                        self.stop_triggered.emit()
                        break

                    self.heard_text.emit(text)
        except Exception as e:
            self.listening = False
            self.status_changed.emit(f"❌ Mic Error: {str(e)}", "#EF4444")