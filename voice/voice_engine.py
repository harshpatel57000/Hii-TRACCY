from collections import deque
import os
import queue
import re
import subprocess
import threading
import time
import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal

SAMPLE_RATE = 16000
BLOCK_SIZE = 1600                 # 0.1s per block
SILENCE_AFTER_SPEECH = 1.6        # Natural pause threshold in hands-free mode
PRE_BUFFER_BLOCKS = 8             # 0.8s pre-roll so first syllable is never clipped
MIN_SPEECH_SECONDS = 0.45

INSTANT_COMMANDS = {
    "stop", "traccy stop", "stop traccy", "halt", "cancel",
    "approve", "approve plan", "execute", "go ahead", "reject"
}


class VoiceEngine(QObject):
    heard_text = Signal(str, str)     # (transcribed_text, detected_language_code)
    interim_text = Signal(str)        # Live status/recognition preview
    status_changed = Signal(str, str)
    mic_level = Signal(int)
    stop_triggered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.listening = False
        self.speaking = False
        self.ptt_active = False       # True while user holds/toggles Push-to-Talk
        self._stop_flag = False
        self._speech_proc = None
        self._speech_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.speech_threshold = 170.0

        # Lazy-loaded neural engines (keep startup instant)
        self._whisper_model = None
        self._kokoro = None
        self._piper_en = None
        self._piper_gu = None
        self._models_lock = threading.Lock()

        threading.Thread(target=self._warmup_models, daemon=True).start()
        threading.Thread(target=self._speech_queue_loop, daemon=True).start()

    def _warmup_models(self):
        """Loads faster-whisper (int8) and Kokoro/Piper neural TTS in background."""
        with self._models_lock:
            try:
                from faster_whisper import WhisperModel
                # 'small' with int8 uses ~995MB RAM and supports English + Indian accents + Gujarati
                self._whisper_model = WhisperModel(
                    "small",
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                )
            except Exception as e:
                print(f"[STT INFO]: faster-whisper unavailable ({e}), will fall back if needed.")

            # Optional Kokoro-82M ONNX (natural ChatGPT-like English voice)
            kokoro_model = os.path.join("models", "tts", "kokoro-v1.0.onnx")
            kokoro_voices = os.path.join("models", "tts", "voices-v1.0.bin")
            if os.path.exists(kokoro_model) and os.path.exists(kokoro_voices):
                try:
                    from kokoro_onnx import Kokoro
                    self._kokoro = Kokoro(kokoro_model, kokoro_voices)
                except Exception as e:
                    print(f"[TTS INFO]: Kokoro load skipped ({e})")

            # Optional Piper ONNX models (English + Gujarati)
            piper_gu_path = os.path.join("models", "tts", "gu_IN-medium.onnx")
            if os.path.exists(piper_gu_path):
                try:
                    from piper.voice import PiperVoice
                    self._piper_gu = PiperVoice.load(piper_gu_path)
                except Exception as e:
                    print(f"[TTS INFO]: Piper Gujarati load skipped ({e})")

    # --- INPUT CONTROL ---
    def start_listening(self):
        if self.listening:
            return
        self.listening = True
        self._stop_flag = False
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def begin_push_to_talk(self):
        """Starts immediate capture when user presses Push-to-Talk."""
        self.interrupt_speech_only()
        self.ptt_active = True
        if not self.listening:
            self.start_listening()
        self.status_changed.emit("🎙 PUSH-TO-TALK ACTIVE — Speak now...", "#00E5FF")

    def end_push_to_talk(self):
        """Signals end of Push-to-Talk utterance for immediate transcription."""
        self.ptt_active = False

    def interrupt_speech_only(self):
        """Stops active audio playback immediately without turning off mic."""
        self._stop_flag = True
        self.speaking = False
        while not self._speech_queue.empty():
            try:
                self._speech_queue.get_nowait()
            except queue.Empty:
                break
        try:
            sd.stop()
        except Exception:
            pass
        if self._speech_proc is not None:
            try:
                self._speech_proc.kill()
            except Exception:
                pass
            self._speech_proc = None

    def stop_all(self):
        self.listening = False
        self.ptt_active = False
        self.interrupt_speech_only()
        self.mic_level.emit(0)
        self.status_changed.emit("Voice Engine: STOPPED", "#EF4444")

    # --- STREAMING TTS OUTPUT ---
    def speak_sentence(self, sentence: str, lang: str = "en"):
        """Queues one sentence at a time so TRACCY speaks while still generating."""
        clean = self._clean_for_speech(sentence)
        if not clean:
            return
        self._stop_flag = False
        self._speech_queue.put((clean, lang))

    def speak(self, text: str, lang: str = "en"):
        self.interrupt_speech_only()
        self._stop_flag = False
        for part in re.split(r"(?<=[.!?।])\s+", text):
            self.speak_sentence(part, lang)

    def _clean_for_speech(self, text: str) -> str:
        text = re.sub(r"```[\s\S]*?```", " Code block shown on screen. ", text)
        text = re.sub(r"[#*`>_~\"'$;\\/\[\]{}]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:400]

    def _speech_queue_loop(self):
        while True:
            sentence, lang = self._speech_queue.get()
            if self._stop_flag:
                continue

            self.speaking = True
            self.status_changed.emit("🔊 SPEAKING... (Press Stop or speak to interrupt)", "#38BDF8")
            try:
                if lang == "gu" and self._piper_gu is not None:
                    self._play_piper(self._piper_gu, sentence)
                elif self._kokoro is not None and lang != "gu":
                    self._play_kokoro(sentence)
                else:
                    self._play_windows_fallback(sentence)
            except Exception as e:
                self.status_changed.emit(f"Speaker Error: {e}", "#EF4444")
            finally:
                if self._speech_queue.empty():
                    self.speaking = False
                    if self.listening and not self._stop_flag:
                        self.status_changed.emit("🎙 Listening — Speak naturally or hold Push-to-Talk...", "#22C55E")
                    elif not self._stop_flag:
                        self.status_changed.emit("Voice Engine: Standby", "#94A3B8")

    def _play_kokoro(self, text: str):
        samples, sample_rate = self._kokoro.create(text, voice="af_heart", speed=1.05, lang="en-us")
        if not self._stop_flag:
            sd.play(samples, samplerate=sample_rate)
            sd.wait()

    def _play_piper(self, voice_engine, text: str):
        chunks = [np.frombuffer(chunk, dtype=np.int16) for chunk in voice_engine.synthesize_stream_raw(text)]
        if chunks and not self._stop_flag:
            audio = np.concatenate(chunks)
            sd.play(audio, samplerate=voice_engine.config.sample_rate)
            sd.wait()

    def _play_windows_fallback(self, text: str):
        env = os.environ.copy()
        env["TRACCY_SPEECH"] = text
        ps_script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 2; $s.Volume = 100; $s.Speak($env:TRACCY_SPEECH);"
        )
        self._speech_proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._speech_proc.wait()
        self._speech_proc = None

    # --- MICROPHONE CAPTURE & NEURAL TRANSCRIPTION ---
    def _calibrate_noise(self, stream):
        levels = []
        for _ in range(5):
            if not self.listening:
                return
            indata, _ = stream.read(BLOCK_SIZE)
            levels.append(float(np.sqrt(np.mean(indata.astype(np.float32) ** 2))))
        ambient = float(np.mean(levels)) if levels else 80.0
        self.speech_threshold = max(130.0, ambient * 2.0)

    def _capture_utterance(self, stream) -> np.ndarray | None:
        pre_buffer = deque(maxlen=PRE_BUFFER_BLOCKS)
        chunks = []
        started = False
        voice_blocks = 0
        last_voice_time = 0.0
        sustain_cutoff = self.speech_threshold * 0.65

        while self.listening:
            indata, _ = stream.read(BLOCK_SIZE)
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))

            # Barge-in interruption if user speaks loudly while TRACCY is speaking
            if self.speaking and not self.ptt_active:
                if rms >= self.speech_threshold * 1.9:
                    self.interrupt_speech_only()
                    self.status_changed.emit("⚡ Interrupted — Listening to you...", "#00E5FF")
                else:
                    continue

            level = min(100, int((rms / (self.speech_threshold * 2.8)) * 100))
            self.mic_level.emit(level)
            now = time.time()

            # Push-to-Talk mode records everything while button is held
            if self.ptt_active:
                if not started:
                    started = True
                    chunks.extend(pre_buffer)
                chunks.append(indata.copy())
                voice_blocks += 1
                continue
            elif started and not chunks:
                break

            # If PTT was just released after recording, finish immediately
            if started and voice_blocks > 0 and not self.ptt_active and len(pre_buffer) == 0:
                break

            cutoff = sustain_cutoff if started else self.speech_threshold
            if rms >= cutoff:
                if not started:
                    started = True
                    chunks.extend(pre_buffer)
                    pre_buffer.clear()
                    self.status_changed.emit("🔴 Listening... (Speak naturally)", "#00E5FF")
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
        if not chunks or (voice_blocks * 0.1) < MIN_SPEECH_SECONDS:
            return None

        pcm_int16 = np.concatenate(chunks, axis=0).flatten()
        return pcm_int16.astype(np.float32) / 32768.0

    def _transcribe_audio(self, audio_float32: np.ndarray) -> tuple[str, str]:
        if self._whisper_model is not None:
            segments, info = self._whisper_model.transcribe(
                audio_float32,
                beam_size=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                initial_prompt="Technical conversation in Indian English or Gujarati about Flutter, Spring Boot, Java, Python, and AI.",
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            lang = info.language if info.language in ("en", "gu", "hi") else "en"
            return text, lang

        # Fallback if faster-whisper model is still downloading
        import speech_recognition as sr
        pcm_bytes = (audio_float32 * 32767).astype(np.int16).tobytes()
        audio_data = sr.AudioData(pcm_bytes, SAMPLE_RATE, 2)
        text = sr.Recognizer().recognize_google(audio_data, language="en-IN").strip()
        return text, "en"

    def _listen_loop(self):
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK_SIZE) as stream:
                self.status_changed.emit("🎚 Calibrating room noise...", "#F59E0B")
                self._calibrate_noise(stream)

                while self.listening:
                    if not self.speaking:
                        self.status_changed.emit("🎙 Listening — Speak naturally or hold Push-to-Talk...", "#22C55E")

                    audio = self._capture_utterance(stream)
                    if audio is None or not self.listening:
                        continue

                    self.status_changed.emit("🧠 Transcribing speech...", "#F59E0B")
                    try:
                        text, lang = self._transcribe_audio(audio)
                    except Exception as e:
                        self.status_changed.emit(f"⚠️ STT Error: {e}", "#EF4444")
                        continue

                    if not text:
                        continue

                    self.interim_text.emit(text)
                    lower = text.lower().strip(" .!?")
                    if lower in {"stop", "traccy stop", "stop traccy", "halt", "cancel"}:
                        self.stop_all()
                        self.stop_triggered.emit()
                        break

                    self.heard_text.emit(text, lang)
        except Exception as e:
            self.listening = False
            self.status_changed.emit(f"❌ Mic Error: {e}", "#EF4444")