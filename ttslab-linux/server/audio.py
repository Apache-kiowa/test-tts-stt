"""Склейка кусков, громкость, кодирование в wav/mp3/opus/aac/flac/pcm."""
from __future__ import annotations

import io
import shutil
import subprocess
import wave

import numpy as np

CONTENT_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or len(x) == 0:
        return x
    n = int(round(len(x) * sr_to / sr_from))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def join(parts: list[tuple[np.ndarray, int]], pause_ms: int) -> tuple[np.ndarray, int]:
    if not parts:
        return np.zeros(0, dtype=np.float32), 24000
    sr = parts[0][1]
    gap = np.zeros(int(sr * max(0, pause_ms) / 1000), dtype=np.float32)
    out: list[np.ndarray] = []
    for i, (a, s) in enumerate(parts):
        if i:
            out.append(gap)
        out.append(resample(np.asarray(a, dtype=np.float32), s, sr))
    return np.concatenate(out), sr


def peak_normalize(x: np.ndarray, peak: float = 0.95) -> np.ndarray:
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return x if m < 1e-6 else (x * (peak / m)).astype(np.float32)


def to_wav_bytes(x: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def encode(x: np.ndarray, sr: int, fmt: str) -> tuple[bytes, str, str]:
    """Возвращает (байты, фактический формат, content-type). Без ffmpeg отдаёт wav."""
    fmt = (fmt or "wav").lower()
    if fmt == "pcm":
        # как у OpenAI: 24 кГц, 16 бит, моно, без заголовка
        y = resample(x, sr, 24000)
        return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes(), "pcm", CONTENT_TYPES["pcm"]
    wav = to_wav_bytes(x, sr)
    if fmt == "wav" or fmt not in CONTENT_TYPES or not has_ffmpeg():
        return wav, "wav", CONTENT_TYPES["wav"]
    args = {
        "mp3": ["-f", "mp3", "-codec:a", "libmp3lame", "-q:a", "2"],
        "opus": ["-f", "ogg", "-codec:a", "libopus", "-b:a", "64k"],
        "aac": ["-f", "adts", "-codec:a", "aac", "-b:a", "128k"],
        "flac": ["-f", "flac"],
    }[fmt]
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", *args, "pipe:1"],
                           input=wav, capture_output=True, timeout=60, check=True)
        return p.stdout, fmt, CONTENT_TYPES[fmt]
    except Exception:
        return wav, "wav", CONTENT_TYPES["wav"]
