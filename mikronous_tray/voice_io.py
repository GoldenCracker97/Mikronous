"""Voice in and out, fully local: hold VOX to record, faster-whisper transcribes; Piper reads replies.

Recording: Linux uses whatever audio recorder the desktop already has (``pw-record``, ``parecord``,
``arecord``) so no PortAudio build is needed; Windows uses ``sounddevice`` (bundles PortAudio).
Transcription: ``faster_whisper.WhisperModel`` (CTranslate2), CUDA when it works, else CPU int8. The
weights (Systran/faster-whisper-<size>) download from Hugging Face once into ``~/.cache``.
Speech: Piper voices download once into ``~/.local/share/mikronous/piper``; playback via
``paplay``/``pw-play``/``aplay`` on Linux, ``sounddevice`` on Windows. Nothing here needs an account.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
RATE = 16_000
STT_MODELS = ("tiny", "base", "small", "turbo")
DEFAULT_TTS_VOICE = "en_US-lessac-medium"
PIPER_DIR = Path(os.environ.get("MIKRONOUS_DATA_DIR") or "~/.local/share/mikronous").expanduser() / "piper"
_stt_lock = threading.Lock()
_stt_models: dict[str, object] = {}
_tts_lock = threading.Lock()
_tts_voices: dict[str, object] = {}


# ----------------------------------------------------------------------------- availability
def _installed(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None       # no import: a cold faster_whisper import takes seconds
    except (ImportError, ValueError):
        return False


def stt_available() -> tuple[bool, str]:
    if not _installed("faster_whisper"):
        return False, "faster-whisper is not installed (scripts/install.sh --voice)"
    if not IS_WINDOWS and recorder_command("x.wav") is None:
        return False, "no recorder found (pw-record, parecord or arecord)"
    if IS_WINDOWS and not _installed("sounddevice"):
        return False, "sounddevice is not installed (scripts/install.ps1 -VoiceInput)"
    return True, "ready"


def tts_available() -> tuple[bool, str]:
    if not _installed("piper"):
        return False, "piper-tts is not installed (scripts/install.sh --voice)"
    if not IS_WINDOWS and player_command("x.wav") is None:
        return False, "no player found (paplay, pw-play or aplay)"
    return True, "ready"


def stt_model_cached(size: str) -> bool:
    hub = Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME", "~/.cache/huggingface")).expanduser()
    if not str(hub).endswith("hub"):
        hub = hub / "hub"
    return any(hub.glob(f"models--Systran--faster-whisper-{size}*"))


# ----------------------------------------------------------------------------- recording
def recorder_command(path: str, which=shutil.which) -> list[str] | None:
    if which("pw-record"):
        return ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", path]
    if which("parecord"):
        return ["parecord", f"--rate={RATE}", "--channels=1", "--format=s16le", "--file-format=wav", path]
    if which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "wav", path]
    return None


def player_command(path: str, which=shutil.which) -> list[str] | None:
    for cmd in (["paplay", path], ["pw-play", path], ["aplay", "-q", path]):
        if which(cmd[0]):
            return cmd
    return None


class Recorder:
    """start() … stop() → path of a 16 kHz mono WAV (or None when nothing was captured)."""

    def __init__(self):
        self.path = os.path.join(tempfile.gettempdir(), f"mikronous-vox-{os.getpid()}-{int(time.time())}.wav")
        self._proc: subprocess.Popen | None = None
        self._stream = None
        self._frames: list = []
        self.started = 0.0
        self.error = ""

    def start(self) -> bool:
        self.started = time.monotonic()
        if IS_WINDOWS:
            try:
                import sounddevice as sd
                self._frames = []
                self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                              callback=lambda data, *_: self._frames.append(bytes(data)))
                self._stream.start()
                return True
            except Exception as exc:  # noqa: BLE001
                self.error = f"microphone: {exc}"
                return False
        cmd = recorder_command(self.path)
        if cmd is None:
            self.error = "no recorder found (pw-record, parecord or arecord)"
            return False
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.error = f"{cmd[0]}: {exc}"
            return False
        return True

    def stop(self) -> str | None:
        if IS_WINDOWS:
            if self._stream is None:
                return None
            self._stream.stop()
            self._stream.close()
            with wave.open(self.path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(b"".join(self._frames))
            return self.path if self._frames else None
        if self._proc is None:
            return None
        try:
            self._proc.send_signal(signal.SIGINT)          # recorders finish the WAV header on SIGINT
            self._proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self._proc.kill()
        return self.path if os.path.exists(self.path) and os.path.getsize(self.path) > 44 else None


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def wav_seconds(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or RATE)
    except (OSError, wave.Error, EOFError):
        return 0.0


# ----------------------------------------------------------------------------- transcription
def _load_stt(size: str):
    from faster_whisper import WhisperModel
    with _stt_lock:
        if size in _stt_models:
            return _stt_models[size]
        try:
            model = WhisperModel(size, device="cuda", compute_type="float16")
        except Exception:  # noqa: BLE001 - no CUDA libs / no GPU: CPU int8 is fine for short clips
            model = WhisperModel(size, device="cpu", compute_type="int8")
        _stt_models[size] = model
        return model


def wav_samples(path: str):
    """16 kHz mono float32 samples of our own recordings (no PyAV: faster-whisper's decoder needs a newer
    ``av`` than some environments have, and we already control the WAV format)."""
    import numpy as np
    with wave.open(path, "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"unexpected sample width {width}")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != RATE:                                   # recorders should honour --rate; resample linearly if not
        n = int(len(data) * RATE / rate)
        data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype(np.float32)
    return data


def transcribe(path: str, size: str = "base", language: str | None = None) -> str:
    """Text of a WAV; '' for silence. Raises on missing deps so the UI can say what to install."""
    if wav_seconds(path) < 0.3:
        return ""
    audio = wav_samples(path)
    model = _load_stt(size if size in STT_MODELS else "base")
    segments, _info = model.transcribe(audio, language=language or None, vad_filter=True, beam_size=1)
    return " ".join(s.text.strip() for s in segments).strip()


# ----------------------------------------------------------------------------- speech
def _load_tts(voice: str):
    from piper import PiperVoice
    from piper.download_voices import download_voice
    with _tts_lock:
        if voice in _tts_voices:
            return _tts_voices[voice]
        PIPER_DIR.mkdir(parents=True, exist_ok=True)
        onnx = PIPER_DIR / f"{voice}.onnx"
        if not onnx.exists():
            download_voice(voice, PIPER_DIR)
        v = PiperVoice.load(onnx)
        _tts_voices[voice] = v
        return v


class Speaker:
    """Synthesise with Piper and play; ``stop()`` cuts playback."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._token = 0                      # the newest say() wins; older ones stop at their next checkpoint

    def say(self, text: str, voice: str = DEFAULT_TTS_VOICE) -> None:
        text = " ".join((text or "").split())
        if not text:
            return
        self._token += 1
        token = self._token
        self._stop.clear()
        v = _load_tts(voice or DEFAULT_TTS_VOICE)
        path = os.path.join(tempfile.gettempdir(), f"mikronous-say-{os.getpid()}-{token}.wav")
        with wave.open(path, "wb") as w:
            v.synthesize_wav(text[:2000], w)
        if self._stop.is_set() or token != self._token:
            _unlink(path)
            return
        if IS_WINDOWS:
            import sounddevice as sd
            with wave.open(path, "rb") as w:
                import numpy as np
                data = np.frombuffer(w.readframes(w.getnframes()), dtype="int16")
                sd.play(data, w.getframerate())
                while sd.get_stream().active and not self._stop.is_set():
                    time.sleep(0.05)
                sd.stop()
            return
        cmd = player_command(path)
        if cmd is None:
            _unlink(path)
            return
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._proc = proc
        proc.wait()
        if self._proc is proc:
            self._proc = None
        _unlink(path)

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
        if IS_WINDOWS:
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:  # noqa: BLE001
                pass
