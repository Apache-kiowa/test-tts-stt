from __future__ import annotations

import os

from .base import Backend


class XttsBackend(Backend):
    def load(self) -> None:
        os.environ.setdefault("COQUI_TOS_AGREED", "1")  # некоммерческая лицензия CPML
        from TTS.api import TTS

        device = self.opts.get("device", "cpu")
        self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
        self.sample_rate = int(self.tts.synthesizer.output_sample_rate)
        self.log.info("XTTS-v2 загружена на %s", device)

    def info(self):
        try:
            return {"voices": list(self.tts.speakers or [])}
        except Exception:
            return {}

    def synth(self, text, s):
        wav, _ = self.ref_paths(s.get("ref_voice"))
        kw = dict(
            temperature=float(s.get("temperature", 0.75)),
            length_penalty=float(s.get("length_penalty", 1.0)),
            repetition_penalty=float(s.get("repetition_penalty", 5.0)),
            top_k=int(s.get("top_k", 50)),
            top_p=float(s.get("top_p", 0.85)),
            speed=float(s.get("speed", 1.0)),
        )
        kw.update(self.extra(s))
        if wav:
            audio = self.tts.tts(text=text, speaker_wav=wav, language="ru", split_sentences=False, **kw)
        else:
            audio = self.tts.tts(text=text, speaker=s.get("speaker", "Ana Florence"), language="ru", split_sentences=False, **kw)
        return self.to_np(audio), self.sample_rate
