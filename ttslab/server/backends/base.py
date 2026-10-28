from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
REFS = ROOT / "refs"
MODELS_DIR = ROOT / "models"


class Backend:
    """Одна модель. Воркер вызывает load() один раз и synth() на каждый кусок текста."""

    #: какие ключи настроек влияют на ударения/нормализацию по умолчанию — для справки
    sample_rate: int = 24000

    def __init__(self, spec: dict[str, Any], load_opts: dict[str, Any], log: logging.Logger):
        self.spec = spec
        self.opts = load_opts
        self.log = log

    # ---- переопределяется
    def load(self) -> None:
        raise NotImplementedError

    def download(self) -> None:
        """По умолчанию скачать = загрузить (веса кэшируются в ~/.cache/huggingface)."""
        self.load()

    def synth(self, text: str, s: dict[str, Any]) -> tuple[np.ndarray, int]:
        raise NotImplementedError

    def info(self) -> dict[str, Any]:
        return {}

    # ---- помощники
    @staticmethod
    def extra(s: dict[str, Any]) -> dict[str, Any]:
        raw = s.get("extra_json") or "{}"
        try:
            val = json.loads(raw) if isinstance(raw, str) else dict(raw)
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def ref_paths(name: str | None) -> tuple[str | None, str | None]:
        """refs/<name>.wav + refs/<name>.txt (расшифровка)."""
        if not name or name == "none":
            return None, None
        wav = REFS / f"{name}.wav"
        if not wav.exists():
            raise FileNotFoundError(f"Нет образца голоса refs/{name}.wav")
        txt = REFS / f"{name}.txt"
        return str(wav), (txt.read_text(encoding="utf-8").strip() if txt.exists() else None)

    @staticmethod
    def seed_all(seed: int | None) -> None:
        if seed is None or int(seed) < 0:
            return
        seed = int(seed)
        random.seed(seed)
        np.random.seed(seed % (2**32))
        try:
            import torch

            torch.manual_seed(seed)
        except Exception:
            pass
        try:
            import mlx.core as mx

            mx.random.seed(seed)
        except Exception:
            pass

    @staticmethod
    def to_np(audio: Any) -> np.ndarray:
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().float().numpy()
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.reshape(-1) if 1 in audio.shape else audio.mean(axis=0)
        return audio

    @staticmethod
    def torch_device(pref: str = "auto") -> str:
        import torch

        if pref and pref != "auto":
            return pref
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"


def get_backend_class(model_id: str):
    if model_id == "silero-v5":
        from .silero import SileroBackend as C
    elif model_id in ("espeech", "f5-russian"):
        from .f5 import F5Backend as C
    elif model_id in ("qwen3-voicedesign", "qwen3-customvoice", "voxcpm2", "omnivoice", "fish-s2-pro", "higgs-v3"):
        from .mlx_models import MlxBackend as C
    elif model_id == "chatterbox-mtl":
        from .chatterbox_mtl import ChatterboxBackend as C
    elif model_id == "xtts-v2":
        from .xtts import XttsBackend as C
    elif model_id == "test-tone":
        from .fake import ToneBackend as C
    else:
        raise KeyError(f"Неизвестная модель {model_id}")
    return C
