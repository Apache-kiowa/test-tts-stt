from __future__ import annotations

from .base import Backend


class VoxCPMBackend(Backend):
    """VoxCPM2 через официальный пакет voxcpm (PyTorch)."""

    def load(self) -> None:
        from voxcpm import VoxCPM

        self.device = self.torch_device(self.opts.get("device", "auto"))
        optimize = bool(self.opts.get("optimize", False)) and self.device.startswith("cuda")
        self.log.info("Загружаю %s на %s (torch.compile=%s)", self.opts["repo"], self.device, optimize)
        self.model = VoxCPM.from_pretrained(self.opts["repo"], load_denoiser=False, optimize=optimize, device=self.device)
        self.sample_rate = int(getattr(self.model.tts_model, "sample_rate", 48000))

    def synth(self, text, s):
        wav, ref_text = self.ref_paths(s.get("ref_voice"))
        kw = dict(cfg_value=float(s.get("cfg_value", 2.0)), inference_timesteps=int(s.get("inference_timesteps", 10)),
                  normalize=False, denoise=False)
        if wav:
            kw["reference_wav_path"] = wav
            if ref_text:  # «полное клонирование»: образец + его расшифровка
                kw["prompt_wav_path"] = wav
                kw["prompt_text"] = ref_text
        elif (s.get("instruct") or "").strip():
            text = f"({s['instruct'].strip()}){text}"  # дизайн голоса: описание в скобках перед текстом
        kw.update(self.extra(s))
        audio = self.model.generate(text=text, **kw)
        return self.to_np(audio), self.sample_rate
