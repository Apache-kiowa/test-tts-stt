from __future__ import annotations

from .base import Backend


class ChatterboxBackend(Backend):
    def load(self) -> None:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        self.device = self.torch_device(self.opts.get("device", "auto"))
        self.model = ChatterboxMultilingualTTS.from_pretrained(device=self.device)
        self.sample_rate = int(self.model.sr)
        self._default_conds = self.model.conds
        self._last_ref = None
        self.log.info("Chatterbox Multilingual загружена на %s", self.device)

    def synth(self, text, s):
        wav, _ = self.ref_paths(s.get("ref_voice"))
        kw = dict(
            exaggeration=float(s.get("exaggeration", 0.5)),
            cfg_weight=float(s.get("cfg_weight", 0.5)),
            temperature=float(s.get("temperature", 0.8)),
            repetition_penalty=float(s.get("repetition_penalty", 2.0)),
            min_p=float(s.get("min_p", 0.05)),
            top_p=float(s.get("top_p", 1.0)),
        )
        kw.update(self.extra(s))
        if wav:
            if wav != self._last_ref:
                self.model.prepare_conditionals(wav, exaggeration=kw["exaggeration"])
                self._last_ref = wav
        elif self._last_ref is not None:
            self.model.conds = self._default_conds
            self._last_ref = None
        audio = self.model.generate(text, language_id="ru", **kw)
        return self.to_np(audio), self.sample_rate
