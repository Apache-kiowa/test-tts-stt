from __future__ import annotations

from .base import Backend


class QwenBackend(Backend):
    """Qwen3-TTS через официальный пакет qwen-tts (PyTorch)."""

    def load(self) -> None:
        from qwen_tts import Qwen3TTSModel

        dev = self.torch_device(self.opts.get("device", "auto"))
        self.device = "cuda:0" if dev == "cuda" else dev
        dtype = self.torch_dtype(self.opts.get("dtype", "auto"), self.device)
        repo = self.opts["repo"]
        self.log.info("Загружаю %s на %s (%s)", repo, self.device, dtype)
        self.model = Qwen3TTSModel.from_pretrained(repo, device_map=self.device, dtype=dtype)
        self.voice_design = "VoiceDesign" in repo

    def info(self):
        try:
            return {"voices": list(self.model.get_supported_speakers() or [])}
        except Exception:
            return {}

    def synth(self, text, s):
        kw = dict(
            language=s.get("language", "Russian"),
            temperature=float(s.get("temperature", 0.9)),
            top_p=float(s.get("top_p", 1.0)),
            top_k=int(s.get("top_k", 50)),
            repetition_penalty=float(s.get("repetition_penalty", 1.05)),
        )
        kw.update(self.extra(s))
        if self.voice_design:
            wavs, sr = self.model.generate_voice_design(text=text, instruct=s.get("instruct") or "A clear, calm adult voice.", **kw)
        else:
            wavs, sr = self.model.generate_custom_voice(text=text, speaker=s.get("voice", "serena"),
                                                        instruct=s.get("instruct") or None, **kw)
        return self.to_np(wavs[0]), int(sr)
