from __future__ import annotations

from .base import Backend


class OmniVoiceBackend(Backend):
    """OmniVoice через официальный пакет omnivoice (PyTorch)."""

    def load(self) -> None:
        from omnivoice import OmniVoice

        dev = self.torch_device(self.opts.get("device", "auto"))
        self.device = "cuda:0" if dev == "cuda" else dev
        dtype = self.torch_dtype(self.opts.get("dtype", "auto"), self.device)
        self.log.info("Загружаю %s на %s (%s)", self.opts["repo"], self.device, dtype)
        self.model = OmniVoice.from_pretrained(self.opts["repo"], device_map=self.device, dtype=dtype)
        self.sample_rate = int(getattr(self.model, "sampling_rate", None) or 24000)

    def synth(self, text, s):
        wav, ref_text = self.ref_paths(s.get("ref_voice"))
        kw = dict(
            language="ru",
            num_step=int(s.get("num_step", 32)),
            guidance_scale=float(s.get("guidance_scale", 2.0)),
            class_temperature=float(s.get("class_temperature", 0.0)),
            position_temperature=float(s.get("position_temperature", 5.0)),
        )
        if float(s.get("speed") or 0) > 0:
            kw["speed"] = float(s["speed"])
        if wav:
            kw["ref_audio"] = wav
            if ref_text:
                kw["ref_text"] = ref_text
        elif (s.get("instruct") or "").strip():
            kw["instruct"] = s["instruct"].strip()
        kw.update(self.extra(s))
        audios = self.model.generate(text=text, **kw)
        return self.to_np(audios[0]), self.sample_rate
