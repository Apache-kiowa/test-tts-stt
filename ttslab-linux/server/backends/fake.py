import numpy as np

from .base import Backend


class ToneBackend(Backend):
    sample_rate = 24000

    def load(self) -> None:
        self.log.info("Тестовая модель: нейросеть не загружается")

    def synth(self, text, s):
        sr = self.sample_rate
        dur = min(20.0, 0.3 + 0.06 * len(text))
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        freq = float(s.get("freq", 440))
        wave = 0.3 * np.sin(2 * np.pi * freq * t) * np.minimum(1, np.minimum(t, dur - t) * 20)
        return wave.astype(np.float32), sr
