"""Модели на Apple MLX через mlx-audio: Qwen3-TTS, VoxCPM2, OmniVoice, Fish S2 Pro, Higgs V3."""
from __future__ import annotations

from typing import Any

import numpy as np

from .base import Backend

# какие ключи настроек отправлять в model.generate() для каждой модели
PARAMS = {
    "qwen3-voicedesign": ["instruct", "lang_code", "temperature", "top_p", "top_k", "repetition_penalty"],
    "qwen3-customvoice": ["voice", "instruct", "lang_code", "temperature", "top_p", "top_k", "repetition_penalty"],
    "voxcpm2": ["instruct", "cfg_value", "inference_timesteps"],
    "omnivoice": ["instruct", "num_steps", "guidance_scale", "class_temperature", "position_temperature", "duration_s"],
    "fish-s2-pro": ["instruct", "temperature", "top_p", "top_k", "chunk_length"],
    "higgs-v3": ["temperature", "top_p", "top_k"],
}
INT_KEYS = {"top_k", "num_steps", "inference_timesteps", "chunk_length"}


class MlxBackend(Backend):
    def load(self) -> None:
        from mlx_audio.tts.utils import load_model

        repo = self.opts.get("repo")
        self.log.info("Загружаю %s (первый раз скачивается с Hugging Face)", repo)
        self.model = load_model(repo)
        self.sample_rate = int(getattr(self.model, "sample_rate", 24000) or 24000)
        self.log.info("Модель загружена, частота %s Гц", self.sample_rate)

    def info(self) -> dict[str, Any]:
        out: dict[str, Any] = {"sample_rate": self.sample_rate}
        if hasattr(self.model, "get_supported_speakers"):
            try:
                out["voices"] = list(self.model.get_supported_speakers())
            except Exception:
                pass
        return out

    def _ref(self, name):
        wav, txt = self.ref_paths(name)
        if not wav:
            return None, None
        if getattr(self.model, "preserve_ref_audio_path", False):
            return wav, txt
        from mlx_audio.utils import load_audio

        return load_audio(wav, sample_rate=self.sample_rate), txt

    def synth(self, text, s):
        mid = self.spec["id"]
        kw: dict[str, Any] = {}
        for k in PARAMS.get(mid, []):
            v = s.get(k)
            if v is None or v == "":
                continue
            kw[k] = int(v) if k in INT_KEYS else v

        if mid == "omnivoice":
            kw["language"] = "ru"
            if not kw.get("instruct"):
                kw["instruct"] = "None"
            if not kw.get("duration_s"):
                kw.pop("duration_s", None)
        if mid in ("qwen3-voicedesign", "qwen3-customvoice") and not kw.get("instruct"):
            kw.pop("instruct", None)
            if mid == "qwen3-voicedesign":
                kw["instruct"] = "A clear, calm adult voice."

        if "ref_voice" in {f["key"] for f in self.spec["settings"]}:
            ref_audio, ref_text = self._ref(s.get("ref_voice"))
            if ref_audio is not None:
                kw["ref_audio"] = ref_audio
                if ref_text:
                    kw["ref_text"] = ref_text
                if mid == "voxcpm2":
                    kw.pop("instruct", None)  # описание и клон вместе не работают

        kw.update(self.extra(s))
        kw.setdefault("verbose", False)
        self.log.debug("generate(%s)", {k: (v if not hasattr(v, "shape") else f"array{tuple(v.shape)}") for k, v in kw.items()})

        try:
            results = list(self.model.generate(text=text, **kw))
        except TypeError as e:
            if "verbose" in str(e):
                kw.pop("verbose", None)
                results = list(self.model.generate(text=text, **kw))
            else:
                raise
        import mlx.core as mx

        def as_np(a):
            if isinstance(a, mx.array):
                a = a.astype(mx.float32)  # bf16 напрямую в numpy не конвертируется
            return np.array(a, dtype=np.float32).reshape(-1)

        parts = [as_np(r.audio) for r in results if r is not None and getattr(r, "audio", None) is not None]
        if not parts:
            raise RuntimeError("Модель не вернула аудио")
        sr = int(getattr(results[-1], "sample_rate", self.sample_rate) or self.sample_rate)
        return np.concatenate(parts), sr
